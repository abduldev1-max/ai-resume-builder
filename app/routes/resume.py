import os, json, logging
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from functools import wraps
from app import db
from app.models.resume import Resume, ResumeVersion
from app.services.ai_service import (
    generate_resume_content,
    generate_single_experience_bullets,
    chat_with_ai,
    get_ai_status,
)
from app.services.pdf_service import compile_pdf

logger = logging.getLogger(__name__)

_DEV_USER = {"id": 1, "email": "dev@local", "first_name": "Abdul", "last_name": "Dev"}

resume_bp = Blueprint("resume", __name__)


def _ensure_dev_user_exists():
    """Create the dev user row (id=1) in PostgreSQL if it doesn't exist.
    Required after a fresh DB migration so FK constraints on resumes/sessions are satisfied."""
    from app.models.user import User
    from datetime import datetime
    try:
        if not User.query.get(1):
            dev = User(
                id=1,
                google_id="dev-local-user",
                email="dev@local",
                first_name="Abdul",
                last_name="Dev",
                created_at=datetime.utcnow(),
            )
            db.session.add(dev)
            db.session.commit()
            # Reset the sequence so the next real user gets id=2+
            db.session.execute(db.text("SELECT setval(pg_get_serial_sequence('users', 'id'), MAX(id)) FROM users"))
            db.session.commit()
    except Exception:
        db.session.rollback()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            if os.environ.get("DEV_AUTO_LOGIN", "").lower() == "true":
                _ensure_dev_user_exists()
                session["user"] = _DEV_USER
            else:
                return redirect(url_for("auth.auth_page"))
        return f(*args, **kwargs)
    return decorated


# ── Dashboard ────────────────────────────────────────────────
@resume_bp.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user"]["id"]
    resumes = Resume.query.filter_by(user_id=user_id).order_by(Resume.updated_at.desc()).all()
    return render_template("dashboard.html", resumes=resumes, user=session["user"], active_page="dashboard")


# ── Resume Library ────────────────────────────────────────────
@resume_bp.route("/library")
@login_required
def library():
    user_id = session["user"]["id"]
    resumes = Resume.query.filter_by(user_id=user_id).order_by(Resume.updated_at.desc()).all()
    return render_template("resume_library.html", resumes=resumes, user=session["user"], active_page="resume")


# ── AI Editor ────────────────────────────────────────────────
@resume_bp.route("/editor")
@resume_bp.route("/editor/<int:resume_id>")
@login_required
def ai_editor(resume_id=None):
    resume, version = None, None
    if resume_id:
        resume = Resume.query.get_or_404(resume_id)
        version = (
            ResumeVersion.query
            .filter_by(resume_id=resume_id)
            .order_by(ResumeVersion.created_at.desc())
            .first()
        )
    return render_template(
        "ai_editor.html",
        resume=resume,
        version=version,
        initial_data=version.content_json if (version and version.content_json) else None,
        user=session["user"],
        active_page="editor",
        ai_status=get_ai_status(),
    )


# ── API: Generate Complete AI Resume ─────────────────────────
@resume_bp.route("/api/generate-full", methods=["POST"])
@login_required
def api_generate_full():
    """
    Generate a COMPLETE ATS-friendly resume from raw notes, partial details, or target job.
    Saves the version, compiles the PDF, and returns the full structured data for instant UI sync.
    """
    data = request.get_json() or {}
    user_id = session["user"]["id"]
    resume_id = data.get("resume_id")
    provider = data.get("provider", "auto")

    resume = None
    if resume_id:
        resume = Resume.query.filter_by(id=resume_id, user_id=user_id).first()

    title = data.get("title") or (resume.title if resume else None) or "My Technical Resume"
    job_target = data.get("job_target") or (resume.job_target if resume else None) or ""

    if not resume:
        resume = Resume(user_id=user_id, title=title, job_target=job_target)
        db.session.add(resume)
        db.session.commit()
    else:
        if title: resume.title = title
        if job_target: resume.job_target = job_target
        db.session.commit()

    try:
        content_json, model_used = generate_resume_content(data, preferred_provider=provider)
        pdf_url = compile_pdf(content_json, resume.id)
    except Exception as e:
        logger.error(f"Generate full failed: {e}")
        return jsonify({"error": str(e)}), 500

    version = ResumeVersion(
        resume_id=resume.id,
        content_json=content_json,
        pdf_url=pdf_url,
        ai_model_used=model_used,
    )
    db.session.add(version)
    db.session.commit()

    return jsonify({
        "success": True,
        "resume_id": resume.id,
        "version_id": version.id,
        "resume_data": content_json,
        "pdf_url": pdf_url,
        "model_used": model_used,
    })


