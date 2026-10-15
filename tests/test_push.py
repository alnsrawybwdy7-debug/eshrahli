"""App notifications (push.py + /api/v1/push/register) against a fake Firebase. No network is used."""
import io
import json
import urllib.error

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import push
from conftest import TMP, api_login, bearer, make_subject, make_user, web_csrf, web_login
from models import Content, LiveSession, PushDevice, User, db

GOOD = "fcm-token-good-" + "a" * 20
DEAD = "fcm-token-dead-" + "b" * 20


class FakeFirebase:
    """Stands in for urllib.request.urlopen inside push.py."""

    def __init__(self, dead=(), fail_oauth=False):
        self.dead, self.fail_oauth = set(dead), fail_oauth
        self.oauth_calls, self.sent = 0, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        if url.startswith("https://oauth2.googleapis.com/"):
            self.oauth_calls += 1
            if self.fail_oauth:
                raise urllib.error.URLError("blocked")
            return io.BytesIO(json.dumps({"access_token": "ya29.fake", "expires_in": 3600}).encode())
        assert url == "https://fcm.googleapis.com/v1/projects/demo-project/messages:send"
        assert req.headers["Authorization"] == "Bearer ya29.fake"
        msg = json.loads(req.data)["message"]
        if msg["token"] in self.dead:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"error":{"status":"NOT_FOUND","details":[{"errorCode":"UNREGISTERED"}]}}'))
        self.sent.append(msg)
        return io.BytesIO(b'{"name":"projects/demo-project/messages/1"}')


@pytest.fixture(scope="session")
def service_account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    path = TMP / "fake-sa.json"
    path.write_text(json.dumps({"type": "service_account", "project_id": "demo-project", "private_key_id": "k1",
                                "private_key": pem, "client_email": "sa@demo-project.iam.gserviceaccount.com",
                                "token_uri": "https://oauth2.googleapis.com/token"}), encoding="utf-8")
    return str(path)


@pytest.fixture()
def firebase(app, service_account, monkeypatch):
    fake = FakeFirebase()
    monkeypatch.setattr(push.urllib.request, "urlopen", fake)
    app.config.update(FCM_SERVICE_ACCOUNT=service_account, PUSH_BACKGROUND=False)
    return fake


def register(client, username, token):
    t = api_login(client, username)
    r = client.post("/api/v1/push/register", headers=bearer(t), json={"token": token, "platform": "android"})
    assert r.status_code == 200, r.get_json()
    return t


def admin_client(app):
    c = app.test_client()
    web_login(c, "teacher", "teacher-pass-123")
    return c


# ---------------- /push/register ----------------
def test_register_needs_login(client):
    assert client.post("/api/v1/push/register", json={"token": GOOD}).status_code == 401


def test_register_validates_token(app, client):
    with app.app_context():
        make_user()
    t = api_login(client)
    for bad in ("", "short", "has spaces " + "x" * 20, "x" * 600, "<script>" + "x" * 20):
        assert client.post("/api/v1/push/register", headers=bearer(t), json={"token": bad}).status_code == 400


def test_register_is_idempotent_and_follows_logins(app, client):
    with app.app_context():
        make_user()
        make_user("sara", name="سارة")
    t = register(client, "ali", GOOD)
    client.post("/api/v1/push/register", headers=bearer(t), json={"token": GOOD})
    with app.app_context():
        assert PushDevice.query.count() == 1
    # A new Firebase token for the same login replaces the old one
    client.post("/api/v1/push/register", headers=bearer(t), json={"token": DEAD})
    with app.app_context():
        assert [d.token for d in PushDevice.query.all()] == [DEAD]
    # Same phone, another account: the device moves to the new account
    register(client, "sara", DEAD)
    with app.app_context():
        d = PushDevice.query.one()
        assert d.user.username == "sara"
    # Logging out removes the phone
    t2 = api_login(client, "sara")  # (single device: this also dropped the old registration)
    client.post("/api/v1/push/register", headers=bearer(t2), json={"token": DEAD})
    client.post("/api/v1/auth/logout", headers=bearer(t2))
    with app.app_context():
        assert PushDevice.query.count() == 0


