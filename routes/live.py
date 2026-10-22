import re
import secrets
from functools import wraps

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

import livekit_util
import push
from models import LiveKick, LiveSession, Subject, User, db, utcnow
from security import admin_required, subscription_required

bp = Blueprint("live", __name__, url_prefix="/live")

KICKED_MSG = "انطلعت من البث من قبل الأستاذة."
IDENTITY_RE = re.compile(r"^u(\d{1,10})$")  # LiveKit identity = "u" + user id (see token())


@bp.route("/")
@subscription_required
def room():
    s = LiveSession.current_for(g.user)
    subjects = Subject.query.order_by(Subject.position, Subject.id).all() if g.user.is_admin else []
    return render_template("live/room.html", s=s, subjects=subjects)


@bp.route("/token", methods=["POST"])
@subscription_required
def token():
    s = LiveSession.current_for(g.user)
    if not s:
        return jsonify(error="ماكو بث هسه."), 404
    if not livekit_util.configured():
        return jsonify(error="البث غير مضبوط بالسيرفر (LiveKit)."), 503
    if LiveKick.exists(s, g.user):
        return jsonify(error=KICKED_MSG, kicked=True, home=url_for("student.home")), 403
    u = g.user
    name = f"{u.name} ({current_app.config['TEACHER_LABEL']})" if u.is_admin else u.name
    tok = livekit_util.join_token(identity=f"u{u.id}", name=name, room=s.room, is_admin=u.is_admin)
    return jsonify(url=current_app.config["LIVEKIT_URL"], token=tok, room=s.room,
                   is_admin=u.is_admin, identity=f"u{u.id}")


@bp.route("/start", methods=["POST"])
@admin_required
def start():
    title = request.form.get("title", "").strip()[:200] or "بث مباشر"
    subject_id = request.form.get("subject_id", type=int)
    if subject_id and not db.session.get(Subject, subject_id):
        subject_id = None
    for old in LiveSession.query.filter(LiveSession.ended_at.is_(None)).all():
        old.ended_at = utcnow()  # its students see "ended" on their next status check
    s = LiveSession(title=title, subject_id=subject_id, room="live-" + secrets.token_hex(8))
    db.session.add(s)
    db.session.commit()
    push.notify_live_started(s)
    flash("بدأ البث. كل المشتركين يشوفون زر الانضمام هسه.", "ok")
    return redirect(url_for("live.room"))


@bp.route("/end", methods=["POST"])
@admin_required
def end():
    """Marks the session ended. The server cannot reach LiveKit, so the live page (fetch, Accept: JSON)
    gets a one-minute token and deletes the room from the teacher's browser."""
    s = LiveSession.current()
    room = None
    if s:
        s.ended_at = utcnow()
        db.session.commit()
        room = s.room
        flash("تم إنهاء البث.", "ok")
    if request.accept_mimetypes.best == "application/json":
        lk = None
        if room and livekit_util.configured() and livekit_util.https_url():
            lk = {"url": livekit_util.https_url(), "room": room, "token": livekit_util.delete_room_token()}
        return jsonify(ok=True, redirect=url_for("admin.dashboard"), livekit=lk)
    return redirect(url_for("admin.dashboard"))


# ---------------- teacher moderation ----------------
# The teacher's browser sends mute / remove to LiveKit itself with the admin token from here.
def admin_json(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            return jsonify(error="سجّل دخول أول."), 401
        if not g.user.is_admin:
            return jsonify(error="ما عندك صلاحية."), 403
        return view(*a, **kw)
    return wrapped


def _running(session_id):
    s = db.session.get(LiveSession, session_id)
    return s if s and s.ended_at is None else None


@bp.route("/<int:session_id>/admin-token", methods=["POST"])
@admin_json
def admin_token(session_id):
    s = _running(session_id)
    if not s:
        return jsonify(error="هذا البث منتهي."), 404
    if not livekit_util.configured() or not livekit_util.https_url():
        return jsonify(error="البث غير مضبوط بالسيرفر (LiveKit)."), 503
    return jsonify(token=livekit_util.admin_token(s.room), url=livekit_util.https_url(), room=s.room,
                   expires_in=livekit_util.ADMIN_TOKEN_TTL)


@bp.route("/<int:session_id>/kick/<identity>", methods=["POST"])
@admin_json
def kick(session_id, identity):
    """Only records the kick (the browser then removes them from the room): the token endpoints
    of the website and the app refuse this student until the session ends."""
    s = _running(session_id)
    if not s:
        return jsonify(error="هذا البث منتهي."), 404
    m = IDENTITY_RE.match(identity)
    user = db.session.get(User, int(m.group(1))) if m else None
    if not user:
        return jsonify(error="المشارك غير موجود."), 404
    if user.is_admin:
        return jsonify(error="ما تكدرين تطلعين الأستاذة/الأدمن."), 400
    if not LiveKick.exists(s, user):
        db.session.add(LiveKick(session_id=s.id, user_id=user.id))
        db.session.commit()
    return jsonify(ok=True)
