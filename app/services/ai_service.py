"""
AI service: Groq / xAI Grok and Google Gemini support.
Returns structured JSON matching the resume schema or answers conversational coaching questions.
"""
import os
import json
import logging
import httpx
from openai import OpenAI
import google.generativeai as genai

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an elite ATS resume writer and executive career strategist specializing in the industry-standard executive ATS format (Resume Worded / Harvard style).
Your mission is to transform the candidate's input and job target into a rich, comprehensive, top-tier ATS resume.

ESSENTIAL REQUIREMENTS — MUST FOLLOW:
1. TARGET ROLE & HEADER:
   - "job_target": The target job title (e.g. "Hotel Security Officer", "Technical Program Manager") must be prominently identified as the professional title.
   - "title": Clean professional title for the resume.
2. EXPERIENCE SECTION — HIGH DEPTH & COMPLETE BULLETS (CRITICAL):
   - Never output empty bullets [] for any listed job experience.
   - For EVERY work experience provided by the candidate, you MUST generate 3 to 4 comprehensive, professional, action-oriented ATS bullet points.
   - Expand the candidate's role description, responsibilities, and job title into powerful achievement bullets using strong past-tense action verbs (e.g., Spearheaded, Orchestrated, Mitigated, Streamlined, Identified, Implemented, Monitored, Coordinated, Resolved).
   - Incorporate relevant industry keywords, best practices, metrics, and compliance standards aligned with the target job description.
   - Do NOT invent fake companies or replace the user's actual company; enhance the actual role with industry-standard achievements, scope, and results.
3. EDUCATION SECTION:
   - Preserve the candidate's actual institution, degree, year, and GPA.
   - Clean up typos and apply standard capitalization (e.g. "Overland High School", "High School Diploma", "B.S. in Computer Science").
4. SKILLS & COMPETENCIES (ADDITIONAL INFORMATION):
   - Group skills into high-impact ATS categories matching the role (e.g., "Technical Skills", "Core Competencies", "Tools & Systems", "Certifications & Compliance").
   - Populate with rich, industry-standard keywords from the target job description and candidate's domain.
5. PROFESSIONAL SUMMARY:
   - Provide a compelling, high-impact 2-3 sentence ATS career summary that connects the candidate's background to their target role.
6. ATS RULES:
   - Third-person phrasing only (never use "I", "my", "we").
   - Quantified impact wherever realistic (percentages, scope, frequency).

Return ONLY valid JSON matching this schema — no markdown fences, no extra text:
{
  "title": "Professional Title",
  "job_target": "Target Job Title",
  "first_name": "First Name",
  "last_name": "Last Name",
  "email": "Email address",
  "phone": "Phone number",
  "location": "City, State",
  "linkedin": "LinkedIn or Portfolio URL",
  "summary": "Compelling 2-3 sentence ATS professional summary.",
  "education": [
    {
      "school": "Institution Name",
      "degree": "Degree & Major",
      "year": "Graduation Year / Dates",
      "dates": "Dates",
      "gpa": "GPA or Honors",
      "coursework": "Relevant Coursework"
    }
  ],
  "experience": [
    {
      "title": "Job Title",
      "company": "Company Name",
      "dates": "Dates / Range",
      "location": "City, State",
      "bullets": [
        "Strong action verb bullet with scope, actions, and quantifiable impact",
        "Second high-impact bullet incorporating target role frameworks and tools",
        "Third bullet demonstrating risk mitigation, collaboration, or operational excellence",
        "Fourth bullet highlighting adherence to protocols, leadership, or customer satisfaction"
      ]
    }
  ],
  "projects": [
    {
      "name": "Project / Capstone Name",
      "tech": "Technologies / Tools used",
      "bullets": [
        "Action bullet describing implementation and outcome"
      ]
    }
  ],
  "skills": {
    "Technical Skills": ["Skill 1", "Skill 2", "Skill 3"],
    "Core Competencies": ["Competency 1", "Competency 2", "Competency 3"]
  }
}
"""

COACH_SYSTEM_PROMPT = """
You are an expert ATS resume writer, career coach, and AI assistant embedded in a live resume editor.
Your PRIMARY job is to execute whatever the user asks you to do to their resume — immediately and accurately.

