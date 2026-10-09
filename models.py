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


def days_until(dt):
    if not dt:
        return 0
    secs = (dt - utcnow()).total_seconds()
    return max(0, int(-(-secs // 86400)))  # ceil


# A code linked to no subjects = "all subjects". Linked to some = only those subjects.
code_subjects = db.Table(
    "sub_code_subjects",
    db.Column("code_id", db.Integer, db.ForeignKey("sub_codes.id", ondelete="CASCADE"), primary_key=True),
    db.Column("subject_id", db.Integer, db.ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True),
)


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

    # sub_expires_at = the "all subjects" subscription. Per-subject ones live in SubjectSub.
    subject_subs = db.relationship("SubjectSub", backref="user", cascade="all, delete-orphan", lazy="selectin")

    @property
    def all_access(self):
        return self.is_admin or bool(self.sub_expires_at and self.sub_expires_at > utcnow())

    @property
    def active_subject_ids(self):
        now = utcnow()
        return {s.subject_id for s in self.subject_subs if s.expires_at and s.expires_at > now}

    def can_access(self, subject_id):
        return self.all_access or (subject_id is not None and subject_id in self.active_subject_ids)

    @property
    def has_active_sub(self):
        """Has at least one active subscription (all subjects or any single subject)."""
        return self.all_access or bool(self.active_subject_ids)

    @property
    def ever_subscribed(self):
        return bool(self.sub_expires_at or self.subject_subs)

    @property
    def sub_status(self):
        if self.is_admin:
            return "admin"
        if self.has_active_sub:
            return "active"
        return "expired" if self.ever_subscribed else "none"

    @property
    def subscriptions(self):
        """All subscriptions for display, active first: [{label, expires, active, days, all}]"""
        now = utcnow()
        out = []
        if self.sub_expires_at:
            out.append({"label": "كل المواد", "expires": self.sub_expires_at, "all": True,
                        "active": self.sub_expires_at > now, "days": days_until(self.sub_expires_at)})
        for s in self.subject_subs:
            out.append({"label": s.subject.name if s.subject else "مادة محذوفة", "expires": s.expires_at,
                        "all": False, "active": s.expires_at > now, "days": days_until(s.expires_at),
                        "subject_id": s.subject_id})
        out.sort(key=lambda x: (not x["active"], not x["all"], x["expires"]))
        return out

    @property
    def days_left(self):
        """Days left on the longest active subscription."""
        active = [x["days"] for x in self.subscriptions if x["active"]]
        return max(active) if active else 0


class SubjectSub(db.Model):
    """Subscription to a single subject."""
    __tablename__ = "subject_subs"
    __table_args__ = (db.UniqueConstraint("user_id", "subject_id", name="uq_subject_sub"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


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
    subjects = db.relationship("Subject", secondary=code_subjects, backref="codes", lazy="selectin",
                               order_by="Subject.position")

    @property
    def is_all(self):
        return not self.subjects

    @property
    def scope_label(self):
        return "كل المواد" if self.is_all else "، ".join(s.name for s in self.subjects)

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
    subscriptions = db.relationship("SubjectSub", backref="subject", cascade="all, delete-orphan")


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

    @staticmethod
    def current_for(user):
        """The running live session if this user may see it: a subject's live is only for that
        subject's subscribers; a live with no subject is for every active subscriber."""
        s = LiveSession.current()
        if not s or not user:
            return None
        if user.is_admin:
            return s
        if s.subject_id is None:
            return s if user.has_active_sub else None
        return s if user.can_access(s.subject_id) else None
