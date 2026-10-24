"""The website itself still works: every page for visitors, students and the teacher, plus app mode."""
import pytest

from conftest import csrf_from, make_content, make_subject, make_user, web_login
from models import LiveSession, db

PUBLIC = ["/login", "/register", "/features", "/privacy", "/delete-account", "/offline",
          "/manifest.webmanifest", "/sw.js", "/.well-known/assetlinks.json", "/api/v1/health"]


@pytest.fixture()
def data(app):
    with app.app_context():
        s = make_subject("رياضيات")
        locked = make_subject("فيزياء")
        v = make_content(s, kind="video")
        f = make_content(s, kind="summary", title="ملخص")
        make_user("ali", subjects=[s.id])
        make_user("noor", name="نور")
        db.session.add(LiveSession(title="بث", room="live-site", subject_id=s.id))
        db.session.commit()
        return {"sid": s.id, "locked": locked.id, "video": v.id, "file": f.id}


@pytest.mark.parametrize("path", PUBLIC)
def test_public_pages(client, path):
    r = client.get(path)
    assert r.status_code == 200, path


def test_protected_pages_redirect_visitors(client, data):
    for path in ("/", "/account", "/subscribe", f"/subject/{data['sid']}", "/live/", "/admin/"):
        assert client.get(path).status_code in (302, 303), path


def test_student_pages(app, client, data):
    web_login(client, "ali", "password1")
    for path in ("/", "/account", "/subscribe", f"/subject/{data['sid']}", f"/watch/{data['video']}",
                 f"/file/{data['file']}", "/live/", "/api/live-status", "/privacy", "/features"):
        r = client.get(path)
        assert r.status_code in (200, 302, 303), (path, r.status_code)
        assert r.status_code != 500
    assert client.get(f"/subject/{data['locked']}").status_code in (302, 303)
    assert client.get("/admin/").status_code == 404


def test_files_open_in_the_browser_without_download_button(app, client, data):
    with app.app_context():
        from models import Subject
        docx = make_content(db.session.get(Subject, data["sid"]), kind="summary", title="وورد", filename="a.docx")
        docx.mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        db.session.commit()
        docx_id = docx.id
    web_login(client, "ali", "password1")
    page = client.get(f"/subject/{data['sid']}?tab=summary").get_data(as_text=True)
    assert "تنزيل" not in page and "dl=1" not in page and f"/file/{data['file']}" in page
    # An old ?dl=1 link no longer forces a download of a PDF
    for url in (f"/file/{data['file']}", f"/file/{data['file']}?dl=1"):
        media = client.get(url).headers["Location"]
        assert "dl=" not in media
        r = client.get(media)
        assert r.status_code == 200 and "attachment" not in r.headers.get("Content-Disposition", "")
        r.close()
    r = client.get(client.get(f"/file/{data['file']}").headers["Location"] + "?dl=1")
    assert "attachment" not in r.headers.get("Content-Disposition", "")
    r.close()
    # Types the browser cannot show (docx...) still download as before
    media = client.get(f"/file/{docx_id}").headers["Location"]
    r = client.get(media)
    assert r.status_code == 200 and r.headers["Content-Disposition"].startswith("attachment")
    r.close()


def test_unsubscribed_student(client, data):
    web_login(client, "noor", "password1")
    assert client.get("/").status_code in (200, 302, 303)
    assert client.get("/subscribe").status_code == 200
    assert client.get("/account").status_code == 200


def test_admin_pages(client, data):
    web_login(client, "teacher", "teacher-pass-123")
    for path in ("/admin/", "/admin/subjects", f"/admin/subjects/{data['sid']}", "/admin/codes", "/admin/users",
                 "/", "/live/", "/account"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)


def test_developer_panel_off_without_password(client):
    assert client.get("/Zahraa-Abdullah/").status_code == 404


def test_app_mode_hides_prices_telegram_and_codes(client, data):
    normal = client.get("/register").get_data(as_text=True)
    assert "price-strip" in normal and "t.me/" in normal
    app_page = client.get("/register?source=twa").get_data(as_text=True)
    assert "price-strip" not in app_page and "t.me/" not in app_page
    # the choice sticks (cookie) until ?source=web
    assert "price-strip" not in client.get("/login").get_data(as_text=True)
    web_login(client, "noor", "password1")
    sub = client.get("/subscribe").get_data(as_text=True)
    assert "t.me/" not in sub and 'name="code"' not in sub
    client.post("/logout", data={"_csrf": csrf_from(sub)})
    assert "price-strip" in client.get("/login?source=web").get_data(as_text=True)
    assert "price-strip" in client.get("/login").get_data(as_text=True)


def test_assetlinks(app, client):
    assert client.get("/.well-known/assetlinks.json").get_json() == []
    app.config["ANDROID_SHA256"] = "aa:bb, CC:DD"
    data = client.get("/.well-known/assetlinks.json").get_json()
    assert data[0]["target"]["package_name"] == "study.eshrahli.app"
    assert data[0]["target"]["sha256_cert_fingerprints"] == ["AA:BB", "CC:DD"]


def test_privacy_and_delete_pages_have_content(client):
    assert "الخصوصية" in client.get("/privacy").get_data(as_text=True)
    assert "حذف" in client.get("/delete-account").get_data(as_text=True)