=== CORE BEHAVIOR ===

FOR GREETINGS ONLY (hi, hello, hey — with NO action request):
→ Respond with a warm 1-2 sentence greeting. Do NOT generate JSON.

FOR ANY OTHER REQUEST — including edits, questions, improvements, or information requests:
→ ALWAYS execute the request and return the updated resume as a ```json_resume``` block.
→ There are NO exceptions to this rule.

=== WHAT THE USER CAN ASK (handle ALL of these) ===
- Change a specific field: "change my GPA to 4.0", "update my email", "set my location to Denver, CO"
- Add content: "add Python to my skills", "add a new job experience", "add Docker and Kubernetes"
- Remove content: "remove my GPA", "delete the project section", "remove Python from skills"
- Rewrite sections: "rewrite my summary", "make my bullets stronger", "improve my professional summary"
- Build from scratch: "write my resume", "generate a resume for a Software Engineer"
- Ask for advice: "what skills should I add?", "how can I improve my resume?" — Answer AND update the resume with your suggestions
- ANY other instruction relating to the resume content

=== RESPONSE FORMAT (for all edit/action requests) ===
1. Write a SHORT (1-3 sentence) friendly confirmation of what you changed.
2. Then output the COMPLETE updated resume as:

```json_resume
{
  "title": "Professional Title",
  "job_target": "Target Job Title",
  "first_name": "First Name",
  "last_name": "Last Name",
  "email": "email@example.com",
  "phone": "Phone number",
  "location": "City, State",
  "linkedin": "LinkedIn or Portfolio URL",
  "summary": "2-3 sentence ATS professional summary.",
  "skills": {
    "Technical Skills": ["Skill 1", "Skill 2"],
    "Core Competencies": ["Competency 1", "Competency 2"]
  },
  "education": [
    {
      "school": "School Name",
      "degree": "Degree & Major",
      "year": "2025",
      "gpa": "4.0",
      "coursework": "Relevant Coursework"
    }
  ],
  "experience": [
    {
      "title": "Job Title",
      "company": "Company Name",
      "dates": "2019 – Present",
      "location": "City, ST",
      "bullets": [
        "Strong ATS bullet with action verb, scope, and quantified impact.",
        "Second bullet demonstrating key skills and outcomes.",
        "Third bullet highlighting collaboration or risk mitigation."
      ]
    }
  ],
  "projects": []
}
```

=== PARTIAL EDIT RULES (CRITICAL) ===
- The user's CURRENT resume data is always provided in the context. Use it as the BASE.
- When the user asks to change ONE thing (e.g. GPA, summary, one skill), only change THAT thing and keep EVERYTHING else exactly the same.
- NEVER drop sections, experience entries, or skills that weren't mentioned.
- NEVER return a partial or empty JSON — always return the FULL complete resume object.
- GPA values should be returned as plain numbers (e.g. "4.0", not "GPA: 4.0").
- For skill additions, ADD to the existing skills list — do not replace the entire list.
- For bullet rewrites for a specific role, only rewrite that company's bullets.

