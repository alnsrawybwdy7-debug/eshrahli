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
            **({} if is_admin else {"canPublishSources": ["microphone", "camera"]}),
            "canPublishData": True,
            "roomAdmin": bool(is_admin),
        },
    }
    return _sign(claims)


def _room_service(method, payload, grants, timeout=5):
    """Call LiveKit's RoomService (Twirp, JSON) with a short-lived server token. Raises on any failure."""
    c = current_app.config
    base = c["LIVEKIT_URL"].replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("LIVEKIT_URL must be wss:// or https://")
    now = int(time.time())
    token = _sign({"iss": c["LIVEKIT_API_KEY"], "sub": "server", "nbf": now - 10, "exp": now + 60,
                   "video": grants})
    req = urllib.request.Request(
        f"{base}/twirp/livekit.RoomService/{method}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return json.loads(raw or b"{}")


def close_room(room):
    """Best-effort: delete the room on LiveKit so everyone is disconnected immediately."""
    if not configured():
        return
    try:
        _room_service("DeleteRoom", {"room": room}, {"roomCreate": True})
    except Exception as e:
        current_app.logger.info("LiveKit DeleteRoom skipped: %s", e)


def _admin(room):
    return {"roomAdmin": True, "room": room}


# Twirp JSON may send enums as names or numbers, and leaves out zero values (AUDIO=0, UNKNOWN=0)
def _is_data(track):
    return track.get("type") in ("DATA", 2)


def _source(track):
    return {1: "CAMERA", 2: "MICROPHONE", 3: "SCREEN_SHARE", 4: "SCREEN_SHARE_AUDIO"}.get(
        track.get("source"), track.get("source") or "UNKNOWN")


def list_participants(room):
    """Everyone in the room right now: identity, name, metadata and which tracks are live (not muted)."""
    data = _room_service("ListParticipants", {"room": room}, _admin(room))
    out = []
    for p in data.get("participants", []):
        live = {_source(t) for t in p.get("tracks", []) if not _is_data(t) and not t.get("muted")}
        out.append({"identity": p.get("identity", ""), "name": p.get("name", ""), "metadata": p.get("metadata", ""),
                    "mic": "MICROPHONE" in live, "camera": "CAMERA" in live,
                    "screen": bool(live & {"SCREEN_SHARE", "SCREEN_SHARE_AUDIO"})})
    return out


def mute_participant(room, identity):
    """Mute every audio/video track this participant publishes. Returns how many were muted."""
    p = _room_service("GetParticipant", {"room": room, "identity": identity}, _admin(room))
    n = 0
    for t in p.get("tracks", []):
        if _is_data(t) or t.get("muted") or not t.get("sid"):
            continue
        _room_service("MutePublishedTrack", {"room": room, "identity": identity, "track_sid": t["sid"],
                                             "muted": True}, _admin(room))
        n += 1
    return n


def remove_participant(room, identity):
    _room_service("RemoveParticipant", {"room": room, "identity": identity}, _admin(room))
