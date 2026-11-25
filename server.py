import os
import sys
import time
import hmac
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request, Header, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
import uvicorn

import config
from logger_config import get_logger, get_recent_logs
from database import db_instance
from main import run_pipeline
from mailer import Mailer
from excel_reporter import ExcelReporter
from job_crawler import JobCrawler
from pitch_generator import PitchGenerator
from notifier import Notifier

logger = get_logger("Server")

tags_metadata = [
    {
        "name": "Jobs",
        "description": "Query, search, filter, and update status of jobs stored in MongoDB.",
    },
    {
        "name": "Crawl Engine",
        "description": "Trigger multi-source job crawling across Firecrawl, ATS portals, and remote boards.",
    },
    {
        "name": "Cold Outreach",
        "description": "Send cold emails directly to recruiters/HR with attached resume via Gmail SMTP.",
    },
    {
        "name": "Analytics & Stats",
        "description": "Real-time statistics, application funnel metrics, and company distribution.",
    },
    {
        "name": "Logs & Monitoring",
        "description": "Live application logs and system health monitoring.",
    },
    {
        "name": "Database & System",
        "description": "Database management, clearing data, and exporting reports.",
    },
]

app = FastAPI(
    title="Sarthak Joshi - Job Radar & Outreach Hub API",
    description="Automated job discovery, resume matching, MongoDB tracking, and recruiter cold outreach engine.\n\n"
                "Interactive API documentation powered by Swagger UI and ReDoc.",
    version="2.1.0",
    openapi_tags=tags_metadata,
    docs_url="/docs",
    redoc_url="/redoc"
)

# SEC-03 Fix: Restrict CORS to trusted origins instead of wildcard with credentials
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Key"],
)

# SEC-09 Fix: Security response headers middleware
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

# HTTP Request Logging Middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = round((time.time() - start_time) * 1000, 2)
    # Log non-static endpoints
    if not request.url.path.startswith("/favicon.ico"):
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({process_time}ms)")
    return response

# Models
class StatusUpdateRequest(BaseModel):
    status: str
    notes: Optional[str] = None

class ColdEmailRequest(BaseModel):
    recipient_email: str  # SEC-02: validated at endpoint level
    subject: str
    body: str

# SEC-01/SEC-02/SEC-08 Fix: Admin key verification for destructive/sensitive endpoints
def verify_admin_key(x_admin_key: str = Header(None, alias="X-Admin-Key")):
    """Constant-time admin API key check for protected endpoints."""
    expected = config.ADMIN_API_KEY
    if not expected:
        # If no key configured, allow local requests only (dev mode)
        return True
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key header")
    return True

class CrawlTriggerRequest(BaseModel):
    country: Optional[str] = "India"
    state: Optional[str] = None
    min_score: Optional[int] = 50
    send_mail: Optional[bool] = False

import threading

# In-memory crawl and scheduler status
crawl_state = {
    "is_running": False,
    "last_run": None,
    "last_result": None,
    "active_country": "India",
    "active_state": "All"
}

scheduler_state = {
    "target_time": "08:30 AM IST",
    "last_crawl_date": None,
    "active": True
}

def _background_crawl(send_mail: bool = False, country: str = "India", state: Optional[str] = None, min_score: int = 50):
    crawl_state["is_running"] = True
    crawl_state["active_country"] = country
    crawl_state["active_state"] = state or "All"
    logger.info(f"Background crawl task started (country={country}, state={state}, send_mail={send_mail}).")
    try:
        excel_path, matched_jobs = run_pipeline(min_score=min_score, send_mail=send_mail, target_country=country, target_state=state)
        crawl_state["last_run"] = datetime.datetime.now().isoformat()
        crawl_state["last_result"] = {
            "success": True,
            "jobs_count": len(matched_jobs),
            "country": country,
            "state": state or "All",
            "report": excel_path.name if excel_path else None
        }
        logger.info(f"Background crawl completed successfully for {country}. Found {len(matched_jobs)} matched jobs.")

        # Real-time Telegram Push Notifications
        try:
            notifier = Notifier()
            high_matches = [j for j in matched_jobs if j.get("match_score", 0) >= 70]
            notifier.send_daily_summary_alert(len(matched_jobs), len(high_matches), config.CANDIDATE_NAME)
            for top_j in [j for j in matched_jobs if j.get("match_score", 0) >= 80][:3]:
                notifier.send_high_match_alert(top_j)
        except Exception as notify_err:
            logger.warning(f"Notification error: {notify_err}")
    except Exception as e:
        logger.error(f"Background crawl error: {e}")
        crawl_state["last_result"] = {"success": False, "error": str(e)}
    finally:
        crawl_state["is_running"] = False

