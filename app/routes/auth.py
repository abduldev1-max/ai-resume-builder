import os
from dotenv import load_dotenv
from flask import Blueprint, redirect, url_for, session, request, render_template, flash
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.consumer import oauth_authorized
from app import db
from app.models.user import User
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv(override=True)

# ── OAuth environment setup ──────────────────────────────────────────────────
# Allow insecure transport (HTTP) only in local development — NEVER in production
if os.environ.get("FLASK_ENV") == "development":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Relax scope checking — Google sometimes returns a slightly different scope set
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# In production (Render) we must tell flask-dance the exact HTTPS callback URL.
# In local dev we leave it as None so flask-dance auto-detects localhost.
_render_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PRODUCTION_URL", "").rstrip("/")
if _render_url:
    _redirect_url = f"{_render_url}/login/google/authorized"
else:
    _redirect_url = None

auth_bp = Blueprint("auth", __name__)

google_bp = make_google_blueprint(
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    scope=["openid", "https://www.googleapis.com/auth/userinfo.email",
           "https://www.googleapis.com/auth/userinfo.profile"],
    # Redirect to our dedicated done route — keeps the flow inside our app
    redirect_to="auth.google_done",
    redirect_url=_redirect_url,
)


# ── Signal-based Google OAuth handler ───────────────────────────────────────
# This fires IMMEDIATELY after the token exchange succeeds, before any redirect.
# Much more reliable than checking google.authorized in a separate route,
# which can be False on the first attempt due to session commit timing.
@oauth_authorized.connect_via(google_bp)
def google_logged_in(blueprint, token):
    if not token:
        flash("Failed to sign in with Google. Please try again.", "error")
        return False  # Don't store token; flask-dance will redirect to redirect_to

    # Fetch user profile directly from the blueprint session (token is fresh)
    resp = blueprint.session.get("/oauth2/v2/userinfo")
    if not resp.ok:
        flash("Could not fetch your Google profile. Please try again.", "error")
        return False

    info = resp.json()
    google_id = info.get("id")
    if not google_id:
        flash("Google did not return a valid account. Please try again.", "error")
        return False

    # Find or create the user
    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        # Also check if they signed up with email/password first
        user = User.query.filter_by(email=info.get("email")).first()
        if user:
            # Link the existing account to their Google ID
            user.google_id = google_id
        else:
            user = User(
                google_id=google_id,
                email=info.get("email"),
                first_name=info.get("given_name"),
                last_name=info.get("family_name"),
            )
            db.session.add(user)
        db.session.commit()

    # Set the user session — this is the key step
    session["user"] = {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
    }

    # Return False so flask-dance does NOT try to store the OAuth token in the DB
    # (we don't need persistent token storage; session is enough)
    return False


@auth_bp.route("/")
def auth_page():
    """Render the sign-in / sign-up page."""
    if "user" in session:
        return redirect(url_for("resume.dashboard"))
    return render_template("auth.html")


@auth_bp.route("/login")
def login():
    """Kick off Google OAuth flow."""
    # Force the session to be saved NOW so the OAuth state cookie persists
    # across Gunicorn workers (avoids state mismatch on callback).
    session.modified = True
    return redirect(url_for("google.login"))


@auth_bp.route("/google/done")
def google_done():
    """Fallback landing page after Google OAuth. The signal handler already
    set session['user'], so we just redirect to the dashboard."""
    if "user" not in session:
        flash("Google sign-in failed. Please try again.", "error")
        return redirect(url_for("auth.auth_page"))
    return redirect(url_for("resume.dashboard"))


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


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.index"))