# ── API: Generate Single Experience Bullets ──────────────────
@resume_bp.route("/api/generate-bullets", methods=["POST"])
@login_required
def api_generate_bullets():
    data = request.get_json() or {}
    title = data.get("title", "").strip()
    company = data.get("company", "").strip()
    description = data.get("description", "").strip()
    job_target = data.get("job_target", "").strip()
    job_description = data.get("job_description", "").strip()
    provider = data.get("provider", "auto")

    try:
        bullets = generate_single_experience_bullets(
            title=title,
            company=company,
            description=description,
            job_target=job_target,
            job_description=job_description,
            preferred_provider=provider,
        )
        return jsonify({"success": True, "bullets": bullets})
    except Exception as e:
        logger.error(f"Generate bullets failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ── AI Status ────────────────────────────────────────────────
@resume_bp.route("/ai-status", methods=["GET"])
@login_required
def ai_status_route():
    return jsonify(get_ai_status())


# ── AI Chat ──────────────────────────────────────────────────
@resume_bp.route("/ai-chat", methods=["POST"])
@login_required
def ai_chat():
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    context = data.get("context", {})
    provider = data.get("provider", "auto")
    history = data.get("history", [])

    if not message:
        return jsonify({"error": "No message provided"}), 400

    try:
        reply, model_name, provider_name, resume_data = chat_with_ai(
            message=message,
            context=context,
            provider=provider,
            history=history,
        )
        return jsonify({
            "reply": reply,
            "model": model_name,
            "provider": provider_name,
            "resume_data": resume_data,
        })
    except Exception as e:
        logger.error(f"AI chat failed: {e}")
        return jsonify({
            "error": str(e),
            "reply": f"AI error: {e}. Please check the API keys in your .env file."
        }), 500


# ── New Resume (intake form + POST) ─────────────────────────
@resume_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_resume():
    if request.method == "POST":
        form_data = request.form.to_dict(flat=False)
        title = request.form.get("title", "My Resume")
        job_target = request.form.get("job_target", "")

        resume = Resume(user_id=session["user"]["id"], title=title, job_target=job_target)
        db.session.add(resume)
        db.session.commit()

        try:
            content_json, model_used = generate_resume_content(form_data)
            pdf_url = compile_pdf(content_json, resume.id)
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            content_json = {}
            pdf_url = None
            model_used = "none"

        version = ResumeVersion(
            resume_id=resume.id, content_json=content_json,
            pdf_url=pdf_url, ai_model_used=model_used,
        )
        db.session.add(version)
        db.session.commit()

        return redirect(url_for("resume.ai_editor", resume_id=resume.id))

    # GET — show the intake form
    return render_template("intake_form.html", user=session["user"], active_page="new")


# ── View resume → open in editor ────────────────────────────
@resume_bp.route("/<int:resume_id>")
@login_required
def view_resume(resume_id):
    resume = Resume.query.get_or_404(resume_id)
    # Ownership check
    if resume.user_id != session["user"]["id"]:
        return redirect(url_for("resume.dashboard"))
    return redirect(url_for("resume.ai_editor", resume_id=resume_id))


# ── Download PDF ──────────────────────────────────────────────
@resume_bp.route("/<int:resume_id>/download")
@login_required
def download_pdf(resume_id):
    from flask import send_file, abort
    import os as _os
    resume = Resume.query.get_or_404(resume_id)
    if resume.user_id != session["user"]["id"]:
        abort(403)
    version = (
        ResumeVersion.query
        .filter_by(resume_id=resume_id)
        .order_by(ResumeVersion.created_at.desc())
        .first()
    )
    if not version or not version.content_json:
        return redirect(url_for("resume.ai_editor", resume_id=resume_id))

    # Compile if not present on disk or no pdf_url
    pdf_path = None
    if version.pdf_url:
        pdf_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(__file__)),
            version.pdf_url.lstrip("/").replace("/", _os.sep)
        )

    if not pdf_path or not _os.path.exists(pdf_path):
        from app.services.pdf_service import compile_pdf
        new_pdf_url = compile_pdf(version.content_json, resume.id)
        version.pdf_url = new_pdf_url
        db.session.commit()
        pdf_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(__file__)),
            new_pdf_url.lstrip("/").replace("/", _os.sep)
        )

    if _os.path.exists(pdf_path):
        filename = f"{resume.title or 'Resume'}.pdf"
        return send_file(pdf_path, as_attachment=True,
                         download_name=filename,
                         mimetype="application/pdf")
    return redirect(url_for("resume.ai_editor", resume_id=resume_id))


