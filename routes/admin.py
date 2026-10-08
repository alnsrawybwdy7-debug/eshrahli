import os
import secrets
from datetime import timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, or_

import storage
from models import KINDS, Content, LiveSession, SubCode, Subject, User, db, utcnow
from routes.student import kind_counts
from security import admin_required, csrf_token

bp = Blueprint("admin", __name__, url_prefix="/admin")

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L confusion


def new_code():
    while True:
        c = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
        if not SubCode.query.filter_by(code=c).first():
            return c


def back(default):
    ref = request.form.get("back") or ""
    return redirect(ref if ref.startswith("/") and not ref.startswith("//") else default)


# ---------------- Dashboard ----------------
@bp.route("/")
@admin_required
def dashboard():
    now = utcnow()
    stats = {
        "students": User.query.filter_by(is_admin=False).count(),
        "active": User.query.filter(User.is_admin.is_(False), User.sub_expires_at > now).count(),
        "unused_codes": SubCode.query.filter_by(used_by_id=None, revoked=False).count(),
        "subjects": Subject.query.count(),
        "videos": Content.query.filter_by(kind="video").count(),
        "files": Content.query.filter(Content.kind != "video").count(),
    }
    recent = (SubCode.query.filter(SubCode.used_by_id.isnot(None))
              .order_by(SubCode.used_at.desc()).limit(8).all())
    expiring = (User.query.filter(User.is_admin.is_(False), User.sub_expires_at > now,
                                  User.sub_expires_at < now + timedelta(days=3))
                .order_by(User.sub_expires_at).limit(8).all())
    subjects = Subject.query.order_by(Subject.position, Subject.id).all()
    return render_template("admin/dashboard.html", stats=stats, recent=recent, expiring=expiring,
                           subjects=subjects, live=LiveSession.current())


# ---------------- Subjects ----------------
@bp.route("/subjects", methods=["GET", "POST"])
@admin_required
def subjects():
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:150]
        if not name:
            flash("اكتب اسم المادة.", "error")
        else:
            pos = (db.session.query(func.max(Subject.position)).scalar() or 0) + 1
            s = Subject(name=name, description=request.form.get("description", "").strip()[:2000], position=pos)
            db.session.add(s)
            db.session.commit()
            flash("تمت إضافة المادة.", "ok")
            return redirect(url_for("admin.subject", sid=s.id))
    items = Subject.query.order_by(Subject.position, Subject.id).all()
    counts = kind_counts([s.id for s in items], include_hidden=True)
    return render_template("admin/subjects.html", subjects=items, counts=counts)


@bp.route("/subjects/<int:sid>", methods=["GET", "POST"])
@admin_required
def subject(sid):
    s = db.session.get(Subject, sid) or abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:150]
        if name:
            s.name = name
        s.description = request.form.get("description", "").strip()[:2000]
        s.is_visible = request.form.get("is_visible") == "1"
        db.session.commit()
        flash("تم حفظ المادة.", "ok")
        return redirect(url_for("admin.subject", sid=sid, tab=request.args.get("tab", "video")))
    tab = request.args.get("tab", "video")
    if tab not in KINDS:
        tab = "video"
    items = Content.query.filter_by(subject_id=sid, kind=tab).order_by(Content.position, Content.id).all()
    counts = kind_counts([sid], include_hidden=True).get(sid, {})
    accept = ",".join(storage.VIDEO_EXT if tab == "video" else storage.DOC_EXT)
    return render_template("admin/subject.html", s=s, tab=tab, items=items, counts=counts, accept=accept)


@bp.route("/subjects/<int:sid>/delete", methods=["POST"])
@admin_required
def subject_delete(sid):
    s = db.session.get(Subject, sid) or abort(404)
    if request.form.get("confirm_name", "").strip() != s.name:
        flash("حتى تحذف المادة اكتب اسمها بالضبط.", "error")
        return redirect(url_for("admin.subject", sid=sid))
    keys = [c.storage_key for c in s.contents]
    LiveSession.query.filter_by(subject_id=sid).update({"subject_id": None})
    db.session.delete(s)
    db.session.commit()
    for k in keys:
        storage.delete(k)
    flash("تم حذف المادة وكل محتواها.", "ok")
    return redirect(url_for("admin.subjects"))