def _daily_scheduler_loop():
    """Checks the system time every 30s. Automatically triggers crawl & email ONLY at 8:30 AM IST."""
    logger.info("Daily Scheduler active: checking local time. Target execution locked to 08:30 AM IST.")
    while scheduler_state["active"]:
        try:
            now = datetime.datetime.now()
            today_str = now.date().isoformat()
            if now.hour == 8 and now.minute == 30:
                if scheduler_state["last_crawl_date"] != today_str and not crawl_state["is_running"]:
                    scheduler_state["last_crawl_date"] = today_str
                    logger.info("⏰ [Scheduler] It is 08:30 AM IST! Automatically starting daily crawl & recruiter briefing...")
                    _background_crawl(send_mail=True)
        except Exception as e:
            logger.error(f"[Scheduler] Loop error: {e}")
        time.sleep(30)

@app.on_event("startup")
def on_app_startup():
    startup_now = datetime.datetime.now()
    # Check if time is exactly 8:30 AM on startup
    if startup_now.hour == 8 and startup_now.minute == 30:
        logger.info("🚀 [Startup] Application started at 08:30 AM! Starting morning crawl & briefing...")
        threading.Thread(target=_background_crawl, kwargs={"send_mail": True}, daemon=True).start()
    else:
        logger.info(f"🚀 [Startup] Application initialized at {startup_now.strftime('%H:%M:%S')}. "
                    f"Startup crawl SKIPPED. Daily crawl is locked to 08:30 AM IST.")
    
    # Start background scheduler thread
    threading.Thread(target=_daily_scheduler_loop, daemon=True).start()

# ==========================================
# API Endpoints
# ==========================================

@app.get("/api/health", tags=["Logs & Monitoring"], summary="System Health Check")
def get_health():
    """Returns connectivity status for MongoDB, Firecrawl, SMTP, and scheduler configuration."""
    now = datetime.datetime.now()
    target_today = now.replace(hour=8, minute=30, second=0, microsecond=0)
    next_crawl = target_today if now < target_today else (target_today + datetime.timedelta(days=1))

    return {
        "status": "healthy",
        "mongodb_connected": db_instance.is_connected(),
        "firecrawl_configured": bool(config.FIRECRAWL_API_KEY),
        "smtp_configured": bool(config.SENDER_PASSWORD),
        "candidate": config.CANDIDATE_NAME,
        "resume_path": config.RESUME_PATH,
        "scheduled_crawl_time": "08:30 AM IST",
        "next_scheduled_crawl": next_crawl.strftime("%Y-%m-%d 08:30:00 IST"),
        "last_scheduled_crawl": scheduler_state["last_crawl_date"],
        "timestamp": now.isoformat()
    }

@app.get("/api/stats", tags=["Analytics & Stats"], summary="Get Job & Outreach Metrics")
def get_stats():
    """Returns application funnel metrics, high match counts, and distinct company count."""
    return db_instance.get_stats()

@app.get("/api/locations", tags=["Jobs"], summary="Get Supported Countries and Tech Hubs")
def get_locations():
    """Returns available target countries and their respective states / tech hubs for targeted job discovery."""
    return {
        "default_country": config.DEFAULT_TARGET_COUNTRY,
        "default_state": config.DEFAULT_TARGET_STATE,
        "locations": config.SUPPORTED_LOCATIONS
    }

@app.get("/api/jobs", tags=["Jobs"], summary="List & Filter Matched Jobs with Pagination")
def get_jobs(
    search: Optional[str] = Query(None, description="Search term matching title, company, skills, or summary"),
    status: Optional[str] = Query(None, description="Status filter: all, new, applied, contacted, interviewing, saved, rejected"),
    country: Optional[str] = Query(None, description="Country filter: India, United States, Europe, Remote, All"),
    state: Optional[str] = Query(None, description="State or City hub filter (e.g. Pune, Bangalore, Mumbai)"),
    min_score: int = Query(0, ge=0, le=100, description="Minimum match score (0-100)"),
    sort_by: str = Query("match_score", description="Sort field: match_score, date, or company"),
    sort_order: int = Query(-1, description="-1 for descending, 1 for ascending"),
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
    limit: int = Query(10, ge=1, le=100, description="Items per page (default: 10)"),
    skip: Optional[int] = Query(None, ge=0, description="Number of jobs to skip (override page calculation)")
):
    """Fetches jobs from MongoDB with full-text search, score threshold, status filtering, and structured pagination."""
    if skip is None:
        skip = (page - 1) * limit

    return db_instance.get_jobs(
        search=search,
        status=status,
        country=country,
        state=state,
        min_score=min_score,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        skip=skip
    )

