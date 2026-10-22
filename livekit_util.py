"""LiveKit helpers: access tokens are plain HS256 JWTs, so no extra SDK is needed.

The server never connects to LiveKit (PythonAnywhere's free plan blocks it): it only signs tokens.
Room commands (mute, remove, delete room) are sent by the teacher's browser with the tokens below.
"""
import json
import time

import jwt
from flask import current_app


def configured():
    c = current_app.config
    return all([c["LIVEKIT_URL"], c["LIVEKIT_API_KEY"], c["LIVEKIT_API_SECRET"]])


def _sign(claims):
    c = current_app.config
    return jwt.encode(claims, c["LIVEKIT_API_SECRET"], algorithm="HS256")


def join_token(identity, name, room, is_admin, ttl=6 * 3600):
    c = current_app.config
    now = int(time.time())
    can_publish = True if is_admin else c["LIVE_STUDENTS_CAN_PUBLISH"]
    claims = {
        "iss": c["LIVEKIT_API_KEY"],
        "sub": identity,
        "name": name,
        "nbf": now - 10,
        "exp": now + ttl,
        "metadata": json.dumps({"role": "teacher" if is_admin else "student"}),
        "video": {
            "room": room,
            "roomJoin": True,
            "canSubscribe": True,
            "canPublish": can_publish,
            **({} if is_admin else {"canPublishSources": ["microphone", "camera"]}),
            "canPublishData": True,
            "roomAdmin": bool(is_admin),
        },
    }
    return _sign(claims)


def https_url():
    """LIVEKIT_URL as the https:// base of LiveKit's RoomService (Twirp) API, for the browser."""
    url = current_app.config["LIVEKIT_URL"].rstrip("/")
    return "https://" + url.split("://", 1)[1] if url.startswith(("wss://", "https://")) else ""


def _server_token(grants, ttl):
    c = current_app.config
    now = int(time.time())
    return _sign({"iss": c["LIVEKIT_API_KEY"], "sub": "teacher-admin", "nbf": now - 10, "exp": now + ttl,
                  "video": grants})


ADMIN_TOKEN_TTL = 600


def admin_token(room, ttl=ADMIN_TOKEN_TTL):
    """roomAdmin for this one room only: list, mute and remove participants. Made locally, no network."""
    return _server_token({"roomAdmin": True, "room": room}, ttl)


def delete_room_token(ttl=60):
    """LiveKit's DeleteRoom needs the roomCreate grant (it cannot be limited to one room), so this token
    is only handed out by /live/end, right when the session ends, and lives one minute."""
    return _server_token({"roomCreate": True}, ttl)