@bp.route("/subjects/<int:sid>/move", methods=["POST"])
@admin_required
def subject_move(sid):
    s = db.session.get(Subject, sid) or abort(404)
    _move(s, Subject.query, request.form.get("dir"))
    return redirect(url_for("admin.subjects"))


def _move(obj, base_query, direction):
    model = type(obj)
    items = base_query.order_by(model.position, model.id).all()
    for i, it in enumerate(items):  # normalize positions
        it.position = i
    idx = items.index(obj)
    j = idx - 1 if direction == "up" else idx + 1
    if 0 <= j < len(items):
        items[idx].position, items[j].position = items[j].position, items[idx].position
    db.session.commit()


# ---------------- Content upload ----------------
@bp.route("/api/upload-url", methods=["POST"])
@admin_required
def upload_url():
    data = request.get_json(silent=True) or {}
    sid = data.get("subject_id")
    kind = data.get("kind")
    filename = (data.get("filename") or "").strip()
    size = int(data.get("size") or 0)
    if kind not in KINDS or not isinstance(sid, int) or not db.session.get(Subject, sid):
        return jsonify(error="طلب غير صالح."), 400
    mime = storage.allowed(kind, filename)
    if not mime:
        allowed = "، ".join(storage.VIDEO_EXT if kind == "video" else storage.DOC_EXT)
        return jsonify(error=f"نوع الملف غير مسموح. المسموح: {allowed}"), 400
    if size <= 0 or size > storage.MAX_SIZE[kind]:
        return jsonify(error="حجم الملف غير مسموح."), 400
    key = storage.make_key(sid, kind, filename)
    target = storage.upload_target(key, mime, csrf_token())
    return jsonify(key=key, mime=mime, **target)


@bp.route("/local-upload/<path:key>", methods=["PUT"])
@admin_required
def local_upload(key):
    if storage.r2_enabled() or not storage.valid_key(key):
        abort(404)
    path = storage.local_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        while True:
            chunk = request.stream.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return jsonify(ok=True)


@bp.route("/subjects/<int:sid>/content", methods=["POST"])
@admin_required
def content_create(sid):
    s = db.session.get(Subject, sid) or abort(404)
    kind = request.form.get("kind")
    key = request.form.get("key", "")
    filename = request.form.get("filename", "").strip()[:255] or "file"
    if kind not in KINDS or not storage.valid_key(key) or not key.startswith(f"subjects/{sid}/{kind}/"):
        return jsonify(error="بيانات غير صالحة."), 400
    mime = storage.allowed(kind, filename)
    if not mime:
        return jsonify(error="نوع الملف غير مسموح."), 400
    size = storage.stored_size(key)
    if size is None:
        return jsonify(error="الملف ما وصل للتخزين. جرّب ترفعه مرة ثانية."), 400
    if size == storage.UNVERIFIED:
        size = max(0, min(request.form.get("size", type=int) or 0, storage.MAX_SIZE[kind]))
    if Content.query.filter_by(storage_key=key).first():
        return jsonify(error="الملف مضاف من قبل."), 400
    title = request.form.get("title", "").strip()[:200] or os.path.splitext(filename)[0][:200]
    pos = (db.session.query(func.max(Content.position))
           .filter_by(subject_id=sid, kind=kind).scalar() or 0) + 1
    c = Content(subject_id=s.id, kind=kind, title=title, filename=filename, mime=mime, size=size,
                description=request.form.get("description", "").strip()[:2000], storage_key=key,
                position=pos, is_visible=request.form.get("is_visible", "1") == "1")
    db.session.add(c)
    db.session.commit()
    return jsonify(ok=True, id=c.id)


@bp.route("/content/<int:cid>/edit", methods=["POST"])
@admin_required
def content_edit(cid):
    c = db.session.get(Content, cid) or abort(404)
    title = request.form.get("title", "").strip()[:200]
    if title:
        c.title = title
    c.description = request.form.get("description", "").strip()[:2000]
    db.session.commit()
    flash("تم الحفظ.", "ok")
    return redirect(url_for("admin.subject", sid=c.subject_id, tab=c.kind))


@bp.route("/content/<int:cid>/action", methods=["POST"])
@admin_required
def content_action(cid):
    c = db.session.get(Content, cid) or abort(404)
    action = request.form.get("action")
    dest = url_for("admin.subject", sid=c.subject_id, tab=c.kind)
    if action == "toggle":
        c.is_visible = not c.is_visible
        db.session.commit()
    elif action in ("up", "down"):
        _move(c, Content.query.filter_by(subject_id=c.subject_id, kind=c.kind), action)
    elif action == "delete":
        key = c.storage_key
        db.session.delete(c)
        db.session.commit()
        storage.delete(key)
        flash("تم الحذف.", "ok")
    return redirect(dest)


