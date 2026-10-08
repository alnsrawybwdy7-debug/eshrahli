import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, current_app, flash, g, redirect, request, session, url_for

UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


# ---------- CSRF ----------
def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok


def check_csrf():
    """Returns None if OK, otherwise a response to send instead of running the view."""
    if request.method not in UNSAFE_METHODS:
        return None
    sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf", "")
    expected = session.get("_csrf", "")
    if sent and expected and hmac.compare_digest(sent, expected):
        return None
    msg = "انتهت صلاحية الصفحة. جرّب مرة ثانية."
    # Background requests (fetch/XHR) get a JSON error; normal forms go back to the page
    if request.headers.get("X-CSRF-Token") is not None or request.is_json or request.method != "POST":
        abort(400, description=msg)
    csrf_token()  # make sure the fresh page gets a valid token
    flash(msg, "warn")
    target = request.full_path.rstrip("?") if request.endpoint not in (None, "auth.logout") else url_for("auth.login")
    if not target.startswith("/") or target.startswith("//"):
        target = "/"
    return redirect(target, code=303)


# ---------- Rate limiting (in-memory, per process) ----------
class RateLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key, limit, window):
        """Register a hit; return False if the key exceeded `limit` hits in `window` seconds."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50000:  # crude memory guard
                self._hits.clear()
            return True


limiter = RateLimiter()


def client_ip():
    """Real visitor IP. When the site sits behind our Cloudflare Worker, the Worker passes the
    visitor's IP in X-Client-IP together with a shared secret, so we only trust it if the secret matches."""
    secret = current_app.config.get("PROXY_SECRET", "")
    if secret:
        sent = request.headers.get("X-Proxy-Secret", "")
        ip = request.headers.get("X-Client-IP", "").strip()
        if ip and sent and hmac.compare_digest(sent, secret):
            return ip[:64]
    return request.remote_addr or "?"


# ---------- Access decorators ----------
def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapped


def subscription_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if not g.user.has_active_sub:
            flash("اشتراكك غير مفعّل أو منتهي. فعّل اشتراكك حتى تكدر تدخل.", "warn")
            return redirect(url_for("student.subscribe"))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if not g.user.is_admin:
            abort(404)
        return view(*a, **kw)
    return wrapped
