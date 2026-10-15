"""Installable app support: web manifest, service worker, offline page and the Android
Digital Asset Links file that lets the Google Play app (TWA) open this site full-screen."""
import json
import os

from flask import Blueprint, Response, current_app, render_template, send_from_directory, url_for

bp = Blueprint("pwa", __name__)


@bp.route("/manifest.webmanifest")
def manifest():
    c = current_app.config
    icon = lambda f: url_for("static", filename=f"icons/{f}")  # noqa: E731
    shot = lambda f: url_for("static", filename=f"img/features/{f}.webp")  # noqa: E731
    data = {
        "id": "/",
        "name": c["SITE_NAME"],
        "short_name": c["SITE_NAME"],
        "description": c["SITE_TAGLINE"],
        "lang": "ar",
        "dir": "rtl",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f4f2ec",
        "theme_color": "#0f6b5c",
        "categories": ["education"],
        "icons": [
            {"src": icon("icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icon("icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icon("icon-maskable-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": icon("icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "screenshots": [
            {"src": shot(n), "sizes": "540x1169", "type": "image/webp", "form_factor": "narrow", "label": label}
            for n, label in (("home", "المواد"), ("watch", "مشاهدة الشرح"), ("summary", "الملخصات"), ("live", "البث المباشر"))
        ],
    }
    resp = Response(json.dumps(data, ensure_ascii=False), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/sw.js")
def service_worker():
    # Served from the site root so the worker controls every page (a /static/ worker could not)
    resp = send_from_directory(os.path.join(current_app.static_folder, "js"), "sw.js",
                               mimetype="application/javascript", max_age=0)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/offline")
def offline():
    resp = Response(render_template("offline.html"))
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/.well-known/assetlinks.json")
def assetlinks():
    """Proves to Android that the Play app and this domain belong to the same owner, so the app
    opens the site without a browser bar. Needs ANDROID_PACKAGE and ANDROID_SHA256 in .env."""
    c = current_app.config
    prints = [p.strip().upper() for p in c["ANDROID_SHA256"].split(",") if p.strip()]
    data = []
    if c["ANDROID_PACKAGE"] and prints:
        data = [{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "android_app", "package_name": c["ANDROID_PACKAGE"],
                       "sha256_cert_fingerprints": prints},
        }]
    resp = Response(json.dumps(data), mimetype="application/json")
    resp.headers["Cache-Control"] = "public, max-age=300"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp
