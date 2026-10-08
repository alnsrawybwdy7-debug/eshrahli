"""File storage: Cloudflare R2 when configured, otherwise local disk (instance/uploads).

With R2 the browser uploads straight to the bucket using a short-lived presigned URL,
so big videos never pass through the Flask server. Viewing uses presigned GET URLs
that expire (MEDIA_URL_TTL), generated only for logged-in subscribers.
"""
import mimetypes
import os
import re
import uuid
from urllib.parse import quote

from flask import current_app, url_for

VIDEO_EXT = {".mp4": "video/mp4", ".webm": "video/webm", ".m4v": "video/mp4", ".mov": "video/quicktime"}
DOC_EXT = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
}
MAX_SIZE = {"video": 5 * 1024**3 - 1, "summary": 300 * 1024**2, "homework": 300 * 1024**2}
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "text/plain",
                "video/mp4", "video/webm", "video/quicktime"}

KEY_RE = re.compile(r"^subjects/\d+/(video|summary|homework)/[0-9a-f]{32}(\.[a-z0-9]{1,9})?$")

_client = None


def r2_enabled():
    c = current_app.config
    return all([c["R2_ACCOUNT_ID"], c["R2_ACCESS_KEY_ID"], c["R2_SECRET_ACCESS_KEY"], c["R2_BUCKET"]])


def r2():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config as BotoConfig
        c = current_app.config
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{c['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=c["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=c["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=BotoConfig(signature_version="s3v4"),
        )
    return _client


def allowed(kind, filename):
    """Return the content type for an allowed file, or None."""
    ext = os.path.splitext(filename or "")[1].lower()
    table = VIDEO_EXT if kind == "video" else DOC_EXT
    return table.get(ext)


def make_key(subject_id, kind, filename):
    ext = os.path.splitext(filename)[1].lower()
    ext = re.sub(r"[^a-z0-9.]", "", ext)[:10]
    return f"subjects/{int(subject_id)}/{kind}/{uuid.uuid4().hex}{ext}"


def valid_key(key):
    return bool(KEY_RE.match(key or ""))


def local_root():
    return os.path.join(current_app.instance_path, "uploads")


def local_path(key):
    if not valid_key(key):
        raise ValueError("bad key")
    return os.path.join(local_root(), *key.split("/"))


def upload_target(key, content_type, csrf):
    """Where the browser should PUT the raw file bytes."""
    if r2_enabled():
        url = r2().generate_presigned_url(
            "put_object",
            Params={"Bucket": current_app.config["R2_BUCKET"], "Key": key, "ContentType": content_type},
            ExpiresIn=3 * 3600,
        )
        return {"url": url, "headers": {"Content-Type": content_type}}
    return {
        "url": url_for("admin.local_upload", key=key),
        "headers": {"Content-Type": content_type, "X-CSRF-Token": csrf},
    }


def stored_size(key):
    """Size of the stored object, or None if it doesn't exist."""
    if r2_enabled():
        try:
            head = r2().head_object(Bucket=current_app.config["R2_BUCKET"], Key=key)
            return int(head.get("ContentLength", 0))
        except Exception:
            return None
    try:
        return os.path.getsize(local_path(key))
    except (OSError, ValueError):
        return None


def _disposition(filename, download):
    kind = "attachment" if download else "inline"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "file"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def view_url(content, download=False):
    """A URL the subscriber's browser can open to view/download the file."""
    download = download or content.mime not in INLINE_TYPES
    if r2_enabled():
        return r2().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": current_app.config["R2_BUCKET"],
                "Key": content.storage_key,
                "ResponseContentType": content.mime,
                "ResponseContentDisposition": _disposition(content.filename, download),
            },
            ExpiresIn=current_app.config["MEDIA_URL_TTL"],
        )
    return url_for("student.media", key=content.storage_key, dl=1 if download else None)


def delete(key):
    try:
        if r2_enabled():
            r2().delete_object(Bucket=current_app.config["R2_BUCKET"], Key=key)
        else:
            os.remove(local_path(key))
    except Exception as e:  # file already gone etc.
        current_app.logger.warning("storage delete failed for %s: %s", key, e)


def guess_mime(filename):
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"
