# Security Best Practices Review Report
**Target Project:** AI Job Radar & Cold Outreach Hub (`ai-job-radar`)  
**Technology Stack:** Python 3.13 (FastAPI, Starlette, Pydantic, PyMongo), Vanilla JavaScript / Tailwind CSS, Vercel Serverless Functions  
**Audit Date:** November 22, 2026  
**Auditor:** Antigravity Agent (Security Best Practices Auditor)  
**Status:** Audit Completed — **Zero Code Modified** (Report-Only as requested)

---

## Executive Summary

A comprehensive security assessment was conducted across the codebase (`server.py`, `database.py`, `mailer.py`, `api/cron.py`, `api/trigger.py`, `api/index.py`, `config.py`, and client-side rendering templates).

The application demonstrates good foundational engineering practices in several areas, including parameterized MongoDB ObjectId handling, input limits on pagination, and escaping for primary job titles and company names. However, because the system interacts with external job boards, automated email dispatchers (Gmail SMTP), third-party web crawlers (Firecrawl), and cloud deployment targets (Vercel, Render), several critical and high-severity security vulnerabilities were identified:

1. **Unauthenticated Destructive Operations:** The database reset endpoint (`DELETE /api/database/clear`) allows anyone with network access to immediately wipe all MongoDB collections and delete reports from the host disk.
2. **Open Email Relay & Abuse Risks:** The cold email endpoint (`POST /api/jobs/{job_id}/send-cold-email`) dispatches arbitrary emails via authenticated Gmail SMTP without authentication or rate-limiting.
3. **Unprotected Webhook & Cron Triggers:** Cloud endpoints (`api/trigger.py` and `api/cron.py`) allow arbitrary callers to trigger expensive crawling pipelines and email blasts due to omitted or bypassable secret checks.
4. **Permissive CORS Configuration:** The backend combines wildcard origins (`allow_origins=["*"]`) with `allow_credentials=True`.
5. **NoSQL / Regex Injection & ReDoS:** Unescaped search queries in `database.py` can cause catastrophic backtracking in MongoDB regex evaluation.
6. **HTML Injection in Email Dispatch:** Unescaped job titles, summaries, and URLs from external job postings are formatted directly into outgoing emails.

Below is the prioritized list of findings, categorized by severity, with exact line numbers, evidence, impact analysis, and concrete remediation steps.

---

## Severity Summary

| Severity | Count | IDs |
| :--- | :---: | :--- |
| **Critical** | 1 | `SEC-01` |
| **High** | 5 | `SEC-02`, `SEC-03`, `SEC-04`, `SEC-05`, `SEC-06` |
| **Medium** | 3 | `SEC-07`, `SEC-08`, `SEC-09` |
| **Low** | 2 | `SEC-10`, `SEC-11` |

---

## Critical Vulnerabilities

### `SEC-01`: Unauthenticated Destructive Database & Filesystem Reset Endpoint

