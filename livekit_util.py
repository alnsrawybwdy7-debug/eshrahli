"""LiveKit helpers: access tokens are plain HS256 JWTs, so no extra SDK is needed."""
import json
import time
import urllib.request

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
            "canPublishData": True,
            "roomAdmin": bool(is_admin),
        },
    }
    return _sign(claims)


def close_room(room):
    """Best-effort: delete the room on LiveKit so everyone is disconnected immediately."""
    if not configured():
        return
    c = current_app.config
    base = c["LIVEKIT_URL"].replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
    now = int(time.time())
    token = _sign({"iss": c["LIVEKIT_API_KEY"], "sub": "server", "nbf": now - 10, "exp": now + 60,
                   "video": {"roomCreate": True}})
    req = urllib.request.Request(
        f"{base}/twirp/livekit.RoomService/DeleteRoom",
        data=json.dumps({"room": room}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        current_app.logger.info("LiveKit DeleteRoom skipped: %s", e)
