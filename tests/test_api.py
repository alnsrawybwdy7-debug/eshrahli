"""The mobile app API (/api/v1)."""
from conftest import api_login, bearer, make_content, make_subject, make_user, web_csrf, web_login
from models import ApiToken, LiveSession, PushDevice, SubCode, User, db


def test_health_is_json(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.is_json
    assert r.get_json()["ok"] is True


def test_unknown_api_path_returns_json_404(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.is_json and "error" in r.get_json()


def test_api_needs_bearer_token(client):
    for path in ("/api/v1/me", "/api/v1/subjects", "/api/v1/live"):
        r = client.get(path)
        assert r.status_code == 401 and r.get_json()["reason"] == "auth"
    assert client.get("/api/v1/me", headers=bearer("esh_not-a-real-token")).status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_login_wrong_password(app, client):
    with app.app_context():
        make_user()
    r = client.post("/api/v1/auth/login", json={"username": "ali", "password": "wrong"})
    assert r.status_code == 401 and r.is_json


def test_login_and_me(app, client):
    with app.app_context():
        make_user(all_days=10)
    token = api_login(client)
    assert token.startswith("esh_")
    me = client.get("/api/v1/me", headers=bearer(token)).get_json()["user"]
    assert me["username"] == "ali" and me["has_active_sub"] is True and me["all_access"] is True
    with app.app_context():
        t = ApiToken.query.one()
        assert t.token_hash != token and len(t.token_hash) == 64  # only the hash is stored


def test_blocked_user_cannot_login(app, client):
    with app.app_context():
        make_user(blocked=True)
    r = client.post("/api/v1/auth/login", json={"username": "ali", "password": "password1"})
    assert r.status_code == 403


def test_login_rate_limit(app, client):
    with app.app_context():
        make_user()
    codes = [client.post("/api/v1/auth/login", json={"username": "ali", "password": "x"}).status_code
             for _ in range(9)]
    assert codes[-1] == 429


def test_api_ignores_browser_cookies(app, client):
    """A web login must never authenticate the API (so the API needs no CSRF)."""
    with app.app_context():
        make_user(all_days=5)
    assert web_login(client, "ali", "password1").status_code in (302, 303)
    assert client.get("/account").status_code == 200
    assert client.get("/api/v1/me").status_code == 401


def test_register_validation_and_success(client):
    bad = [
        ({"name": "x", "username": "good_name", "password": "password1"}, "name"),
        ({"name": "علي حسن", "username": "No Spaces", "password": "password1"}, "username"),
        ({"name": "علي حسن", "username": "good_name", "password": "short"}, "password"),
    ]
    for data, field in bad:
        r = client.post("/api/v1/auth/register", json=data)
        assert r.status_code == 400 and r.get_json()["field"] == field
    r = client.post("/api/v1/auth/register", json={"name": "علي حسن", "username": "Good_Name", "password": "password1"})
    assert r.status_code == 201
    data = r.get_json()
    assert data["user"]["username"] == "good_name" and data["user"]["status"] == "none"
    dup = client.post("/api/v1/auth/register", json={"name": "ثاني", "username": "good_name", "password": "password1"})
    assert dup.status_code == 400 and dup.get_json()["field"] == "username"


def test_register_rejects_non_json(client):
    r = client.post("/api/v1/auth/register", data="garbage", content_type="text/plain")
    assert r.status_code == 400 and r.is_json


def test_single_device_new_login_ends_old_ones(app, client):
    with app.app_context():
        make_user(all_days=5)
    web = app.test_client()
    web_login(web, "ali", "password1")
    assert web.get("/account").status_code == 200
    first = api_login(client)
    second = api_login(client)
    assert client.get("/api/v1/me", headers=bearer(first)).status_code == 401
    assert client.get("/api/v1/me", headers=bearer(second)).status_code == 200
    # The browser session was logged out too
    assert web.get("/account").status_code in (302, 303)
    # And a web login logs the app out
    web_login(web, "ali", "password1")
    assert client.get("/api/v1/me", headers=bearer(second)).status_code == 401


def test_logout_ends_token(app, client):
    with app.app_context():
        make_user()
    t = api_login(client)
    assert client.post("/api/v1/auth/logout", headers=bearer(t)).status_code == 200
    assert client.get("/api/v1/me", headers=bearer(t)).status_code == 401


def test_subjects_open_and_locked(app, client):
    with app.app_context():
        s1, s2 = make_subject("رياضيات", position=1), make_subject("فيزياء", position=2)
        make_subject("مخفية", visible=False)
        make_content(s1)
        make_content(s1, kind="summary", title="ملخص")
        make_content(s1, title="مخفي", visible=False)
        make_user(subjects=[s2.id])
        sid1, sid2 = s1.id, s2.id
    t = api_login(client)
    items = client.get("/api/v1/subjects", headers=bearer(t)).get_json()["subjects"]
    assert [x["name"] for x in items] == ["فيزياء", "رياضيات"]  # open first, hidden subject not listed
    assert items[0]["open"] is True and items[1]["open"] is False
    assert items[1]["counts"] == {"video": 1, "summary": 1, "homework": 0}  # hidden content not counted
    locked = client.get(f"/api/v1/subjects/{sid1}", headers=bearer(t))
    assert locked.status_code == 403 and locked.get_json()["locked"] is True
    ok = client.get(f"/api/v1/subjects/{sid2}", headers=bearer(t))
    assert ok.status_code == 200 and set(ok.get_json()["subject"]["contents"]) == {"video", "summary", "homework"}
    assert client.get("/api/v1/subjects/999", headers=bearer(t)).status_code == 404


def test_hidden_subject_is_404_for_students(app, client):
    with app.app_context():
        s = make_subject(visible=False)
        make_user(all_days=5)
        sid = s.id
    t = api_login(client)
    assert client.get(f"/api/v1/subjects/{sid}", headers=bearer(t)).status_code == 404


def test_open_content_signed_media(app, client):
    with app.app_context():
        s = make_subject()
        c = make_content(s, data=b"0123456789" * 10)
        hidden = make_content(s, title="مخفي", visible=False)
        make_user(subjects=[s.id])
        cid, hid = c.id, hidden.id
    t = api_login(client)
    r = client.get(f"/api/v1/contents/{cid}/open", headers=bearer(t))
    assert r.status_code == 200
    data = r.get_json()
    assert data["watermark"] == "ali" and "/api/v1/media/" in data["url"]
    path = data["url"].split("localhost", 1)[1]
    media = client.get(path)
    assert media.status_code == 200 and media.data == b"0123456789" * 10
    part = client.get(path, headers={"Range": "bytes=0-4"})
    assert part.status_code == 206 and part.data == b"01234"
    assert client.get(f"/api/v1/media/{cid}?t=tampered").status_code == 403
    assert client.get(f"/api/v1/contents/{hid}/open", headers=bearer(t)).status_code == 404


def test_open_content_locked_subject(app, client):
    with app.app_context():
        s = make_subject()
        c = make_content(s)
        make_user()
        cid = c.id
    t = api_login(client)
    r = client.get(f"/api/v1/contents/{cid}/open", headers=bearer(t))
    assert r.status_code == 403 and r.get_json()["locked"] is True


def test_media_link_dies_when_subscription_ends(app, client):
    with app.app_context():
        s = make_subject()
        c = make_content(s)
        make_user(all_days=5)
        cid = c.id
    t = api_login(client)
    url = client.get(f"/api/v1/contents/{cid}/open", headers=bearer(t)).get_json()["url"].split("localhost", 1)[1]
    with app.app_context():
        u = User.query.filter_by(username="ali").one()
        u.sub_expires_at = None
        db.session.commit()
    assert client.get(url).status_code == 403


def test_live_status_and_token(app, client):
    with app.app_context():
        s1, s2 = make_subject("أ"), make_subject("ب")
        make_user(subjects=[s1.id])
        sid1 = s1.id
        db.session.add(LiveSession(title="مراجعة", room="live-test", subject_id=s2.id))
        db.session.commit()
    t = api_login(client)
    assert client.get("/api/v1/live", headers=bearer(t)).get_json() == {"live": False}  # other subject
    with app.app_context():
        live = LiveSession.query.one()
        live.subject_id = sid1
        db.session.commit()
    r = client.get("/api/v1/live", headers=bearer(t)).get_json()
    assert r["live"] is True and r["title"] == "مراجعة"
    assert client.post("/api/v1/live/token", headers=bearer(t)).status_code == 503  # LiveKit not configured
    app.config.update(LIVEKIT_URL="wss://example.livekit.cloud", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s" * 32)
    tok = client.post("/api/v1/live/token", headers=bearer(t)).get_json()
    assert tok["room"] == "live-test" and tok["token"] and tok["is_admin"] is False


def test_change_password(app, client):
    with app.app_context():
        make_user()
    t = api_login(client)
    r = client.post("/api/v1/account/password", headers=bearer(t), json={"current": "bad", "new": "newpassword"})
    assert r.status_code == 400 and r.get_json()["field"] == "current"
    r = client.post("/api/v1/account/password", headers=bearer(t), json={"current": "password1", "new": "short"})
    assert r.status_code == 400 and r.get_json()["field"] == "new"
    r = client.post("/api/v1/account/password", headers=bearer(t), json={"current": "password1", "new": "newpassword"})
    assert r.status_code == 200
    assert client.get("/api/v1/me", headers=bearer(t)).status_code == 200  # this phone stays signed in
    assert client.post("/api/v1/auth/login", json={"username": "ali", "password": "password1"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "ali", "password": "newpassword"}).status_code == 200


def test_delete_account(app, client):
    with app.app_context():
        u = make_user(all_days=5)
        db.session.add(SubCode(code="ABCD1234ABCD1234", days=30, used_by_id=u.id))
        db.session.commit()
    t = api_login(client)
    r = client.post("/api/v1/push/register", headers=bearer(t), json={"token": "fcm-token-" + "x" * 30})
    assert r.status_code == 200
    bad = client.post("/api/v1/account/delete", headers=bearer(t), json={"password": "wrong"})
    assert bad.status_code == 400
    ok = client.post("/api/v1/account/delete", headers=bearer(t), json={"password": "password1"})
    assert ok.status_code == 200
    with app.app_context():
        assert User.query.filter_by(username="ali").first() is None
        assert ApiToken.query.count() == 0 and PushDevice.query.count() == 0
        code = SubCode.query.one()
        assert code.revoked is True and code.used_by_id is None  # the used code can never be redeemed again
    assert client.get("/api/v1/me", headers=bearer(t)).status_code == 401


def test_admin_cannot_delete_from_app(app, client):
    t = api_login(client, "teacher", "teacher-pass-123")
    r = client.post("/api/v1/account/delete", headers=bearer(t), json={"password": "teacher-pass-123"})
    assert r.status_code == 403


def test_admin_keeps_web_session_on_app_login(app, client):
    web = app.test_client()
    web_login(web, "teacher", "teacher-pass-123")
    api_login(client, "teacher", "teacher-pass-123")
    assert web.get("/admin/").status_code == 200


def test_web_delete_account_still_works(app, client):
    with app.app_context():
        make_user()
    web_login(client, "ali", "password1")
    tok = web_csrf(client)
    r = client.post("/account/delete", data={"_csrf": tok, "password": "password1", "confirm": "1"})
    assert r.status_code in (302, 303)
    with app.app_context():
        assert User.query.filter_by(username="ali").first() is None
