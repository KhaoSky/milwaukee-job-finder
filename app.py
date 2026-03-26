import os
import io
import json
import requests
from flask import Flask, request, jsonify, render_template

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv("API_key.env", override=True)
except ImportError:
    pass

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# ============================================================
# AI PROVIDER
# ============================================================
def call_ai(prompt, provider="openai"):
    """Call the selected AI provider and return the response text."""
    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-2.0-flash")
        resp = model.generate_content(prompt)
        return resp.text or ""

    else:  # openai (default)
        from openai import OpenAI
        oai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        resp = oai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8000,
            temperature=0.3
        )
        return resp.choices[0].message.content or ""

# ============================================================
# REAL JOB FETCHING — JSearch API (RapidAPI)
# ============================================================
def fetch_real_jobs(query, location=None, is_remote=False, num_pages=3):
    """Fetch real job listings from JSearch API via RapidAPI."""
    api_key = os.environ.get("JSEARCH_API_KEY")
    if not api_key:
        raise ValueError(
            "JSEARCH_API_KEY not configured. "
            "Sign up free at https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch "
            "and add JSEARCH_API_KEY to your environment variables."
        )

    search_query = query
    if location and not is_remote:
        search_query += f" in {location}"

    params = {
        "query": search_query,
        "page": "1",
        "num_pages": str(num_pages),
        "date_posted": "month",
    }
    if is_remote:
        params["remote_jobs_only"] = "true"

    resp = requests.get(
        "https://jsearch.p.rapidapi.com/search",
        headers={
            "X-RapidAPI-Key": api_key,
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


def rank_jobs_with_ai(raw_jobs, resume_text, career_field, keywords, provider):
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
        f"CANDIDATE RESUME:\n---\n{resume_text[:3000]}\n---\n" if resume_text else ""
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

    response_text = call_ai(prompt, provider)

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
    ai_provider = request.form.get("ai_provider", "openai")
    resume_text = extract_resume(request.files)

    # Build search query
    query = " ".join(filter(None, [career_field, keywords])).strip() or "jobs"

    try:
        # Fetch real jobs from JSearch
        if location_pref == "remote":
            raw_jobs = fetch_real_jobs(query, is_remote=True, num_pages=3)
        elif location_pref == "milwaukee":
            raw_jobs = fetch_real_jobs(query, location="Milwaukee, WI", num_pages=3)
        else:  # both — search Milwaukee + remote, deduplicate
            local = fetch_real_jobs(query, location="Milwaukee, WI", num_pages=2)
            remote = fetch_real_jobs(query, is_remote=True, num_pages=2)
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

        # AI ranks the real jobs
        ranked_jobs = rank_jobs_with_ai(raw_jobs, resume_text, career_field, keywords, ai_provider)

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

    ai_provider = request.form.get("ai_provider", "openai")

    try:
        response_text = call_ai(prompt, ai_provider)
        analysis = json.loads(response_text)
        return jsonify({"success": True, "analysis": analysis})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