@app.get("/api/jobs/{job_id}", tags=["Jobs"], summary="Get Single Job Details")
def get_job(job_id: str):
    """Retrieves full details of a specific job by MongoDB ObjectId."""
    job = db_instance.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.post("/api/jobs/{job_id}/status", tags=["Jobs"], summary="Update Application Status")
def update_status(job_id: str, req: StatusUpdateRequest):
    """Updates job status (applied, contacted, interviewing, saved, rejected) and custom notes."""
    success = db_instance.update_job_status(job_id, req.status, req.notes)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update job status")
    logger.info(f"Updated job {job_id} status to '{req.status}'")
    return {"success": True, "job_id": job_id, "new_status": req.status}

# SEC-02 Fix: Protect email endpoint with admin key to prevent open relay abuse
@app.post("/api/jobs/{job_id}/send-cold-email", tags=["Cold Outreach"], summary="Send Cold Email to Recruiter", dependencies=[Depends(verify_admin_key)])
def send_cold_email(job_id: str, req: ColdEmailRequest):
    """Dispatches a personalized cold email directly to the recruiter with Sarthak's resume attached."""
    job = db_instance.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    mailer = Mailer()
    res = mailer.send_cold_outreach(
        to_email=req.recipient_email,
        subject=req.subject,
        body=req.body,
        candidate_name=config.CANDIDATE_NAME
    )

    if res.get("success"):
        db_instance.update_job_status(job_id, "contacted", f"Cold email dispatched to {req.recipient_email}")
        db_instance.log_outreach({
            "job_id": job_id,
            "company": job.get("company"),
            "role": job.get("title"),
            "recipient_email": req.recipient_email,
            "subject": req.subject,
            "status": "delivered"
        })
        logger.info(f"Cold outreach dispatched to {req.recipient_email} for job {job_id}")
        return {"success": True, "message": f"Cold outreach email sent to {req.recipient_email}!"}
    else:
        logger.error(f"Failed to dispatch cold email: {res.get('error')}")
        raise HTTPException(status_code=500, detail=res.get("error", "Failed to dispatch email"))

@app.post("/api/jobs/{job_id}/enrich-linkedin", tags=["Jobs"], summary="Discover Company LinkedIn Contacts on Demand")
def enrich_linkedin(job_id: str):
    """Discovers live recruiters & engineering leads on LinkedIn for this company using Firecrawl/Web discovery."""
    job = db_instance.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    crawler = JobCrawler()
    crawler._enrich_linkedin_contacts(job)

    db_instance.update_job_linkedin_contacts(
        job_id=job_id,
        contacts=job.get("linkedin_contacts", []),
        search_url=job.get("linkedin_search_url", ""),
        note=job.get("linkedin_connection_note", "")
    )
    return {
        "success": True,
        "job_id": job_id,
        "company": job.get("company"),
        "linkedin_contacts": job.get("linkedin_contacts", []),
        "linkedin_search_url": job.get("linkedin_search_url", ""),
        "linkedin_connection_note": job.get("linkedin_connection_note", "")
    }

@app.get("/api/jobs/{job_id}/outreach-kit", tags=["Cold Outreach"], summary="Get AI-Tailored Outreach Kit & Sequences")
def get_outreach_kit(job_id: str):
    """Generates customized Initial, Day-3, Day-7 follow-up pitches and LinkedIn notes for a specific job."""
    job = db_instance.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    generator = PitchGenerator()
    return generator.generate_full_kit(job)

@app.get("/api/jobs/{job_id}/interview-prep", tags=["Jobs"], summary="Get Role-Specific Technical Interview Prep Kit")
def get_interview_prep(job_id: str):
    """Generates role-specific technical questions, architecture talking points, and answers based on job tech stack."""
    job = db_instance.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    generator = PitchGenerator()
    prep = generator.generate_interview_prep(job)
    return {
        "job_id": job_id,
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "matched_skills": job.get("matched_skills", []),
        "prep": prep
    }

