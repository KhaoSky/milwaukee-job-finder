import os
import io
import re
import sys
import json
import requests
import threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv("API_key.env", override=True)
except ImportError:
    pass

# ── Directory layout ──────────────────────────────────────────────────────────
# MKE_BASE_DIR: where templates live (set by main.py to sys._MEIPASS when frozen)
# MKE_DATA_DIR: persistent data files (set by main.py to %APPDATA%\MKEJobFinder)
_BASE_DIR = os.environ.get('MKE_BASE_DIR') or os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.environ.get('MKE_DATA_DIR') or _BASE_DIR

app = Flask(__name__, template_folder=os.path.join(_BASE_DIR, 'templates'))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# ============================================================
# AI PROVIDER
# ============================================================
def call_ai(prompt, provider="claude", api_key=None):
    """Call Claude AI and return the response text."""
    import anthropic
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or _load_saved_keys().get("anthropic")
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text

# ============================================================
# REAL JOB FETCHING — JSearch API (RapidAPI)
# ============================================================
def fetch_real_jobs(query, location=None, is_remote=False, num_pages=2, date_posted="month", api_key=None):
    """Fetch real job listings from JSearch API via RapidAPI."""
    key = api_key or os.environ.get("JSEARCH_API_KEY") or _load_saved_keys().get("jsearch")
    if not key:
        raise ValueError(
            "JSEARCH_API_KEY not configured. "
            "Enter it in the ⚙️ Settings panel or add it to your environment variables."
        )

    search_query = query
    if location and not is_remote:
        search_query += f" in {location}"

    params = {
        "query": search_query,
        "page": "1",
        "num_pages": str(num_pages),
        "date_posted": date_posted,
    }
    if is_remote:
        params["remote_jobs_only"] = "true"

    resp = requests.get(
        "https://jsearch.p.rapidapi.com/search",
        headers={
            "X-RapidAPI-Key": key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
        },
        params=params,
        timeout=20
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def format_salary(job):
    min_s = job.get("job_min_salary")
    max_s = job.get("job_max_salary")
    if not (min_s and max_s):
        return ""
    period = (job.get("job_salary_period") or "").lower()
    if period in ("hour", "hourly"):
        return f"${int(min_s)}-${int(max_s)}/hr"
    elif period in ("month", "monthly"):
        return f"${int(min_s):,}-${int(max_s):,}/mo"
    else:
        return f"${int(min_s):,}-${int(max_s):,}/yr"


def rank_jobs_with_ai(raw_jobs, resume_text, career_field, keywords, provider, anthropic_key=None):
    """Use AI to score and rank real job listings against the candidate's profile."""
    if not raw_jobs:
        return []

    # Compact summaries to minimise tokens
    summaries = []
    for i, job in enumerate(raw_jobs):
        desc = (job.get("job_description") or "")[:400]
        summaries.append({
            "idx": i,
            "title": job.get("job_title", ""),
            "company": job.get("employer_name", ""),
            "location": f"{job.get('job_city','')}, {job.get('job_state','')}",
            "is_remote": job.get("job_is_remote", False),
            "type": job.get("job_employment_type", ""),
            "description": desc,
        })

    resume_section = (
        f"CANDIDATE RESUME:\n---\n{strip_pii(resume_text)[:3000]}\n---\n" if resume_text else ""
    )
    career_section = f"Career Field: {career_field}" if career_field else ""
    keywords_section = f"Keywords: {keywords}" if keywords else ""

    prompt = f"""You are a job matching expert. Score each job listing for how well it matches this candidate.

{resume_section}{career_section}
{keywords_section}

JOB LISTINGS:
{json.dumps(summaries, indent=2)}

For EACH job return a JSON object with:
- "idx": job index (integer)
- "match_score": 1-100 integer (fit to candidate's skills, experience, and preferences)
- "match_reasons": exactly 2 short strings explaining the match

Return ONLY a valid JSON array of all {len(raw_jobs)} jobs. No other text."""

    response_text = call_ai(prompt, provider, api_key=anthropic_key)

    try:
        scores = json.loads(response_text)
    except Exception:
        import re
        m = re.search(r'\[\s*\{.*\}\s*\]', response_text, re.DOTALL)
        scores = json.loads(m.group()) if m else []

    scores_map = {s["idx"]: s for s in scores}

    results = []
    for i, job in enumerate(raw_jobs):
        sd = scores_map.get(i, {"match_score": 50, "match_reasons": []})
        city = job.get("job_city") or ""
        state = job.get("job_state") or ""
        location_str = ", ".join(filter(None, [city, state]))
        emp_type = (job.get("job_employment_type") or "").replace("_", " ").title()

        results.append({
            "title": job.get("job_title", ""),
            "company": job.get("employer_name", ""),
            "employer_logo": job.get("employer_logo") or "",
            "location": location_str,
            "salary": format_salary(job),
            "apply_link": job.get("job_apply_link", ""),
            "source": job.get("job_publisher", "Indeed"),
            "is_remote": job.get("job_is_remote", False),
            "posted_at": job.get("job_posted_at_datetime_utc", ""),
            "description_snippet": (job.get("job_description") or "")[:300],
            "employment_type": emp_type,
            "match_score": sd.get("match_score", 50),
            "match_reasons": sd.get("match_reasons", []),
        })

    results.sort(key=lambda j: j["match_score"], reverse=True)
    return results


# ============================================================
# CAREER FIELDS
# ============================================================
CAREER_FIELDS = [
    {"group": "Technology & IT", "fields": [
        "Software Development", "Web Development & Design", "Data Science & Analytics",
        "Cybersecurity", "IT Support & Systems Administration", "Network & Cloud Engineering",
        "DevOps & Site Reliability", "Artificial Intelligence & Machine Learning",
        "Database Administration", "UX/UI & Product Design",
    ]},
    {"group": "Engineering", "fields": [
        "Electrical Engineering", "Mechanical Engineering", "Civil & Structural Engineering",
        "Chemical Engineering", "Industrial & Manufacturing Engineering",
        "Aerospace & Aviation Engineering", "Biomedical Engineering",
        "Environmental Engineering", "Systems Engineering", "Petroleum & Energy Engineering",
    ]},
    {"group": "Healthcare & Medical", "fields": [
        "Nursing", "Physician & Physician Assistant", "Physical & Occupational Therapy",
        "Pharmacy", "Radiology & Medical Imaging", "Mental Health & Counseling",
        "Dental", "Emergency Medicine & Paramedics", "Medical Laboratory & Research",
        "Medical Administration",
    ]},
    {"group": "Finance & Accounting", "fields": [
        "Accounting", "Financial Analysis", "Banking", "Insurance",
        "Tax & Auditing", "Investment & Wealth Management", "Payroll", "Risk Management",
    ]},
    {"group": "Science & Research", "fields": [
        "Biology & Life Sciences", "Chemistry", "Physics", "Environmental Science",
        "Food Science & Agriculture", "Materials Science", "Geology & Earth Science", "Biotechnology",
    ]},
    {"group": "Construction & Trades", "fields": [
        "Construction Management", "Electrician & Electrical Trades", "Plumbing & Pipefitting",
        "HVAC & Refrigeration", "Carpentry & Woodworking", "Welding & Metalworking",
        "Heavy Equipment Operation", "Masonry & Concrete",
    ]},
    {"group": "Sales & Marketing", "fields": [
        "Sales", "Digital Marketing", "Marketing & Advertising",
        "Public Relations & Communications", "Brand Management",
        "Market Research & Analytics", "E-Commerce",
    ]},
    {"group": "Education", "fields": [
        "K-12 Teaching", "Higher Education", "Special Education",
        "Early Childhood Education", "School Administration",
        "Corporate Training & Instructional Design", "Tutoring & Academic Coaching",
    ]},
    {"group": "Manufacturing", "fields": [
        "Production & Assembly", "Quality Control & Assurance",
        "CNC Machining & Tool & Die", "Supply Chain & Procurement",
        "Warehouse & Inventory Management", "Lean & Process Improvement",
    ]},
    {"group": "Arts & Design", "fields": [
        "Graphic Design", "Architecture", "Interior Design",
        "Photography & Videography", "Animation & Game Design",
        "Fashion Design", "Music & Audio Production",
    ]},
    {"group": "Transportation & Logistics", "fields": [
        "Truck Driving & Delivery", "Logistics & Supply Chain Management",
        "Warehouse Operations", "Fleet Management",
        "Aviation & Aerospace Operations", "Rail & Transit",
    ]},
    {"group": "Legal", "fields": [
        "Attorney & Lawyer", "Paralegal", "Legal Administration",
        "Compliance & Regulatory Affairs", "Contract Management",
    ]},
    {"group": "Human Resources", "fields": [
        "Recruiting & Talent Acquisition", "HR Generalist",
        "Benefits & Compensation", "HR Management",
        "Training & Organizational Development",
    ]},
    {"group": "Administrative & Office", "fields": [
        "Administrative Assistant", "Executive Assistant", "Office Management",
        "Data Entry & Clerical", "Project Coordination",
    ]},
    {"group": "Customer Service", "fields": [
        "Call Center & Customer Support", "Retail & Store Management",
        "Client Relations", "Technical Support",
    ]},
    {"group": "Food Service & Hospitality", "fields": [
        "Restaurant & Food Service", "Hotel & Hospitality Management",
        "Event Planning", "Culinary & Chef", "Bartending & Mixology",
    ]},
    {"group": "Nonprofit & Social Services", "fields": [
        "Social Work", "Community Outreach & Development", "Nonprofit Management",
        "Case Management", "Volunteer Coordination",
    ]},
    {"group": "Real Estate", "fields": [
        "Real Estate Sales & Brokerage", "Property Management",
        "Real Estate Development", "Appraisal & Assessment", "Mortgage & Lending",
    ]},
]

CAREER_FIELDS.sort(key=lambda g: g["group"])
for _group in CAREER_FIELDS:
    _group["fields"].sort()

CAREER_FIELDS_FLAT = [field for group in CAREER_FIELDS for field in group["fields"]]

# ============================================================
# PII STRIPPING
# ============================================================
def strip_pii(text):
    """Remove personal contact info from resume text before AI use or server storage."""
    if not text:
        return text

    # Remove email addresses
    text = re.sub(
        r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b',
        '[email removed]', text
    )

    # Remove phone numbers — handles (555) 555-5555 / 555-555-5555 / +1 555 555 5555 / etc.
    text = re.sub(
        r'(\+?1[\s.\-]?)?(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})\b',
        '[phone removed]', text
    )

    # Remove full name: first occurrence in the top 6 lines that looks like
    # "Firstname [Middle] Lastname" (2–4 title-cased words, optional middle initial)
    lines = text.split('\n')
    filtered = []
    name_removed = False
    checked = 0
    for line in lines:
        stripped = line.strip()
        if not name_removed and stripped and checked < 6:
            checked += 1
            if re.match(
                r'^[A-Z][a-zA-Z\'\-]{1,}(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-zA-Z\'\-]{1,}){1,2}$',
                stripped
            ):
                name_removed = True
                continue  # drop this line
        filtered.append(line)

    text = '\n'.join(filtered)
    # Collapse runs of 3+ blank lines left by removals
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ============================================================
# RESUME PARSING
# ============================================================
def extract_text_from_pdf(file_bytes):
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = ""
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text.strip()
    except Exception as e:
        return f"Error extracting PDF text: {str(e)}"


def extract_text_from_docx(file_bytes):
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
        return text.strip()
    except Exception as e:
        return f"Error extracting DOCX text: {str(e)}"


def extract_resume(request_files):
    if "resume" not in request_files:
        return ""
    file = request_files["resume"]
    if not (file and file.filename):
        return ""
    file_bytes = file.read()
    filename = file.filename.lower()
    if filename.endswith(".pdf"):
        text = extract_text_from_pdf(file_bytes)
    elif filename.endswith(".docx") or filename.endswith(".doc"):
        text = extract_text_from_docx(file_bytes)
    elif filename.endswith(".txt"):
        text = file_bytes.decode("utf-8", errors="ignore")
    else:
        return ""
    if len(text) > 6000:
        text = text[:6000] + "\n[Resume truncated]"
    return text

# ============================================================
# ROUTES
# ============================================================
@app.route("/")
def index():
    return render_template("index.html", career_fields=CAREER_FIELDS)


@app.route("/api/career-fields")
def get_career_fields():
    return jsonify(CAREER_FIELDS_FLAT)


@app.route("/api/search-jobs", methods=["POST"])
def search_jobs():
    career_field = request.form.get("career_field", "")
    location_pref = request.form.get("location_pref", "both")
    keywords = request.form.get("keywords", "")
    date_posted = request.form.get("date_posted", "month")
    anthropic_key = request.form.get("anthropic_key") or None
    jsearch_key = request.form.get("jsearch_key") or None
    resume_text = extract_resume(request.files)

    # Build search query
    query = " ".join(filter(None, [career_field, keywords])).strip() or "jobs"

    try:
        # Fetch real jobs from JSearch
        if location_pref == "remote":
            raw_jobs = fetch_real_jobs(query, is_remote=True, num_pages=2,
                                       date_posted=date_posted, api_key=jsearch_key)
        elif location_pref == "milwaukee":
            raw_jobs = fetch_real_jobs(query, location="Milwaukee, WI", num_pages=2,
                                       date_posted=date_posted, api_key=jsearch_key)
        else:  # both — search Milwaukee + remote, deduplicate
            local = fetch_real_jobs(query, location="Milwaukee, WI", num_pages=1,
                                    date_posted=date_posted, api_key=jsearch_key)
            remote = fetch_real_jobs(query, is_remote=True, num_pages=1,
                                     date_posted=date_posted, api_key=jsearch_key)
            seen = set()
            raw_jobs = []
            for job in local + remote:
                jid = job.get("job_id", "")
                if jid not in seen:
                    seen.add(jid)
                    raw_jobs.append(job)

        if not raw_jobs:
            return jsonify({
                "success": False,
                "error": "No jobs found. Try broader keywords or a different career field."
            }), 404

        # Auto-save resume for scheduled search
        if resume_text:
            filename = request.files["resume"].filename if "resume" in request.files else ""
            _save_resume_cache(resume_text, filename)

        # AI ranks the real jobs
        ranked_jobs = rank_jobs_with_ai(raw_jobs, resume_text, career_field, keywords,
                                        "claude", anthropic_key=anthropic_key)

        # Group by source board
        jobs_by_board = {}
        for job in ranked_jobs:
            board = job["source"]
            jobs_by_board.setdefault(board, [])
            jobs_by_board[board].append(job)

        return jsonify({
            "success": True,
            "jobs": ranked_jobs,
            "jobs_by_board": jobs_by_board,
            "resume_analyzed": bool(resume_text),
            "location_pref": location_pref,
            "career_field": career_field,
            "total": len(ranked_jobs)
        })

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/analyze-resume", methods=["POST"])
def analyze_resume():
    resume_text = extract_resume(request.files)
    if not resume_text:
        return jsonify({"success": False, "error": "No resume text could be extracted"}), 400

    prompt = f"""Analyze this resume and extract key information. Return a JSON object with:
- "name": Candidate's name (or "Not found" if not present)
- "top_skills": Array of top 8 technical/professional skills identified
- "experience_level": "Entry Level", "Mid Level", or "Senior Level"
- "years_experience": Estimated years of relevant experience (number)
- "suggested_career_fields": Array of 3 career fields from this list that best match the resume: {json.dumps(CAREER_FIELDS_FLAT)}
- "education": Highest education level
- "summary": 2-3 sentence professional summary of the candidate

RESUME:
---
{resume_text}
---

Return ONLY valid JSON, no other text."""

    anthropic_key = request.form.get("anthropic_key") or None

    try:
        response_text = call_ai(prompt, "claude", api_key=anthropic_key)
        analysis = json.loads(response_text)
        return jsonify({"success": True, "analysis": analysis})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# SERVER-SIDE SCHEDULER + DISCORD NOTIFICATIONS
# ============================================================
os.makedirs(_DATA_DIR, exist_ok=True)
KEYS_FILE        = os.path.join(_DATA_DIR, "keys_config.json")
SCHEDULE_FILE    = os.path.join(_DATA_DIR, "schedule_config.json")
LAST_JOBS_FILE   = os.path.join(_DATA_DIR, "last_jobs_cache.json")
RESUME_CACHE_FILE = os.path.join(_DATA_DIR, "resume_cache.json")

_scheduler_lock = threading.Lock()
_scheduler = None


def _load_saved_keys():
    try:
        with open(KEYS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_keys_to_disk(data):
    with open(KEYS_FILE, "w") as f:
        json.dump(data, f)


def _load_schedule_config():
    try:
        with open(SCHEDULE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _save_schedule_config(config):
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _load_last_job_keys():
    try:
        with open(LAST_JOBS_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_last_job_keys(keys):
    with open(LAST_JOBS_FILE, "w") as f:
        json.dump(list(keys)[-500:], f)


def _load_saved_resume():
    try:
        with open(RESUME_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _save_resume_cache(text, filename=""):
    clean = strip_pii(text)
    with open(RESUME_CACHE_FILE, "w") as f:
        json.dump({
            "text": clean,
            "filename": filename,
            "char_count": len(clean),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }, f)


def send_discord_notification(webhook_url, new_jobs, search_summary):
    """Send Discord embed notification for new job matches."""
    url = webhook_url or os.environ.get("DISCORD_WEBHOOK", "")
    if not url or not new_jobs:
        return
    embeds = []
    for job in new_jobs[:5]:
        loc = job.get("location") or ("Remote" if job.get("is_remote") else "N/A")
        salary = job.get("salary") or "Not listed"
        score = job.get("match_score", "?")
        reasons = job.get("match_reasons", [])
        desc = " · ".join(reasons[:2]) if reasons else job.get("description_snippet", "")[:120]
        embeds.append({
            "title": f"{job.get('title', 'Job')} @ {job.get('company', '')}",
            "url": job.get("apply_link", "") or "",
            "color": 3447003,
            "description": desc or "",
            "fields": [
                {"name": "Match", "value": f"{score}%", "inline": True},
                {"name": "Location", "value": loc, "inline": True},
                {"name": "Salary", "value": salary, "inline": True},
            ],
            "footer": {"text": job.get("source", "")},
        })

    content = (
        f"🎯 **{len(new_jobs)} new job match{'es' if len(new_jobs) != 1 else ''} found!**"
        f"  Search: _{search_summary}_"
    )
    payload = {"content": content, "embeds": embeds[:10]}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[scheduler] Discord notification failed: {e}")


def run_server_scheduled_search():
    """Background job: fetch jobs, diff against cache, notify Discord."""
    config = _load_schedule_config()
    if not config or not config.get("enabled"):
        return

    # Prevent duplicate runs within the interval
    last_run = config.get("last_run_at")
    if last_run:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_run)).total_seconds()
        interval_secs = 86400 if config.get("interval") != "weekly" else 604800
        if elapsed < interval_secs * 0.9:
            return

    print(f"[scheduler] Running scheduled search at {datetime.now(timezone.utc).isoformat()}")
    try:
        query = " ".join(filter(None, [
            config.get("career_field", ""),
            config.get("keywords", "")
        ])).strip() or "jobs"
        location_pref = config.get("location_pref", "both")
        date_posted = config.get("date_posted", "week")
        jsearch_key = config.get("jsearch_key") or os.environ.get("JSEARCH_API_KEY")
        anthropic_key = config.get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY")

        if location_pref == "remote":
            raw_jobs = fetch_real_jobs(query, is_remote=True, num_pages=2,
                                       date_posted=date_posted, api_key=jsearch_key)
        elif location_pref == "milwaukee":
            raw_jobs = fetch_real_jobs(query, location="Milwaukee, WI", num_pages=2,
                                       date_posted=date_posted, api_key=jsearch_key)
        else:
            local = fetch_real_jobs(query, location="Milwaukee, WI", num_pages=1,
                                    date_posted=date_posted, api_key=jsearch_key)
            remote = fetch_real_jobs(query, is_remote=True, num_pages=1,
                                     date_posted=date_posted, api_key=jsearch_key)
            seen_ids = set()
            raw_jobs = []
            for job in local + remote:
                jid = job.get("job_id", "")
                if jid not in seen_ids:
                    seen_ids.add(jid)
                    raw_jobs.append(job)

        if raw_jobs:
            resume_cache = _load_saved_resume()
            resume_text = resume_cache["text"] if resume_cache else ""
            ranked = rank_jobs_with_ai(raw_jobs, resume_text, config.get("career_field", ""),
                                       config.get("keywords", ""), "claude",
                                       anthropic_key=anthropic_key)
            prev_keys = _load_last_job_keys()
            new_jobs = [j for j in ranked if j.get("apply_link", "") not in prev_keys]
            all_keys = prev_keys | {j.get("apply_link", "") for j in ranked}
            _save_last_job_keys(all_keys)

            webhook = config.get("discord_webhook") or os.environ.get("DISCORD_WEBHOOK", "")
            if webhook and new_jobs:
                summary = (config.get("career_field", "") + " " + config.get("keywords", "")).strip() or "All fields"
                send_discord_notification(webhook, new_jobs, summary)
            print(f"[scheduler] Done — {len(ranked)} jobs, {len(new_jobs)} new, Discord: {bool(webhook and new_jobs)}")

        config["last_run_at"] = datetime.now(timezone.utc).isoformat()
        config.pop("last_error", None)
    except Exception as e:
        print(f"[scheduler] Error: {e}")
        config["last_run_at"] = datetime.now(timezone.utc).isoformat()
        config["last_error"] = str(e)
    _save_schedule_config(config)


def _start_scheduler(config):
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("[scheduler] APScheduler not installed — background scheduling unavailable")
        return
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = BackgroundScheduler(daemon=True)
            _scheduler.start()
        # Remove old job if present
        try:
            _scheduler.remove_job("scheduled_search")
        except Exception:
            pass
        if config and config.get("enabled"):
            weeks = 1 if config.get("interval") == "weekly" else 0
            hours = 0 if weeks else 24
            _scheduler.add_job(
                run_server_scheduled_search,
                "interval",
                hours=hours, weeks=weeks,
                id="scheduled_search",
                replace_existing=True,
            )
            print(f"[scheduler] Scheduled: {config.get('interval','daily')}")


# Restore schedule on startup — also fire immediately if overdue
def _startup_search_check():
    """Run after a short delay so Flask is fully up; fires if search is overdue."""
    import time
    time.sleep(6)
    run_server_scheduled_search()

with app.app_context():
    _cfg = _load_schedule_config()
    if _cfg and _cfg.get("enabled"):
        _start_scheduler(_cfg)
        # Trigger an immediate overdue check (run_server_scheduled_search guards
        # against running too early via its elapsed-time check)
        threading.Thread(target=_startup_search_check, daemon=True).start()


# ============================================================
# SCHEDULE API ROUTES
# ============================================================
@app.route("/api/schedule", methods=["GET"])
def get_schedule():
    config = _load_schedule_config()
    if not config:
        return jsonify({"enabled": False})
    safe = {k: v for k, v in config.items() if k not in ("anthropic_key", "jsearch_key")}
    return jsonify(safe)


@app.route("/api/schedule", methods=["POST"])
def save_schedule():
    data = request.json or {}
    existing = _load_schedule_config() or {}
    config = {
        "enabled":         data.get("enabled", True),
        "interval":        data.get("interval", "daily"),
        "career_field":    data.get("career_field", ""),
        "keywords":        data.get("keywords", ""),
        "location_pref":   data.get("location_pref", "both"),
        "date_posted":     data.get("date_posted", "week"),
        "discord_webhook": data.get("discord_webhook", ""),
        # Keep old keys if not re-supplied (so we don't clear them on UI toggle)
        "anthropic_key":   data.get("anthropic_key") or existing.get("anthropic_key", ""),
        "jsearch_key":     data.get("jsearch_key") or existing.get("jsearch_key", ""),
        "last_run_at":     existing.get("last_run_at"),
        "saved_at":        datetime.now(timezone.utc).isoformat(),
    }
    _save_schedule_config(config)
    _start_scheduler(config)
    return jsonify({"success": True})


@app.route("/api/schedule", methods=["DELETE"])
def delete_schedule():
    try:
        os.remove(SCHEDULE_FILE)
    except Exception:
        pass
    global _scheduler
    with _scheduler_lock:
        if _scheduler:
            try:
                _scheduler.remove_job("scheduled_search")
            except Exception:
                pass
    return jsonify({"success": True})


@app.route("/api/test-discord", methods=["POST"])
def test_discord():
    data = request.json or {}
    webhook_url = data.get("webhook_url") or os.environ.get("DISCORD_WEBHOOK", "")
    if not webhook_url:
        return jsonify({"success": False, "error": "No webhook URL provided"}), 400
    fake_job = {
        "title": "Senior Software Engineer", "company": "Acme Corp",
        "location": "Milwaukee, WI", "salary": "$120,000-$150,000/yr",
        "match_score": 92, "source": "LinkedIn",
        "apply_link": "https://example.com",
        "match_reasons": ["Strong Python & AWS experience", "Remote-friendly team"],
    }
    try:
        send_discord_notification(webhook_url, [fake_job], "Test notification")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# API KEY STORAGE ROUTES
# ============================================================
@app.route("/api/keys", methods=["GET"])
def get_keys():
    keys = _load_saved_keys()
    return jsonify({
        "anthropic": keys.get("anthropic", ""),
        "jsearch":   keys.get("jsearch", ""),
    })


@app.route("/api/keys", methods=["POST"])
def save_keys():
    data = request.json or {}
    existing = _load_saved_keys()
    updated = {
        "anthropic": data.get("anthropic") or existing.get("anthropic", ""),
        "jsearch":   data.get("jsearch")   or existing.get("jsearch", ""),
    }
    _save_keys_to_disk(updated)
    return jsonify({"success": True})


@app.route("/api/keys", methods=["DELETE"])
def delete_keys():
    try:
        os.remove(KEYS_FILE)
    except Exception:
        pass
    return jsonify({"success": True})


# ============================================================
# SAVED RESUME ROUTES
# ============================================================
@app.route("/api/saved-resume", methods=["GET"])
def get_saved_resume():
    cache = _load_saved_resume()
    if not cache:
        return jsonify({"saved": False})
    return jsonify({
        "saved": True,
        "filename": cache.get("filename", ""),
        "char_count": cache.get("char_count", 0),
        "saved_at": cache.get("saved_at", ""),
        "preview": (cache.get("text", "")[:120] + "…") if cache.get("text") else "",
    })


@app.route("/api/save-resume", methods=["POST"])
def save_resume_endpoint():
    resume_text = extract_resume(request.files)
    if not resume_text:
        return jsonify({"success": False, "error": "Could not extract text from file"}), 400
    filename = request.files["resume"].filename if "resume" in request.files else ""
    _save_resume_cache(resume_text, filename)
    return jsonify({"success": True, "char_count": len(resume_text), "filename": filename})


@app.route("/api/saved-resume", methods=["DELETE"])
def delete_saved_resume():
    try:
        os.remove(RESUME_CACHE_FILE)
    except Exception:
        pass
    return jsonify({"success": True})


@app.route("/api/run-now", methods=["POST"])
def run_now():
    """Trigger the scheduled search immediately (used by tray menu & UI)."""
    threading.Thread(target=run_server_scheduled_search, daemon=True).start()
    return jsonify({"success": True, "message": "Search started in background"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
