import os
import io
import json
import base64
from flask import Flask, request, jsonify, render_template
import anthropic

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv("API_key.env", override=True)
except ImportError:
    pass

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

CAREER_FIELDS = [
    {"group": "Technology & IT", "fields": [
        "Software Development",
        "Web Development & Design",
        "Data Science & Analytics",
        "Cybersecurity",
        "IT Support & Systems Administration",
        "Network & Cloud Engineering",
        "DevOps & Site Reliability",
        "Artificial Intelligence & Machine Learning",
        "Database Administration",
        "UX/UI & Product Design",
    ]},
    {"group": "Engineering", "fields": [
        "Electrical Engineering",
        "Mechanical Engineering",
        "Civil & Structural Engineering",
        "Chemical Engineering",
        "Industrial & Manufacturing Engineering",
        "Aerospace & Aviation Engineering",
        "Biomedical Engineering",
        "Environmental Engineering",
        "Systems Engineering",
        "Petroleum & Energy Engineering",
    ]},
    {"group": "Healthcare & Medical", "fields": [
        "Nursing",
        "Physician & Physician Assistant",
        "Physical & Occupational Therapy",
        "Pharmacy",
        "Radiology & Medical Imaging",
        "Mental Health & Counseling",
        "Dental",
        "Emergency Medicine & Paramedics",
        "Medical Laboratory & Research",
        "Medical Administration",
    ]},
    {"group": "Finance & Accounting", "fields": [
        "Accounting",
        "Financial Analysis",
        "Banking",
        "Insurance",
        "Tax & Auditing",
        "Investment & Wealth Management",
        "Payroll",
        "Risk Management",
    ]},
    {"group": "Science & Research", "fields": [
        "Biology & Life Sciences",
        "Chemistry",
        "Physics",
        "Environmental Science",
        "Food Science & Agriculture",
        "Materials Science",
        "Geology & Earth Science",
        "Biotechnology",
    ]},
    {"group": "Construction & Trades", "fields": [
        "Construction Management",
        "Electrician & Electrical Trades",
        "Plumbing & Pipefitting",
        "HVAC & Refrigeration",
        "Carpentry & Woodworking",
        "Welding & Metalworking",
        "Heavy Equipment Operation",
        "Masonry & Concrete",
    ]},
    {"group": "Sales & Marketing", "fields": [
        "Sales",
        "Digital Marketing",
        "Marketing & Advertising",
        "Public Relations & Communications",
        "Brand Management",
        "Market Research & Analytics",
        "E-Commerce",
    ]},
    {"group": "Education", "fields": [
        "K-12 Teaching",
        "Higher Education",
        "Special Education",
        "Early Childhood Education",
        "School Administration",
        "Corporate Training & Instructional Design",
        "Tutoring & Academic Coaching",
    ]},
    {"group": "Manufacturing", "fields": [
        "Production & Assembly",
        "Quality Control & Assurance",
        "CNC Machining & Tool & Die",
        "Supply Chain & Procurement",
        "Warehouse & Inventory Management",
        "Lean & Process Improvement",
    ]},
    {"group": "Arts & Design", "fields": [
        "Graphic Design",
        "Architecture",
        "Interior Design",
        "Photography & Videography",
        "Animation & Game Design",
        "Fashion Design",
        "Music & Audio Production",
    ]},
    {"group": "Transportation & Logistics", "fields": [
        "Truck Driving & Delivery",
        "Logistics & Supply Chain Management",
        "Warehouse Operations",
        "Fleet Management",
        "Aviation & Aerospace Operations",
        "Rail & Transit",
    ]},
    {"group": "Legal", "fields": [
        "Attorney & Lawyer",
        "Paralegal",
        "Legal Administration",
        "Compliance & Regulatory Affairs",
        "Contract Management",
    ]},
    {"group": "Human Resources", "fields": [
        "Recruiting & Talent Acquisition",
        "HR Generalist",
        "Benefits & Compensation",
        "HR Management",
        "Training & Organizational Development",
    ]},
    {"group": "Administrative & Office", "fields": [
        "Administrative Assistant",
        "Executive Assistant",
        "Office Management",
        "Data Entry & Clerical",
        "Project Coordination",
    ]},
    {"group": "Customer Service", "fields": [
        "Call Center & Customer Support",
        "Retail & Store Management",
        "Client Relations",
        "Technical Support",
    ]},
    {"group": "Food Service & Hospitality", "fields": [
        "Restaurant & Food Service",
        "Hotel & Hospitality Management",
        "Event Planning",
        "Culinary & Chef",
        "Bartending & Mixology",
    ]},
    {"group": "Nonprofit & Social Services", "fields": [
        "Social Work",
        "Community Outreach & Development",
        "Nonprofit Management",
        "Case Management",
        "Volunteer Coordination",
    ]},
    {"group": "Real Estate", "fields": [
        "Real Estate Sales & Brokerage",
        "Property Management",
        "Real Estate Development",
        "Appraisal & Assessment",
        "Mortgage & Lending",
    ]},
]

