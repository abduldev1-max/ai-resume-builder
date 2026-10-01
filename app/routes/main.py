from flask import Blueprint, render_template, session, redirect, url_for, make_response

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    user = session.get("user")
    # Already logged in → go straight to dashboard
    if user:
        return redirect(url_for("resume.dashboard"))
    
    # Render index but prevent caching so OAuth redirects don't show stale page
    resp = make_response(render_template("index.html", user=user))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "-1"
    return resp
