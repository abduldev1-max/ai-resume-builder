from app import db
from datetime import datetime


class Resume(db.Model):
    """A resume belongs to one User and can have many versions."""
    __tablename__ = "resumes"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title      = db.Column(db.String(256), nullable=False, default="My Resume")
    job_target = db.Column(db.String(256), nullable=True)   # For keyword matching (stretch)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    versions = db.relationship(
        "ResumeVersion", backref="resume", lazy=True, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Resume {self.title} (user={self.user_id})>"


class ResumeVersion(db.Model):
    """One snapshot of a resume — stores AI output JSON + PDF link."""
    __tablename__ = "resume_versions"

    id            = db.Column(db.Integer, primary_key=True)
    resume_id     = db.Column(db.Integer, db.ForeignKey("resumes.id"), nullable=False)
    content_json  = db.Column(db.JSON, nullable=False)     # Structured AI output
    pdf_url       = db.Column(db.String(512), nullable=True)
    ai_model_used = db.Column(db.String(64), nullable=True)  # "grok" or "gemini"
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ResumeVersion resume={self.resume_id} model={self.ai_model_used}>"
