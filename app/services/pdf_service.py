"""
PDF service: fills the LaTeX template and compiles to PDF.
Supports LATEX_COMPILE_MODE=local (pdflatex) or hosted (API).
"""
import os
import re
import subprocess
import tempfile
import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / "latex_templates"
COMPILE_MODE = os.environ.get("LATEX_COMPILE_MODE", "local")


# ── Unicode → LaTeX safe replacements (prevents black boxes in PDF) ────────
# These characters are not supported by pdflatex's default T1/OT1 encoding
# and render as solid black boxes without explicit replacement.
_UNICODE_REPLACEMENTS = [
    # Dashes
    ("\u2014", "---"),          # em-dash  —
    ("\u2013", "--"),           # en-dash  –
    ("\u2012", "--"),           # figure dash
    ("\u2015", "---"),          # horizontal bar
    # Quotes
    ("\u201C", "``"),           # left double quote  "
    ("\u201D", "''"),           # right double quote  "
    ("\u2018", "`"),            # left single quote  '
    ("\u2019", "'"),            # right single quote  '
    ("\u201A", ","),            # single low-9 quote
    ("\u201E", ",,"),           # double low-9 quote
    ("\u2039", "<"),            # single left angle quote
    ("\u203A", ">"),            # single right angle quote
    ("\u00AB", "<<"),           # left double angle quote  «
    ("\u00BB", ">>"),           # right double angle quote  »
    # Ellipsis & spacing
    ("\u2026", "\\ldots{}"),    # ellipsis  …
    ("\u00A0", " "),            # non-breaking space
    ("\u202F", " "),            # narrow no-break space
    ("\u2009", " "),            # thin space
    # Bullets & symbols
    ("\u2022", "\\textbullet{}"),  # bullet  •
    ("\u2023", "\\textbullet{}"),  # triangular bullet
    ("\u25E6", "o"),            # white bullet
    ("\u2043", "-"),            # hyphen bullet
    ("\u00B7", "\\textperiodcentered{}"),  # middle dot  ·
    # Fractions & math
    ("\u00BD", "1/2"),          # ½
    ("\u00BC", "1/4"),          # ¼
    ("\u00BE", "3/4"),          # ¾
    ("\u00D7", "x"),            # multiplication sign  ×
    ("\u00F7", "/"),            # division sign  ÷
    ("\u2212", "-"),            # minus sign  −
    ("\u00B1", "+/-"),          # plus-minus  ±
    # Arrows
    ("\u2192", "\\textrightarrow{}"),  # →
    ("\u2190", "\\textleftarrow{}"),   # ←
    ("\u2194", "\\textleftrightarrow{}"),  # ↔
    # Accented / latin extended (common in names)
    ("\u00E9", "{\\'{e}}"),     # é
    ("\u00E8", "{\\`{e}}"),     # è
    ("\u00EA", "{\\^{e}}"),     # ê
    ("\u00EB", '{\\"e}'),       # ë
    ("\u00E0", "{\\`{a}}"),     # à
    ("\u00E2", "{\\^{a}}"),     # â
    ("\u00E4", '{\\"a}'),       # ä
    ("\u00FC", '{\\"u}'),       # ü
    ("\u00F6", '{\\"o}'),       # ö
    ("\u00EF", '{\\"i}'),       # ï
    ("\u00F1", "{\\~{n}}"),     # ñ
    ("\u00E7", "{\\c{c}}"),     # ç
    ("\u00C9", "{\\'{E}}"),     # É
    ("\u00C0", "{\\`{A}}"),     # À
    # Misc
    ("\u00AE", "\\textregistered{}"),   # ®
    ("\u00A9", "\\textcopyright{}"),    # ©
    ("\u2122", "\\texttrademark{}"),    # ™
    ("\u00B0", "\\textdegree{}"),       # °
    ("\u2030", "\\textperthousand{}"),  # ‰
]


def _sanitize_unicode(text: str) -> str:
    """Replace Unicode characters that pdflatex cannot render (black boxes) with LaTeX equivalents."""
    if not isinstance(text, str):
        return str(text)
    for char, replacement in _UNICODE_REPLACEMENTS:
        text = text.replace(char, replacement)
    # Strip any remaining non-ASCII characters that would cause unknown errors
    text = text.encode("ascii", errors="ignore").decode("ascii")
    return text


# ── LaTeX special-character escaping ────────────────────────
_LATEX_SPECIAL = re.compile(r'([&%$#_{}~^\\])')


