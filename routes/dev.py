"""Developer panel: hosting/storage status, security events, IP blocking, and file updates.

Protected by developer_required: an admin account PLUS the DEV_PASSWORD entered this session.
Disabled entirely until DEV_PASSWORD is set in .env.
"""
import datetime as dt
import hashlib
import hmac
import io
import os
import re
import secrets
import shutil
import time

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_file, url_for)

import storage
from config import Config
from models import (AppSetting, BlockedIP, Content, DeployLog, SecurityEvent, Subject, SubjectSub,
                    User, db, utcnow)
from security import client_ip, csrf_token, developer_required, invalidate_blocks, limiter, log_event

bp = Blueprint("dev", __name__, url_prefix="/Zahraa-Abdullah")

# Files the developer panel is allowed to replace (relative to project root).
# Only these extensions, only inside these folders, never dotfiles or anything outside the project.
ALLOWED_DIRS = ("templates/", "static/", "routes/")
ALLOWED_ROOT_FILES = ("app.py", "config.py", "models.py", "security.py", "storage.py",
                      "livekit_util.py", "requirements.txt", "README.md", "cloudflare-worker.js")
ALLOWED_EXT = (".py", ".html", ".css", ".js", ".txt", ".json", ".md", ".svg")
PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def project_root():
    return os.path.dirname(current_app.root_path) if False else os.path.abspath(
        os.path.join(current_app.root_path, ".."))


def root_dir():
    # app.root_path is the package dir (…/course_platform). That's our project root here.
    return current_app.root_path


def safe_target(rel):
    """Return the absolute path for an allowed project file, or None."""
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel or not PATH_RE.match(rel) or ".." in rel.split("/"):
        return None
    if os.path.splitext(rel)[1].lower() not in ALLOWED_EXT:
        return None
    allowed = rel in ALLOWED_ROOT_FILES or rel.startswith(ALLOWED_DIRS)
    if not allowed:
        return None
    base = root_dir()
    full = os.path.abspath(os.path.join(base, rel))
    # must stay inside the project and not touch instance/ or dotfiles
    if not (full == base or full.startswith(base + os.sep)):
        return None
    parts = rel.split("/")
    if any(p.startswith(".") for p in parts) or parts[0] == "instance":
        return None
    return full


def listable_files():
    base = root_dir()
    out = []
    for folder in ("templates", "static/css", "static/js", "routes", "."):
        d = os.path.join(base, folder)
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            rel = name if folder == "." else f"{folder}/{name}"
            full = os.path.join(base, rel)
            if os.path.isfile(full) and safe_target(rel):
                out.append(rel)
    # include nested template folders
    for dirpath, _, files in os.walk(os.path.join(base, "templates")):
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), base).replace("\\", "/")
            if safe_target(rel) and rel not in out:
                out.append(rel)
    return sorted(set(out))


def py_syntax_ok(path, data):
    if not path.endswith(".py"):
        return True, ""
    try:
        compile(data, path, "exec")
        return True, ""
    except SyntaxError as e:
        return False, f"خطأ لغوي بالسطر {e.lineno}: {e.msg}"


# ---------------- Gate ----------------
@bp.route("/unlock", methods=["GET", "POST"])
def gate():
    # Separate entrance: no account needed, only the developer password.
    if not current_app.config.get("DEV_PASSWORD"):
        abort(404)
    from flask import session
    if session.get("dev_ok") == current_app.config["DEV_SESSION_TAG"]:
        return redirect(url_for("dev.dashboard"))
    if request.method == "POST":
        if not limiter.hit(f"devgate:{client_ip()}", 6, 600):
            log_event("dev_gate_ratelimit")
            flash("محاولات كثيرة، انتظر شوية.", "error")
            return render_template("dev/gate.html"), 429
        pw = request.form.get("password", "")
        if hmac.compare_digest(pw, current_app.config["DEV_PASSWORD"]):
            session["dev_ok"] = current_app.config["DEV_SESSION_TAG"]
            log_event("dev_unlock")
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith(bp.url_prefix) else url_for("dev.dashboard"))
        log_event("dev_gate_fail")
        flash("كلمة مرور المطوّر غلط.", "error")
    return render_template("dev/gate.html")


@bp.route("/lock", methods=["POST"])
@developer_required
def lock():
    from flask import session
    session.pop("dev_ok", None)
    flash("تم الخروج من لوحة المطوّر.", "ok")
    return redirect(url_for("dev.gate"))


