import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def _flag(name, default="0"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _db_url():
    url = os.getenv("DATABASE_URL", "").strip() or "sqlite:///data.db"
    # Neon/Heroku style URLs -> psycopg3 driver
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    SECRET_KEY_IS_RANDOM = not os.getenv("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = _db_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    # Sessions / cookies
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _flag("COOKIE_SECURE")
    TRUST_PROXY = _flag("TRUST_PROXY")

    # Site
    SITE_NAME = os.getenv("SITE_NAME", "اشرحلي")
    SITE_TAGLINE = os.getenv("SITE_TAGLINE", "شروحات خاصة لطلاب القسم")
    DEVELOPER_NAME = os.getenv("DEVELOPER_NAME", "المطور")
    # Title shown next to the teacher's name in the live room, e.g. "فاطمة ثائر (المدرّسة)"
    TEACHER_LABEL = os.getenv("TEACHER_LABEL", "المدرّسة")
    CONTACT_WHATSAPP = os.getenv("CONTACT_WHATSAPP", "").strip()  # e.g. 9647800000000 (optional)
    CONTACT_TELEGRAM = os.getenv("CONTACT_TELEGRAM", "nomiya_0").strip().lstrip("@")  # username
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Baghdad")
    # Student logging in on a new device logs out the old one (anti account-sharing)
    SINGLE_DEVICE = _flag("SINGLE_DEVICE", "1")

    # First admin account (created on startup if missing)
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lower()
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
    ADMIN_NAME = os.getenv("ADMIN_NAME", "فاطمة ثائر").strip()

    # Cloudflare R2 (leave empty to store files locally in instance/uploads)
    R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
    R2_BUCKET = os.getenv("R2_BUCKET", "")
    MEDIA_URL_TTL = int(os.getenv("MEDIA_URL_TTL", "10800"))  # seconds
    # Check with R2 that an upload really arrived. Set 0 on hosts that block outbound
    # traffic (PythonAnywhere free plan); it also falls back automatically if R2 is unreachable.
    VERIFY_UPLOADS = _flag("VERIFY_UPLOADS", "1")

    # LiveKit (live calls)
    LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")  # wss://xxxx.livekit.cloud
    LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
    LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
    LIVE_STUDENTS_CAN_PUBLISH = _flag("LIVE_STUDENTS_CAN_PUBLISH", "1")
