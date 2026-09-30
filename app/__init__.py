import os
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_session import Session
from dotenv import load_dotenv

load_dotenv(override=True)

db = SQLAlchemy()
migrate = Migrate()


def create_app():
    app = Flask(__name__)

    # ── Config ──────────────────────────────────────────────
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
    # Neon / Render provide "postgres://" or "postgresql://" but we explicitly want psycopg2
    db_url = os.environ.get("DATABASE_URL", "sqlite:///resume_builder.db")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SESSION_TYPE"] = "sqlalchemy"

    # ── Force HTTPS scheme in production (Render) ───────────
    if os.environ.get("FLASK_ENV") != "development":
        app.config["PREFERRED_URL_SCHEME"] = "https"

    # ── Extensions ──────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    app.config["SESSION_SQLALCHEMY"] = db
    Session(app)

    # ── Trust reverse-proxy headers (Render, Heroku, etc.) ──
    # x_proto=1 makes Flask read X-Forwarded-Proto so url_for() generates https://
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_prefix=1)

    # ── Allow HTTP for local OAuth development ─────────────
    if app.debug or os.environ.get("FLASK_ENV") == "development":
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    # ── Blueprints ──────────────────────────────────────────
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp, google_bp
    from app.routes.resume import resume_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(google_bp, url_prefix="/login")
    app.register_blueprint(resume_bp, url_prefix="/resume")

    return app
