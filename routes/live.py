import json
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
        old.ended_at = utcnow()
        livekit_util.close_room(old.room)
    s = LiveSession(title=title, subject_id=subject_id, room="live-" + secrets.token_hex(8))
    db.session.add(s)
    db.session.commit()
    push.notify_live_started(s)
    flash("بدأ البث. كل المشتركين يشوفون زر الانضمام هسه.", "ok")
    return redirect(url_for("live.room"))


@bp.route("/end", methods=["POST"])
@admin_required
def end():
    s = LiveSession.current()
    if s:
        s.ended_at = utcnow()
        db.session.commit()
        livekit_util.close_room(s.room)
        flash("تم إنهاء البث.", "ok")
    return redirect(url_for("admin.dashboard"))


# ---------------- teacher moderation (JSON, called from the live page with the CSRF header) ----------------
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


def _role(metadata):
    try:
        return json.loads(metadata or "{}").get("role", "student")
    except (ValueError, AttributeError):
        return "student"


@bp.route("/<int:session_id>/participants")
@admin_json
def participants(session_id):
    s = _running(session_id)
    if not s:
        return jsonify(error="هذا البث منتهي."), 404
    if not livekit_util.configured():
        return jsonify(error="البث غير مضبوط بالسيرفر (LiveKit)."), 503
    try:
        people = livekit_util.list_participants(s.room)
    except Exception as e:
        current_app.logger.warning("LiveKit ListParticipants failed: %s", e)
        return jsonify(error="ما كدرنا نجيب قائمة الحاضرين من LiveKit."), 502
    return jsonify(participants=[{"identity": p["identity"], "name": p["name"],
                                  "is_teacher": _role(p["metadata"]) == "teacher",
                                  "mic": p["mic"], "camera": p["camera"], "screen": p["screen"]} for p in people])


def _target(session_id, identity):
    """(session, student) or an error response."""
    s = _running(session_id)
    if not s:
        return None, (jsonify(error="هذا البث منتهي."), 404)
    m = IDENTITY_RE.match(identity)
    user = db.session.get(User, int(m.group(1))) if m else None
    if not user:
        return None, (jsonify(error="المشارك غير موجود."), 404)
    if user.is_admin:
        return None, (jsonify(error="ما تكدرين تكتمين أو تطلعين الأستاذة/الأدمن."), 400)
    if not livekit_util.configured():
        return None, (jsonify(error="البث غير مضبوط بالسيرفر (LiveKit)."), 503)
    return (s, user), None


@bp.route("/<int:session_id>/mute/<identity>", methods=["POST"])
@admin_json
def mute(session_id, identity):
    target, error = _target(session_id, identity)
    if error:
        return error
    s, _ = target
    try:
        n = livekit_util.mute_participant(s.room, identity)
    except Exception as e:
        current_app.logger.warning("LiveKit mute failed: %s", e)
        return jsonify(error="ما كدرنا نكتم الطالب. يمكن طلع من البث."), 502
    return jsonify(ok=True, muted=n)


@bp.route("/<int:session_id>/kick/<identity>", methods=["POST"])
@admin_json
def kick(session_id, identity):
    target, error = _target(session_id, identity)
    if error:
        return error
    s, user = target
    if not LiveKick.exists(s, user):
        db.session.add(LiveKick(session_id=s.id, user_id=user.id))
        db.session.commit()
    try:
        livekit_util.remove_participant(s.room, identity)
        removed = True
    except Exception as e:  # e.g. already left: the kick is saved anyway, so they cannot come back
        current_app.logger.info("LiveKit RemoveParticipant: %s", e)
        removed = False
    return jsonify(ok=True, removed=removed)
