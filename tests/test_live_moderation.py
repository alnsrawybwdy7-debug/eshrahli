"""Teacher moderation of a live session (mute / kick) against a fake LiveKit server. No network is used."""
import io
import json

import jwt
import pytest

import livekit_util
from conftest import api_login, bearer, make_user, web_csrf, web_login
from models import LiveKick, LiveSession, User, db

LK_URL = "wss://demo.livekit.cloud"
SECRET = "s" * 32


class FakeLiveKit:
    """Stands in for urllib.request.urlopen inside livekit_util.py."""

    def __init__(self, participants=()):
        self.participants = {p["identity"]: p for p in participants}
        self.calls = []

    def __call__(self, req, timeout=None):
        prefix = "https://demo.livekit.cloud/twirp/livekit.RoomService/"
        assert req.full_url.startswith(prefix)
        method = req.full_url[len(prefix):]
        claims = jwt.decode(req.headers["Authorization"].split()[1], SECRET, algorithms=["HS256"])
        body = json.loads(req.data)
        self.calls.append((method, body, claims["video"]))
        if method != "DeleteRoom":
            assert claims["video"] == {"roomAdmin": True, "room": body["room"]}
        if method == "ListParticipants":
            return io.BytesIO(json.dumps({"participants": list(self.participants.values())}).encode())
        if method == "GetParticipant":
            return io.BytesIO(json.dumps(self.participants[body["identity"]]).encode())
        return io.BytesIO(b"{}")


def student(uid, name, tracks):
    return {"identity": f"u{uid}", "name": name, "metadata": json.dumps({"role": "student"}), "tracks": tracks}


@pytest.fixture()
def live(app, monkeypatch):
    """A running live session, LiveKit configured, students ali (in the room, mic on) and sara."""
    app.config.update(LIVEKIT_URL=LK_URL, LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET=SECRET)
    with app.app_context():
        ali = make_user("ali", all_days=5)
        sara = make_user("sara", name="سارة", all_days=5)
        s = LiveSession(title="مراجعة", room="live-mod")
        db.session.add(s)
        db.session.commit()
        ids = {"ali": ali.id, "sara": sara.id, "session": s.id,
               "teacher": User.query.filter_by(username="teacher").one().id}
    fake = FakeLiveKit([
        {"identity": f"u{ids['teacher']}", "name": "المدرّسة", "metadata": json.dumps({"role": "teacher"}),
         "tracks": [{"sid": "TR_t1", "source": "CAMERA", "type": "VIDEO"}]},
        # Twirp JSON leaves out zero enum values: type AUDIO (0) is missing here on purpose
        student(ids["ali"], "علي", [{"sid": "TR_a1", "source": "MICROPHONE"},
                                    {"sid": "TR_a2", "source": 1, "type": 1, "muted": True},
                                    {"sid": "TR_a3", "type": "DATA"}]),
    ])
    monkeypatch.setattr(livekit_util.urllib.request, "urlopen", fake)
    ids["fake"] = fake
    return ids


def admin_client(app):
    c = app.test_client()
    web_login(c, "teacher", "teacher-pass-123")
    return c


def student_client(app, username="ali"):
    c = app.test_client()
    web_login(c, username, "password1")
    return c


def post(c, url):
    return c.post(url, headers={"X-CSRF-Token": web_csrf(c, "/account")})


def test_students_cannot_moderate(app, live):
    sid, target = live["session"], f"u{live['sara']}"
    c = student_client(app)
    assert c.get(f"/live/{sid}/participants").status_code == 403
    assert post(c, f"/live/{sid}/mute/{target}").status_code == 403
    assert post(c, f"/live/{sid}/kick/{target}").status_code == 403
    assert app.test_client().get(f"/live/{sid}/participants").status_code == 401
    assert live["fake"].calls == []
    with app.app_context():
        assert LiveKick.query.count() == 0


def test_moderation_needs_csrf(app, live):
    c = admin_client(app)
    r = c.post(f"/live/{live['session']}/kick/u{live['ali']}", headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 400
    with app.app_context():
        assert LiveKick.query.count() == 0


def test_admin_lists_participants(app, live):
    r = admin_client(app).get(f"/live/{live['session']}/participants")
    assert r.status_code == 200
    people = {p["identity"]: p for p in r.get_json()["participants"]}
    assert people[f"u{live['teacher']}"]["is_teacher"] is True
    assert people[f"u{live['ali']}"] == {"identity": f"u{live['ali']}", "name": "علي", "is_teacher": False,
                                         "mic": True, "camera": False, "screen": False}


def test_admin_mutes_published_tracks(app, live):
    r = post(admin_client(app), f"/live/{live['session']}/mute/u{live['ali']}")
    assert r.status_code == 200 and r.get_json() == {"ok": True, "muted": 1}
    mutes = [c[1] for c in live["fake"].calls if c[0] == "MutePublishedTrack"]
    # only the live mic: the camera is already muted and data tracks are skipped
    assert mutes == [{"room": "live-mod", "identity": f"u{live['ali']}", "track_sid": "TR_a1", "muted": True}]


def test_admin_cannot_target_teacher_or_bad_identity(app, live):
    c = admin_client(app)
    assert post(c, f"/live/{live['session']}/kick/u{live['teacher']}").status_code == 400
    assert post(c, f"/live/{live['session']}/mute/u999999").status_code == 404
    assert post(c, f"/live/{live['session']}/mute/ali").status_code == 404
    assert live["fake"].calls == []


def test_kicked_student_cannot_rejoin_until_next_live(app, live):
    sid, ali = live["session"], live["ali"]
    web = student_client(app)
    assert post(web, "/live/token").status_code == 200

    r = post(admin_client(app), f"/live/{sid}/kick/u{ali}")
    assert r.status_code == 200 and r.get_json() == {"ok": True, "removed": True}
    assert ("RemoveParticipant", {"room": "live-mod", "identity": f"u{ali}"}) in [c[:2] for c in live["fake"].calls]

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


def test_kick_is_saved_even_if_student_already_left(app, live, monkeypatch):
    def gone(req, timeout=None):
        raise OSError("twirp error not_found: participant not found")
    monkeypatch.setattr(livekit_util.urllib.request, "urlopen", gone)
    r = post(admin_client(app), f"/live/{live['session']}/kick/u{live['sara']}")
    assert r.status_code == 200 and r.get_json()["removed"] is False
    assert post(student_client(app, "sara"), "/live/token").status_code == 403


def test_ended_session_cannot_be_moderated(app, live):
    admin = admin_client(app)
    admin.post("/live/end", data={"_csrf": web_csrf(admin, "/live/")})
    assert admin.get(f"/live/{live['session']}/participants").status_code == 404
    assert post(admin, f"/live/{live['session']}/kick/u{live['ali']}").status_code == 404