- **Severity:** Critical
- **Location:** [server.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/server.py#L353-L370) (`clear_database` function)
- **One-Sentence Impact:** *Any unauthenticated remote attacker can completely wipe all job records, crawl runs, and outreach logs from MongoDB, and permanently delete all generated Excel reports from the server filesystem.*

#### Evidence
```python
# server.py:353-370
@app.delete("/api/database/clear", tags=["Database & System"], summary="Reset Database & Clean Reports")
def clear_database():
    """Empties all jobs, crawl runs, outreach logs from MongoDB, and cleans the reports directory for a fresh start."""
    deleted = db_instance.clear_all_data()
    cleared_reports = 0
    if config.REPORTS_DIR.exists():
        for f in config.REPORTS_DIR.glob("*"):
            if f.is_file():
                f.unlink()
                cleared_reports += 1
    logger.info(f"Database cleared: {deleted}, {cleared_reports} reports deleted.")
    return {
        "success": True,
        "message": "Database and reports successfully cleared for a fresh start.",
        "deleted_records": deleted,
        "cleared_reports": cleared_reports
    }
```

#### Detailed Impact
The endpoint does not verify any session cookie, API key, JWT, or admin secret. If the service is deployed to Render or made publicly accessible, any web scanner, bot, or hostile actor can issue a single `curl -X DELETE http://<host>/api/database/clear` and immediately cause catastrophic data loss.

#### Remediation Recommendation
Protect the endpoint behind an administrative API key or authorization dependency using constant-time comparison:
```python
import hmac
from fastapi import Header, HTTPException, Security, status

def verify_admin_key(x_admin_key: str = Header(..., alias="X-Admin-Key")):
    expected_key = os.getenv("ADMIN_API_KEY", "")
    if not expected_key or not hmac.compare_digest(x_admin_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing administrative API key"
        )
    return True

@app.delete("/api/database/clear", dependencies=[Security(verify_admin_key)])
def clear_database():
    ...
```

---

## High Vulnerabilities

### `SEC-02`: Unauthenticated Open Email Relay & Absence of Rate Limiting

- **Severity:** High
- **Location:** [server.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/server.py#L249-L279) (`send_cold_email`), [mailer.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/mailer.py#L154-L203) (`send_cold_outreach`)
- **Impact:** An attacker can abuse Sarthak's authenticated Gmail SMTP account to send arbitrary phishing or spam emails to any recipient on the internet, leading to domain blacklisting and suspension of Sarthak's Google account.

#### Evidence
```python
# server.py:249-263
@app.post("/api/jobs/{job_id}/send-cold-email", tags=["Cold Outreach"], summary="Send Cold Email to Recruiter")
def send_cold_email(job_id: str, req: ColdEmailRequest):
    ...
    mailer = Mailer()
    res = mailer.send_cold_outreach(
        to_email=req.recipient_email,
        subject=req.subject,
        body=req.body,
        candidate_name=config.CANDIDATE_NAME
    )
```

#### Detailed Impact
`ColdEmailRequest` accepts user-supplied `recipient_email`, `subject`, and `body` without authorization checks or sender-recipient relationship validation. Because the backend authenticates directly to `smtp.gmail.com` using the configured `SENDER_PASSWORD`, an unauthenticated actor could automate thousands of requests, turning the server into a spam relay.

#### Remediation Recommendation
1. Enforce admin authentication on the endpoint.
2. Validate that `req.recipient_email` matches a verified contact associated with `job_id` (or matches an email format validation regex).
3. Apply rate-limiting (e.g. maximum 5 emails per hour using `slowapi` or Redis token-bucket).

---

### `SEC-03`: Insecure Permissive CORS Configuration with Credentials

- **Severity:** High
- **Location:** [server.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/server.py#L61-L67)
- **Impact:** Combining `allow_origins=["*"]` with `allow_credentials=True` violates the CORS specification and exposes internal endpoints and session contexts to arbitrary third-party web pages.

#### Evidence
```python
# server.py:61-67
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

#### Detailed Impact
According to the W3C CORS specification and modern browser implementations, `Access-Control-Allow-Origin: *` cannot be combined with `Access-Control-Allow-Credentials: true`. In environments where an upstream proxy or server normalizes this by echoing the requester's `Origin` header, any malicious website can make cross-origin requests to read logs, fetch job metrics, or trigger actions.

#### Remediation Recommendation
Restrict allowed origins to trusted domains, or disable `allow_credentials` if wildcard access is strictly required:
```python
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Key"],
)
```

---

### `SEC-04`: ReDoS & Unescaped MongoDB Regex Injection

- **Severity:** High
- **Location:** [database.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/database.py#L271-L288) (`get_jobs` search filter)
- **Impact:** User-supplied search queries are passed directly into MongoDB `$regex` queries without escaping, enabling Regular Expression Denial of Service (ReDoS) and potential query logic alteration.

#### Evidence
```python
# database.py:271-280
if search and search.strip():
    s = search.strip()
    regex = {"$regex": s, "$options": "i"}
    search_clause = {"$or": [
        {"title": regex},
        {"company": regex},
        {"location": regex},
        {"matched_skills": regex},
        {"match_summary": regex}
    ]}
```

#### Detailed Impact
If an attacker or user inputs regular expression metacharacters or nested quantifiers such as `((a+)+)+$` or `.*.*.*.*.*.*a`, MongoDB's PCRE regex engine can suffer from exponential backtracking. This causes MongoDB CPU utilization to spike to 100%, locking worker threads and denying service to all other users. Furthermore, unclosed parentheses `(` cause MongoDB to throw an unhandled query exception.

#### Remediation Recommendation
Always escape search terms using `re.escape()` before constructing regular expression patterns, and enforce a maximum query length:
```python
import re

if search and search.strip():
    s = re.escape(search.strip()[:100])  # Escaped and length-bounded
    regex = {"$regex": s, "$options": "i"}
```

---

### `SEC-05`: Vercel Cron Secret Authentication Bypass

- **Severity:** High
- **Location:** [api/cron.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/api/cron.py#L20-L29) (`do_GET` handler)
- **Impact:** When `CRON_SECRET` is unset or empty (the default in `.env.example`), the authentication check evaluates to false and is bypassed entirely, exposing the cron crawler to the public internet.

#### Evidence
```python
# api/cron.py:20-28
# Optional Cron Secret validation
cron_secret = os.getenv("CRON_SECRET", "")
auth_header = self.headers.get("Authorization", "")
if cron_secret and auth_header != f"Bearer {cron_secret}":
    self.send_response(401)
    self.send_header("Content-Type", "application/json")
    self.end_headers()
    self.wfile.write(json.dumps({"error": "Unauthorized cron request"}).encode("utf-8"))
    return
```

#### Detailed Impact
The condition `if cron_secret and auth_header != f"Bearer {cron_secret}":` requires `cron_secret` to be truthy. If `CRON_SECRET` is not configured in Vercel environment variables, the condition is skipped, allowing any anonymous user to invoke `/api/cron` repeatedly, triggering pipeline runs and sending emails.

#### Remediation Recommendation
Fail closed if `CRON_SECRET` is missing in production environments, and use `hmac.compare_digest` to prevent timing attacks:
```python
import hmac

cron_secret = os.getenv("CRON_SECRET", "").strip()
auth_header = self.headers.get("Authorization", "").strip()

if not cron_secret or not hmac.compare_digest(auth_header, f"Bearer {cron_secret}"):
    self.send_response(401)
    self.send_header("Content-Type", "application/json")
    self.end_headers()
    self.wfile.write(json.dumps({"error": "Unauthorized: valid CRON_SECRET required"}).encode("utf-8"))
    return
```

---

### `SEC-06`: Completely Unauthenticated Manual Crawler Webhook

- **Severity:** High
- **Location:** [api/trigger.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/api/trigger.py#L15-L26)
- **Impact:** Anyone can trigger resource-intensive scraping cycles and automatic email dispatches with zero authentication via `GET` or `POST /api/trigger`.

#### Evidence
```python
# api/trigger.py:21-26
def _handle_request(self):
    """Manual trigger point accessible via browser, curl, webhook, or Postman."""
    try:
        # Run pipeline
        excel_path, matched_jobs = run_pipeline(min_score=50, send_mail=True)
```

#### Detailed Impact
The file comments state: `"Manual trigger point accessible via browser, curl, webhook, or Postman."` Because it runs `run_pipeline(..., send_mail=True)`, an automated bot scanning Vercel endpoints could continuously trigger web crawling, exhaust Firecrawl API credits, and flood Sarthak's inbox with emails.

#### Remediation Recommendation
Add authentication validation identical to `api/cron.py` or restrict trigger invocations to verified origins/API keys.

---

## Medium Vulnerabilities

### `SEC-07`: HTML & Link Injection in Automated Email Reports

- **Severity:** Medium
- **Location:** [mailer.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/mailer.py#L95-L108) (`send_daily_report`)
- **Impact:** External untrusted data crawled from job postings (job titles, descriptions, match summaries, apply URLs) is directly interpolated into HTML emails without HTML escaping.

#### Evidence
```python
# mailer.py:98-106
url = j.get("url", "#")
html_content += f"""
                <tr>
                    <td><span class="{badge_class}">{score}%</span></td>
                    <td><strong>{j.get('title', '')}</strong><br><small style="color: #64748b;">{j.get('match_summary', '')}</small></td>
                    <td>{j.get('company', '')}</td>
                    <td>{j.get('location', 'Remote')}</td>
                    <td><a href="{url}" class="btn-apply" target="_blank">Direct Apply</a></td>
                </tr>
"""
```

#### Detailed Impact
If an external company posts a malicious job listing containing HTML markup (e.g. `<img src="https://attacker.com/tracker.png">` or `<a href="phishing-link">`), that payload renders directly inside Sarthak's email client. In modern email clients, this can lead to tracking pixel leakage or link spoofing.

#### Remediation Recommendation
Import `html` and escape all dynamically crawled strings before interpolating them into HTML templates:
```python
import html

title = html.escape(str(j.get("title", "")))
company = html.escape(str(j.get("company", "")))
summary = html.escape(str(j.get("match_summary", "")))
location = html.escape(str(j.get("location", "Remote")))
# Ensure URL uses safe scheme
raw_url = str(j.get("url", "#"))
safe_url = raw_url if raw_url.startswith(("http://", "https://")) else "#"
```

---

### `SEC-08`: Sensitive Internal Log Exposure Endpoint

- **Severity:** Medium
- **Location:** [server.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/server.py#L348-L351) (`get_logs`)
- **Impact:** Live application logs containing HTTP paths, query parameters, timing data, and potential system exceptions are publicly exposed via `GET /api/logs`.

#### Evidence
```python
# server.py:348-351
@app.get("/api/logs", tags=["Logs & Monitoring"], summary="Live Application Logs")
def get_logs(lines: int = Query(100, ge=10, le=500, description="Number of recent log lines to fetch")):
    """Returns recent log entries from the rotating log file for live frontend streaming."""
    return {"logs": get_recent_logs(lines)}
```

#### Detailed Impact
Application logs can inadvertently record sensitive internal details, including server file paths, stack traces, IP addresses, and user activity. Providing an unauthenticated endpoint to view system logs violates the principle of least privilege.

#### Remediation Recommendation
Gate `/api/logs` behind local-host-only access (`request.client.host in ("127.0.0.1", "::1")`) or an authenticated admin session.

---

### `SEC-09`: Missing Security Headers & Content Security Policy (CSP)

- **Severity:** Medium
- **Location:** [server.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/server.py#L70-L78) (Middleware)
- **Impact:** The application does not send standard defensive HTTP headers (`Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`), leaving users vulnerable to clickjacking and MIME-type confusion attacks.

#### Evidence
The request middleware only logs request duration:
```python
# server.py:70-78
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = round((time.time() - start_time) * 1000, 2)
    if not request.url.path.startswith("/favicon.ico"):
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({process_time}ms)")
    return response
```

#### Detailed Impact
Without `X-Frame-Options: DENY`, the web dashboard can be embedded inside an attacker's iframe (clickjacking). Without `X-Content-Type-Options: nosniff`, browsers may execute untrusted files. Without a Content Security Policy, script injection risks increase.

#### Remediation Recommendation
Add standard security response headers in the FastAPI middleware:
```python
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "font-src 'self' https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self';"
    )
    return response
```

---

## Low Vulnerabilities

### `SEC-10`: Unbounded Synchronous Memory Consumption on Excel Export

- **Severity:** Low
- **Location:** [server.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/server.py#L371-L385) (`export_excel`)
- **Impact:** Repeated concurrent requests can trigger synchronous OpenPyXL workbook generation in memory, leading to high CPU usage and temporary event-loop blocking.

#### Evidence
```python
# server.py:374-377
jobs_res = db_instance.get_jobs(min_score=0, limit=500)
jobs = jobs_res.get("jobs", [])
reporter = ExcelReporter(config.REPORTS_DIR)
excel_path = reporter.generate_report(jobs, config.CANDIDATE_NAME)
```

#### Remediation Recommendation
Cache the generated file on disk or in memory with a short time-to-live (TTL, e.g. 5 minutes) rather than re-querying MongoDB and rebuilding the OpenPyXL workbook on every HTTP request.

---

### `SEC-11`: Unescaped Skill Tags & Potential Pseudo-Protocol in Frontend JS

- **Severity:** Low
- **Location:** [server.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/server.py#L1346-L1348), [server.py](file:///d:/ImpPDF/sarthakResume/crwaler_job/server.py#L1358)
- **Impact:** In the client-side JavaScript rendering function `renderJobCard`, skill strings `${s}` and contact URLs `${c.url}` are interpolated without escaping or URL scheme verification.

#### Evidence
```javascript
// server.py:1346
return `<span class="...">${s}</span>`;

// server.py:1358
<a href="${c.url}" target="_blank" ...>
```

#### Remediation Recommendation
1. Apply `escapeHtml(s)` to skill pills.
2. Validate that `c.url` starts with `https://` or `http://` before placing it inside an `<a href="...">` tag to prevent `javascript:` pseudo-protocol execution.

---

## Recommended Action Plan

To systematically remediate these findings while preserving normal developer workflows:

1. **Phase 1: Immediate Protection (Critical & High)**
   - Protect `/api/database/clear`, `/api/jobs/{job_id}/send-cold-email`, and `/api/trigger` behind an administrative authorization check (`X-Admin-Key` or `CRON_SECRET`).
   - Fix `database.py` search query by applying `re.escape()` to avoid ReDoS attacks.
   - Fix CORS by specifying trusted origins instead of wildcard with credentials.
2. **Phase 2: Output Encoding & Defense-in-Depth (Medium)**
   - Add `html.escape()` sanitization in `mailer.py` for email reports.
   - Attach standard HTTP security headers (`X-Frame-Options`, `X-Content-Type-Options`, `Content-Security-Policy`) in FastAPI middleware.
3. **Phase 3: Frontend & Resource Optimization (Low)**
   - Validate URL schemes in client-side link generators.
   - Cache generated Excel exports with a 5-minute TTL.

---
*Report generated in compliance with the `security-best-practices` specification.*

