import os
from datetime import timezone
from zoneinfo import ZoneInfo

import click
from flask import Flask, g, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

import livekit_util
import storage
from config import Config
from models import KIND_LABELS, KIND_SINGULAR, LiveSession, User, db
from security import check_csrf, csrf_token


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    os.makedirs(app.instance_path, exist_ok=True)

    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    if app.config["SECRET_KEY_IS_RANDOM"]:
        # No SECRET_KEY in .env: keep a generated one on disk so logins survive reloads/workers
        key_file = os.path.join(app.instance_path, "secret_key")
        try:
            with open(key_file) as f:
                app.config["SECRET_KEY"] = f.read().strip() or app.config["SECRET_KEY"]
        except FileNotFoundError:
            with open(key_file, "w") as f:
                f.write(app.config["SECRET_KEY"])
        app.logger.warning("SECRET_KEY غير مضبوط بملف .env، استخدمنا مفتاح محفوظ بـ instance/secret_key")

    db.init_app(app)

    from routes.admin import bp as admin_bp
    from routes.auth import bp as auth_bp
    from routes.live import bp as live_bp
    from routes.student import bp as student_bp
    for bp in (auth_bp, student_bp, live_bp, admin_bp):
        app.register_blueprint(bp)

    tz = ZoneInfo(app.config["TIMEZONE"])

    @app.url_defaults
    def static_cache_bust(endpoint, values):
        # /static/css/style.css?v=<mtime>: a new deploy gets a new URL, so caches never serve stale files
        if endpoint == "static" and "filename" in values and "v" not in values:
            try:
                values["v"] = int(os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime)
            except OSError:
                pass

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            user = db.session.get(User, uid)
            if user is None or user.is_blocked or session.get("ver") != user.session_version:
                tok = session.get("_csrf")
                session.clear()
                if tok:
                    session["_csrf"] = tok  # keep open forms (e.g. the login page) valid
            else:
                g.user = user
        return check_csrf()

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), display-capture=(self)")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob: https:; "
            "media-src 'self' blob: https:; "
            "connect-src 'self' https: wss:; "
            "worker-src 'self' blob:; "
            "frame-ancestors 'self'; base-uri 'self'; form-action 'self'",
        )
        if request.endpoint != "static":
            # Pages contain per-user data and form tokens: never let the browser reuse an old copy
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.context_processor
    def inject():
        live = None
        if g.get("user") and g.user.has_active_sub:
            live = LiveSession.current()
        c = app.config
        tg = c["CONTACT_TELEGRAM"]
        return {
            "csrf_token": csrf_token,
            "site_name": c["SITE_NAME"],
            "site_tagline": c["SITE_TAGLINE"],
            "teacher_label": c["TEACHER_LABEL"],
            "telegram_user": tg,
            "telegram_url": f"https://t.me/{tg}" if tg else "",
            "live_now": live,
            "KIND_LABELS": KIND_LABELS,
            "KIND_SINGULAR": KIND_SINGULAR,
            "r2_on": storage.r2_enabled(),
            "livekit_on": livekit_util.configured(),
        }

    @app.template_filter("local")
    def local_dt(dt, fmt="%Y/%m/%d %I:%M %p"):
        if not dt:
            return "—"
        out = dt.replace(tzinfo=timezone.utc).astimezone(tz).strftime(fmt)
        return out.replace("AM", "ص").replace("PM", "م")

    @app.template_filter("filesize")
    def filesize(n):
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
            n /= 1024

    @app.template_filter("code")
    def fmt_code(c):
        return "-".join(c[i:i + 4] for i in range(0, len(c), 4))

    def error_page(code, default_msg):
        def handler(e):
            desc = getattr(e, "description", None)
            # Only show custom (Arabic) descriptions passed to abort(); hide werkzeug's English defaults
            msg = desc if desc and desc != getattr(type(e), "description", None) else default_msg
            return render_template("error.html", code=code, message=msg), code
        return handler

    app.register_error_handler(400, error_page(400, "طلب غير صالح."))
    app.register_error_handler(403, error_page(403, "ما عندك صلاحية."))
    app.register_error_handler(404, error_page(404, "الصفحة غير موجودة."))
    app.register_error_handler(500, error_page(500, "صار خطأ بالسيرفر."))

    with app.app_context():
        db.create_all()
        ensure_admin(app)

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.argument("password")
    @click.option("--name", default="فاطمة ثائر")
    def create_admin_cmd(username, password, name):
        """Create an admin, or reset an existing user's password and make them admin."""
        username = username.strip().lower()
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User(username=username, name=name)
            db.session.add(user)
        user.is_admin = True
        user.is_blocked = False
        user.set_password(password)
        user.session_version = (user.session_version or 0) + 1
        db.session.commit()
        click.echo(f"Admin ready: {username}")

    return app


def ensure_admin(app):
    c = app.config
    if c["ADMIN_USERNAME"] and c["ADMIN_PASSWORD"]:
        u = User.query.filter_by(username=c["ADMIN_USERNAME"]).first()
        if not u:
            u = User(username=c["ADMIN_USERNAME"], name=c["ADMIN_NAME"], is_admin=True)
            u.set_password(c["ADMIN_PASSWORD"])
            db.session.add(u)
            db.session.commit()
            app.logger.info("Created admin %s", c["ADMIN_USERNAME"])
        elif u.is_admin and c["ADMIN_NAME"] and u.name != c["ADMIN_NAME"]:
            # Keep the teacher's display name in sync with ADMIN_NAME in .env
            u.name = c["ADMIN_NAME"]
            db.session.commit()
    elif not User.query.filter_by(is_admin=True).first():
        app.logger.warning("ماكو حساب أدمن. حط ADMIN_USERNAME و ADMIN_PASSWORD بملف .env")


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
