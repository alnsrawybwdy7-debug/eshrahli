"""JSON API for the اشرحلي mobile app (Flutter).

Auth: `Authorization: Bearer <token>` from /api/v1/auth/login or /register. The API never reads the
browser session cookie, so it needs no CSRF token and a web page cannot call it on a user's behalf.
Inside the app there is no buying (Google Play payments policy): subscriptions are activated on the website.
"""
import hashlib
import re
import secrets
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request, send_file, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func

import livekit_util
import storage
from models import (KINDS, ApiToken, Content, DeployLog, LiveSession, PushDevice, SecurityEvent, SubCode,
                    Subject, User, db, utcnow)
from security import client_ip, limiter, log_event

bp = Blueprint("api", __name__, url_prefix="/api/v1")

USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,32}$")


# ---------------- helpers ----------------
def err(message, status=400, **extra):
    return jsonify(error=message, **extra), status


def iso(dt):
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(user):
    """New app login. One device per student: older app tokens and web sessions are ended."""
    if not user.is_admin and current_app.config["SINGLE_DEVICE"]:
        user.session_version += 1
        PushDevice.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        ApiToken.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    raw = "esh_" + secrets.token_urlsafe(32)
    device = (request.headers.get("X-Device") or request.user_agent.string or "")[:120]
    db.session.add(ApiToken(user_id=user.id, token_hash=hash_token(raw), ver=user.session_version, device=device))
    user.last_login_at = utcnow()
    db.session.commit()
    return raw


def api_auth(view):
    @wraps(view)
    def wrapped(*a, **kw):
        header = request.headers.get("Authorization", "")
        raw = header[7:].strip() if header.lower().startswith("bearer ") else ""
        tok = ApiToken.query.filter_by(token_hash=hash_token(raw)).first() if raw.startswith("esh_") else None
        user = tok.user if tok else None
        if not tok or user is None or user.is_blocked or tok.ver != user.session_version:
            return err("انتهت الجلسة. سجّل دخول مرة ثانية.", 401, reason="auth")
        now = utcnow()
        if not tok.last_used_at or (now - tok.last_used_at).total_seconds() > 300:
            tok.last_used_at = now
            db.session.commit()
        g.user, g.api_token = user, tok
        return view(*a, **kw)
    return wrapped


def user_json(u):
    return {
        "id": u.id, "name": u.name, "username": u.username, "is_admin": u.is_admin,
        "status": u.sub_status, "has_active_sub": u.has_active_sub, "all_access": u.all_access,
        "days_left": u.days_left,
        "subscriptions": [{"label": x["label"], "all": x["all"], "active": x["active"], "days": x["days"],
                           "expires": iso(x["expires"]), "subject_id": x.get("subject_id")}
                          for x in u.subscriptions],
    }


def content_json(c):
    return {"id": c.id, "kind": c.kind, "title": c.title, "description": c.description or "",
            "filename": c.filename, "mime": c.mime, "size": c.size or 0, "created_at": iso(c.created_at)}


def counts_for(subject_ids, include_hidden):
    q = db.session.query(Content.subject_id, Content.kind, func.count(Content.id))
    if not include_hidden:
        q = q.filter(Content.is_visible.is_(True))
    out = {}
    for sid, kind, n in q.filter(Content.subject_id.in_(subject_ids)).group_by(Content.subject_id, Content.kind):
        out.setdefault(sid, {})[kind] = n
    return out


def media_signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="api-media")


def visible_content(cid):
    c = db.session.get(Content, cid)
    if not c or (not g.user.is_admin and (not c.is_visible or not c.subject.is_visible)):
        return None
    return c


# ---------------- public ----------------
@bp.route("/health")
def health():
    c = current_app.config
    return jsonify(ok=True, site_name=c["SITE_NAME"], tagline=c["SITE_TAGLINE"],
                   teacher=c["ADMIN_NAME"], teacher_label=c["TEACHER_LABEL"])


@bp.route("/auth/login", methods=["POST"])
def login():
    d = body()
    username = str(d.get("username", "")).strip().lower()[:64]
    password = str(d.get("password", ""))
    if not (limiter.hit(f"login-ip:{client_ip()}", 25, 600) and limiter.hit(f"login-u:{username}", 8, 600)
            and limiter.hit(f"login-u-day:{username}", 40, 86400)):
        log_event("login_ratelimit", f"app:{username[:56]}")
        return err("محاولات كثيرة. انتظر 10 دقايق وجرّب مرة ثانية.", 429)
    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        log_event("login_fail", f"app:{username[:56]}")
        return err("اسم المستخدم أو كلمة المرور غلط.", 401)
    if user.is_blocked:
        return err("هذا الحساب موقوف. تواصل مع المطور.", 403)
    return jsonify(token=issue_token(user), user=user_json(user))