# Sort groups alphabetically, and fields within each group alphabetically
CAREER_FIELDS.sort(key=lambda g: g["group"])
for _group in CAREER_FIELDS:
    _group["fields"].sort()

# Flat list of all field names — used for resume analysis prompt and API endpoint
CAREER_FIELDS_FLAT = [field for group in CAREER_FIELDS for field in group["fields"]]

JOB_BOARDS = [
    {"name": "Indeed", "url_template": "https://www.indeed.com/jobs?q={query}&l={location}"},
    {"name": "LinkedIn", "url_template": "https://www.linkedin.com/jobs/search/?keywords={query}&location={location}"},
    {"name": "ZipRecruiter", "url_template": "https://www.ziprecruiter.com/jobs-search?search={query}&location={location}"},
    {"name": "Glassdoor", "url_template": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={query}+Milwaukee+WI"},
    {"name": "CareerBuilder", "url_template": "https://www.careerbuilder.com/jobs?keywords={query}&location={location}"},
]

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

def build_job_links(job_title, is_remote=False):
    from urllib.parse import quote_plus
    query = quote_plus(job_title)
    location_encoded = quote_plus("Remote" if is_remote else "Milwaukee, WI")
    links = []
    for board in JOB_BOARDS:
        url = board["url_template"].format(query=query, location=location_encoded)
        if is_remote:
            # Remote-specific URLs with date filter
            if "indeed" in url:
                url = f"https://www.indeed.com/jobs?q={query}&remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11&fromage=30"
            elif "linkedin" in url:
                url = f"https://www.linkedin.com/jobs/search/?keywords={query}&f_WT=2&f_TPR=r2592000"
            elif "ziprecruiter" in url:
                url = f"https://www.ziprecruiter.com/jobs-search?search={query}&location=Remote&days=30"
            elif "careerbuilder" in url:
                url = f"https://www.careerbuilder.com/jobs?keywords={query}&location=Remote&posted=30d"
            elif "glassdoor" in url:
                url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={query}&remoteWorkType=1&fromAge=30"
        else:
            # Local jobs — add date filter
            if "indeed" in url:
                url += "&fromage=30"
            elif "linkedin" in url:
                url += "&f_TPR=r2592000"
            elif "glassdoor" in url:
                url += "&fromAge=30"
            elif "ziprecruiter" in url:
                url += "&days=30"
            elif "careerbuilder" in url:
                url += "&posted=30d"
        links.append({"name": board["name"], "url": url})

    # Add Milwaukee-specific boards for local (non-remote) jobs
    if not is_remote:
        links.append({
            "name": "MilwaukeeJobs",
            "url": f"https://www.milwaukeejobs.com/search/?q={query}&l=Milwaukee%2C+WI"
        })
        links.append({
            "name": "WI Job Center",
            "url": f"https://jobcenterofwisconsin.com/jobseekers/find-a-job/?q={query}&l=Milwaukee%2C+WI"
        })
        links.append({
            "name": "Built In MKE",
            "url": f"https://builtin.com/jobs?q={query}&city=milwaukee"
        })

    return links

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
    zip_code = request.form.get("zip_code", "").strip()
    resume_text = ""

    # Handle resume upload
    if "resume" in request.files:
        file = request.files["resume"]
        if file and file.filename:
            file_bytes = file.read()
            filename = file.filename.lower()
            if filename.endswith(".pdf"):
                resume_text = extract_text_from_pdf(file_bytes)
            elif filename.endswith(".docx") or filename.endswith(".doc"):
                resume_text = extract_text_from_docx(file_bytes)
            elif filename.endswith(".txt"):
                resume_text = file_bytes.decode("utf-8", errors="ignore")

    # Build the AI prompt
    location_context = ""
    if location_pref == "milwaukee":
        location_context = "Milwaukee, Wisconsin area (in-person or hybrid)"
    elif location_pref == "remote":
        location_context = "remote/work-from-home positions only"
    else:
        location_context = "Milwaukee, Wisconsin area AND remote/work-from-home positions"

    resume_section = ""
    if resume_text:
        if len(resume_text) > 8000:
            resume_text = resume_text[:8000] + "\n[Resume truncated for length]"
        resume_section = f"""
CANDIDATE'S RESUME:
---
{resume_text}
---
"""

    career_section = f"Career Field of Interest: {career_field}" if career_field else "Open to various career fields"
    keywords_section = f"Additional keywords/interests: {keywords}" if keywords else ""
    zip_section = f"Candidate's ZIP code: {zip_code} (use this for precise salary ranges and commute context)" if zip_code else ""

    prompt = f"""You are an expert job search assistant specializing in the Milwaukee, Wisconsin job market and remote work opportunities.

{resume_section}

{career_section}
{keywords_section}
{zip_section}
Location Preference: {location_context}

Based on this information, provide 10-12 specific, realistic job recommendations. For each job, return a JSON object with these exact fields:
- "title": The specific job title
- "company_type": Type of employer (e.g., "Tech startup", "Regional hospital", "Accounting firm")
- "location": Either "Milwaukee, WI", "Milwaukee, WI (Hybrid)", or "Remote (Work from Home)"
- "salary_range": Realistic salary range for this role in the Milwaukee market (e.g., "$55,000 - $75,000/year" or "$25-$35/hour")
- "match_reasons": Array of 3-4 bullet points explaining why this is a good match (based on resume if provided, or career field)
- "key_skills": Array of 4-6 key skills/qualifications typically needed
- "is_remote": boolean, true if remote position
- "search_title": A clean job title optimized for job board searching (e.g., "Software Engineer" instead of "Sr. Full Stack Engineer")
- "company_examples": Array of 2-3 real Milwaukee-area companies or national companies with remote roles that hire for this position
- "primary_company_domain": The website domain of the most recognizable company in company_examples (e.g., "aurora.org", "johnsoncontrols.com", "amazon.com"). Use only the bare domain — no "www." prefix and no "https://". If unsure, use the most well-known company's domain.
- "job_tags": Array of exactly 4-6 tags. Must include: one work arrangement tag ("Remote", "On-Site", or "Hybrid"), one experience level tag ("Entry Level", "Mid Level", or "Senior Level"), one employment type tag ("Full-Time", "Part-Time", or "Contract"), and 1-2 relevant industry or skill tags specific to the role (e.g., "Python", "Patient Care", "CAD", "Logistics").
- "match_score": Integer from 1 to 100 representing how strong a match this job is for the candidate based on their resume, career field, and keywords. If no resume was provided, base it on how well the role aligns with the stated career field and keywords. Order the array from highest to lowest match_score.
- "estimated_applicants": Realistic estimated number of applicants typically seen for this type of role in this market, as a string range (e.g., "35–80 applicants", "100–200 applicants", "10–25 applicants"). Base this on actual labor market competition for the role — niche/senior roles get fewer applicants, popular entry-level roles get more.
- "competition_level": One of "Low", "Medium", or "High" reflecting how competitive the applicant pool typically is for this role.

Focus on realistic jobs available in the Milwaukee job market. Include a mix of entry-level, mid-level, and senior positions if the career field is broad. If a resume was provided, tailor recommendations to the candidate's actual experience and skills.

Return ONLY a valid JSON array of job objects sorted by match_score descending, no other text."""

    try:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=10000,   # Increased — thinking blocks consume tokens before the text block
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}]
        )

        # Collect ALL text blocks (adaptive thinking can produce multiple)
        response_text = ""
        for block in response.content:
            if block.type == "text":
                response_text += block.text

        if not response_text.strip():
            return jsonify({"success": False, "error": "AI returned an empty response. Please try again."}), 500

        # Parse JSON from response
        jobs_data = json.loads(response_text)

        # Sort by match_score descending (best matches first)
        jobs_data.sort(key=lambda j: j.get("match_score", 0), reverse=True)

        # Add job board links to each job
        for job in jobs_data:
            search_title = job.get("search_title", job.get("title", ""))
            is_remote = job.get("is_remote", False)
            job["job_links"] = build_job_links(search_title, is_remote)

        return jsonify({
            "success": True,
            "jobs": jobs_data,
            "resume_analyzed": bool(resume_text),
            "location_pref": location_pref,
            "career_field": career_field
        })

    except json.JSONDecodeError as e:
        # Try to extract JSON array from the response (handles extra prose around it)
        try:
            import re
            json_match = re.search(r'\[\s*\{.*\}\s*\]', response_text, re.DOTALL)
            if json_match:
                jobs_data = json.loads(json_match.group())
                jobs_data.sort(key=lambda j: j.get("match_score", 0), reverse=True)
                for job in jobs_data:
                    search_title = job.get("search_title", job.get("title", ""))
                    is_remote = job.get("is_remote", False)
                    job["job_links"] = build_job_links(search_title, is_remote)
                return jsonify({
                    "success": True,
                    "jobs": jobs_data,
                    "resume_analyzed": bool(resume_text),
                    "location_pref": location_pref,
                    "career_field": career_field
                })
        except Exception:
            pass
        return jsonify({"success": False, "error": f"Failed to parse AI response: {str(e)}"}), 500
    except anthropic.APIError as e:
        return jsonify({"success": False, "error": f"AI API error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/analyze-resume", methods=["POST"])
def analyze_resume():
    resume_text = ""
    if "resume" in request.files:
        file = request.files["resume"]
        if file and file.filename:
            file_bytes = file.read()
            filename = file.filename.lower()
            if filename.endswith(".pdf"):
                resume_text = extract_text_from_pdf(file_bytes)
            elif filename.endswith(".docx") or filename.endswith(".doc"):
                resume_text = extract_text_from_docx(file_bytes)
            elif filename.endswith(".txt"):
                resume_text = file_bytes.decode("utf-8", errors="ignore")

    if not resume_text:
        return jsonify({"success": False, "error": "No resume text could be extracted"}), 400

    if len(resume_text) > 8000:
        resume_text = resume_text[:8000] + "\n[Resume truncated]"

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

    try:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = ""
        for block in response.content:
            if block.type == "text":
                response_text = block.text
                break

        analysis = json.loads(response_text)
        return jsonify({"success": True, "analysis": analysis})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
