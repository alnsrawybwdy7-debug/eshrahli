"""Notifications to the mobile app through Firebase Cloud Messaging (HTTP v1 API).

Set FCM_SERVICE_ACCOUNT in .env to the service-account JSON file of the Firebase project. Without it
everything here is a silent no-op, so the site works the same with or without notifications.

Only the standard library and PyJWT (RS256, needs `cryptography`) are used: no Google SDK.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt
from flask import current_app

from models import KIND_SINGULAR, ApiToken, PushDevice, User, db

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
CHANNEL_ID = "eshrahli_default"  # created by the app (MainActivity.kt)

_lock = threading.Lock()
_cache = {"path": None, "account": None, "access": None, "exp": 0}


def configured():
    return bool(current_app.config.get("FCM_SERVICE_ACCOUNT"))


def _account(path):
    with _lock:
        if _cache["path"] != path:
            with open(path, encoding="utf-8") as f:
                acc = json.load(f)
            for key in ("client_email", "private_key", "project_id"):
                if not acc.get(key):
                    raise ValueError(f"service account file is missing {key}")
            _cache.update(path=path, account=acc, access=None, exp=0)
        return _cache["account"]


def _access_token(acc):
    """OAuth access token for FCM, cached until a minute before it expires."""
    with _lock:
        if _cache["access"] and _cache["exp"] - 60 > time.time():
            return _cache["access"]
        now = int(time.time())
        token_uri = acc.get("token_uri") or DEFAULT_TOKEN_URI
        assertion = jwt.encode(
            {"iss": acc["client_email"], "scope": FCM_SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600},
            acc["private_key"], algorithm="RS256", headers={"kid": acc.get("private_key_id", "")},
        )
        body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                       "assertion": assertion}).encode()
        req = urllib.request.Request(token_uri, data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        _cache["access"] = data["access_token"]
        _cache["exp"] = now + int(data.get("expires_in", 3600))
        return _cache["access"]


def _send_one(project_id, access, token, title, body, data):
    """Returns "ok", "gone" (the phone uninstalled the app / token dead) or "error"."""
    message = {"message": {
        "token": token,
        "notification": {"title": title, "body": body},
        "data": {k: str(v) for k, v in data.items()},
        "android": {"priority": "high", "notification": {
            "channel_id": CHANNEL_ID, "icon": "ic_stat_eshrahli", "color": "#0F6B5C", "sound": "default"}},
    }}
    req = urllib.request.Request(
        f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
        data=json.dumps(message).encode(),
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return "ok"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        if e.code == 404 or "UNREGISTERED" in detail or (e.code == 400 and "registration token" in detail.lower()):
            return "gone"
        return "error"
    except Exception:
        return "error"


def _deliver(app, rows, title, body, data):
    with app.app_context():
        try:
            acc = _account(app.config["FCM_SERVICE_ACCOUNT"])
            access = _access_token(acc)
        except Exception as e:
            app.logger.warning("FCM: could not get an access token: %s", e)
            return {"ok": 0, "gone": 0, "error": len(rows)}
        stats = {"ok": 0, "gone": 0, "error": 0}
        gone = []
        for device_id, token in rows:
            result = _send_one(acc["project_id"], access, token, title, body, data)
            stats[result] += 1
            if result == "gone":
                gone.append(device_id)
        if gone:
            PushDevice.query.filter(PushDevice.id.in_(gone)).delete(synchronize_session=False)
            db.session.commit()
        if stats["error"]:
            app.logger.info("FCM: %s", stats)
        return stats


def notify_users(users, title, body, data):
    """Send to every app login of these users that is still valid. Returns how many phones were targeted."""
    if not configured():
        return 0
    ids = [u.id for u in users if not u.is_blocked]
    if not ids:
        return 0
    rows = (db.session.query(PushDevice.id, PushDevice.token)
            .join(ApiToken, PushDevice.api_token_id == ApiToken.id)
            .join(User, PushDevice.user_id == User.id)
            .filter(PushDevice.user_id.in_(ids), ApiToken.ver == User.session_version)
            .all())
    if not rows:
        return 0
    rows = [(r[0], r[1]) for r in rows]
    app = current_app._get_current_object()
    if app.config.get("PUSH_BACKGROUND", True):
        threading.Thread(target=_deliver, args=(app, rows, title, body, data), daemon=True).start()
    else:
        _deliver(app, rows, title, body, data)
    return len(rows)


def _students_with_devices():
    return (User.query.filter(User.is_admin.is_(False), User.is_blocked.is_(False))
            .filter(User.id.in_(db.session.query(PushDevice.user_id))).all())


def notify_live_started(session):
    """A live session started: tell everyone who is allowed to join it."""
    try:
        if not configured():
            return 0
        if session.subject_id is None:
            users = [u for u in _students_with_devices() if u.has_active_sub]
        else:
            users = [u for u in _students_with_devices() if u.can_access(session.subject_id)]
        where = f" · {session.subject.name}" if session.subject else ""
        return notify_users(users, "🔴 بث مباشر هسه", f"{session.title}{where}", {"type": "live", "id": session.id})
    except Exception as e:
        current_app.logger.warning("FCM live notify failed: %s", e)
        return 0


def notify_new_content(content):
    """A new lecture / summary / homework was published: tell that subject's subscribers."""
    try:
        if not configured() or not content.is_visible or not content.subject or not content.subject.is_visible:
            return 0
        users = [u for u in _students_with_devices() if u.can_access(content.subject_id)]
        kind = KIND_SINGULAR.get(content.kind, "ملف")
        return notify_users(users, f"{kind} جديد · {content.subject.name}", content.title,
                            {"type": "content", "subject_id": content.subject_id, "content_id": content.id})
    except Exception as e:
        current_app.logger.warning("FCM content notify failed: %s", e)
        return 0