# ---------------- Dashboard ----------------
def storage_usage():
    """Bytes stored: sum of content sizes (works for both R2 and local)."""
    total = db.session.query(db.func.coalesce(db.func.sum(Content.size), 0)).scalar() or 0
    return int(total)


def checklist():
    c = current_app.config
    items = []
    def add(ok, label, hint=""):
        items.append({"ok": bool(ok), "label": label, "hint": hint})
    add(not c["SECRET_KEY_IS_RANDOM"], "SECRET_KEY مضبوط بـ .env",
        "بدونه الجلسات تنمسح كل Reload" if c["SECRET_KEY_IS_RANDOM"] else "")
    add(c["SESSION_COOKIE_SECURE"], "COOKIE_SECURE=1 (HTTPS)", "خليها 1 بالإنتاج")
    add(bool(c["PROXY_SECRET"]), "PROXY_SECRET مضبوط", "مطلوب للدومين عبر Cloudflare")
    add(storage.r2_enabled(), "تخزين R2 مفعّل", "بدونه الفيديوات تنخزن على السيرفر")
    add(bool(c["LIVEKIT_API_KEY"]), "LiveKit مضبوط", "مطلوب للبث المباشر")
    add(bool(c["DEV_PASSWORD"]), "DEV_PASSWORD مضبوط", "")
    add(User.query.filter_by(is_admin=True).count() >= 1, "يوجد حساب أدمن", "")
    return items


@bp.route("/")
@developer_required
def dashboard():
    c = current_app.config
    now = utcnow()
    since = now - dt.timedelta(hours=24)
    used = storage_usage()
    free = int(c["R2_FREE_GB"] * 1024**3)
    # security summary (last 24h)
    recent = SecurityEvent.query.filter(SecurityEvent.at >= since).all()
    by_kind = {}
    for e in recent:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    top_ips = {}
    for e in recent:
        if e.kind in ("login_fail", "probe", "login_ratelimit", "redeem_ratelimit", "dev_gate_fail"):
            top_ips[e.ip] = top_ips.get(e.ip, 0) + 1
    top_ips = sorted(top_ips.items(), key=lambda x: -x[1])[:8]
    # renewal days left
    renew_days = None
    if c["PA_RENEW_DATE"]:
        try:
            d = dt.datetime.strptime(c["PA_RENEW_DATE"], "%Y-%m-%d").date()
            renew_days = (d - dt.date.today()).days
        except ValueError:
            pass
    stats = {
        "students": User.query.filter_by(is_admin=False).count(),
        "subjects": Subject.query.count(),
        "files": Content.query.count(),
        "used": used, "free": free, "pct": round(used / free * 100, 1) if free else 0,
        "events24": len(recent),
        "blocked": BlockedIP.query.count(),
        "renew_days": renew_days, "renew_date": c["PA_RENEW_DATE"],
        "last_deploy": DeployLog.query.order_by(DeployLog.at.desc()).first(),
    }
    return render_template("dev/dashboard.html", stats=stats, by_kind=by_kind, top_ips=top_ips,
                           checklist=checklist(), blocks=BlockedIP.query.order_by(BlockedIP.created_at.desc()).limit(20).all())


# ---------------- Security log ----------------
@bp.route("/security")
@developer_required
def security():
    kind = request.args.get("kind", "")
    q = SecurityEvent.query
    if kind:
        q = q.filter_by(kind=kind)
    events = q.order_by(SecurityEvent.at.desc()).limit(300).all()
    kinds = [r[0] for r in db.session.query(SecurityEvent.kind).distinct().all()]
    return render_template("dev/security.html", events=events, kinds=sorted(kinds), kind=kind)


@bp.route("/block", methods=["POST"])
@developer_required
def block_ip():
    ip = (request.form.get("ip") or "").strip()[:64]
    hours = request.form.get("hours", type=int)
    if not ip:
        flash("اكتب عنوان IP.", "error")
    else:
        row = BlockedIP.query.filter_by(ip=ip).first() or BlockedIP(ip=ip)
        row.reason = (request.form.get("reason") or "")[:200]
        row.expires_at = (utcnow() + dt.timedelta(hours=hours)) if hours and hours > 0 else None
        db.session.add(row)
        db.session.commit()
        invalidate_blocks()
        log_event("ip_blocked_manual", ip)
        flash(f"تم حظر {ip}.", "ok")
    return redirect(request.form.get("back") or url_for("dev.dashboard"))


