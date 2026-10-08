import re
from datetime import timedelta

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)
from sqlalchemy import func

import storage
from models import KINDS, Content, LiveSession, SubCode, Subject, db, utcnow
from security import client_ip, limiter, login_required, subscription_required

bp = Blueprint("student", __name__)


def normalize_code(raw):
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def kind_counts(subject_ids=None, include_hidden=False):
    q = db.session.query(Content.subject_id, Content.kind, func.count(Content.id))
    if not include_hidden:
        q = q.filter(Content.is_visible.is_(True))
    if subject_ids is not None:
        q = q.filter(Content.subject_id.in_(subject_ids))
    out = {}
    for sid, kind, n in q.group_by(Content.subject_id, Content.kind):
        out.setdefault(sid, {})[kind] = n
    return out


def get_visible_content(cid):
    c = db.session.get(Content, cid) or abort(404)
    if not g.user.is_admin and (not c.is_visible or not c.subject.is_visible):
        abort(404)
    return c


@bp.route("/")
@login_required
def home():
    if not g.user.has_active_sub:
        return redirect(url_for("student.subscribe"))
    q = Subject.query
    if not g.user.is_admin:
        q = q.filter_by(is_visible=True)
    subjects = q.order_by(Subject.position, Subject.id).all()
    counts = kind_counts([s.id for s in subjects], include_hidden=g.user.is_admin)
    return render_template("student/home.html", subjects=subjects, counts=counts)


@bp.route("/subscribe", methods=["GET", "POST"])
@login_required
def subscribe():
    if request.method == "POST":
        if not (limiter.hit(f"redeem:{g.user.id}", 6, 900) and limiter.hit(f"redeem-ip:{client_ip()}", 20, 900)):
            flash("جرّبت رموز كثيرة. انتظر ربع ساعة وجرّب مرة ثانية.", "error")
            return redirect(url_for("student.subscribe"))
        raw = normalize_code(request.form.get("code"))
        code = SubCode.query.filter_by(code=raw).first() if 8 <= len(raw) <= 32 else None
        if not code or code.used_by_id or code.revoked:
            flash("الرمز غير صحيح أو مستخدم من قبل.", "error")
            return redirect(url_for("student.subscribe"))
        now = utcnow()
        # Atomic claim: only succeeds if nobody used it in the meantime
        claimed = (SubCode.query.filter_by(id=code.id, used_by_id=None, revoked=False)
                   .update({"used_by_id": g.user.id, "used_at": now}, synchronize_session=False))
        if claimed != 1:
            db.session.rollback()
            flash("الرمز غير صحيح أو مستخدم من قبل.", "error")
            return redirect(url_for("student.subscribe"))
        base = g.user.sub_expires_at if g.user.sub_expires_at and g.user.sub_expires_at > now else now
        g.user.sub_expires_at = base + timedelta(days=code.days)
        db.session.commit()
        flash(f"تم تفعيل اشتراكك لمدة {code.days} يوم. بالتوفيق!", "ok")
        return redirect(url_for("student.home"))
    c = current_app.config
    return render_template("student/subscribe.html", whatsapp=c["CONTACT_WHATSAPP"],
                           telegram=c["CONTACT_TELEGRAM"], developer=c["DEVELOPER_NAME"])


@bp.route("/subject/<int:sid>")
@subscription_required
def subject(sid):
    s = db.session.get(Subject, sid) or abort(404)
    if not s.is_visible and not g.user.is_admin:
        abort(404)
    tab = request.args.get("tab", "video")
    if tab not in KINDS:
        tab = "video"
    q = Content.query.filter_by(subject_id=sid, kind=tab)
    if not g.user.is_admin:
        q = q.filter_by(is_visible=True)
    items = q.order_by(Content.position, Content.id).all()
    counts = kind_counts([sid], include_hidden=g.user.is_admin).get(sid, {})
    return render_template("student/subject.html", s=s, tab=tab, items=items, counts=counts)


@bp.route("/watch/<int:cid>")
@subscription_required
def watch(cid):
    c = get_visible_content(cid)
    if c.kind != "video":
        return redirect(url_for("student.file", cid=cid))
    q = Content.query.filter_by(subject_id=c.subject_id, kind="video")
    if not g.user.is_admin:
        q = q.filter_by(is_visible=True)
    playlist = q.order_by(Content.position, Content.id).all()
    return render_template("student/watch.html", c=c, playlist=playlist, src=storage.view_url(c))


@bp.route("/file/<int:cid>")
@subscription_required
def file(cid):
    c = get_visible_content(cid)
    return redirect(storage.view_url(c, download=request.args.get("dl") == "1"))


@bp.route("/media/<path:key>")
@subscription_required
def media(key):
    """Local-storage file serving (only used when R2 isn't configured). Supports Range for video seeking."""
    if storage.r2_enabled() or not storage.valid_key(key):
        abort(404)
    c = Content.query.filter_by(storage_key=key).first_or_404()
    get_visible_content(c.id)
    try:
        path = storage.local_path(key)
    except ValueError:
        abort(404)
    return send_file(path, mimetype=c.mime, as_attachment=request.args.get("dl") == "1",
                     download_name=c.filename, conditional=True, max_age=0)


@bp.route("/api/live-status")
def live_status():
    if not g.user or not g.user.has_active_sub:
        return jsonify(live=False)
    s = LiveSession.current()
    if not s:
        return jsonify(live=False)
    return jsonify(live=True, id=s.id, title=s.title, url=url_for("live.room"))
