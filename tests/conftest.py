"""Test setup: a throw-away SQLite database and no real secrets.

Every setting the app reads is forced here BEFORE the app is imported. load_dotenv() never overrides
a variable that is already set, so the real .env (Neon, R2, LiveKit...) is never used by the tests.
Run: python -m pytest -q
"""
import os
import pathlib
import re
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = pathlib.Path(tempfile.mkdtemp(prefix="eshrahli-tests-"))

TEST_ENV = {
    "SECRET_KEY": "test-secret-key-not-real",
    "DATABASE_URL": "sqlite:///" + (TMP / "test.db").as_posix(),
    "ADMIN_USERNAME": "teacher", "ADMIN_PASSWORD": "teacher-pass-123", "ADMIN_NAME": "المدرّسة",
    "R2_ACCOUNT_ID": "", "R2_ACCESS_KEY_ID": "", "R2_SECRET_ACCESS_KEY": "", "R2_BUCKET": "",
    "LIVEKIT_URL": "", "LIVEKIT_API_KEY": "", "LIVEKIT_API_SECRET": "",
    "FCM_SERVICE_ACCOUNT": "", "PUSH_BACKGROUND": "0",
    "DEV_PASSWORD": "", "PROXY_SECRET": "", "ANDROID_SHA256": "", "ANDROID_PACKAGE": "study.eshrahli.app",
    "CONTACT_TELEGRAM": "nomiya_0", "CONTACT_EMAIL": "", "CONTACT_WHATSAPP": "",
    "SINGLE_DEVICE": "1", "COOKIE_SECURE": "0", "TRUST_PROXY": "0", "VERIFY_UPLOADS": "1",
    "PA_RENEW_DATE": "",
}
os.environ.update(TEST_ENV)
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402  (must come after the env above)
import push  # noqa: E402
import security  # noqa: E402
from models import Content, LiveSession, Subject, SubjectSub, User, db, utcnow  # noqa: E402

assert app_module.app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"), "tests must never touch a real DB"


@pytest.fixture()
def app():
    a = app_module.app
    a.config.update(TESTING=True, FCM_SERVICE_ACCOUNT="", PUSH_BACKGROUND=False, LIVEKIT_URL="",
                    LIVEKIT_API_KEY="", LIVEKIT_API_SECRET="", DEV_PASSWORD="", ANDROID_SHA256="")
    a.instance_path = str(TMP / "instance")
    os.makedirs(a.instance_path, exist_ok=True)
    with a.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        app_module.ensure_admin(a)
    security.limiter._hits.clear()
    security._block_cache.update(at=0.0, ips={})
    push._cache.update(path=None, account=None, access=None, exp=0)
    yield a
    with a.app_context():
        db.session.remove()


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------- helpers ----------------
def make_user(username="ali", password="password1", all_days=None, subjects=(), name="علي", blocked=False):
    u = User(name=name, username=username, is_blocked=blocked)
    u.set_password(password)
    if all_days:
        from datetime import timedelta
        u.sub_expires_at = utcnow() + timedelta(days=all_days)
    db.session.add(u)
    db.session.flush()
    for sid in subjects:
        from datetime import timedelta
        db.session.add(SubjectSub(user_id=u.id, subject_id=sid, expires_at=utcnow() + timedelta(days=30)))
    db.session.commit()
    return u


def make_subject(name="رياضيات", visible=True, position=0):
    s = Subject(name=name, is_visible=visible, position=position)
    db.session.add(s)
    db.session.commit()
    return s


def make_content(subject, kind="video", title="محاضرة 1", visible=True, data=b"hello-file-data", filename=None):
    from storage import local_path
    filename = filename or ("lec.mp4" if kind == "video" else "sum.pdf")
    key = f"subjects/{subject.id}/{kind}/{os.urandom(16).hex()}{os.path.splitext(filename)[1]}"
    path = local_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    c = Content(subject_id=subject.id, kind=kind, title=title, filename=filename,
                mime="video/mp4" if kind == "video" else "application/pdf", size=len(data),
                storage_key=key, is_visible=visible)
    db.session.add(c)
    db.session.commit()
    return c


def api_login(client, username="ali", password="password1"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def csrf_from(html):
    m = re.search(r'name="csrf-token" content="([^"]+)"', html) or re.search(r'name="_csrf" value="([^"]+)"', html)
    return m.group(1) if m else ""


def web_login(client, username, password):
    page = client.get("/login").get_data(as_text=True)
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf_from(page)})


def web_csrf(client, path="/account"):
    return csrf_from(client.get(path).get_data(as_text=True))


__all__ = ["make_user", "make_subject", "make_content", "api_login", "bearer", "web_login", "web_csrf",
           "csrf_from", "LiveSession", "Content", "db"]