@bp.route("/unblock/<int:bid>", methods=["POST"])
@developer_required
def unblock_ip(bid):
    row = db.session.get(BlockedIP, bid)
    if row:
        db.session.delete(row)
        db.session.commit()
        invalidate_blocks()
        flash("تم رفع الحظر.", "ok")
    return redirect(request.form.get("back") or url_for("dev.dashboard"))


# ---------------- File updates ----------------
@bp.route("/files")
@developer_required
def files():
    return render_template("dev/files.html", files=listable_files(),
                           logs=DeployLog.query.order_by(DeployLog.at.desc()).limit(30).all())


@bp.route("/files/view")
@developer_required
def file_view():
    target = safe_target(request.args.get("path", ""))
    if not target or not os.path.isfile(target):
        abort(404)
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(200_000)
    return render_template("dev/file_view.html", path=request.args.get("path"), content=content)


@bp.route("/files/upload", methods=["POST"])
@developer_required
def file_upload():
    rel = (request.form.get("path") or "").strip()
    f = request.files.get("file")
    target = safe_target(rel)
    if not target:
        flash("المسار غير مسموح. المسموح: ملفات المشروع فقط (templates/ static/ routes/ أو ملفات .py بالجذر).", "error")
        return redirect(url_for("dev.files"))
    if not f or not f.filename:
        flash("اختار ملف للرفع.", "error")
        return redirect(url_for("dev.files"))
    data = f.read(8 * 1024 * 1024)  # 8 MB cap per code/template file
    if f.read(1):
        flash("الملف كبير جداً (الحد 8MB).", "error")
        return redirect(url_for("dev.files"))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        flash("الملف لازم يكون نصي (UTF-8).", "error")
        return redirect(url_for("dev.files"))
    ok, err = py_syntax_ok(rel, text)
    if not ok:
        flash(f"ما تم التحديث: {err}", "error")
        return redirect(url_for("dev.files"))
    # backup the current version
    backups = os.path.join(current_app.instance_path, "backups")
    os.makedirs(backups, exist_ok=True)
    backup_rel = ""
    if os.path.isfile(target):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_name = rel.replace("/", "__") + f".{stamp}-{secrets.token_hex(3)}.bak"
        shutil.copy2(target, os.path.join(backups, backup_name))
        backup_rel = backup_name
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as out:
        out.write(text)
    os.replace(tmp, target)
    db.session.add(DeployLog(user_id=(g.user.id if g.get("user") else None), path=rel, size=len(data),
                             sha256=hashlib.sha256(data).hexdigest(), backup=backup_rel,
                             note=(request.form.get("note") or "")[:200], action="upload"))
    db.session.commit()
    log_event("file_update", rel)
    flash(f"تم تحديث {rel}. اضغط Reload بتبويب Web بـ PythonAnywhere حتى يشتغل التغيير.", "ok")
    return redirect(url_for("dev.files"))


@bp.route("/files/rollback/<int:log_id>", methods=["POST"])
@developer_required
def file_rollback(log_id):
    rec = db.session.get(DeployLog, log_id)
    if not rec or not rec.backup:
        flash("ماكو نسخة احتياطية لهذا الملف.", "error")
        return redirect(url_for("dev.files"))
    target = safe_target(rec.path)
    backup = os.path.join(current_app.instance_path, "backups", rec.backup)
    if not target or not os.path.isfile(backup):
        flash("النسخة الاحتياطية مو موجودة.", "error")
        return redirect(url_for("dev.files"))
    # backup current before rolling back, so rollback is itself reversible
    stamp = time.strftime("%Y%m%d-%H%M%S")
    cur_bak = rec.path.replace("/", "__") + f".{stamp}-{secrets.token_hex(3)}.bak"
    if os.path.isfile(target):
        shutil.copy2(target, os.path.join(current_app.instance_path, "backups", cur_bak))
    shutil.copy2(backup, target)
    with open(target, "rb") as fh:
        data = fh.read()
    db.session.add(DeployLog(user_id=(g.user.id if g.get("user") else None), path=rec.path, size=len(data),
                             sha256=hashlib.sha256(data).hexdigest(), backup=cur_bak,
                             note=f"رجوع لنسخة {rec.at:%Y/%m/%d %H:%M}", action="rollback"))
    db.session.commit()
    log_event("file_rollback", rec.path)
    flash(f"تم إرجاع {rec.path} للنسخة السابقة. اضغط Reload بـ PythonAnywhere.", "ok")
    return redirect(url_for("dev.files"))
