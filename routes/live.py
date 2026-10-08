import secrets

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

import livekit_util
from models import LiveSession, Subject, db, utcnow
from security import admin_required, subscription_required

bp = Blueprint("live", __name__, url_prefix="/live")


@bp.route("/")
@subscription_required
def room():
    s = LiveSession.current()
    return render_template("live/room.html", s=s)


@bp.route("/token", methods=["POST"])
@subscription_required
def token():
    s = LiveSession.current()
    if not s:
        return jsonify(error="ماكو بث هسه."), 404
    if not livekit_util.configured():
        return jsonify(error="البث غير مضبوط بالسيرفر (LiveKit)."), 503
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