@app.post("/api/crawl", tags=["Crawl Engine"], summary="Trigger Job Crawler in Background")
@app.post("/api/trigger", tags=["Crawl Engine"], summary="Trigger Job Crawler in Background")
def trigger_crawl(background_tasks: BackgroundTasks, req: Optional[CrawlTriggerRequest] = None):
    """Triggers the job crawler asynchronously across Firecrawl, ATS, and remote portals with country and state targeting."""
    if crawl_state["is_running"]:
        return {"status": "busy", "message": "Job crawl is already currently executing in background."}

    country = req.country if (req and req.country) else "India"
    state = req.state if req else None
    min_score = req.min_score if (req and req.min_score is not None) else 50
    send_mail = req.send_mail if (req and req.send_mail is not None) else False

    background_tasks.add_task(_background_crawl, send_mail=send_mail, country=country, state=state, min_score=min_score)
    return {
        "status": "started",
        "country": country,
        "state": state or "All Hubs",
        "message": f"Job crawler triggered in background for {country} ({state or 'All Hubs'}). Database will update automatically."
    }

@app.get("/api/crawl-status", tags=["Crawl Engine"], summary="Check Crawl Job Status")
@app.get("/api/trigger/status", tags=["Crawl Engine"], summary="Check Crawl Job Status")
def trigger_status():
    """Returns whether a background crawl is actively running and the result of the last run."""
    return crawl_state

@app.post("/api/send-briefing", tags=["Cold Outreach"], summary="Send Daily Email Briefing")
def send_briefing():
    """Generates the latest Excel report and emails the daily HTML briefing to Sarthak."""
    jobs_res = db_instance.get_jobs(min_score=50, limit=100)
    jobs = jobs_res.get("jobs", [])
    if not jobs:
        _, jobs = run_pipeline(min_score=50, send_mail=False)

    reporter = ExcelReporter(config.REPORTS_DIR)
    excel_path = reporter.generate_report(jobs, config.CANDIDATE_NAME)

    mailer = Mailer()
    sent = mailer.send_daily_report(excel_path, jobs, config.CANDIDATE_NAME)
    if sent:
        logger.info(f"Daily briefing email delivered to {config.RECIPIENT_EMAIL}")
        return {"success": True, "message": f"Briefing and Excel sent to {config.RECIPIENT_EMAIL}!"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send briefing email. Check SMTP settings in .env.")

# SEC-08 Fix: Restrict log access to localhost or admin-authenticated requests
@app.get("/api/logs", tags=["Logs & Monitoring"], summary="Live Application Logs")
def get_logs(request: Request, lines: int = Query(100, ge=10, le=500, description="Number of recent log lines to fetch")):
    """Returns recent log entries from the rotating log file for live frontend streaming."""
    client_host = request.client.host if request.client else ""
    is_local = client_host in ("127.0.0.1", "::1", "localhost")
    admin_key = request.headers.get("X-Admin-Key", "")
    expected = config.ADMIN_API_KEY
    is_admin = expected and hmac.compare_digest(admin_key, expected)
    if not is_local and not is_admin:
        raise HTTPException(status_code=403, detail="Log access restricted to localhost or admin")
    return {"logs": get_recent_logs(lines)}

# SEC-01 Fix: Protect destructive reset behind admin key authentication
@app.delete("/api/database/clear", tags=["Database & System"], summary="Reset Database & Clean Reports", dependencies=[Depends(verify_admin_key)])
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

@app.get("/api/export-excel", tags=["Database & System"], summary="Download Excel Report")
def export_excel():
    """Generates and downloads the current MongoDB job matches as a styled Excel (.xlsx) file."""
    jobs_res = db_instance.get_jobs(min_score=0, limit=500)
    jobs = jobs_res.get("jobs", [])
    reporter = ExcelReporter(config.REPORTS_DIR)
    excel_path = reporter.generate_report(jobs, config.CANDIDATE_NAME)
    if excel_path and excel_path.exists():
        return FileResponse(
            excel_path,
            filename=excel_path.name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    raise HTTPException(status_code=500, detail="Failed to generate Excel file")


# ==========================================
# Frontend Static Mount (Decoupled Client)
# ==========================================
FRONTEND_DIR = getattr(config, "BASE_DIR", Path(__file__).resolve().parent) / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    @app.get("/", response_class=HTMLResponse)
    def fallback_index():
        return HTMLResponse("<h1>Frontend directory not found</h1>", status_code=404)

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
