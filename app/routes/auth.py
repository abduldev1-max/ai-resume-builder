import os
from dotenv import load_dotenv
from flask import Blueprint, redirect, url_for, session, request, render_template, flash
from flask_dance.contrib.google import make_google_blueprint, google
from app import db
from app.models.user import User
from werkzeug.security import generate_password_hash, check_password_hash
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


@auth_bp.route("/signin", methods=["POST"])
def signin():
    email = request.form.get("email")
    password = request.form.get("password")
    user = User.query.filter_by(email=email).first()

    if user and user.password_hash and check_password_hash(user.password_hash, password):
        session["user"] = {
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name,
        }
        return redirect(url_for("resume.dashboard"))
    
    flash("Invalid email or password.", "error")
    return redirect(url_for("auth.auth_page"))


@auth_bp.route("/signup", methods=["POST"])
def signup():
    first_name = request.form.get("first_name")
    last_name = request.form.get("last_name")
    email = request.form.get("email")
    password = request.form.get("password")

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        flash("An account with this email already exists.", "error")
        return redirect(url_for("auth.auth_page"))

    new_user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        password_hash=generate_password_hash(password)
    )
    db.session.add(new_user)
    db.session.commit()

    session["user"] = {
        "id": new_user.id,
        "email": new_user.email,
        "first_name": new_user.first_name,
    }
    return redirect(url_for("resume.dashboard"))


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
