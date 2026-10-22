"""Teacher moderation of a live session. The server never connects to LiveKit (PythonAnywhere blocks it):
it only signs short tokens and records kicks; the teacher's browser sends the LiveKit commands."""
import time
import urllib.request

import jwt
import pytest

from conftest import api_login, bearer, make_user, web_csrf, web_login
from models import LiveKick, LiveSession, User, db

SECRET = "s" * 32


@pytest.fixture()
def live(app, monkeypatch):
    """A running live session, LiveKit configured, students ali and sara. Any network call fails the test."""
    app.config.update(LIVEKIT_URL="wss://demo.livekit.cloud", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET=SECRET)

    def no_network(*a, **k):
        raise AssertionError("the server must not connect to LiveKit")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    with app.app_context():
        ali = make_user("ali", all_days=5)
        sara = make_user("sara", name="سارة", all_days=5)
        s = LiveSession(title="مراجعة", room="live-mod")
        db.session.add(s)
        db.session.commit()
        return {"ali": ali.id, "sara": sara.id, "session": s.id,
                "teacher": User.query.filter_by(username="teacher").one().id}


def admin_client(app):
    c = app.test_client()
    web_login(c, "teacher", "teacher-pass-123")
    return c


def student_client(app, username="ali"):
    c = app.test_client()
    web_login(c, username, "password1")
    return c


def post(c, url, **headers):
    return c.post(url, headers={"X-CSRF-Token": web_csrf(c, "/account"), **headers})


def anon_post(app, url):
    c = app.test_client()
    return c.post(url, headers={"X-CSRF-Token": web_csrf(c, "/login")})


def decode(token):
    return jwt.decode(token, SECRET, algorithms=["HS256"])


# ---------------- admin token ----------------
def test_admin_token_is_room_admin_for_this_room_only(app, live):
    r = post(admin_client(app), f"/live/{live['session']}/admin-token")
    assert r.status_code == 200
    d = r.get_json()
    assert d["url"] == "https://demo.livekit.cloud" and d["room"] == "live-mod" and d["expires_in"] == 600
    claims = decode(d["token"])
    assert claims["video"] == {"roomAdmin": True, "room": "live-mod"}
    assert claims["iss"] == "k" and 590 <= claims["exp"] - time.time() <= 600


def test_only_admins_get_the_admin_token(app, live):
    url = f"/live/{live['session']}/admin-token"
    assert post(student_client(app), url).status_code == 403
    assert anon_post(app, url).status_code == 401
    assert admin_client(app).post(url, headers={"X-CSRF-Token": "wrong"}).status_code == 400


def test_admin_token_needs_running_session_and_livekit(app, live):
    admin = admin_client(app)
    app.config["LIVEKIT_URL"] = ""
    assert post(admin, f"/live/{live['session']}/admin-token").status_code == 503
    app.config["LIVEKIT_URL"] = "wss://demo.livekit.cloud"
    admin.post("/live/end", data={"_csrf": web_csrf(admin, "/live/")})
    assert post(admin, f"/live/{live['session']}/admin-token").status_code == 404


# ---------------- kick ----------------
def test_students_cannot_kick(app, live):
    url = f"/live/{live['session']}/kick/u{live['sara']}"
    assert post(student_client(app), url).status_code == 403
    assert anon_post(app, url).status_code == 401
    assert admin_client(app).post(url, headers={"X-CSRF-Token": "wrong"}).status_code == 400
    with app.app_context():
        assert LiveKick.query.count() == 0


def test_admin_cannot_kick_teacher_or_bad_identity(app, live):
    c = admin_client(app)
    assert post(c, f"/live/{live['session']}/kick/u{live['teacher']}").status_code == 400
    assert post(c, f"/live/{live['session']}/kick/u999999").status_code == 404
    assert post(c, f"/live/{live['session']}/kick/ali").status_code == 404
    with app.app_context():
        assert LiveKick.query.count() == 0


def test_kick_is_recorded_without_network_and_blocks_rejoining(app, live):
    sid, ali = live["session"], live["ali"]
    web = student_client(app)
    assert post(web, "/live/token").status_code == 200

    r = post(admin_client(app), f"/live/{sid}/kick/u{ali}")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert post(admin_client(app), f"/live/{sid}/kick/u{ali}").status_code == 200  # twice is fine
    with app.app_context():
        assert LiveKick.query.count() == 1

    # Website: refused with a clear Arabic message
    r = post(web, "/live/token")
    assert r.status_code == 403
    d = r.get_json()
    assert d["kicked"] is True and d["error"] == "انطلعت من البث من قبل الأستاذة." and d["home"] == "/"
    # App API: same
    t = api_login(app.test_client(), "ali")
    r = app.test_client().post("/api/v1/live/token", headers=bearer(t))
    assert r.status_code == 403 and r.get_json()["kicked"] is True and "الأستاذة" in r.get_json()["error"]
    # Other students are not affected
    assert post(student_client(app, "sara"), "/live/token").status_code == 200

    # A new live session: the kick is over
    admin = admin_client(app)
    admin.post("/live/start", data={"_csrf": web_csrf(admin, "/live/"), "title": "بث جديد"})
    with app.app_context():
        assert LiveSession.query.filter(LiveSession.ended_at.is_(None)).one().id != sid
    r = app.test_client().post("/api/v1/live/token", headers=bearer(t))
    assert r.status_code == 200 and r.get_json()["title"] == "بث جديد"


def test_ended_session_cannot_be_moderated(app, live):
    admin = admin_client(app)
    admin.post("/live/end", data={"_csrf": web_csrf(admin, "/live/")})
    assert post(admin, f"/live/{live['session']}/kick/u{live['ali']}").status_code == 404


# ---------------- ending ----------------
def test_end_without_network_gives_browser_a_delete_token(app, live):
    admin = admin_client(app)
    r = post(admin, "/live/end", Accept="application/json")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["redirect"] == "/admin/"
    assert d["livekit"]["url"] == "https://demo.livekit.cloud" and d["livekit"]["room"] == "live-mod"
    claims = decode(d["livekit"]["token"])
    assert claims["video"] == {"roomCreate": True} and claims["exp"] - time.time() <= 60
    with app.app_context():
        assert db.session.get(LiveSession, live["session"]).ended_at is not None
    # Nothing left to end: no token
    assert post(admin, "/live/end", Accept="application/json").get_json()["livekit"] is None


def test_plain_form_end_still_redirects(app, live):
    admin = admin_client(app)
    r = admin.post("/live/end", data={"_csrf": web_csrf(admin, "/live/")})
    assert r.status_code in (302, 303) and r.headers["Location"].endswith("/admin/")
