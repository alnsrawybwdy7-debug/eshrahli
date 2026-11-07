"""In-site PDF viewer: /files/<cid>/view and the short-lived source URL it fetches. No network is used."""
from urllib.parse import parse_qs, urlparse

import pytest

import storage
from conftest import make_content, make_subject, make_user, web_login
from models import db


@pytest.fixture()
def data(app):
    with app.app_context():
        s, other = make_subject("كيمياء"), make_subject("أحياء")
        pdf = make_content(s, kind="summary", title="ملخص الفصل", data=b"%PDF-1.4 test")
        hidden = make_content(s, kind="homework", title="مخفي", visible=False)
        docx = make_content(s, kind="summary", title="وورد", filename="a.docx")
        docx.mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        db.session.commit()
        make_user("ali", subjects=[s.id])
        make_user("sara", name="سارة", subjects=[other.id])
        make_user("noor", name="نور")  # no subscription at all
        return {"sid": s.id, "pdf": pdf.id, "hidden": hidden.id, "docx": docx.id}


def login(app, username):
    c = app.test_client()
    web_login(c, username, "password1")
    return c


def test_visitor_goes_to_login(client, data):
    for url in (f"/files/{data['pdf']}/view", f"/files/{data['pdf']}/source"):
        r = client.get(url)
        assert r.status_code in (302, 303) and "/login" in r.headers["Location"]


def test_students_without_access_get_403(app, data):
    for username in ("sara", "noor"):  # other subject / no subscription
        c = login(app, username)
        r = c.get(f"/files/{data['pdf']}/view")
        assert r.status_code == 403, username
        assert "غير مفعّل" in r.get_data(as_text=True)
        assert c.get(f"/files/{data['pdf']}/source").status_code == 403


def test_subscriber_sees_the_viewer(app, data):
    c = login(app, "ali")
    r = c.get(f"/files/{data['pdf']}/view")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    html = r.get_data(as_text=True)
    assert 'id="pdf-root"' in html and "ملخص الفصل" in html
    assert "vendor/pdfjs-4.10.38/pdf.min.js" in html and "vendor/pdfjs-4.10.38/pdf.worker.min.js" in html
    assert 'data-wm="ali"' in html
    assert "تنزيل" not in html and "طباعة" not in html and "/media/" not in html  # the file URL is never in the page
    src = c.get(f"/files/{data['pdf']}/source")
    assert src.status_code == 200 and src.headers["Cache-Control"] == "no-store"
    f = c.get(src.get_json()["url"])
    assert f.status_code == 200 and f.data == b"%PDF-1.4 test"
    assert f.headers["Cache-Control"] == "no-store" and "attachment" not in f.headers.get("Content-Disposition", "")
    f.close()


def test_old_file_link_redirects_pdf_to_viewer(app, data):
    c = login(app, "ali")
    for url in (f"/file/{data['pdf']}", f"/file/{data['pdf']}?dl=1"):
        r = c.get(url)
        assert r.status_code in (302, 303) and r.headers["Location"].endswith(f"/files/{data['pdf']}/view")
    # Other types keep opening the file itself
    r = c.get(f"/file/{data['docx']}")
    assert "/media/" in r.headers["Location"]


def test_subject_page_opens_pdfs_in_the_viewer(app, data):
    html = login(app, "ali").get(f"/subject/{data['sid']}?tab=summary").get_data(as_text=True)
    assert f"/files/{data['pdf']}/view" in html
    assert f"/file/{data['docx']}" in html and f"/files/{data['docx']}/view" not in html


def test_hidden_or_non_pdf_is_not_found(app, data):
    c = login(app, "ali")
    assert c.get(f"/files/{data['hidden']}/view").status_code == 404
    assert c.get(f"/files/{data['docx']}/view").status_code == 404
    assert c.get("/files/999999/view").status_code == 404


def test_teacher_can_preview(app, data):
    c = app.test_client()
    web_login(c, "teacher", "teacher-pass-123")
    assert c.get(f"/files/{data['pdf']}/view").status_code == 200


def test_r2_source_is_a_short_inline_no_store_presigned_url(app, data, monkeypatch):
    """Signed locally by boto3 (no network): checks the URL the viewer would get with R2."""
    app.config.update(R2_ACCOUNT_ID="acc", R2_ACCESS_KEY_ID="key", R2_SECRET_ACCESS_KEY="secret", R2_BUCKET="bucket")
    monkeypatch.setattr(storage, "_client", None)
    try:
        c = login(app, "ali")
        url = c.get(f"/files/{data['pdf']}/source").get_json()["url"]
    finally:
        app.config.update(R2_ACCOUNT_ID="", R2_ACCESS_KEY_ID="", R2_SECRET_ACCESS_KEY="", R2_BUCKET="")
        storage._client = None
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://acc.r2.cloudflarestorage.com/bucket/subjects/")
    assert q["X-Amz-Expires"] == ["300"]
    assert q["response-content-type"] == ["application/pdf"]
    assert q["response-cache-control"] == ["no-store"]
    assert q["response-content-disposition"][0].startswith("inline")