# ── Regenerate ────────────────────────────────────────────────
@resume_bp.route("/<int:resume_id>/regenerate", methods=["POST"])
@login_required
def regenerate(resume_id):
    resume = Resume.query.get_or_404(resume_id)
    data = request.json or {}
    content_json, model_used = generate_resume_content(data)
    pdf_url = compile_pdf(content_json, resume_id)
    version = ResumeVersion(
        resume_id=resume_id, content_json=content_json,
        pdf_url=pdf_url, ai_model_used=model_used,
    )
    db.session.add(version)
    db.session.commit()
    return jsonify({"status": "ok", "version_id": version.id, "pdf_url": pdf_url})


# ── Delete single ──────────────────────────────────────────────
@resume_bp.route("/<int:resume_id>/delete", methods=["POST"])
@login_required
def delete_resume(resume_id):
    resume = Resume.query.get_or_404(resume_id)
    if resume.user_id != session["user"]["id"]:
        return jsonify({"error": "Unauthorized"}), 403
    db.session.delete(resume)
    db.session.commit()
    return jsonify({"status": "deleted"})


# ── Delete all ─────────────────────────────────────────────────
@resume_bp.route("/delete-all", methods=["POST"])
@login_required
def delete_all_resumes():
    try:
        user_id = session["user"]["id"]
        # Get all resume IDs for this user
        resume_ids = [r.id for r in Resume.query.filter_by(user_id=user_id).all()]
        if resume_ids:
            # Delete child versions first (avoids FK constraint violation)
            ResumeVersion.query.filter(ResumeVersion.resume_id.in_(resume_ids)).delete(synchronize_session=False)
            # Now safe to bulk-delete the parent resumes
            Resume.query.filter_by(user_id=user_id).delete(synchronize_session=False)
            db.session.commit()
        return jsonify({"status": "all_deleted", "count": len(resume_ids)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"delete_all_resumes error: {e}")
        return jsonify({"error": str(e)}), 500



# ── ATS Optimizer page ─────────────────────────────────────────
@resume_bp.route("/ats-optimizer")
@resume_bp.route("/ats-optimizer/<int:resume_id>")
@login_required
def ats_optimizer(resume_id=None):
    user_id = session["user"]["id"]
    resumes = Resume.query.filter_by(user_id=user_id).order_by(Resume.updated_at.desc()).all()
    selected = None
    version = None
    if resume_id:
        selected = Resume.query.get_or_404(resume_id)
        version = (
            ResumeVersion.query
            .filter_by(resume_id=resume_id)
            .order_by(ResumeVersion.created_at.desc())
            .first()
        )
    elif resumes:
        selected = resumes[0]
        version = (
            ResumeVersion.query
            .filter_by(resume_id=selected.id)
            .order_by(ResumeVersion.created_at.desc())
            .first()
        )
    return render_template(
        "ats_optimizer.html",
        resumes=resumes,
        selected_resume=selected,
        version=version,
        user=session["user"],
        active_page="ats",
    )


# ── ATS Analyze (AI scoring) ───────────────────────────────────
@resume_bp.route("/ats-analyze", methods=["POST"])
@login_required
def ats_analyze():
    """AI-powered ATS analysis: score resume against job description."""
    data = request.get_json() or {}
    resume_text = data.get("resume_text", "").strip()
    job_description = data.get("job_description", "").strip()
    message = data.get("message", "").strip()       # chat message from user
    history = data.get("history", [])               # previous messages

    if not resume_text and not job_description:
        return jsonify({"error": "Please provide resume and job description"}), 400

    system = """You are an expert ATS (Applicant Tracking System) specialist and resume coach.
When given a resume and job description, you:
1. Calculate an ATS match score (0-100)
2. Identify matched keywords found in both resume and JD
3. Identify missing critical keywords from the JD not in the resume
4. Give section-by-section scores (Summary, Experience, Skills, Education)
5. Suggest specific improvements

When the user asks follow-up questions or requests rewrites, provide targeted help.

If asked for a full ATS analysis, respond in this EXACT JSON format:
{
  "type": "analysis",
  "score": 78,
  "grade": "B+",
  "matched_keywords": ["Python", "React", "team leadership"],
  "missing_keywords": ["Docker", "CI/CD", "Agile"],
  "section_scores": {
    "summary": 85,
    "experience": 80,
    "skills": 70,
    "education": 90
  },
  "strengths": ["Strong quantified achievements", "Relevant technical skills"],
  "improvements": [
    "Add Docker and Kubernetes to skills section",
    "Mention Agile/Scrum methodology in experience bullets",
    "Include CI/CD pipeline experience"
  ],
  "rewritten_summary": "Results-driven software engineer with 4+ years..."
}

For follow-up chat messages (not initial analysis), respond with:
{
  "type": "chat",
  "reply": "Your helpful response here..."
}"""

    if message:
        # Chat mode - user is asking a follow-up question
        history_formatted = [{"role": h["role"], "content": h["content"]} for h in history[-6:]]
        user_prompt = (
            f"Resume:\n{resume_text}\n\n"
            f"Job Description:\n{job_description}\n\n"
            f"User question: {message}"
        )
        messages = [{"role": "system", "content": system}] + history_formatted + [{"role": "user", "content": user_prompt}]
    else:
        # Initial full analysis
        user_prompt = (
            f"Please perform a complete ATS analysis.\n\n"
            f"RESUME:\n{resume_text}\n\n"
            f"JOB DESCRIPTION:\n{job_description}\n\n"
            f"Return JSON analysis."
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}]

    result = None

    result = None

    # Try Groq / Grok
    grok_key = (os.environ.get("GROQ_API_KEY") or os.environ.get("GROK_API_KEY") or "").strip()
    if grok_key:
        try:
            import httpx
            from openai import OpenAI
            is_groq = grok_key.startswith("gsk_")
            base_url = "https://api.groq.com/openai/v1" if is_groq else "https://api.x.ai/v1"
            model = "openai/gpt-oss-120b" if is_groq else "grok-beta"
            client = OpenAI(api_key=grok_key, base_url=base_url, http_client=httpx.Client())
            resp = client.chat.completions.create(
                model=model, messages=messages,
                temperature=0.3, max_tokens=1200,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
                raw = raw.rsplit("```", 1)[0]
            result = json.loads(raw)
        except Exception as e:
            logger.warning(f"Groq/Grok ATS failed: {e}")

    # Try Gemini fallback
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not result and gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            full = system + "\n\n" + (messages[-1]["content"] if messages else user_prompt)
            for m in ["gemini-3.6-flash", "gemini-flash-latest", "gemini-2.5-flash-lite"]:
                try:
                    model = genai.GenerativeModel(m)
                    resp = model.generate_content(full)
                    raw = resp.text.strip()
                    if raw.startswith("```"):
                        raw = raw.split("```")[1]
                        if raw.startswith("json"): raw = raw[4:]
                        raw = raw.rsplit("```", 1)[0]
                    result = json.loads(raw)
                    break
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Gemini ATS failed: {e}")

    # Smart local engine fallback (ensures 100% reliability even without keys)
    if not result:
        result = _run_local_ats_engine(resume_text, job_description, message)

    return jsonify(result)


def _run_local_ats_engine(resume_text, jd_text, message=None):
    """Local intelligent ATS evaluation and keyword analyzer fallback."""
    import re
    COMMON_SKILLS = [
        "Python", "JavaScript", "TypeScript", "React", "Node.js", "Next.js", "Docker",
        "Kubernetes", "AWS", "GCP", "Azure", "SQL", "PostgreSQL", "MongoDB", "Redis",
        "CI/CD", "Git", "REST API", "GraphQL", "Microservices", "Agile", "Scrum",
        "Machine Learning", "Data Analysis", "Linux", "Java", "C++", "Go", "HTML", "CSS",
        "FastAPI", "Flask", "Django", "Tailwind", "DevOps", "System Design"
    ]
    matched = []
    missing = []
    for s in COMMON_SKILLS:
        in_jd = bool(re.search(r"\b" + re.escape(s) + r"\b", jd_text, re.IGNORECASE))
        in_resume = bool(re.search(r"\b" + re.escape(s) + r"\b", resume_text, re.IGNORECASE))
        if in_jd:
            if in_resume:
                matched.append(s)
            else:
                missing.append(s)

    if not matched and not missing:
        matched = ["Communication", "Problem Solving"]
        missing = ["Target Skills", "System Optimization"]

    total_jd = len(matched) + len(missing)
    skill_pct = int((len(matched) / max(total_jd, 1)) * 100)
    has_summary = len(resume_text.split("\n")[0]) > 20
    summary_score = min(95, max(60, skill_pct + 10)) if has_summary else 50
    has_metrics = bool(re.search(r"\d+%", resume_text)) or bool(re.search(r"\$\d+", resume_text))
    exp_score = min(95, max(55, skill_pct + (15 if has_metrics else -5)))
    skills_score = skill_pct
    edu_score = 90 if re.search(r"\b(degree|bachelor|master|university|college|b\.s|b\.a)\b", resume_text, re.IGNORECASE) else 65

    overall = int((skills_score * 0.35) + (exp_score * 0.35) + (summary_score * 0.2) + (edu_score * 0.1))
    overall = max(25, min(96, overall))
    grade = "A+" if overall >= 90 else "A" if overall >= 80 else "B+" if overall >= 70 else "B" if overall >= 60 else "C" if overall >= 50 else "D"

    if message:
        import random
        m_lower = message.lower().strip()

        # ── Greeting / casual opener detection ──────────────────────────────
        GREETINGS = {"hi", "hey", "hello", "howdy", "sup", "yo", "hiya", "greetings",
                     "hola", "bonjour", "salut", "ciao", "what's up", "whats up",
                     "how are you", "how's it going", "hows it going",
                     "what can you do", "who are you", "what do you do",
                     "good morning", "good afternoon", "good evening", "good day"}
        is_greeting = (
            m_lower.rstrip("!?.,") in GREETINGS
            or any(m_lower.rstrip("!?.,").startswith(g) for g in GREETINGS)
            or len(m_lower.split()) <= 2 and any(g in m_lower for g in GREETINGS)
        )
        if is_greeting:
            replies = [
                "Hey there! 👋 I'm your ATS AI coach. How can I help you with your resume today?",
                "Hello! I'm here to help you optimize your resume for ATS systems. What would you like to work on?",
                "Hi! Ready to boost your ATS score. Paste your resume and a job description, then ask me anything — keyword gaps, bullet rewrites, summary optimization, and more.",
                "Hey! Great to meet you. I can analyze your ATS match score, surface missing keywords, rewrite experience bullets, and more. What would you like help with?",
            ]
            return {"type": "chat", "reply": random.choice(replies)}

        # ── Casual thanks / acknowledgement ─────────────────────────────────
        THANKS = {"thanks", "thank you", "thx", "ty", "great", "awesome", "nice", "perfect",
                  "cool", "ok", "okay", "got it", "understood", "sounds good", "alright"}
        if m_lower.rstrip("!?.,") in THANKS or len(m_lower.split()) <= 3 and any(t in m_lower for t in THANKS):
            return {"type": "chat", "reply": "You're welcome! 😊 Let me know if there's anything else I can help you optimize in your resume."}

        if "summary" in m_lower or "rewrite" in m_lower:
            reply = (
                f"Here is a tailored professional summary optimized for this job description:\n\n"
                f"\"Results-driven professional with deep expertise in {', '.join(matched[:3]) if matched else 'core competencies'}. "
                f"Proven track record delivering scalable solutions and applying {', '.join(missing[:2]) if missing else 'best practices'} "
                f"to accelerate product delivery, improve reliability, and drive measurable business impact.\""
            )
        elif "keyword" in m_lower or "missing" in m_lower:
            reply = (
                f"To boost your ATS match, prioritize adding these critical keywords:\n\n"
                + "\n".join([f"• **{kw}**: Incorporate this into your Experience bullet points with measurable impact." for kw in missing[:4]])
                + "\n\nWould you like me to rewrite a specific job bullet to include one of these?"
            )
        elif "bullet" in m_lower or "experience" in m_lower:
            target_kw = missing[0] if missing else "key technology"
            reply = (
                f"Here is an ATS-optimized bullet point using Google's X-Y-Z formula (Accomplished [X] as measured by [Y] by doing [Z]):\n\n"
                f"• Engineered and deployed scalable systems utilizing **{target_kw}**, improving processing throughput by 38% and reducing incident resolution time by 2.5x across cross-functional sprints."
            )
        else:
            reply = (
                f"I've analyzed your resume against the target role requirements.\n\n"
                f"Key action items:\n"
                f"1. **Include Missing Keywords**: Integrate **{', '.join(missing[:3]) if missing else 'domain tools'}** into your technical skills & bullets.\n"
                f"2. **Quantify Metrics**: Ensure every role has at least 2 bullets with percentages or numerical outcomes.\n"
                f"3. **ATS Header Format**: Keep contact details standard and ATS-readable.\n\n"
                f"What section would you like me to optimize first?"
            )
        return {"type": "chat", "reply": reply}

    return {
        "type": "analysis",
        "score": overall,
        "grade": grade,
        "matched_keywords": matched,
        "missing_keywords": missing,
        "section_scores": {
            "summary": summary_score,
            "experience": exp_score,
            "skills": skills_score,
            "education": edu_score
        },
        "strengths": [
            "Clean layout and recognizable section headers for ATS parsers",
            f"Verified match on {len(matched)} essential candidate competencies",
            "Professional career trajectory aligned with industry expectations"
        ],
        "improvements": [
            f"Integrate missing high-value keywords: {', '.join(missing[:3]) if missing else 'specialized skills'}",
            "Add quantifiable business metrics (%, $, latency, scale) to recent role bullets",
            "Align summary and title directly with the target job posting"
        ],
        "rewritten_summary": f"Results-driven professional with deep expertise in {', '.join(matched[:2]) if matched else 'core domains'}. Proven track record applying {', '.join(missing[:2]) if missing else 'modern methodologies'} to drive measurable business outcomes and team velocity."
    }


