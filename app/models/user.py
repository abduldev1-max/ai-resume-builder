from app import db
from datetime import datetime


class User(db.Model):
    """Stores authenticated users (Google OAuth)."""
    __tablename__ = "users"

    id         = db.Column(db.Integer, primary_key=True)
    google_id  = db.Column(db.String(128), unique=True, nullable=False)
    email      = db.Column(db.String(256), unique=True, nullable=False)
    first_name = db.Column(db.String(128), nullable=True)
    last_name  = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    resumes = db.relationship("Resume", backref="owner", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email}>"