=== QUALITY RULES ===
- All bullet points must start with strong action verbs (Spearheaded, Coordinated, Implemented, etc.)
- Summaries must be ATS-optimized, third-person, 2-3 sentences
- Never use "I", "my", "we" in resume content
- Quantify impact wherever realistic
"""



def _get_grok_key() -> str:
    key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROK_API_KEY") or ""
    return key.strip()


def _get_gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or ""
    return key.strip()


def get_ai_status() -> dict:
    grok_key = _get_grok_key()
    gemini_key = _get_gemini_key()
    is_groq = grok_key.startswith("gsk_")

    providers = []
    if grok_key:
        providers.append("Groq" if is_groq else "xAI Grok")
    if gemini_key:
        providers.append("Gemini")

    return {
        "groq_available": bool(grok_key),
        "groq_label": "Groq (Lightning Fast)" if is_groq else "xAI Grok",
        "gemini_available": bool(gemini_key),
        "gemini_label": "Google Gemini (3.6 Flash)",
        "active_providers": providers,
    }


def _build_user_prompt(form_data: dict) -> str:
    cleaned = dict(form_data)

    role = cleaned.get("job_target") or cleaned.get("title") or "Professional"
    job_description = (cleaned.get("job_description") or "").strip()

    prompt = (
        f"Generate a COMPLETE, comprehensive, top-tier ATS resume matching the standard Ivy/Resume-Worded executive layout for target role: '{role}'.\n\n"
        f"CRITICAL INSTRUCTION FOR WORK EXPERIENCE BULLETS:\n"
        f"- For EVERY job experience listed below, you MUST generate 3 to 4 rich, detailed, quantifiable bullet points starting with strong action verbs (e.g., Spearheaded, Orchestrated, Mitigated, Streamlined, Identified, Implemented, Monitored, Coordinated).\n"
        f"- Expand the user's role description and responsibilities into professional resume bullets tailored to the target role and job description.\n"
        f"- Never return an empty bullets [] list for any experience.\n\n"
    )

    if job_description:
        prompt += (
            f"=== TARGET JOB DESCRIPTION ===\n"
            f"{job_description}\n"
            f"=== END JOB DESCRIPTION ===\n\n"
            f"Requirements based on the Job Description:\n"
            f"1. Directly incorporate relevant keywords, competencies, and terminology from the Job Description into the summary, bullet points, and skills.\n"
            f"2. Tailor each experience bullet so it demonstrates transferable competencies and qualifications sought in the Job Description.\n\n"
        )

    prompt += "=== CANDIDATE INPUT ===\n"
    prompt += f"Name: {cleaned.get('first_name', '')} {cleaned.get('last_name', '')}\n"
    prompt += f"Target Role: {role}\n"
    prompt += f"Contact: Email: {cleaned.get('email', '')}, Phone: {cleaned.get('phone', '')}, Location: {cleaned.get('location', '')}, LinkedIn: {cleaned.get('linkedin', '')}\n"

    if cleaned.get("summary"):
        prompt += f"Candidate Summary / Notes: {cleaned.get('summary')}\n"

    if cleaned.get("skills_technical") or cleaned.get("skills_soft") or cleaned.get("skills"):
        prompt += f"Candidate Skills: Technical: {cleaned.get('skills_technical', '')} | Soft / Competencies: {cleaned.get('skills_soft', '')} | Other: {cleaned.get('skills', '')}\n"

    educations = cleaned.get("education") or []
    if educations:
        prompt += "\nEducation (Polish and format):\n"
        for edu in educations:
            prompt += f"- School: {edu.get('school', '')}, Degree: {edu.get('degree', '')}, Dates/Year: {edu.get('year') or edu.get('dates', '')}, GPA/Honors: {edu.get('gpa', '')}\n"

    experiences = cleaned.get("experience") or []
    if not experiences and cleaned.get("experience_descriptions"):
        experiences = [{"description": d} for d in cleaned.get("experience_descriptions")]

    if experiences:
        prompt += "\nWork Experience (MANDATORY: GENERATE 3-4 STRONG ATS BULLETS FOR EACH):\n"
        for i, exp in enumerate(experiences, 1):
            prompt += f"Experience {i}:\n"
            prompt += f"  Job Title: {exp.get('title', '')}\n"
            prompt += f"  Company: {exp.get('company', '')}\n"
            prompt += f"  Dates: {exp.get('dates', '')}\n"
            if exp.get('location'): prompt += f"  Location: {exp.get('location', '')}\n"
            if exp.get('description'): prompt += f"  Role Description / Responsibilities: {exp.get('description', '')}\n"
            if exp.get('bullets'): prompt += f"  Existing Bullets: {exp.get('bullets', '')}\n"

    projects = cleaned.get("projects") or []
    if projects:
        prompt += "\nProjects:\n"
        for p in projects:
            prompt += f"- Name: {p.get('name', '')}, Tech: {p.get('tech', '')}, Bullets: {p.get('bullets', '')}\n"

    prompt += "\nOutput ONLY valid JSON matching the schema."
    return prompt


def _normalize_and_merge_resume(content: dict, form_data: dict) -> dict:
    """Merge user-provided values into AI output, ensuring full experience and education are preserved."""
    if not isinstance(content, dict):
        content = {}

    def _val(k):
        v = form_data.get(k)
        if isinstance(v, list) and v:
            return str(v[0]).strip()
        return str(v).strip() if v else ""

    fn = _val("first_name")
    ln = _val("last_name")
    em = _val("email")
    ph = _val("phone")
    loc = _val("location")
    lk = _val("linkedin")
    jt = _val("job_target")
    ti = _val("title")

    if fn: content["first_name"] = fn
    if ln: content["last_name"] = ln
    if em: content["email"] = em
    if ph: content["phone"] = ph
    if loc: content["location"] = loc
    if lk: content["linkedin"] = lk
    if jt: content["job_target"] = jt
    if ti: content["title"] = ti

    name_str = f"{content.get('first_name', '')} {content.get('last_name', '')}".strip()
    content["contact"] = {
        "name": name_str or "",
        "email": content.get("email") or "",
        "phone": content.get("phone") or "",
        "location": content.get("location") or "",
        "linkedin": content.get("linkedin") or "",
    }

    # Ensure correct types
    if not isinstance(content.get("education"), list):
        content["education"] = []
    if not isinstance(content.get("experience"), list):
        content["experience"] = []
    if not isinstance(content.get("projects"), list):
        content["projects"] = []
    if not isinstance(content.get("skills"), dict):
        content["skills"] = {}

    # Education merge
    user_edus = form_data.get("education") or []
    if user_edus and not content["education"]:
        content["education"] = user_edus
    elif user_edus and content["education"]:
        for i, u_edu in enumerate(user_edus):
            if i < len(content["education"]):
                ai_edu = content["education"][i]
                if u_edu.get("school") and not ai_edu.get("school"):
                    ai_edu["school"] = u_edu["school"]
                if u_edu.get("degree") and not ai_edu.get("degree"):
                    ai_edu["degree"] = u_edu["degree"]
                if u_edu.get("year") and not ai_edu.get("year"):
                    ai_edu["year"] = u_edu["year"]
                if u_edu.get("gpa") and not ai_edu.get("gpa"):
                    ai_edu["gpa"] = u_edu["gpa"]

    # Experience merge
    user_exps = form_data.get("experience") or []
    if user_exps and not content["experience"]:
        content["experience"] = user_exps
    elif user_exps and content["experience"]:
        for i, u_exp in enumerate(user_exps):
            if i < len(content["experience"]):
                ai_exp = content["experience"][i]
                if u_exp.get("title") and not ai_exp.get("title"):
                    ai_exp["title"] = u_exp["title"]
                if u_exp.get("company") and not ai_exp.get("company"):
                    ai_exp["company"] = u_exp["company"]
                if u_exp.get("dates") and not ai_exp.get("dates"):
                    ai_exp["dates"] = u_exp["dates"]
                if u_exp.get("location") and not ai_exp.get("location"):
                    ai_exp["location"] = u_exp["location"]

    return content


def _call_grok_json(prompt: str) -> tuple[dict, str]:
    key = _get_grok_key()
    if not key:
        raise ValueError("No Groq / Grok API key configured in .env")

    is_groq = key.startswith("gsk_")
    base_url = "https://api.groq.com/openai/v1" if is_groq else "https://api.x.ai/v1"
    models_to_try = (
        ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
        if is_groq
        else ["grok-beta", "grok-2"]
    )

    client = OpenAI(
        api_key=key,
        base_url=base_url,
        http_client=httpx.Client(),
    )

    last_err = None
    for model in models_to_try:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.rsplit("```", 1)[0]
            return json.loads(raw), f"{'groq' if is_groq else 'grok'}:{model}"
        except Exception as e:
            last_err = e
            logger.warning(f"Model {model} failed: {e}")
            continue

    raise RuntimeError(f"All Groq/Grok models failed: {last_err}")


def _call_gemini_json(prompt: str) -> tuple[dict, str]:
    key = _get_gemini_key()
    if not key:
        raise ValueError("No Gemini API key configured in .env")

    genai.configure(api_key=key, transport="rest")
    full_prompt = SYSTEM_PROMPT + "\n\n" + prompt

    models_to_try = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest"]
    last_err = None
    for model_name in models_to_try:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(full_prompt)
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.rsplit("```", 1)[0]
            return json.loads(raw), f"gemini:{model_name}"
        except Exception as e:
            last_err = e
            logger.warning(f"Gemini model {model_name} failed: {e}")
            continue

    raise RuntimeError(f"All Gemini models failed: {last_err}")


def generate_resume_content(form_data: dict, preferred_provider: str = "auto") -> tuple[dict, str]:
    """
    Generate complete resume JSON using Groq/Grok or Gemini based on availability and preference.
    Returns (content_json, model_used).
    """
    prompt = _build_user_prompt(form_data)

    if preferred_provider in ("auto", "groq", "grok") and _get_grok_key():
        try:
            content, model_used = _call_grok_json(prompt)
            content = _normalize_and_merge_resume(content, form_data)
            logger.info(f"Resume generated using {model_used}.")
            return content, model_used
        except Exception as e:
            logger.warning(f"Groq/Grok generation failed ({e}), attempting fallback.")
            if preferred_provider not in ("auto",):
                raise

    if _get_gemini_key():
        try:
            content, model_used = _call_gemini_json(prompt)
            content = _normalize_and_merge_resume(content, form_data)
            logger.info(f"Resume generated using {model_used}.")
            return content, model_used
        except Exception as e:
            logger.error(f"Gemini generation failed: {e}")
            raise RuntimeError(f"AI generation failed: {e}")

    raise RuntimeError("No AI providers succeeded or no valid API keys configured in .env")


def generate_single_experience_bullets(
    title: str,
    company: str,
    description: str,
    job_target: str = "",
    job_description: str = "",
    preferred_provider: str = "auto",
) -> list[str]:
    """Generate 3-4 professional ATS bullet points from a single role description."""
    prompt = (
        f"You are an ATS resume optimization expert.\n"
        f"Generate exactly 3 to 4 high-impact, quantifiable ATS resume bullet points for this work experience:\n"
        f"Job Title: {title or 'Professional'}\n"
        f"Company: {company or 'Company'}\n"
        f"Role Description / Duties: {description or 'Managed operations and project support.'}\n"
        f"Target Role: {job_target or 'Professional'}\n"
    )
    if job_description:
        prompt += f"\nTarget Job Description Context:\n{job_description}\n"

    prompt += (
        "\nRules:\n"
        "1. Start every bullet with a strong past-tense action verb (e.g. Spearheaded, Orchestrated, Mitigated, Streamlined, Identified, Coordinated, Implemented, Monitored).\n"
        "2. Expand on the role description with professional scope, risk mitigation, and operational metrics.\n"
        "3. Align naturally with the target role and job description keywords.\n"
        "4. No first-person pronouns (no I, my, we).\n"
        "5. Return ONLY a valid JSON array of 3 to 4 bullet strings, like: [\"Bullet 1...\", \"Bullet 2...\", \"Bullet 3...\"]\n"
    )

    def _parse_bullets(raw: str) -> list[str]:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]
        data = json.loads(raw.strip())
        if isinstance(data, list):
            return [str(b).strip() for b in data if str(b).strip()]
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return [str(b).strip() for b in v if str(b).strip()]
        return []

    # Try Groq first if preferred or auto
    if preferred_provider in ("auto", "groq", "grok") and _get_grok_key():
        key = _get_grok_key()
        is_groq = key.startswith("gsk_")
        base_url = "https://api.groq.com/openai/v1" if is_groq else "https://api.x.ai/v1"
        models_to_try = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"] if is_groq else ["grok-beta"]
        client = OpenAI(api_key=key, base_url=base_url, http_client=httpx.Client())
        for model in models_to_try:
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                raw = resp.choices[0].message.content.strip()
                bullets = _parse_bullets(raw)
                if bullets:
                    return bullets
            except Exception as e:
                logger.warning(f"Groq bullet gen error ({model}): {e}")
                continue

    # Try Gemini
    if _get_gemini_key():
        key = _get_gemini_key()
        genai.configure(api_key=key, transport="rest")
        for mname in ["gemini-3.6-flash", "gemini-3.5-flash"]:
            try:
                model = genai.GenerativeModel(mname)
                resp = model.generate_content(prompt)
                bullets = _parse_bullets(resp.text)
                if bullets:
                    return bullets
            except Exception as e:
                logger.warning(f"Gemini bullet gen error ({mname}): {e}")
                continue

    # Fallback to smart sentence split of description if LLM unavailable
    if description:
        sentences = [s.strip() for s in description.replace(";", ".").split(".") if len(s.strip()) > 10]
        if sentences:
            return [s if s.endswith(".") else s + "." for s in sentences[:4]]

    return [
        f"Spearheaded operational processes and project deliverables as {title or 'team lead'}, ensuring full adherence to organizational protocols.",
        f"Coordinated cross-functional communications with leadership and clients, improving response efficiency and resolution times.",
        "Identified operational risks and implemented preventative mitigation strategies to safeguard facility and personnel safety."
    ]


def _extract_resume_json(raw_text: str) -> tuple[str, dict | None]:
    """
    Extract structured resume JSON from AI reply and return (clean_text, resume_data).
    ALWAYS strips the json block from clean_text, even if JSON parsing fails,
    so raw JSON never leaks into the chat display.
    """
    if not raw_text:
        return "", None

    resume_data = None
    clean_text = raw_text

    import re
    # Match ```json_resume ... ``` or ```json ... ``` blocks (case insensitive)
    pattern = re.compile(r"```(?:json_resume|json)\s*([\s\S]*?)\s*```", re.IGNORECASE)
    match = pattern.search(raw_text)

    if match:
        raw_json_str = match.group(1).strip()

        # Always strip the block from display text (even if parse fails)
        before = raw_text[:match.start()].strip()
        after = raw_text[match.end():].strip()
        clean_text = (before + ("\n\n" if before and after else "") + after).strip()

        # Try to parse the JSON
        try:
            candidate = json.loads(raw_json_str)
            if isinstance(candidate, dict) and any(
                k in candidate for k in ("first_name", "skills", "summary", "experience", "education", "job_target", "projects", "title")
            ):
                resume_data = candidate
        except json.JSONDecodeError:
            # Try extracting the largest JSON object from the block as a fallback
            try:
                obj_match = re.search(r"\{[\s\S]*\}", raw_json_str)
                if obj_match:
                    candidate = json.loads(obj_match.group(0))
                    if isinstance(candidate, dict):
                        resume_data = candidate
            except Exception:
                pass
            if not resume_data:
                logger.warning(f"Could not parse resume JSON block. Preview not updated.")

    # Fallback: also strip any json_resume block that wasn't caught (e.g. no closing ```)
    clean_text = re.sub(r"```json_resume[\s\S]*?(?:```|$)", "", clean_text, flags=re.IGNORECASE).strip()
    clean_text = re.sub(r"```json[\s\S]{20,}?(?:```|$)", "", clean_text, flags=re.IGNORECASE).strip()

    return clean_text, resume_data


def chat_with_ai(
    message: str,
    context: dict = None,
    provider: str = "auto",
    history: list = None,
) -> tuple[str, str, str, dict | None]:
    """
    Unified conversational chat handler for AI resume coach.
    Returns (reply_text, model_name, provider_name, resume_data).
    """
    import random


    # ── Action keyword guard: if any resume action word present, ALWAYS call AI ──
    import re as _re
    _ACTION_RE = _re.compile(
        r'\b(change|update|edit|add|remove|delete|fix|improve|make|rewrite|write|generate|create|build|'
        r'adjust|set|replace|rename|modify|boost|strengthen|tailor|optimize|enhance|increase|decrease|'
        r'raise|lower|upgrade|switch|move|insert|include|exclude|put|give|get|use|try|do|help|please|'
        r'want|need|should|would|could|gpa|summary|bullet|skill|experience|education|job|title|name|'
        r'email|phone|location|linkedin|company|degree|school|project|year|date|role|position|work|'
        r'section|resume|cv|objective|certif|award|volunteer|language|reference)\b',
        _re.IGNORECASE
    )
    _has_action = bool(_ACTION_RE.search(message))
    _msg_lower = message.lower().strip().rstrip("!?.,")

    # ── Greeting short-circuit — ONLY if no action keyword present ────────────
    _GREETINGS = {
        "hi", "hey", "hello", "howdy", "sup", "yo", "hiya", "greetings",
        "hola", "bonjour", "salut", "ciao", "what's up", "whats up",
        "how are you", "how's it going", "hows it going",
        "what can you do", "who are you", "what do you do",
        "good morning", "good afternoon", "good evening", "good day",
    }
    _THANKS = {"thanks", "thank you", "thx", "ty"}

    if not _has_action and (_msg_lower in _GREETINGS or any(_msg_lower.startswith(g) for g in _GREETINGS)):
        _replies = [
            "Hey there! 👋 I'm your AI resume coach. Tell me what to change — I'll update the resume live!",
            "Hello! Ready to help. Say things like \"Change my GPA\", \"Add Python to skills\", or \"Rewrite my bullets\" — I'll do it instantly.",
            "Hi! I can edit any part of your resume. Just tell me what you want changed!",
        ]
        return random.choice(_replies), "local", "AI Coach", None

    if not _has_action and _msg_lower in _THANKS:
        return "You're welcome! 😊 Let me know if there's anything else you'd like to change in your resume.", "local", "AI Coach", None

    grok_key = _get_grok_key()
    gemini_key = _get_gemini_key()

    # Build a rich context string so the AI can do targeted partial edits
    ctx = context or {}
    context_parts = []
    if ctx:
        context_parts.append("=== CURRENT RESUME DATA (use this as the base for any edits) ===")
        context_parts.append(json.dumps(ctx, indent=2))
        context_parts.append("=== END RESUME DATA ===")
        context_parts.append("")
        context_parts.append(
            "IMPORTANT: The above is the candidate's CURRENT resume. When the user asks to edit, "
            "update, or change anything, start from this data and return the COMPLETE updated resume "
            "as a ```json_resume``` block with all sections preserved (only modify what was requested)."
        )

    context_parts.append(f"\nUser Request:\n{message}")
    user_prompt = "\n".join(context_parts)

    # Try Groq / Grok if requested or auto
    if provider in ("auto", "groq", "grok") and grok_key:
        try:
            is_groq = grok_key.startswith("gsk_")
            base_url = "https://api.groq.com/openai/v1" if is_groq else "https://api.x.ai/v1"
            model_name = "openai/gpt-oss-120b" if is_groq else "grok-beta"
            provider_title = "Groq" if is_groq else "xAI Grok"

            client = OpenAI(
                api_key=grok_key,
                base_url=base_url,
                http_client=httpx.Client(),
            )

            messages = [{"role": "system", "content": COACH_SYSTEM_PROMPT}]
            if history:
                for h in history[-4:]:
                    messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
            messages.append({"role": "user", "content": user_prompt})

            resp = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0.4,
                max_tokens=1800,
            )
            raw_reply = resp.choices[0].message.content.strip()
            clean_reply, resume_data = _extract_resume_json(raw_reply)
            return clean_reply, model_name, provider_title, resume_data
        except Exception as e:
            logger.warning(f"Groq/Grok chat failed: {e}")
            if provider != "auto":
                raise

    # Try Gemini if requested or auto
    if gemini_key:
        try:
            genai.configure(api_key=gemini_key)
            models_to_try = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-2.5-flash-lite"]
            for m in models_to_try:
                try:
                    model = genai.GenerativeModel(m)
                    full = COACH_SYSTEM_PROMPT + "\n\n"
                    if history:
                        for h in history[-4:]:
                            role_label = "User" if h.get("role") == "user" else "Assistant"
                            full += f"{role_label}: {h.get('content', '')}\n\n"
                    full += user_prompt
                    resp = model.generate_content(full)
                    raw_reply = resp.text.strip()
                    clean_reply, resume_data = _extract_resume_json(raw_reply)
                    return clean_reply, m, "Google Gemini", resume_data
                except Exception as model_err:
                    logger.warning(f"Gemini {m} chat error: {model_err}")
                    continue
        except Exception as e:
            logger.error(f"Gemini chat failed: {e}")
            if provider != "auto":
                raise

    raise RuntimeError("Unable to communicate with AI providers. Please check your .env keys.")
