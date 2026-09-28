import os
from dotenv import load_dotenv
from flask import Blueprint, redirect, url_for, session, request, render_template
from flask_dance.contrib.google import make_google_blueprint, google
from app import db
from app.models.user import User

load_dotenv(override=True)
# Only allow insecure transport in local development — NEVER in production
if os.environ.get("FLASK_ENV") == "development":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

auth_bp = Blueprint("auth", __name__)

google_bp = make_google_blueprint(
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    scope=["openid", "https://www.googleapis.com/auth/userinfo.email",
           "https://www.googleapis.com/auth/userinfo.profile"],
    redirect_to="auth.after_google_login",
)


@auth_bp.route("/")
def auth_page():
    """Render the sign-in / sign-up page."""
    if "user" in session:
        return redirect(url_for("resume.dashboard"))
    return render_template("auth.html")


@auth_bp.route("/login")
def login():
    if not google.authorized:
        return redirect(url_for("google.login"))
    return redirect(url_for("auth.after_google_login"))


@auth_bp.route("/google/callback")
def after_google_login():
    if not google.authorized:
        return redirect(url_for("auth.login"))

    resp = google.get("/oauth2/v2/userinfo")
    if not resp.ok:
        return "Failed to fetch user info from Google.", 400

    info = resp.json()
    user = User.query.filter_by(google_id=info["id"]).first()

    if not user:
        user = User(
            google_id=info["id"],
            email=info["email"],
            first_name=info.get("given_name"),
            last_name=info.get("family_name"),
        )
        db.session.add(user)
        db.session.commit()

    session["user"] = {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
    }

    return redirect(url_for("resume.dashboard"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.index"))
