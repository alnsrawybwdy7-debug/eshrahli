from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

KINDS = ("video", "summary", "homework")
KIND_LABELS = {"video": "فيديوات الشرح", "summary": "الملخصات", "homework": "الواجبات"}
KIND_SINGULAR = {"video": "فيديو", "summary": "ملخص", "homework": "واجب"}


def utcnow():
    """Naive UTC datetime (works the same on SQLite and Postgres)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_blocked = db.Column(db.Boolean, default=False, nullable=False)
    sub_expires_at = db.Column(db.DateTime)
    session_version = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def has_active_sub(self):
        if self.is_admin:
            return True
        return bool(self.sub_expires_at and self.sub_expires_at > utcnow())

    @property
    def sub_status(self):
        if self.is_admin:
            return "admin"
        if not self.sub_expires_at:
            return "none"
        return "active" if self.sub_expires_at > utcnow() else "expired"

    @property
    def days_left(self):
        if not self.sub_expires_at:
            return 0
        secs = (self.sub_expires_at - utcnow()).total_seconds()
        return max(0, int(-(-secs // 86400)))  # ceil


class SubCode(db.Model):
    __tablename__ = "sub_codes"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    days = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    used_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    used_at = db.Column(db.DateTime)
    revoked = db.Column(db.Boolean, default=False, nullable=False)

    used_by = db.relationship("User", backref="codes")

    @property
    def status(self):
        if self.revoked:
            return "revoked"
        return "used" if self.used_by_id else "unused"


class Subject(db.Model):
    __tablename__ = "subjects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, default="")
    position = db.Column(db.Integer, default=0, nullable=False)
    is_visible = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    contents = db.relationship(
        "Content", backref="subject", cascade="all, delete-orphan",
        order_by="(Content.position, Content.id)",
    )


class Content(db.Model):
    __tablename__ = "contents"
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)  # video | summary | homework
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    storage_key = db.Column(db.String(300), nullable=False, unique=True)
    filename = db.Column(db.String(255), nullable=False)
    mime = db.Column(db.String(120), default="application/octet-stream")
    size = db.Column(db.BigInteger, default=0)
    position = db.Column(db.Integer, default=0, nullable=False)
    is_visible = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class LiveSession(db.Model):
    __tablename__ = "live_sessions"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    room = db.Column(db.String(64), nullable=False, unique=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    started_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    ended_at = db.Column(db.DateTime)

    subject = db.relationship("Subject")

    @staticmethod
    def current():
        return (LiveSession.query.filter(LiveSession.ended_at.is_(None))
                .order_by(LiveSession.started_at.desc()).first())