@bp.route("/auth/register", methods=["POST"])
def register():
    d = body()
    name = str(d.get("name", "")).strip()
    username = str(d.get("username", "")).strip().lower()
    password = str(d.get("password", ""))
    if not limiter.hit(f"register:{client_ip()}", 6, 3600):
        return err("سويت حسابات كثيرة من نفس الجهاز. جرّب بعدين.", 429)
    if not (2 <= len(name) <= 80):
        return err("اكتب اسمك الثلاثي (من 2 إلى 80 حرف).", field="name")
    if not USERNAME_RE.match(username):
        return err("اسم المستخدم لازم يكون 3-32 حرف إنكليزي أو أرقام أو _ أو .", field="username")
    if len(password) < 8:
        return err("كلمة المرور لازم تكون 8 أحرف أو أكثر.", field="password")
    if User.query.filter_by(username=username).first():
        return err("اسم المستخدم محجوز، اختار غيره.", field="username")
    user = User(name=name, username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify(token=issue_token(user), user=user_json(user)), 201


# ---------------- signed in ----------------
@bp.route("/auth/logout", methods=["POST"])
@api_auth
def logout():
    db.session.delete(g.api_token)
    db.session.commit()
    return jsonify(ok=True)


@bp.route("/push/register", methods=["POST"])
@api_auth
def push_register():
    """The app sends its Firebase token after login. Logging out deletes it with the login."""
    token = str(body().get("token", "")).strip()
    if not (20 <= len(token) <= 512) or not re.fullmatch(r"[A-Za-z0-9_:\-.]+", token):
        return err("رمز الإشعارات غير صالح.")
    if not limiter.hit(f"push:{g.user.id}", 30, 3600):
        return err("طلبات كثيرة، جرّب بعدين.", 429)
    now = utcnow()
    # One registration per app login: a reinstall gets a new Firebase token.
    PushDevice.query.filter(PushDevice.api_token_id == g.api_token.id, PushDevice.token != token).delete(
        synchronize_session=False)
    dev = PushDevice.query.filter_by(token=token).first()
    if dev:  # same phone, maybe a different account now
        dev.user_id, dev.api_token_id, dev.updated_at = g.user.id, g.api_token.id, now
    else:
        db.session.add(PushDevice(user_id=g.user.id, api_token_id=g.api_token.id, token=token,
                                  platform=str(body().get("platform", "android"))[:16], updated_at=now))
    db.session.commit()
    return jsonify(ok=True)


@bp.route("/me")
@api_auth
def me():
    return jsonify(user=user_json(g.user))


@bp.route("/subjects")
@api_auth
def subjects():
    q = Subject.query if g.user.is_admin else Subject.query.filter_by(is_visible=True)
    rows = q.order_by(Subject.position, Subject.id).all()
    counts = counts_for([s.id for s in rows], g.user.is_admin)
    items = [{"id": s.id, "name": s.name, "description": s.description or "",
              "open": g.user.can_access(s.id),
              "counts": {k: counts.get(s.id, {}).get(k, 0) for k in KINDS}} for s in rows]
    items.sort(key=lambda x: not x["open"])  # subscribed subjects first
    return jsonify(subjects=items)


@bp.route("/subjects/<int:sid>")
@api_auth
def subject(sid):
    s = db.session.get(Subject, sid)
    if not s or (not s.is_visible and not g.user.is_admin):
        return err("المادة غير موجودة.", 404)
    if not g.user.can_access(s.id):
        return err(f"مادة «{s.name}» مو ضمن اشتراكك.", 403, locked=True)
    q = Content.query.filter_by(subject_id=sid)
    if not g.user.is_admin:
        q = q.filter_by(is_visible=True)
    grouped = {k: [] for k in KINDS}
    for c in q.order_by(Content.position, Content.id).all():
        if c.kind in grouped:
            grouped[c.kind].append(content_json(c))
    return jsonify(subject={"id": s.id, "name": s.name, "description": s.description or "", "contents": grouped})


@bp.route("/contents/<int:cid>/open")
@api_auth
def open_content(cid):
    c = visible_content(cid)
    if not c:
        return err("الملف غير موجود.", 404)
    if not g.user.can_access(c.subject_id):
        return err(f"مادة «{c.subject.name}» مو ضمن اشتراكك.", 403, locked=True)
    if storage.r2_enabled():
        url = storage.view_url(c)
    else:
        t = media_signer().dumps({"c": c.id, "u": g.user.id})
        url = url_for("api.media", cid=c.id, t=t, _external=True)
    return jsonify(url=url, content=content_json(c), watermark=g.user.username)


@bp.route("/media/<int:cid>")
def media(cid):
    """Local-storage files for the app (no R2). The link is signed and expires; Range works for seeking."""
    if storage.r2_enabled():
        return err("غير متاح.", 404)
    try:
        data = media_signer().loads(request.args.get("t", ""), max_age=current_app.config["MEDIA_URL_TTL"])
    except SignatureExpired:
        return err("انتهت صلاحية الرابط.", 403)
    except BadSignature:
        return err("رابط غير صالح.", 403)
    c = db.session.get(Content, cid)
    user = db.session.get(User, data.get("u"))
    if not c or data.get("c") != cid or not user or user.is_blocked or not user.can_access(c.subject_id):
        return err("ما عندك صلاحية.", 403)
    try:
        path = storage.local_path(c.storage_key)
    except ValueError:
        return err("الملف غير موجود.", 404)
    return send_file(path, mimetype=c.mime, download_name=c.filename, conditional=True, max_age=0)


@bp.route("/live")
@api_auth
def live():
    s = LiveSession.current_for(g.user)
    if not s:
        return jsonify(live=False)
    return jsonify(live=True, id=s.id, title=s.title, subject=s.subject.name if s.subject else None,
                   started_at=iso(s.started_at))


@bp.route("/live/token", methods=["POST"])
@api_auth
def live_token():
    s = LiveSession.current_for(g.user)
    if not s:
        return err("ماكو بث هسه.", 404)
    if not livekit_util.configured():
        return err("البث غير مضبوط بالسيرفر (LiveKit).", 503)
    u = g.user
    name = f"{u.name} ({current_app.config['TEACHER_LABEL']})" if u.is_admin else u.name
    tok = livekit_util.join_token(identity=f"u{u.id}", name=name, room=s.room, is_admin=u.is_admin)
    return jsonify(url=current_app.config["LIVEKIT_URL"], token=tok, room=s.room,
                   is_admin=u.is_admin, identity=f"u{u.id}", title=s.title)


@bp.route("/account/password", methods=["POST"])
@api_auth
def change_password():
    d = body()
    current, new = str(d.get("current", "")), str(d.get("new", ""))
    if not limiter.hit(f"pwchange:{g.user.id}", 6, 900):
        return err("محاولات كثيرة، جرّب بعدين.", 429)
    if not g.user.check_password(current):
        return err("كلمة المرور الحالية غلط.", field="current")
    if len(new) < 8:
        return err("كلمة المرور الجديدة لازم تكون 8 أحرف أو أكثر.", field="new")
    g.user.set_password(new)
    g.user.session_version += 1  # ends every other login
    PushDevice.query.filter(PushDevice.user_id == g.user.id, PushDevice.api_token_id != g.api_token.id).delete(
        synchronize_session=False)
    ApiToken.query.filter(ApiToken.user_id == g.user.id, ApiToken.id != g.api_token.id).delete(
        synchronize_session=False)
    g.api_token.ver = g.user.session_version  # keep this phone signed in
    db.session.commit()
    return jsonify(ok=True)


@bp.route("/account/delete", methods=["POST"])
@api_auth
def delete_account():
    if g.user.is_admin:
        return err("حساب المدرّسة/الأدمن ما ينحذف من التطبيق.", 403)
    if not limiter.hit(f"delacct:{g.user.id}", 5, 900):
        return err("محاولات كثيرة، جرّب بعدين.", 429)
    if not g.user.check_password(str(body().get("password", ""))):
        return err("كلمة المرور غلط، ما انحذف الحساب.", field="password")
    uid = g.user.id
    SubCode.query.filter_by(used_by_id=uid).update({"used_by_id": None, "revoked": True}, synchronize_session=False)
    SecurityEvent.query.filter_by(user_id=uid).update({"user_id": None}, synchronize_session=False)
    DeployLog.query.filter_by(user_id=uid).update({"user_id": None}, synchronize_session=False)
    db.session.delete(g.user)  # its app tokens and subject subscriptions go with it
    db.session.commit()
    return jsonify(ok=True)
