import re

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from models import User, db, utcnow
from security import client_ip, limiter, login_required

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,32}$")
_DUMMY_HASH = generate_password_hash("timing-equalizer")


def start_session(user):
    session.clear()
    if not user.is_admin and current_app.config["SINGLE_DEVICE"]:
        user.session_version += 1  # logs out any other device
    user.last_login_at = utcnow()
    db.session.commit()
    session["uid"] = user.id
    session["ver"] = user.session_version
    session.permanent = True


def safe_next(target):
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("student.home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if not (limiter.hit(f"login-ip:{client_ip()}", 25, 600) and limiter.hit(f"login-u:{username}", 8, 600)):
            flash("محاولات كثيرة. انتظر 10 دقايق وجرّب مرة ثانية.", "error")
            return render_template("auth/login.html", username=username), 429
        user = User.query.filter_by(username=username).first()
        if not user:
            from werkzeug.security import check_password_hash
            check_password_hash(_DUMMY_HASH, password)
            flash("اسم المستخدم أو كلمة المرور غلط.", "error")
        elif not user.check_password(password):
            flash("اسم المستخدم أو كلمة المرور غلط.", "error")
        elif user.is_blocked:
            flash("هذا الحساب موقوف. تواصل مع المطور.", "error")
        else:
            start_session(user)
            return redirect(safe_next(request.args.get("next")) or url_for("student.home"))
        return render_template("auth/login.html", username=username)
    return render_template("auth/login.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("student.home"))
    form = {}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in ("name", "username")}
        form["username"] = form["username"].lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        error = None
        if not limiter.hit(f"register:{client_ip()}", 6, 3600):
            error = "سويت حسابات كثيرة من نفس الجهاز. جرّب بعدين."
        elif not (2 <= len(form["name"]) <= 80):
            error = "اكتب اسمك الثلاثي (من 2 إلى 80 حرف)."
        elif not USERNAME_RE.match(form["username"]):
            error = "اسم المستخدم لازم يكون 3-32 حرف إنكليزي أو أرقام أو _ أو ."
        elif len(password) < 8:
            error = "كلمة المرور لازم تكون 8 أحرف أو أكثر."
        elif password != confirm:
            error = "كلمتين المرور مو متطابقات."
        elif User.query.filter_by(username=form["username"]).first():
            error = "اسم المستخدم محجوز، اختار غيره."
        if error:
            flash(error, "error")
        else:
            user = User(name=form["name"], username=form["username"])
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            start_session(user)
            flash("تم إنشاء حسابك. هسه فعّل اشتراكك.", "ok")
            return redirect(url_for("student.subscribe"))
    return render_template("auth/register.html", form=form)


@bp.route("/features")
def features():
    """Public page: what the platform offers (linked from the login page)."""
    return render_template("features.html", teacher=current_app.config["ADMIN_NAME"])


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        if not limiter.hit(f"pwchange:{g.user.id}", 6, 900):
            flash("محاولات كثيرة، جرّب بعدين.", "error")
        elif not g.user.check_password(current):
            flash("كلمة المرور الحالية غلط.", "error")
        elif len(new) < 8:
            flash("كلمة المرور الجديدة لازم تكون 8 أحرف أو أكثر.", "error")
        elif new != confirm:
            flash("كلمتين المرور مو متطابقات.", "error")
        else:
            g.user.set_password(new)
            g.user.session_version += 1  # log out other devices
            db.session.commit()
            session["ver"] = g.user.session_version
            flash("تم تغيير كلمة المرور.", "ok")
            return redirect(url_for("auth.account"))
    return render_template("auth/account.html")