def _escape_latex(text: str) -> str:
    """Replace Unicode chars then escape LaTeX special characters."""
    if not isinstance(text, str):
        text = str(text)
    # Step 1: Replace known Unicode → LaTeX safe equivalents
    text = _sanitize_unicode(text)
    # Step 2: Escape LaTeX special chars (skip backslashes we just inserted)
    result = []
    i = 0
    while i < len(text):
        # Preserve already-inserted LaTeX commands (\textXxx{}, ---. etc.)
        if text[i] == "\\":
            result.append(text[i])
            i += 1
            continue
        ch = text[i]
        if ch in "&%$#_{}~^":
            result.append("\\" + ch)
        else:
            result.append(ch)
        i += 1
    return "".join(result)


def _render_template(content_json: dict) -> str:
    """Render the Jinja2-based LaTeX template with escaped resume content."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=False,
        variable_start_string="(((",
        variable_end_string=")))",
        block_start_string="((%",
        block_end_string="%))",
    )
    env.filters["latex"] = _escape_latex
    template = env.get_template("resume.tex.jinja2")
    return template.render(data=content_json)


def _compile_reportlab(content: dict, output_name: str) -> str:
    """Generate professional ATS-friendly PDF matching LaTeX formatting using ReportLab."""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors

    def _rl_safe(text: str) -> str:
        """Sanitize text for ReportLab: replace Unicode smart chars with ASCII equivalents."""
        if not isinstance(text, str):
            return str(text) if text else ""
        # Replace Unicode smart punctuation with plain ASCII
        replacements = [
            ("\u2014", "-"),   # em-dash
            ("\u2013", "-"),   # en-dash
            ("\u201C", '"'),   # left double quote
            ("\u201D", '"'),   # right double quote
            ("\u2018", "'"),   # left single quote
            ("\u2019", "'"),   # right single quote
            ("\u2026", "..."), # ellipsis
            ("\u00A0", " "),   # non-breaking space
            ("\u00B7", "-"),   # middle dot
            ("\u2022", "*"),   # bullet
            ("\u2212", "-"),   # minus sign
            ("\u00D7", "x"),   # multiplication sign
            ("\u00B1", "+/-"), # plus-minus
        ]
        for char, repl in replacements:
            text = text.replace(char, repl)
        # Encode to latin-1 (ReportLab's native encoding), replacing unknown chars
        return text.encode("latin-1", errors="replace").decode("latin-1")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Name", parent=styles["Heading1"], alignment=1, fontSize=16, leading=19,
        fontName="Helvetica-Bold", spaceAfter=2
    )
    contact_style = ParagraphStyle(
        "Contact", parent=styles["Normal"], alignment=1, fontSize=9, leading=12,
        fontName="Helvetica", textColor=colors.HexColor("#333333")
    )
    sec_style = ParagraphStyle(
        "Sec", parent=styles["Heading2"], fontSize=10.5, leading=13,
        fontName="Helvetica-Bold", spaceBefore=7, spaceAfter=2, textTransform="uppercase"
    )
    bold_style = ParagraphStyle(
        "EntryTitle", parent=styles["Normal"], fontSize=9.5, leading=13,
        fontName="Helvetica-Bold"
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=9, leading=13, fontName="Helvetica"
    )
    bullet_style = ParagraphStyle(
        "Bullet", parent=styles["Normal"], fontSize=8.5, leading=12,
        fontName="Helvetica", leftIndent=12, spaceAfter=1.5
    )

    story = []

    # Contact header
    c = content.get("contact", {})
    name = (c.get("name") or (content.get("first_name", "") + " " + content.get("last_name", "")).strip()) or "Your Name"
    story.append(Paragraph(f"<b>{_rl_safe(name).upper()}</b>", title_style))

    target_role = content.get("job_target") or content.get("title") or ""
    if target_role:
        role_style = ParagraphStyle(
            "TargetRole", parent=styles["Normal"], alignment=1, fontSize=11, leading=14,
            fontName="Helvetica-Bold", textColor=colors.HexColor("#111111"), spaceAfter=3
        )
        story.append(Paragraph(f"<b>{_rl_safe(target_role)}</b>", role_style))

    contact_parts = [
        _rl_safe(c.get("location") or content.get("location") or ""),
        _rl_safe(c.get("email") or content.get("email") or ""),
        _rl_safe(c.get("phone") or content.get("phone") or ""),
        _rl_safe(c.get("linkedin") or content.get("linkedin") or ""),
    ]
    contact_str = " &nbsp;&bull;&nbsp; ".join([p for p in contact_parts if p])
    if contact_str:
        story.append(Paragraph(contact_str, contact_style))
    story.append(Spacer(1, 3))

    # Summary
    summary = content.get("summary")
    if summary:
        story.append(Paragraph("<b>PROFESSIONAL SUMMARY</b>", sec_style))
        story.append(HRFlowable(width="100%", thickness=1.0, color=colors.HexColor("#111111"), spaceBefore=1, spaceAfter=4))
        story.append(Paragraph(_rl_safe(str(summary)), body_style))

    # Experience (Image 5 standard: Professional Experience first)
    experience = content.get("experience", [])
    if experience:
        story.append(Paragraph("<b>PROFESSIONAL EXPERIENCE</b>", sec_style))
        story.append(HRFlowable(width="100%", thickness=1.0, color=colors.HexColor("#111111"), spaceBefore=1, spaceAfter=4))
        for exp in experience:
            t = _rl_safe(exp.get("title", ""))
            comp = _rl_safe(exp.get("company", ""))
            d = _rl_safe(exp.get("dates", ""))
            loc = _rl_safe(exp.get("location", ""))
            loc_str = f" &middot; {loc}" if loc else ""
            story.append(Paragraph(f"<b>{comp.upper()}</b> <font color=\"#444444\">{loc_str}</font>", bold_style))
            story.append(Paragraph(f"<b>{t}</b> &nbsp;|&nbsp; <font color=\"#444444\">{d}</font>", ParagraphStyle("ExpSub", parent=styles["Normal"], fontSize=9, leading=12, fontName="Helvetica", spaceAfter=2)))
            
            bullets = exp.get("bullets", [])
            if not bullets and exp.get("description"):
                desc = exp.get("description", "")
                bullets = [s.strip() for s in desc.replace(";", ".").split(".") if len(s.strip()) > 8]
            for b in bullets:
                story.append(Paragraph(f"&bull; {_rl_safe(b)}", bullet_style))
            story.append(Spacer(1, 3))

    # Education
    education = content.get("education", [])
    if education:
        story.append(Paragraph("<b>EDUCATION</b>", sec_style))
        story.append(HRFlowable(width="100%", thickness=1.0, color=colors.HexColor("#111111"), spaceBefore=1, spaceAfter=4))
        for edu in education:
            school = _rl_safe(edu.get("school", ""))
            dates = _rl_safe(edu.get("dates") or edu.get("year") or "")
            deg = _rl_safe(edu.get("degree", ""))
            gpa = edu.get("gpa")
            gpa_str = f" &mdash; GPA: {_rl_safe(str(gpa))}" if gpa else ""
            cw = edu.get("coursework")
            cw_str = f"<br/><font size=\"8\"><b>Relevant Coursework:</b> {_rl_safe(cw)}</font>" if cw else ""
            story.append(Paragraph(f"<b>{school.upper()}</b> <font color=\"#444444\">({dates})</font>", bold_style))
            story.append(Paragraph(f"<i>{deg}</i>{gpa_str}{cw_str}", body_style))
            story.append(Spacer(1, 2))

    # Projects
    projects = content.get("projects", [])
    if projects:
        story.append(Paragraph("<b>PROJECTS</b>", sec_style))
        story.append(HRFlowable(width="100%", thickness=1.0, color=colors.HexColor("#111111"), spaceBefore=1, spaceAfter=4))
        for proj in projects:
            pname = _rl_safe(proj.get("name", ""))
            tech = _rl_safe(proj.get("tech") or proj.get("dates") or "")
            story.append(Paragraph(f"<b>{pname}</b> &nbsp;|&nbsp; <i><font color=\"#444444\">{tech}</font></i>", bold_style))
            for b in proj.get("bullets", []):
                story.append(Paragraph(f"&bull; {_rl_safe(b)}", bullet_style))
            story.append(Spacer(1, 2))

    # Additional Information / Skills
    skills = content.get("skills")
    if skills:
        story.append(Paragraph("<b>ADDITIONAL INFORMATION</b>", sec_style))
        story.append(HRFlowable(width="100%", thickness=1.0, color=colors.HexColor("#111111"), spaceBefore=1, spaceAfter=4))
        if isinstance(skills, dict):
            for cat, items in skills.items():
                items_str = ", ".join(items) if isinstance(items, list) else str(items)
                story.append(Paragraph(f"&bull; <b>{cat}:</b> {items_str}", bullet_style))
        else:
            sk_str = ", ".join(skills) if isinstance(skills, list) else str(skills)
            story.append(Paragraph(f"&bull; <b>Skills:</b> {sk_str}", bullet_style))

    output_dir = Path(__file__).parent.parent / "static" / "pdfs"
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"{output_name}.pdf"

    doc = SimpleDocTemplate(
        str(dest), pagesize=letter,
        leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36
    )
    doc.build(story)
    return f"/static/pdfs/{output_name}.pdf"


def _build_html_context(content: dict) -> dict:
    """Build a clean context dict for the HTML PDF template from resume JSON."""
    c = content.get("contact", {})
    name = (
        c.get("name")
        or f"{content.get('first_name', '')} {content.get('last_name', '')}".strip()
        or "Your Name"
    )
    parts = [
        c.get("location") or content.get("location") or "",
        c.get("email") or content.get("email") or "",
        c.get("phone") or content.get("phone") or "",
        c.get("linkedin") or content.get("linkedin") or "",
    ]
    contact_line = "  \u2022  ".join([p for p in parts if p])
    return {
        "name": name,
        "target_role": content.get("job_target") or content.get("title") or "",
        "contact_line": contact_line,
        "summary": content.get("summary") or "",
        "experience": content.get("experience") or [],
        "education": content.get("education") or [],
        "projects": content.get("projects") or [],
        "skills": content.get("skills") or None,
    }


def _compile_weasyprint(content_json: dict, output_name: str) -> str:
    """
    Generate a PDF from the HTML preview template using WeasyPrint.
    Output exactly matches the browser preview styling.
    """
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML, CSS
    from weasyprint.text.fonts import FontConfiguration

    # Render the HTML template
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,   # auto-escape HTML entities
    )
    template = env.get_template("resume_pdf.html")
    ctx = _build_html_context(content_json)
    html_str = template.render(**ctx)

    output_dir = Path(__file__).parent.parent / "static" / "pdfs"
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"{output_name}.pdf"

    font_config = FontConfiguration()
    HTML(string=html_str, base_url=str(TEMPLATE_DIR)).write_pdf(
        str(dest),
        font_config=font_config,
    )
    logger.info(f"WeasyPrint PDF generated: {dest}")
    return f"/static/pdfs/{output_name}.pdf"


def _find_chrome() -> str | None:
    """Find installed Chrome / Chromium binary."""
    import shutil
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _compile_chrome_headless(content_json: dict, output_name: str) -> str:
    """Generate a pixel-perfect PDF using headless Chrome from resume_pdf.html."""
    chrome_bin = _find_chrome()
    if not chrome_bin:
        raise RuntimeError("Chrome/Chromium binary not found.")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    template = env.get_template("resume_pdf.html")
    ctx = _build_html_context(content_json)
    html_str = template.render(**ctx)

    output_dir = Path(__file__).parent.parent / "static" / "pdfs"
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"{output_name}.pdf"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_html = Path(tmpdir) / "resume.html"
        tmp_html.write_text(html_str, encoding="utf-8")
        cmd = [
            chrome_bin,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={str(dest)}",
            str(tmp_html),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            logger.info(f"Headless Chrome PDF generated: {dest}")
            return f"/static/pdfs/{output_name}.pdf"
        raise RuntimeError(f"Chrome PDF generation failed: {result.stderr}")


def _compile_local(latex_source: str, output_name: str, content_json: dict) -> str:
    """Compile PDF: try WeasyPrint → Headless Chrome → pdflatex → ReportLab."""
    # ── 1. WeasyPrint (pixel-perfect HTML → PDF, matches the browser preview) ──
    try:
        return _compile_weasyprint(content_json, output_name)
    except Exception as e:
        logger.warning(f"WeasyPrint failed ({e}), trying headless Chrome.")

    # ── 2. Headless Chrome (pixel-perfect HTML → PDF, native on macOS) ──
    try:
        return _compile_chrome_headless(content_json, output_name)
    except Exception as e:
        logger.warning(f"Headless Chrome failed ({e}), trying pdflatex.")

    # ── 3. pdflatex (if installed) ──
    import shutil
    if shutil.which("pdflatex"):
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tex_path = Path(tmpdir) / f"{output_name}.tex"
                pdf_path = Path(tmpdir) / f"{output_name}.pdf"
                tex_path.write_text(latex_source, encoding="utf-8")

                result = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, str(tex_path)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if result.returncode == 0 and pdf_path.exists():
                    output_dir = Path(__file__).parent.parent / "static" / "pdfs"
                    output_dir.mkdir(parents=True, exist_ok=True)
                    dest = output_dir / f"{output_name}.pdf"
                    dest.write_bytes(pdf_path.read_bytes())
                    return f"/static/pdfs/{output_name}.pdf"
                else:
                    logger.warning(f"pdflatex failed ({result.stderr}), using ReportLab.")
        except Exception as e:
            logger.warning(f"pdflatex run error ({e}), using ReportLab.")

    # ── 4. ReportLab (emergency fallback) ──
    return _compile_reportlab(content_json, output_name)


def compile_pdf(content_json: dict, resume_id: int) -> str:
    """
    Render template + compile PDF.
    Returns a URL path to the generated PDF.
    """
    latex_source = _render_template(content_json)
    output_name = f"resume_{resume_id}"

    if COMPILE_MODE == "local":
        return _compile_local(latex_source, output_name, content_json)
    else:
        raise NotImplementedError("Hosted LaTeX compile API not yet implemented.")