# ---------------- sending ----------------
def test_nothing_happens_without_firebase(app, client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no network without FCM_SERVICE_ACCOUNT")
    monkeypatch.setattr(push.urllib.request, "urlopen", boom)
    with app.app_context():
        make_user(all_days=5)
    register(client, "ali", GOOD)
    admin = admin_client(app)
    r = admin.post("/live/start", data={"_csrf": web_csrf(admin, "/live/"), "title": "بث"})
    assert r.status_code in (302, 303)


def test_live_start_notifies_only_allowed_students(app, client, firebase):
    with app.app_context():
        s1, s2 = make_subject("أ"), make_subject("ب")
        make_user("ali", subjects=[s1.id])
        make_user("sara", name="سارة", subjects=[s2.id])
        make_user("omar", name="عمر", all_days=3)
        make_user("noor", name="نور")  # no subscription
        sid1 = s1.id
    tokens = {"ali": GOOD + "1", "sara": GOOD + "2", "omar": GOOD + "3", "noor": GOOD + "4"}
    for name, tok in tokens.items():
        register(app.test_client(), name, tok)
    admin = admin_client(app)
    r = admin.post("/live/start", data={"_csrf": web_csrf(admin, "/live/"), "title": "مراجعة", "subject_id": sid1})
    assert r.status_code in (302, 303)
    got = sorted(m["token"] for m in firebase.sent)
    assert got == sorted([tokens["ali"], tokens["omar"]])
    msg = firebase.sent[0]
    assert msg["notification"]["title"] == "🔴 بث مباشر هسه" and "مراجعة" in msg["notification"]["body"]
    assert msg["data"]["type"] == "live" and all(isinstance(v, str) for v in msg["data"].values())
    assert msg["android"]["notification"]["channel_id"] == "eshrahli_default"
    assert firebase.oauth_calls == 1  # access token is cached across messages


def test_live_without_subject_goes_to_all_subscribers(app, firebase):
    with app.app_context():
        s = make_subject()
        make_user("ali", subjects=[s.id])
        make_user("noor", name="نور")
    register(app.test_client(), "ali", GOOD)
    register(app.test_client(), "noor", DEAD)
    admin = admin_client(app)
    admin.post("/live/start", data={"_csrf": web_csrf(admin, "/live/"), "title": "عام"})
    assert [m["token"] for m in firebase.sent] == [GOOD]


def test_new_content_notifies_subject_subscribers(app, firebase):
    from storage import local_path
    import os
    with app.app_context():
        s, other = make_subject("كيمياء"), make_subject("أحياء")
        make_user("ali", subjects=[s.id])
        make_user("sara", name="سارة", subjects=[other.id])
        sid = s.id
        key = f"subjects/{sid}/summary/{'ab' * 16}.pdf"
        os.makedirs(os.path.dirname(local_path(key)), exist_ok=True)
        with open(local_path(key), "wb") as f:
            f.write(b"%PDF-1.4 test")
    register(app.test_client(), "ali", GOOD)
    register(app.test_client(), "sara", DEAD)
    admin = admin_client(app)
    tok = web_csrf(admin, f"/admin/subjects/{sid}")
    r = admin.post(f"/admin/subjects/{sid}/content", headers={"X-CSRF-Token": tok},
                   data={"kind": "summary", "key": key, "filename": "notes.pdf", "title": "ملخص الفصل"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert [m["token"] for m in firebase.sent] == [GOOD]
    msg = firebase.sent[0]
    assert msg["notification"]["title"] == "ملخص جديد · كيمياء" and msg["notification"]["body"] == "ملخص الفصل"
    assert msg["data"] == {"type": "content", "subject_id": str(sid), "content_id": str(r.get_json()["id"])}


def test_hidden_content_is_not_announced(app, firebase):
    with app.app_context():
        s = make_subject()
        make_user("ali", all_days=5)
        c = Content(subject_id=s.id, kind="video", title="مخفي", filename="a.mp4", storage_key=f"subjects/{s.id}/video/{'cd' * 16}.mp4",
                    is_visible=False)
        db.session.add(c)
        db.session.commit()
        register(app.test_client(), "ali", GOOD)
        assert push.notify_new_content(c) == 0
    assert firebase.sent == []


def test_dead_tokens_are_removed(app, firebase):
    firebase.dead.add(DEAD)
    with app.app_context():
        make_user("ali", all_days=5)
        make_user("sara", name="سارة", all_days=5)
    register(app.test_client(), "ali", GOOD)
    register(app.test_client(), "sara", DEAD)
    with app.app_context():
        live = LiveSession(title="x", room="live-dead")
        db.session.add(live)
        db.session.commit()
        assert push.notify_live_started(live) == 2
        assert [d.token for d in PushDevice.query.all()] == [GOOD]


def test_old_logins_and_blocked_users_get_nothing(app, firebase):
    with app.app_context():
        make_user("ali", all_days=5)
        make_user("sara", name="سارة", all_days=5)
    register(app.test_client(), "ali", GOOD)
    register(app.test_client(), "sara", DEAD)
    with app.app_context():
        ali = User.query.filter_by(username="ali").one()
        ali.session_version += 1  # e.g. logged in from the browser: the app login is no longer valid
        sara = User.query.filter_by(username="sara").one()
        sara.is_blocked = True
        db.session.commit()
        live = LiveSession(title="x", room="live-old")
        db.session.add(live)
        db.session.commit()
        assert push.notify_live_started(live) == 0
    assert firebase.sent == []


def test_firebase_down_never_breaks_the_site(app, firebase):
    firebase.fail_oauth = True
    with app.app_context():
        make_user("ali", all_days=5)
    register(app.test_client(), "ali", GOOD)
    admin = admin_client(app)
    r = admin.post("/live/start", data={"_csrf": web_csrf(admin, "/live/"), "title": "بث"})
    assert r.status_code in (302, 303)
    assert firebase.sent == []
    with app.app_context():
        assert LiveSession.query.count() == 1


def test_background_mode_sends_in_a_thread(app, firebase):
    app.config["PUSH_BACKGROUND"] = True
    with app.app_context():
        make_user("ali", all_days=5)
    register(app.test_client(), "ali", GOOD)
    import threading
    started = []
    real_thread = threading.Thread

    class SpyThread(real_thread):
        def start(self):
            started.append(self)
            super().start()
    push.threading.Thread = SpyThread
    try:
        with app.app_context():
            live = LiveSession(title="x", room="live-bg")
            db.session.add(live)
            db.session.commit()
            assert push.notify_live_started(live) == 1
        for t in started:
            t.join(5)
    finally:
        push.threading.Thread = real_thread
    assert len(started) == 1 and [m["token"] for m in firebase.sent] == [GOOD]