# ---------------- Subscription codes ----------------
@bp.route("/codes", methods=["GET", "POST"])
@admin_required
def codes():
    fresh = []
    if request.method == "POST":
        count = max(1, min(100, request.form.get("count", type=int) or 1))
        days = request.form.get("days", type=int) or 0
        if not (1 <= days <= 730):
            flash("المدة لازم تكون بين 1 و 730 يوم.", "error")
        else:
            note = request.form.get("note", "").strip()[:200]
            for _ in range(count):
                code = SubCode(code=new_code(), days=days, note=note)
                db.session.add(code)
                db.session.flush()
                fresh.append(code)
            db.session.commit()
            flash(f"تم توليد {count} رمز بمدة {days} يوم.", "ok")
    status = request.args.get("status", "unused")
    q = SubCode.query
    if status == "unused":
        q = q.filter_by(used_by_id=None, revoked=False)
    elif status == "used":
        q = q.filter(SubCode.used_by_id.isnot(None))
    elif status == "revoked":
        q = q.filter_by(revoked=True)
    items = q.order_by(SubCode.created_at.desc(), SubCode.id.desc()).limit(500).all()
    return render_template("admin/codes.html", items=items, status=status, fresh=fresh)


@bp.route("/codes/<int:code_id>/revoke", methods=["POST"])
@admin_required
def code_revoke(code_id):
    code = db.session.get(SubCode, code_id) or abort(404)
    if code.used_by_id:
        flash("الرمز مستخدم، ما ينلغى. إذا تريد توقف الطالب روح لصفحة الطلاب.", "error")
    else:
        code.revoked = True
        db.session.commit()
        flash("تم إلغاء الرمز.", "ok")
    return redirect(url_for("admin.codes", status=request.args.get("status", "unused")))


# ---------------- Students ----------------
@bp.route("/users")
@admin_required
def users():
    qtext = request.args.get("q", "").strip()
    status = request.args.get("status", "all")
    now = utcnow()
    q = User.query.filter_by(is_admin=False)
    if qtext:
        like = f"%{qtext}%"
        q = q.filter(or_(User.name.ilike(like), User.username.ilike(like)))
    if status == "active":
        q = q.filter(User.sub_expires_at > now)
    elif status == "expired":
        q = q.filter(User.sub_expires_at <= now)
    elif status == "none":
        q = q.filter(User.sub_expires_at.is_(None))
    elif status == "blocked":
        q = q.filter_by(is_blocked=True)
    items = q.order_by(User.created_at.desc()).limit(500).all()
    return render_template("admin/users.html", items=items, q=qtext, status=status)


@bp.route("/users/<int:uid>/action", methods=["POST"])
@admin_required
def user_action(uid):
    u = db.session.get(User, uid) or abort(404)
    if u.is_admin:
        abort(400)
    action = request.form.get("action")
    now = utcnow()
    if action == "add_days":
        days = request.form.get("days", type=int) or 0
        if not (-730 <= days <= 730) or days == 0:
            flash("عدد أيام غير صالح.", "error")
        else:
            base = u.sub_expires_at if u.sub_expires_at and u.sub_expires_at > now else now
            u.sub_expires_at = base + timedelta(days=days)
            flash(f"تم تعديل اشتراك {u.name}.", "ok")
    elif action == "end_sub":
        u.sub_expires_at = now
        flash(f"تم إنهاء اشتراك {u.name}.", "ok")
    elif action == "block":
        u.is_blocked = True
        u.session_version += 1
        flash(f"تم إيقاف حساب {u.name}.", "ok")
    elif action == "unblock":
        u.is_blocked = False
        flash(f"تم تفعيل حساب {u.name}.", "ok")
    elif action == "logout_all":
        u.session_version += 1
        flash(f"تم تسجيل خروج {u.name} من كل الأجهزة.", "ok")
    elif action == "reset_pw":
        temp = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))
        u.set_password(temp)
        u.session_version += 1
        flash(f"كلمة المرور المؤقتة لـ {u.username}: {temp} — انطيها للطالب وكله يغيرها من حسابي.", "ok")
    db.session.commit()
    return back(url_for("admin.users"))
