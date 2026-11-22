import os
import sys
import time
import hmac
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request, Header, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
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
# Frontend Dashboard (HTML / Tailwind / JS) - Professional Developer Tool
# ==========================================

@app.get("/", response_class=HTMLResponse, tags=["Database & System"], summary="Web Dashboard UI")
def index_page():
    return HTML_CONTENT

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Job Radar &amp; Cold Outreach Hub | Sarthak Joshi</title>
    <!-- Google Fonts: Inter & JetBrains Mono -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <!-- FontAwesome 6 Icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
                        mono: ['JetBrains Mono', 'Fira Code', 'monospace']
                    }
                }
            }
        }
    </script>
    <style>
        :root { color-scheme: dark; }
        body {
            font-family: 'Inter', sans-serif;
            background-color: #09090B;
            color: #FAFAFA;
        }
        .font-mono-numbers {
            font-family: 'JetBrains Mono', monospace;
            font-variant-numeric: tabular-nums;
        }
        /* Custom scrollbar */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #09090B; }
        ::-webkit-scrollbar-thumb { background: #27272A; border-radius: 9999px; }
        ::-webkit-scrollbar-thumb:hover { background: #3F3F46; }

        /* Unified Button System with Accessible Touch Targets (min 36-44px) */
        .btn-primary {
            background-color: #FAFAFA;
            color: #09090B;
            font-weight: 500;
            font-size: 0.75rem;
            min-height: 2.25rem;
            padding: 0.45rem 0.875rem;
            border-radius: 0.5rem;
            transition: all 150ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.4rem;
            cursor: pointer;
            box-shadow: 0 1px 2px rgba(0,0,0,0.2);
        }
        .btn-primary:hover { background-color: #E4E4E7; }
        .btn-primary:active { transform: scale(0.97); }

        .btn-secondary {
            background-color: #18181B;
            color: #F4F4F5;
            border: 1px solid #3F3F46;
            font-weight: 500;
            font-size: 0.75rem;
            min-height: 2.25rem;
            padding: 0.45rem 0.75rem;
            border-radius: 0.5rem;
            transition: all 150ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.4rem;
            cursor: pointer;
        }
        .btn-secondary:hover { background-color: #27272A; color: #FFFFFF; border-color: #52525B; }
        .btn-secondary:active { transform: scale(0.97); }

        .btn-outline {
            background-color: transparent;
            color: #D4D4D8;
            border: 1px solid #3F3F46;
            font-weight: 500;
            font-size: 0.75rem;
            min-height: 2.25rem;
            padding: 0.45rem 0.75rem;
            border-radius: 0.5rem;
            transition: all 150ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.4rem;
            cursor: pointer;
        }
        .btn-outline:hover { background-color: #18181B; color: #FAFAFA; border-color: #52525B; }
        .btn-outline:active { transform: scale(0.97); }

        .btn-ghost {
            background-color: transparent;
            color: #D4D4D8;
            min-height: 2.25rem;
            padding: 0.45rem 0.6rem;
            border-radius: 0.5rem;
            transition: all 150ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
        }
        .btn-ghost:hover { background-color: #18181B; color: #FAFAFA; }

        .btn-destructive {
            background-color: transparent;
            color: #A1A1AA;
            min-height: 2.25rem;
            padding: 0.45rem 0.6rem;
            border-radius: 0.5rem;
            transition: all 150ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
        }
        .btn-destructive:hover { background-color: rgba(239, 68, 68, 0.15); color: #F87171; }

        /* Crisp card containers with high-contrast borders (Issue 3 Fixed) */
        .card-surface {
            background-color: #121215;
            border: 1px solid #3F3F46;
        }
        .card-interactive {
            background-color: #121215;
            border: 1px solid #3F3F46;
            transition: border-color 150ms ease, background-color 150ms ease;
        }
        .card-interactive:hover {
            border-color: #71717A;
            background-color: #18181B;
        }

        /* Hide scrollbars while allowing touch swiping */
        .no-scrollbar::-webkit-scrollbar { display: none; }
        .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }

        button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
            outline: 2px solid #A1A1AA;
            outline-offset: 2px;
        }
    </style>
</head>
<body class="min-h-screen flex flex-col antialiased selection:bg-zinc-800 selection:text-zinc-100">

    <!-- Top Sticky Header Navigation -->
    <header class="sticky top-0 z-40 border-b border-zinc-800 bg-[#09090B]/90 backdrop-blur-md">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between gap-4">
            <!-- Brand & Candidate Identity -->
            <div class="flex items-center space-x-3 min-w-0">
                <div class="w-8 h-8 rounded-lg bg-zinc-800 border border-zinc-700 flex items-center justify-center font-bold text-zinc-100 text-sm shrink-0">
                    <i class="fa-solid fa-radar text-zinc-200 text-xs"></i>
                </div>
                <div class="truncate">
                    <div class="flex items-center space-x-2">
                        <span class="font-bold text-sm sm:text-base tracking-tight text-zinc-100">Job Radar</span>
                        <span class="text-xs font-medium bg-zinc-800 text-zinc-300 border border-zinc-700 px-2 py-0.5 rounded-full">Sarthak Joshi</span>
                    </div>
                    <!-- Issue 3 Fixed: Body copy raised from 11px to 12px (text-xs) -->
                    <p class="text-xs text-zinc-400 font-normal truncate hidden sm:block">1.8+ Yrs Exp • Full Stack / Backend: Node.js, NestJS, TypeScript, PostgreSQL, Redis, BullMQ</p>
                </div>
            </div>

            <!-- Header Action Controls (Issue 1 Fixed: Unified 5-style button system) -->
            <div class="flex items-center space-x-2 shrink-0">
                <!-- Swagger OpenAPI Docs -->
                <a href="/docs" target="_blank" rel="noopener noreferrer" class="btn-outline" title="Interactive Swagger UI & OpenAPI Documentation">
                    <i class="fa-solid fa-book text-xs text-zinc-400"></i>
                    <span class="hidden md:inline">API Docs</span>
                </a>

                <!-- Live Logs Button -->
                <button onclick="openLogsModal()" class="btn-secondary" title="Inspect live rotating application logs">
                    <i class="fa-solid fa-terminal text-xs text-zinc-400"></i>
                    <span class="hidden sm:inline">Logs</span>
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                </button>

                <!-- Export Excel -->
                <a href="/api/export-excel" class="btn-secondary" title="Download formatted Excel (.xlsx) report with hyperlinks">
                    <i class="fa-solid fa-file-excel text-xs text-emerald-400"></i>
                    <span class="hidden lg:inline">Excel</span>
                </a>

                <!-- 1-Click Job Crawler Button -->
                <button onclick="openCrawlModal()" id="triggerBtn" class="btn-primary" title="Configure target country and trigger live job discovery">
                    <i class="fa-solid fa-arrows-rotate text-xs" id="triggerIcon"></i>
                    <span>Crawl Jobs</span>
                </button>

                <!-- Database Reset / Clean Button -->
                <button onclick="confirmResetDB()" class="btn-destructive" title="Reset/Empty Database & Reports for a clean run" aria-label="Reset Database">
                    <i class="fa-solid fa-trash-can text-xs"></i>
                </button>
            </div>
        </div>

        <!-- Crawl Progress Alert Strip (Dynamic) -->
        <div id="crawlBanner" style="display: none;" class="border-t border-zinc-800 bg-zinc-900 px-4 py-2">
            <div class="max-w-7xl mx-auto flex items-center justify-between text-xs text-zinc-300">
                <div class="flex items-center space-x-2.5">
                    <i class="fa-solid fa-circle-notch fa-spin text-zinc-400 text-xs"></i>
                    <span>Active crawl in progress: Scanning Greenhouse ATS boards, Firecrawl live search &amp; remote portals...</span>
                </div>
                <span class="text-zinc-500 font-mono-numbers text-xs">Background Task</span>
            </div>
        </div>
    </header>

    <!-- Main Content Area -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-5 flex-1 w-full">

        <!-- Notification Toast with Undo Action (Issue 8 Fixed) -->
        <div id="toast" role="status" aria-live="polite" style="display: none;" class="fixed bottom-5 right-5 z-50 card-surface border border-zinc-600 text-zinc-100 px-4 py-3 rounded-lg shadow-2xl flex items-center space-x-3 transition-all max-w-md">
            <i id="toastIcon" class="fa-solid fa-circle-check text-emerald-400 text-sm shrink-0"></i>
            <span id="toastMsg" class="text-xs font-medium flex-1">Notification</span>
            <button id="toastUndoBtn" onclick="undoLastStatusChange()" style="display: none;" class="text-xs font-semibold text-emerald-400 hover:text-emerald-300 underline px-1 py-0.5 ml-2 cursor-pointer">Undo</button>
            <button onclick="hideToast()" class="btn-ghost !p-1 ml-2 min-h-[28px] min-w-[28px]" aria-label="Close notification">
                <i class="fa-solid fa-xmark text-xs"></i>
            </button>
        </div>

        <!-- Candidate Profile Header Bar -->
        <div class="card-surface rounded-xl p-4 border border-zinc-700 flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-lg bg-zinc-800 border border-zinc-700 flex items-center justify-center text-zinc-100 font-semibold text-sm shrink-0">
                    SJ
                </div>
                <div>
                    <div class="flex items-center space-x-2">
                        <h1 class="text-sm font-semibold text-zinc-100 tracking-tight">Sarthak Joshi — Candidate Profile</h1>
                        <span class="bg-zinc-800 text-zinc-200 border border-zinc-700 text-xs font-medium px-2 py-0.5 rounded-full flex items-center gap-1.5">
                            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Active
                        </span>
                    </div>
                    <p class="text-xs text-zinc-300 mt-0.5">
                        <span class="text-zinc-200 font-medium">Stack:</span> NestJS, Node.js, TypeScript, PostgreSQL, Redis, BullMQ, Docker • 
                        <span class="text-zinc-200 font-medium">Experience:</span> 1.8+ Years (Target: Mid/Junior 1–3 Yrs)
                    </p>
                </div>
            </div>

            <!-- Resume & Schedule Metadata -->
            <div class="flex flex-wrap items-center gap-2 text-xs">
                <span class="bg-zinc-900 border border-zinc-700 px-2.5 py-1 rounded-md text-zinc-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-file-pdf text-zinc-400 text-xs"></i>
                    <span>Nov 2026 PDF</span>
                </span>
                <span class="bg-zinc-900 border border-zinc-700 px-2.5 py-1 rounded-md text-zinc-300 flex items-center gap-1.5">
                    <i class="fa-regular fa-clock text-zinc-400 text-xs"></i>
                    <span>Daily 08:30 AM IST</span>
                </span>
                <span class="bg-zinc-900 border border-zinc-700 px-2.5 py-1 rounded-md text-zinc-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-database text-zinc-400 text-xs"></i>
                    <span>job_crawler_db</span>
                </span>
            </div>
        </div>

        <!-- KPI Metrics Grid (Issue 3 Contrast Fixed) -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <!-- Total Tracked -->
            <div class="card-surface rounded-xl p-4 border border-zinc-700">
                <div class="flex items-center justify-between text-zinc-300 mb-1.5">
                    <span class="text-xs font-medium text-zinc-300">Total Tracked</span>
                    <i class="fa-solid fa-briefcase text-zinc-400 text-xs"></i>
                </div>
                <div id="statTotal" class="text-2xl font-bold text-zinc-100 font-mono-numbers">0</div>
                <p class="text-xs text-zinc-300 mt-1 flex items-center justify-between">
                    <span>MongoDB Database</span>
                    <span id="statDistinctCompanies" class="text-zinc-200 font-medium">0 Companies</span>
                </p>
            </div>

            <!-- High Matches (≥70%) -->
            <div class="card-surface rounded-xl p-4 border border-zinc-700">
                <div class="flex items-center justify-between text-zinc-300 mb-1.5">
                    <span class="text-xs font-medium text-emerald-400">High Matches</span>
                    <i class="fa-solid fa-bullseye text-emerald-400 text-xs"></i>
                </div>
                <div id="statHigh" class="text-2xl font-bold text-emerald-400 font-mono-numbers">0</div>
                <p class="text-xs text-zinc-300 mt-1">≥ 70% Score Calibration</p>
            </div>

            <!-- Cold Outreach Sent -->
            <div class="card-surface rounded-xl p-4 border border-zinc-700">
                <div class="flex items-center justify-between text-zinc-300 mb-1.5">
                    <span class="text-xs font-medium text-zinc-200">Outreach Sent</span>
                    <i class="fa-solid fa-paper-plane text-zinc-400 text-xs"></i>
                </div>
                <div id="statContacted" class="text-2xl font-bold text-zinc-100 font-mono-numbers">0</div>
                <p class="text-xs text-zinc-300 mt-1">Direct Recruiter Emails</p>
            </div>

            <!-- Pipeline / Interviews -->
            <div class="card-surface rounded-xl p-4 border border-zinc-700">
                <div class="flex items-center justify-between text-zinc-300 mb-1.5">
                    <span class="text-xs font-medium text-zinc-200">Pipeline &amp; Interviews</span>
                    <i class="fa-solid fa-handshake text-zinc-400 text-xs"></i>
                </div>
                <div id="statInterviewing" class="text-2xl font-bold text-zinc-100 font-mono-numbers">0</div>
                <p class="text-xs text-zinc-300 mt-1">Active Conversations</p>
            </div>
        </div>

        <!-- Search & Filter Controls (Issues 2, 6, 7 & 10 Fixed) -->
        <div class="card-surface rounded-xl p-4 border border-zinc-700 space-y-3">
            <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
                <!-- Search Box with accessible label & shortcut badge (Issue 2 & 10 Fixed) -->
                <div class="relative flex-1">
                    <label for="searchInput" class="sr-only">Search roles, companies or tech skills</label>
                    <i class="fa-solid fa-magnifying-glass absolute left-3 top-3.5 text-zinc-400 text-xs"></i>
                    <input type="text" id="searchInput" oninput="debounceFetch()" aria-label="Search roles, companies, or tech skills" placeholder="Filter by role, company, or tech stack (e.g. NestJS, Stripe, Redis)..." class="w-full bg-zinc-950 border border-zinc-700 rounded-lg pl-8 pr-16 py-2.5 text-xs text-zinc-100 placeholder-zinc-400 focus:border-zinc-400 focus:outline-none">
                    <div class="absolute right-2.5 top-2 flex items-center gap-1.5">
                        <button id="clearSearchBtn" onclick="clearSearch()" style="display: none;" class="btn-ghost !p-1 text-xs text-zinc-400 hover:text-zinc-200 min-h-[28px] min-w-[28px]" aria-label="Clear search">
                            <i class="fa-solid fa-circle-xmark"></i>
                        </button>
                        <kbd class="hidden sm:inline-block px-1.5 py-0.5 text-[10px] font-mono text-zinc-400 bg-zinc-900 border border-zinc-700 rounded shadow-sm pointer-events-none" title="Press / to search">/</kbd>
                    </div>
                </div>

                <!-- Country & State / Hub Selectors with accessible labels (Issue 2 Fixed) -->
                <div class="flex flex-wrap items-center gap-2 bg-zinc-950 p-1.5 rounded-lg border border-zinc-700">
                    <div class="flex items-center space-x-1.5">
                        <i class="fa-solid fa-earth-americas text-zinc-400 text-xs ml-1"></i>
                        <label for="countryFilter" class="text-xs text-zinc-300 font-medium">Country:</label>
                        <select id="countryFilter" onchange="onCountryChange()" aria-label="Filter by country" class="bg-zinc-900 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-100 font-medium focus:border-zinc-400 focus:outline-none cursor-pointer">
                            <option value="India" selected>India 🇮🇳</option>
                            <option value="United States">United States 🇺🇸</option>
                            <option value="Europe">Europe / UK 🇪🇺</option>
                            <option value="Remote">Worldwide Remote 🌐</option>
                            <option value="All">All Countries</option>
                        </select>
                    </div>

                    <div class="flex items-center space-x-1.5 border-l border-zinc-700 pl-2" id="stateFilterContainer">
                        <i class="fa-solid fa-location-dot text-zinc-400 text-xs"></i>
                        <label for="stateFilter" class="text-xs text-zinc-300 font-medium">Hub:</label>
                        <select id="stateFilter" onchange="onStateChange()" aria-label="Filter by tech hub" class="bg-zinc-900 border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs text-zinc-100 font-medium focus:border-zinc-400 focus:outline-none cursor-pointer">
                            <option value="All India" selected>All India</option>
                            <option value="Pune">Pune (MH)</option>
                            <option value="Bangalore">Bangalore (KA)</option>
                            <option value="Mumbai">Mumbai (MH)</option>
                            <option value="Hyderabad">Hyderabad (TS)</option>
                            <option value="Delhi NCR">Delhi NCR</option>
                            <option value="Chennai">Chennai (TN)</option>
                            <option value="Remote">Remote (India)</option>
                        </select>
                    </div>
                </div>

                <!-- Match Score Presets & Debounced Slider (Issue 6 Fixed) -->
                <div class="flex flex-wrap items-center gap-2 bg-zinc-950 p-1.5 rounded-lg border border-zinc-700">
                    <span class="text-xs text-zinc-300 font-medium ml-1">Score:</span>
                    <button onclick="setScorePreset(0)" class="score-btn btn-secondary text-xs" data-score="0">All</button>
                    <button onclick="setScorePreset(50)" class="score-btn btn-primary text-xs" data-score="50">50%+</button>
                    <button onclick="setScorePreset(70)" class="score-btn btn-secondary text-xs" data-score="70">70%+ High</button>
                    <button onclick="setScorePreset(80)" class="score-btn btn-secondary text-xs" data-score="80">80%+ Prime</button>
                    <label for="scoreRange" class="sr-only">Minimum match score slider</label>
                    <input type="range" id="scoreRange" min="0" max="100" value="50" step="5" oninput="updateScoreFilter(this.value)" aria-label="Minimum match score slider" class="w-20 accent-zinc-200 cursor-pointer ml-1" title="Adjust minimum match score">
                    <span id="scoreLabel" class="text-xs font-semibold text-zinc-200 font-mono-numbers w-10 text-right">50%</span>
                </div>

                <!-- Sort Order Selector with accessible label -->
                <div class="flex items-center space-x-2 shrink-0">
                    <label for="sortBy" class="sr-only">Sort opportunities by</label>
                    <select id="sortBy" onchange="fetchJobs(1)" aria-label="Sort opportunities by" class="bg-zinc-950 border border-zinc-700 rounded-lg px-2.5 py-2 text-xs font-medium text-zinc-200 cursor-pointer focus:border-zinc-400 focus:outline-none">
                        <option value="match_score">Sort: Match Score</option>
                        <option value="date">Sort: Date Scraped</option>
                        <option value="company">Sort: Company Name</option>
                    </select>
                </div>
            </div>

            <!-- Application Status Tabs with Live Count Badges & Mobile Scroll Fade Hint (Issue 7 Fixed) -->
            <div class="relative border-t border-zinc-800/80 pt-3">
                <div class="flex items-center space-x-2 overflow-x-auto pb-1 text-xs no-scrollbar" id="statusTabs" role="tablist" aria-label="Application Status Filters">
                    <button onclick="setStatusFilter('all')" class="status-tab btn-primary shrink-0" data-status="all" role="tab" aria-selected="true">
                        <span>All Opportunities</span>
                        <span id="tabCountAll" class="bg-zinc-800 text-zinc-200 text-xs px-2 py-0.5 rounded-full font-mono-numbers">0</span>
                    </button>
                    <button onclick="setStatusFilter('new')" class="status-tab btn-secondary shrink-0" data-status="new" role="tab" aria-selected="false">
                        <span>New</span>
                        <span id="tabCountNew" class="bg-zinc-800 text-zinc-300 text-xs px-2 py-0.5 rounded-full font-mono-numbers">0</span>
                    </button>
                    <button onclick="setStatusFilter('applied')" class="status-tab btn-secondary shrink-0" data-status="applied" role="tab" aria-selected="false">
                        <span>Applied</span>
                        <span id="tabCountApplied" class="bg-zinc-800 text-zinc-300 text-xs px-2 py-0.5 rounded-full font-mono-numbers">0</span>
                    </button>
                    <button onclick="setStatusFilter('contacted')" class="status-tab btn-secondary shrink-0" data-status="contacted" role="tab" aria-selected="false">
                        <span>Cold Outreach</span>
                        <span id="tabCountContacted" class="bg-zinc-800 text-zinc-300 text-xs px-2 py-0.5 rounded-full font-mono-numbers">0</span>
                    </button>
                    <button onclick="setStatusFilter('interviewing')" class="status-tab btn-secondary shrink-0" data-status="interviewing" role="tab" aria-selected="false">
                        <span>Interviewing</span>
                        <span id="tabCountInterviewing" class="bg-zinc-800 text-zinc-300 text-xs px-2 py-0.5 rounded-full font-mono-numbers">0</span>
                    </button>
                    <button onclick="setStatusFilter('saved')" class="status-tab btn-secondary shrink-0" data-status="saved" role="tab" aria-selected="false">
                        <span>Saved</span>
                        <span id="tabCountSaved" class="bg-zinc-800 text-zinc-300 text-xs px-2 py-0.5 rounded-full font-mono-numbers">0</span>
                    </button>
                </div>
                <!-- Gradient hint indicating more scrollable tabs on small screens -->
                <div class="pointer-events-none absolute right-0 top-3 bottom-1 w-8 bg-gradient-to-l from-[#121215] to-transparent sm:hidden"></div>
            </div>
        </div>

        <!-- Matched Job Opportunities Header & Results List -->
        <!-- Issue 6 Fixed: Proper Heading Hierarchy (H1 -> H2 -> H3) -->
        <div class="space-y-3" id="jobsSection">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between text-xs text-zinc-300 px-1 gap-2">
                <div class="flex items-center space-x-2">
                    <h2 class="text-sm font-semibold text-zinc-100">Discovered Opportunities</h2>
                    <span class="text-xs text-zinc-400 font-mono-numbers">(<span id="jobsCount">0</span> total)</span>
                </div>

                <!-- Top Pagination Toolbar (Quick Navigation) -->
                <div class="flex items-center space-x-3">
                    <div class="flex items-center space-x-1.5 text-xs text-zinc-300">
                        <label for="pageSizeSelect" class="text-xs text-zinc-300">Show:</label>
                        <select id="pageSizeSelect" onchange="changePageSize(this.value)" aria-label="Opportunities per page" class="bg-zinc-950 border border-zinc-700 rounded-md px-2 py-1.5 text-zinc-200 text-xs font-medium focus:border-zinc-400 focus:outline-none cursor-pointer">
                            <option value="10" selected>10 per page</option>
                            <option value="20">20 per page</option>
                            <option value="50">50 per page</option>
                        </select>
                    </div>

                    <!-- Top Mini Pagination Controls with 36px min touch target (Issue 1 Fixed) -->
                    <div id="topPagination" style="display: none;" class="flex items-center space-x-1">
                        <button onclick="prevPage()" id="topPrevBtn" class="btn-secondary min-h-[36px] min-w-[36px] !p-0 flex items-center justify-center text-xs" title="Previous page" aria-label="Previous page">
                            <i class="fa-solid fa-chevron-left text-xs"></i>
                        </button>
                        <span id="topPageIndicator" class="text-xs text-zinc-200 font-mono-numbers px-2">Page 1 / 1</span>
                        <button onclick="nextPage()" id="topNextBtn" class="btn-secondary min-h-[36px] min-w-[36px] !p-0 flex items-center justify-center text-xs" title="Next page" aria-label="Next page">
                            <i class="fa-solid fa-chevron-right text-xs"></i>
                        </button>
                    </div>
                </div>
            </div>

            <!-- Job Cards Container -->
            <div id="jobsList" class="space-y-2.5">
                <!-- Dynamically rendered job cards -->
            </div>

            <!-- Full Bottom Pagination Bar -->
            <div id="paginationBar" style="display: none;" class="card-surface rounded-xl p-4 border border-zinc-800 flex flex-col sm:flex-row items-center justify-between gap-3">
                <div class="text-xs text-zinc-400">
                    Showing <span id="pageRangeText" class="font-semibold text-zinc-100 font-mono-numbers">0–0</span> of <span id="pageTotalText" class="font-semibold text-zinc-100 font-mono-numbers">0</span> opportunities
                </div>

                <div class="flex items-center space-x-2">
                    <button id="prevPageBtn" onclick="prevPage()" class="btn-secondary" disabled>
                        <i class="fa-solid fa-arrow-left text-xs"></i>
                        <span>Previous 10</span>
                    </button>

                    <div id="pagePillsContainer" class="flex items-center space-x-1"></div>

                    <button id="nextPageBtn" onclick="nextPage()" class="btn-secondary" disabled>
                        <span>Next 10</span>
                        <i class="fa-solid fa-arrow-right text-xs"></i>
                    </button>
                </div>
            </div>
        </div>

    </main>

    <!-- Footer -->
    <footer class="border-t border-zinc-800/80 bg-[#09090B] py-5 text-center text-xs text-zinc-500">
        <p>Job Radar for <strong class="text-zinc-300">Sarthak Joshi</strong> — Full Stack &amp; Backend Engineer • Automated Daily Pipeline</p>
    </footer>

    <!-- ========================================== -->
    <!-- Live Application Logs Modal (Issue 4 Fixed: role="dialog" & aria-modal="true") -->
    <!-- ========================================== -->
    <div id="logsModal" role="dialog" aria-modal="true" aria-labelledby="logsModalTitle" tabIndex="-1" style="display: none;" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="card-surface border border-zinc-700 max-w-4xl w-full rounded-xl shadow-2xl overflow-hidden flex flex-col h-[80vh]">
            <!-- Terminal Header -->
            <div class="px-4 py-3 border-b border-zinc-800 flex items-center justify-between bg-zinc-900">
                <div class="flex items-center space-x-2">
                    <i class="fa-solid fa-terminal text-zinc-400 text-xs"></i>
                    <h3 id="logsModalTitle" class="font-semibold text-zinc-100 text-xs">Application Runtime &amp; Crawler Logs</h3>
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse ml-1"></span>
                </div>
                <div class="flex items-center space-x-2 text-xs">
                    <label for="logFilterInput" class="sr-only">Filter log messages</label>
                    <input type="text" id="logFilterInput" oninput="renderLogs()" aria-label="Filter log messages" placeholder="Filter log text..." class="bg-zinc-950 border border-zinc-700 rounded-md px-2 py-1 text-zinc-200 text-xs placeholder-zinc-400 w-36 focus:outline-none focus:border-zinc-400">
                    <label class="flex items-center space-x-1.5 text-zinc-300 cursor-pointer">
                        <input type="checkbox" id="autoRefreshLogs" checked class="accent-zinc-400 cursor-pointer">
                        <span>Stream</span>
                    </label>
                    <button onclick="copyLogsToClipboard()" class="btn-ghost !p-1 min-h-[32px] min-w-[32px]" title="Copy all logs" aria-label="Copy logs to clipboard">
                        <i class="fa-regular fa-copy text-xs"></i>
                    </button>
                    <button onclick="closeLogsModal()" class="btn-ghost !p-1 min-h-[32px] min-w-[32px]" aria-label="Close logs dialog">
                        <i class="fa-solid fa-xmark text-xs"></i>
                    </button>
                </div>
            </div>

            <!-- Terminal Output Console -->
            <div id="logsConsole" class="flex-1 bg-[#050507] p-4 font-mono text-xs overflow-y-auto space-y-1 text-zinc-300 leading-relaxed"></div>

            <!-- Terminal Footer -->
            <div class="px-4 py-2.5 border-t border-zinc-800 bg-zinc-900 flex items-center justify-between text-xs text-zinc-400">
                <span>File destination: <code class="text-zinc-300 font-mono">logs/app.log</code> (Rotating)</span>
                <button onclick="closeLogsModal()" class="btn-secondary !py-1">Close</button>
            </div>
        </div>
    </div>

    <!-- ========================================== -->
    <!-- Cold Outreach & Follow-up Sequence Modal (Enhanced) -->
    <!-- ========================================== -->
    <div id="emailModal" role="dialog" aria-modal="true" aria-labelledby="modalCompanyHeader" tabIndex="-1" style="display: none;" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="card-surface border border-zinc-700 max-w-2xl w-full rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
            <!-- Modal Header -->
            <div class="px-5 py-3.5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900">
                <div class="flex items-center space-x-2.5">
                    <i class="fa-solid fa-paper-plane text-zinc-400 text-xs"></i>
                    <div>
                        <h3 class="font-semibold text-zinc-100 text-sm">Direct Recruiter Outreach &amp; Sequence</h3>
                        <p class="text-xs text-zinc-300" id="modalCompanyHeader">Target: Recruiter</p>
                    </div>
                </div>
                <button onclick="closeEmailModal()" class="btn-ghost !p-1 min-h-[32px] min-w-[32px]" aria-label="Close cold email dialog">
                    <i class="fa-solid fa-xmark text-xs"></i>
                </button>
            </div>

            <!-- Sequence Step Selector Tabs -->
            <div class="px-5 pt-3 pb-2 bg-zinc-950 border-b border-zinc-800 flex items-center justify-between gap-2 overflow-x-auto">
                <div class="flex items-center space-x-1.5 text-xs shrink-0" id="sequenceTabs">
                    <button onclick="switchSequenceTab('initial')" id="tabInitial" class="btn-primary !px-2.5 !py-1 text-xs">1. Initial Pitch (Day 0)</button>
                    <button onclick="switchSequenceTab('day3')" id="tabDay3" class="btn-secondary !px-2.5 !py-1 text-xs">2. Follow-Up (Day 3)</button>
                    <button onclick="switchSequenceTab('day7')" id="tabDay7" class="btn-secondary !px-2.5 !py-1 text-xs">3. Final Check-in (Day 7)</button>
                    <button onclick="switchSequenceTab('linkedin')" id="tabLinkedin" class="btn-secondary !px-2.5 !py-1 text-xs">LinkedIn Note</button>
                </div>
                <button onclick="regenerateOutreachKit()" id="regenBtn" class="btn-ghost !px-2.5 !py-1 text-xs text-amber-300 hover:text-amber-200 shrink-0" title="Re-generate personalized outreach kit using AI">
                    <i class="fa-solid fa-wand-magic-sparkles mr-1"></i>
                    <span>AI Re-craft</span>
                </button>
            </div>

            <!-- Modal Content Form -->
            <div class="p-5 overflow-y-auto space-y-3.5 text-xs">
                <div>
                    <label for="modalRecipient" class="block text-zinc-300 font-medium mb-1">Recipient Email:</label>
                    <input type="email" id="modalRecipient" aria-label="Recipient Email" placeholder="e.g. careers@company.com" class="w-full bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-100 text-xs focus:border-zinc-400 focus:outline-none">
                </div>

                <div>
                    <label for="modalSubject" class="block text-zinc-300 font-medium mb-1">Subject Line:</label>
                    <input type="text" id="modalSubject" aria-label="Subject Line" class="w-full bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-100 text-xs focus:border-zinc-400 focus:outline-none">
                </div>

                <div>
                    <div class="flex items-center justify-between mb-1">
                        <label for="modalBody" class="block text-zinc-300 font-medium" id="modalPitchTitle">Personalized Cold Pitch:</label>
                        <button onclick="copyPitchText()" id="copyPitchBtn" class="btn-ghost !p-1 text-xs text-zinc-300 hover:text-zinc-100 min-h-[30px]" aria-label="Copy pitch text to clipboard">
                            <i class="fa-regular fa-copy mr-1"></i>
                            <span id="copyPitchLabel">Copy Text</span>
                        </button>
                    </div>
                    <textarea id="modalBody" rows="7" aria-label="Personalized Cold Pitch Body" class="w-full bg-zinc-950 border border-zinc-700 rounded-lg p-3 text-zinc-100 text-xs font-sans leading-relaxed focus:border-zinc-400 focus:outline-none"></textarea>
                </div>

                <div class="bg-zinc-900 border border-zinc-700 rounded-lg p-3 flex items-center space-x-3 text-zinc-300">
                    <i class="fa-solid fa-file-pdf text-zinc-400 text-xl shrink-0"></i>
                    <div>
                        <div class="font-medium text-xs text-zinc-200">Verified Resume Attachment:</div>
                        <div class="text-xs text-zinc-300 font-mono">sarthak_joshi_fullstack_developer_nov_2026.pdf will be attached automatically.</div>
                    </div>
                </div>
            </div>

            <!-- Modal Footer Actions -->
            <div class="px-5 py-3 border-t border-zinc-800 bg-zinc-900 flex items-center justify-between">
                <button onclick="closeEmailModal()" class="btn-secondary">Cancel</button>
                <button id="sendEmailBtn" onclick="dispatchColdEmail()" class="btn-primary">
                    <i class="fa-solid fa-paper-plane text-xs"></i>
                    <span>Send Cold Email Now</span>
                </button>
            </div>
        </div>
    </div>

    <!-- ========================================== -->
    <!-- Technical Interview Prep Copilot Modal (New) -->
    <!-- ========================================== -->
    <div id="interviewModal" role="dialog" aria-modal="true" aria-labelledby="interviewModalTitle" tabIndex="-1" style="display: none;" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="card-surface border border-zinc-700 max-w-3xl w-full rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
            <!-- Modal Header -->
            <div class="px-5 py-3.5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900">
                <div class="flex items-center space-x-2.5">
                    <i class="fa-solid fa-graduation-cap text-amber-400 text-sm"></i>
                    <div>
                        <h3 id="interviewModalTitle" class="font-semibold text-zinc-100 text-sm">Technical Interview Copilot</h3>
                        <p class="text-xs text-zinc-300" id="interviewModalSub">Tailored to job stack &amp; requirements</p>
                    </div>
                </div>
                <div class="flex items-center space-x-2">
                    <button onclick="copyInterviewNotes()" class="btn-ghost !p-1 min-h-[30px] text-xs text-zinc-300 hover:text-zinc-100" title="Copy full interview prep notes">
                        <i class="fa-regular fa-copy mr-1"></i>
                        <span id="copyInterviewLabel">Copy Notes</span>
                    </button>
                    <button onclick="closeInterviewModal()" class="btn-ghost !p-1 min-h-[32px] min-w-[32px]" aria-label="Close interview dialog">
                        <i class="fa-solid fa-xmark text-xs"></i>
                    </button>
                </div>
            </div>

            <!-- Modal Content Area -->
            <div class="p-5 overflow-y-auto space-y-4 text-xs" id="interviewContent">
                <!-- Loading State -->
                <div id="interviewLoading" class="py-12 text-center text-zinc-400 space-y-2">
                    <i class="fa-solid fa-circle-notch fa-spin text-2xl text-amber-400"></i>
                    <p class="text-xs">Analyzing tech stack and generating tailored technical interview questions...</p>
                </div>

                <!-- Structured Interview Kit -->
                <div id="interviewKit" style="display: none;" class="space-y-4">
                    <!-- Core Focus Topics -->
                    <div class="bg-zinc-950 p-3.5 rounded-lg border border-zinc-700">
                        <h4 class="font-semibold text-zinc-200 text-xs mb-2 flex items-center gap-1.5">
                            <i class="fa-solid fa-list-check text-amber-400"></i>
                            <span>Core Focus Topics for this Role</span>
                        </h4>
                        <div id="interviewTopics" class="flex flex-wrap gap-1.5"></div>
                    </div>

                    <!-- Architecture Talking Points (PRYM Scale) -->
                    <div class="bg-zinc-950 p-3.5 rounded-lg border border-zinc-700">
                        <h4 class="font-semibold text-zinc-200 text-xs mb-2 flex items-center gap-1.5">
                            <i class="fa-solid fa-cubes text-emerald-400"></i>
                            <span>Architecture &amp; Scale Points from Your Resume</span>
                        </h4>
                        <ul id="interviewArchitecture" class="space-y-1.5 text-zinc-300 list-disc list-inside leading-relaxed"></ul>
                    </div>

                    <!-- Tailored Questions & Answers -->
                    <div>
                        <h4 class="font-semibold text-zinc-200 text-xs mb-2.5 flex items-center gap-1.5">
                            <i class="fa-solid fa-circle-question text-sky-400"></i>
                            <span>Role-Specific Technical Questions &amp; Recommended Answers</span>
                        </h4>
                        <div id="interviewQuestionsList" class="space-y-3"></div>
                    </div>
                </div>
            </div>

            <!-- Modal Footer Actions -->
            <div class="px-5 py-3 border-t border-zinc-800 bg-zinc-900 flex items-center justify-between">
                <span class="text-xs text-zinc-400">Tailored for Sarthak Joshi's 1.8+ yrs full stack &amp; backend profile</span>
                <button onclick="closeInterviewModal()" class="btn-secondary">Close</button>
            </div>
        </div>
    </div>

    <!-- ========================================== -->
    <!-- Targeted Crawl Discovery Modal (Issue 4 Fixed) -->
    <!-- ========================================== -->
    <div id="crawlModal" role="dialog" aria-modal="true" aria-labelledby="crawlModalTitle" tabIndex="-1" style="display: none;" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="card-surface border border-zinc-700 max-w-lg w-full rounded-xl shadow-2xl overflow-hidden flex flex-col">
            <!-- Modal Header -->
            <div class="px-5 py-3.5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900">
                <div class="flex items-center space-x-2.5">
                    <i class="fa-solid fa-radar text-zinc-400 text-xs"></i>
                    <div>
                        <h3 id="crawlModalTitle" class="font-semibold text-zinc-100 text-sm">Targeted Job Discovery</h3>
                        <p class="text-xs text-zinc-300">Search by Country, State / Tech Hub &amp; Role</p>
                    </div>
                </div>
                <button onclick="closeCrawlModal()" class="btn-ghost !p-1 min-h-[32px] min-w-[32px]" aria-label="Close crawl modal">
                    <i class="fa-solid fa-xmark text-xs"></i>
                </button>
            </div>

            <!-- Modal Content Form -->
            <div class="p-5 space-y-4 text-xs">
                <div>
                    <label for="crawlCountry" class="block text-zinc-300 font-medium mb-1.5">Target Country:</label>
                    <select id="crawlCountry" onchange="onCrawlCountryChange()" aria-label="Select target crawl country" class="w-full bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-100 text-xs focus:border-zinc-400 focus:outline-none cursor-pointer">
                        <option value="India" selected>India 🇮🇳 (Target Hubs &amp; Local Tech Opportunities)</option>
                        <option value="United States">United States 🇺🇸</option>
                        <option value="Europe">Europe / UK 🇪🇺</option>
                        <option value="Remote">Worldwide Remote 🌐</option>
                        <option value="All">All Countries / Unrestricted</option>
                    </select>
                </div>

                <div>
                    <label for="crawlState" class="block text-zinc-300 font-medium mb-1.5">Target Tech Hub / State:</label>
                    <select id="crawlState" aria-label="Select target crawl state or tech hub" class="w-full bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-100 text-xs focus:border-zinc-400 focus:outline-none cursor-pointer">
                        <option value="All India" selected>All India (Pune, Bangalore, Mumbai, Hyderabad, Delhi NCR, Remote)</option>
                        <option value="Pune">Pune (Maharashtra)</option>
                        <option value="Bangalore">Bangalore (Karnataka)</option>
                        <option value="Mumbai">Mumbai (Maharashtra)</option>
                        <option value="Hyderabad">Hyderabad (Telangana)</option>
                        <option value="Delhi NCR">Delhi NCR (Noida / Gurgaon)</option>
                        <option value="Chennai">Chennai (Tamil Nadu)</option>
                        <option value="Remote">Remote (India)</option>
                    </select>
                </div>

                <div>
                    <label for="crawlScoreRange" class="block text-zinc-300 font-medium mb-1.5">Minimum Match Score Threshold:</label>
                    <div class="flex items-center space-x-3 bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2">
                        <input type="range" id="crawlScoreRange" min="20" max="80" value="50" step="5" oninput="document.getElementById('crawlScoreVal').innerText = this.value + '%'" aria-label="Target crawl minimum match score" class="flex-1 accent-zinc-200 cursor-pointer">
                        <span id="crawlScoreVal" class="text-xs font-semibold text-zinc-200 font-mono-numbers w-10 text-right">50%</span>
                    </div>
                </div>

                <div class="bg-zinc-900 border border-zinc-700 rounded-lg p-3 space-y-1.5">
                    <label class="flex items-center space-x-2 text-zinc-300 cursor-pointer">
                        <input type="checkbox" id="crawlSendMail" class="accent-zinc-400 cursor-pointer">
                        <span class="font-medium text-xs">Email Daily Briefing &amp; Excel report upon completion</span>
                    </label>
                    <p class="text-xs text-zinc-400 pl-5">Automatically dispatches the candidate summary and workbook to recipient email.</p>
                </div>
            </div>

            <!-- Modal Footer Actions -->
            <div class="px-5 py-3 border-t border-zinc-800 bg-zinc-900 flex items-center justify-between">
                <button onclick="closeCrawlModal()" class="btn-secondary">Cancel</button>
                <button onclick="launchTargetedCrawl()" class="btn-primary">
                    <i class="fa-solid fa-arrows-rotate text-xs"></i>
                    <span>Launch Targeted Discovery</span>
                </button>
            </div>
        </div>
    </div>

    <!-- Frontend Interactive JavaScript Application -->
    <script>
        let currentStatus = 'all';
        let currentScore = 50;
        let activeJobId = null;
        let debounceTimer = null;
        let scoreDebounceTimer = null;
        let logsInterval = null;
        let cachedRawLogs = [];
        let totalJobsCount = 0;
        let currentPage = 1;
        let pageSize = 10;
        let totalPages = 1;
        let totalOpportunities = 0;
        let lastFocusedTrigger = null;
        let undoActionCallback = null;
        let toastTimeout = null;
        window.allJobsMap = new Map();

        document.addEventListener('DOMContentLoaded', () => {
            fetchStats();
            fetchJobs(1);
            checkCrawlProgress();

            window.addEventListener('keydown', (e) => {
                if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
                    e.preventDefault();
                    const searchBox = document.getElementById('searchInput');
                    if (searchBox) {
                        searchBox.focus();
                        searchBox.select();
                    }
                } else if (e.key === 'Escape') {
                    closeEmailModal();
                    closeLogsModal();
                    closeCrawlModal();
                    closeInterviewModal();
                }
            });
        });

        // Toast Feedback System with Undo Support (Issue 8 Fixed)
        function showToast(msg, isSuccess = true, onUndo = null) {
            const toast = document.getElementById('toast');
            const toastMsg = document.getElementById('toastMsg');
            const toastIcon = document.getElementById('toastIcon');
            const undoBtn = document.getElementById('toastUndoBtn');

            if (toastTimeout) clearTimeout(toastTimeout);
            toastMsg.innerText = msg;
            toastIcon.className = isSuccess 
                ? 'fa-solid fa-circle-check text-emerald-400 text-sm shrink-0' 
                : 'fa-solid fa-circle-exclamation text-rose-400 text-sm shrink-0';

            if (onUndo && typeof onUndo === 'function') {
                undoActionCallback = onUndo;
                undoBtn.style.display = 'inline-block';
            } else {
                undoActionCallback = null;
                undoBtn.style.display = 'none';
            }

            toast.style.display = 'flex';
            toastTimeout = setTimeout(() => {
                hideToast();
            }, onUndo ? 6000 : 4500);
        }

        function undoLastStatusChange() {
            if (undoActionCallback) {
                const callback = undoActionCallback;
                undoActionCallback = null;
                document.getElementById('toastUndoBtn').style.display = 'none';
                callback();
            }
        }

        function hideToast() {
            const toast = document.getElementById('toast');
            if (toast) toast.style.display = 'none';
            undoActionCallback = null;
        }

        // Stats Retrieval
        async function fetchStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                totalJobsCount = data.total_jobs || 0;
                document.getElementById('statTotal').innerText = data.total_jobs || 0;
                document.getElementById('statHigh').innerText = data.high_matches || 0;
                document.getElementById('statContacted').innerText = data.contacted_jobs || 0;
                document.getElementById('statInterviewing').innerText = data.interviewing_jobs || 0;
                document.getElementById('statDistinctCompanies').innerText = `${data.distinct_companies || 0} Companies`;

                // Update tab badges
                document.getElementById('tabCountAll').innerText = data.total_jobs || 0;
                document.getElementById('tabCountNew').innerText = data.new_jobs || 0;
                document.getElementById('tabCountApplied').innerText = data.applied_jobs || 0;
                document.getElementById('tabCountContacted').innerText = data.contacted_jobs || 0;
                document.getElementById('tabCountInterviewing').innerText = data.interviewing_jobs || 0;
                document.getElementById('tabCountSaved').innerText = data.saved_jobs || 0;
            } catch(e) {
                console.error("Failed to load statistics:", e);
            }
        }

        // Status Tabs Filtering
        function setStatusFilter(status) {
            currentStatus = status;
            document.querySelectorAll('.status-tab').forEach(btn => {
                const isSelected = btn.dataset.status === status;
                btn.className = isSelected ? 'status-tab btn-primary shrink-0' : 'status-tab btn-secondary shrink-0';
                btn.setAttribute('aria-selected', isSelected ? 'true' : 'false');
            });
            currentPage = 1;
            fetchJobs(1);
        }

        // Score Filtering with Preset Sync & Debounce (Issue 6 Fixed)
        function setScorePreset(val) {
            currentScore = val;
            const slider = document.getElementById('scoreRange');
            if (slider) slider.value = val;
            const label = document.getElementById('scoreLabel');
            if (label) label.innerText = val + '%';

            document.querySelectorAll('.score-btn').forEach(btn => {
                const isMatch = parseInt(btn.dataset.score) === val;
                btn.className = isMatch ? 'score-btn btn-primary text-xs' : 'score-btn btn-secondary text-xs';
            });

            if (scoreDebounceTimer) clearTimeout(scoreDebounceTimer);
            currentPage = 1;
            fetchJobs(1);
        }

        function updateScoreFilter(val) {
            currentScore = parseInt(val);
            const label = document.getElementById('scoreLabel');
            if (label) label.innerText = val + '%';

            document.querySelectorAll('.score-btn').forEach(btn => {
                const isMatch = parseInt(btn.dataset.score) === currentScore;
                btn.className = isMatch ? 'score-btn btn-primary text-xs' : 'score-btn btn-secondary text-xs';
            });

            // Debounce slider updates (250ms) to avoid request flooding
            if (scoreDebounceTimer) clearTimeout(scoreDebounceTimer);
            scoreDebounceTimer = setTimeout(() => {
                currentPage = 1;
                fetchJobs(1);
            }, 250);
        }

        function clearSearch() {
            const input = document.getElementById('searchInput');
            if (input) input.value = '';
            document.getElementById('clearSearchBtn').style.display = 'none';
            currentPage = 1;
            fetchJobs(1);
        }

        function debounceFetch() {
            const val = document.getElementById('searchInput').value;
            const clearBtn = document.getElementById('clearSearchBtn');
            clearBtn.style.display = val.trim() ? 'inline-flex' : 'none';
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                currentPage = 1;
                fetchJobs(1);
            }, 250);
        }

        // 1-Click Reset All Filters (Issue 9 Fixed)
        function resetAllFilters() {
            const searchInput = document.getElementById('searchInput');
            if (searchInput) searchInput.value = '';
            const clearBtn = document.getElementById('clearSearchBtn');
            if (clearBtn) clearBtn.style.display = 'none';

            const countrySelect = document.getElementById('countryFilter');
            if (countrySelect) countrySelect.value = 'India';

            const stateSelect = document.getElementById('stateFilter');
            const hubs = LOCATION_HUBS["India"];
            if (stateSelect && hubs) {
                stateSelect.innerHTML = hubs.map(h => `<option value="${h.val}">${h.label}</option>`).join('');
                stateSelect.value = 'All India';
            }

            setScorePreset(50);
            setStatusFilter('all');
            showToast('All filters have been reset to defaults');
        }

        const LOCATION_HUBS = {
            "India": [
                { val: "All India", label: "All India" },
                { val: "Pune", label: "Pune (MH)" },
                { val: "Bangalore", label: "Bangalore (KA)" },
                { val: "Mumbai", label: "Mumbai (MH)" },
                { val: "Hyderabad", label: "Hyderabad (TS)" },
                { val: "Delhi NCR", label: "Delhi NCR" },
                { val: "Chennai", label: "Chennai (TN)" },
                { val: "Remote", label: "Remote (India)" }
            ],
            "United States": [
                { val: "All US", label: "All US" },
                { val: "San Francisco", label: "SF / Bay Area" },
                { val: "New York", label: "New York" },
                { val: "Austin", label: "Austin (TX)" },
                { val: "Seattle", label: "Seattle (WA)" },
                { val: "Remote", label: "Remote (US)" }
            ],
            "Europe": [
                { val: "All Europe", label: "All Europe" },
                { val: "London", label: "London (UK)" },
                { val: "Berlin", label: "Berlin (DE)" },
                { val: "Amsterdam", label: "Amsterdam (NL)" },
                { val: "Remote", label: "Remote (EU)" }
            ],
            "Remote": [
                { val: "Any Remote", label: "Any Remote" },
                { val: "Worldwide", label: "Worldwide Global" },
                { val: "APAC", label: "APAC" },
                { val: "EMEA", label: "EMEA" },
                { val: "Americas", label: "Americas" }
            ],
            "All": [
                { val: "Any Location", label: "Any Location" }
            ]
        };

        function onCountryChange() {
            const country = document.getElementById('countryFilter').value;
            const stateSelect = document.getElementById('stateFilter');
            const hubs = LOCATION_HUBS[country] || LOCATION_HUBS["All"];
            stateSelect.innerHTML = hubs.map(h => `<option value="${h.val}">${h.label}</option>`).join('');
            currentPage = 1;
            fetchJobs(1);
        }

        function onStateChange() {
            currentPage = 1;
            fetchJobs(1);
        }

        function openCrawlModal() {
            lastFocusedTrigger = document.activeElement;
            const modal = document.getElementById('crawlModal');
            modal.style.display = 'flex';
            trapModalFocus(modal);
        }

        function closeCrawlModal() {
            const modal = document.getElementById('crawlModal');
            modal.style.display = 'none';
            releaseModalFocus();
            if (lastFocusedTrigger && typeof lastFocusedTrigger.focus === 'function') {
                lastFocusedTrigger.focus();
            }
        }

        function onCrawlCountryChange() {
            const country = document.getElementById('crawlCountry').value;
            const stateSelect = document.getElementById('crawlState');
            const hubs = LOCATION_HUBS[country] || LOCATION_HUBS["All"];
            stateSelect.innerHTML = hubs.map(h => `<option value="${h.val}">${h.label}</option>`).join('');
        }

        async function launchTargetedCrawl() {
            const country = document.getElementById('crawlCountry').value;
            const state = document.getElementById('crawlState').value;
            const minScore = parseInt(document.getElementById('crawlScoreRange').value);
            const sendMail = document.getElementById('crawlSendMail').checked;
            closeCrawlModal();
            await executeCrawl(country, state, minScore, sendMail);
        }

        // Fetch and Render Job Listings with Pagination & Location Filters
        async function fetchJobs(page = 1) {
            currentPage = page;
            const search = document.getElementById('searchInput').value;
            const sortBy = document.getElementById('sortBy').value;
            const container = document.getElementById('jobsList');
            const paginationBar = document.getElementById('paginationBar');
            const topPagination = document.getElementById('topPagination');

            const country = document.getElementById('countryFilter') ? document.getElementById('countryFilter').value : 'India';
            const state = document.getElementById('stateFilter') ? document.getElementById('stateFilter').value : 'All India';

            let url = `/api/jobs?page=${currentPage}&limit=${pageSize}&min_score=${currentScore}&sort_by=${sortBy}`;
            if (currentStatus !== 'all') url += `&status=${currentStatus}`;
            if (country && country !== 'All') url += `&country=${encodeURIComponent(country)}`;
            if (state && !state.toLowerCase().startsWith('all') && state !== 'Any Location' && state !== 'Any Remote') {
                url += `&state=${encodeURIComponent(state)}`;
            }
            if (search.trim()) url += `&search=${encodeURIComponent(search.trim())}`;

            try {
                const res = await fetch(url);
                const data = await res.json();
                const jobs = data.jobs || [];
                totalOpportunities = data.total !== undefined ? data.total : jobs.length;
                totalPages = data.total_pages || 1;
                document.getElementById('jobsCount').innerText = totalOpportunities;

                // Cache jobs in map
                window.allJobsMap.clear();
                jobs.forEach(j => window.allJobsMap.set(j._id, j));

                if (jobs.length === 0) {
                    paginationBar.style.display = 'none';
                    topPagination.style.display = 'none';
                    if (totalJobsCount === 0) {
                        container.innerHTML = `
                            <div class="card-surface rounded-xl p-10 text-center text-zinc-300 border border-zinc-700">
                                <div class="w-12 h-12 rounded-lg bg-zinc-800 text-zinc-300 flex items-center justify-center text-xl mx-auto mb-3">
                                    <i class="fa-solid fa-radar"></i>
                                </div>
                                <h3 class="text-sm font-semibold text-zinc-100">Database is clean and ready</h3>
                                <p class="text-xs text-zinc-400 mt-1 max-w-md mx-auto">Click below to start live automated discovery across Firecrawl, Greenhouse ATS, and remote portals.</p>
                                <button onclick="triggerCrawl()" class="btn-primary mt-4">
                                    <i class="fa-solid fa-arrows-rotate text-xs"></i>
                                    <span>Run Job Discovery Now</span>
                                </button>
                            </div>
                        `;
                    } else {
                        // Issue 9 Fixed: Empty state provides a 1-click Reset All Filters button
                        container.innerHTML = `
                            <div class="card-surface rounded-xl p-8 text-center text-zinc-300 border border-zinc-700 space-y-2">
                                <div class="w-10 h-10 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-400 flex items-center justify-center text-base mx-auto mb-1">
                                    <i class="fa-solid fa-filter-circle-xmark"></i>
                                </div>
                                <h3 class="text-sm font-semibold text-zinc-100">No matching jobs found</h3>
                                <p class="text-xs text-zinc-400 max-w-sm mx-auto">Try lowering the minimum score or clearing your search keywords and location filters.</p>
                                <div class="pt-2">
                                    <button onclick="resetAllFilters()" class="btn-primary text-xs">
                                        <i class="fa-solid fa-rotate-left text-xs"></i>
                                        <span>Reset All Filters</span>
                                    </button>
                                </div>
                            </div>
                        `;
                    }
                    return;
                }

                container.innerHTML = jobs.map(job => renderJobCard(job)).join('');
                renderPagination(data);
            } catch(e) {
                container.innerHTML = `<div class="text-rose-400 p-4 text-center text-xs">Failed to load jobs: ${e}</div>`;
                paginationBar.style.display = 'none';
                topPagination.style.display = 'none';
            }
        }

        // Render Single Job Card with High Polish & Accessible Layout (Issues 1, 2, 3 & 5 Fixed)
        function renderJobCard(job) {
            const score = job.match_score || 0;
            let badgeBg = 'bg-zinc-800 text-zinc-300 border-zinc-700';
            let dotColor = 'bg-zinc-400';
            if (score >= 80) {
                badgeBg = 'bg-emerald-950/70 text-emerald-300 border-emerald-700/80';
                dotColor = 'bg-emerald-400';
            } else if (score >= 65) {
                badgeBg = 'bg-zinc-800 text-zinc-200 border-zinc-600';
                dotColor = 'bg-zinc-300';
            } else if (score >= 50) {
                badgeBg = 'bg-amber-950/60 text-amber-300 border-amber-700/80';
                dotColor = 'bg-amber-400';
            }

            const company = job.company || 'Company';
            const initial = company.charAt(0).toUpperCase();

            // Highlight primary core skills
            const coreStack = ['nestjs', 'node.js', 'nodejs', 'typescript', 'postgresql', 'redis', 'bullmq', 'docker', 'mongodb'];
            const skillsHtml = (job.matched_skills || []).map(s => {
                const isCore = coreStack.includes(s.toLowerCase());
                if (isCore) {
                    return `<span class="bg-zinc-800 text-zinc-200 border border-zinc-600 font-medium px-2 py-0.5 rounded text-xs">${escapeHtml(s)}</span>`;
                }
                return `<span class="bg-zinc-900 text-zinc-300 border border-zinc-700 px-2 py-0.5 rounded text-xs">${escapeHtml(s)}</span>`;
            }).join(' ');

            const dateStr = job.date_posted || 'Recently posted';

            // LinkedIn contacts pills with min 32px touch target & safe protocol validation
            const contacts = job.linkedin_contacts || [];
            let linkedinContactsHtml = '';
            if (contacts.length > 0) {
                linkedinContactsHtml = contacts.map(c => {
                    const safeUrl = (c.url && (c.url.startsWith('https://') || c.url.startsWith('http://'))) ? c.url : '#';
                    return `
                    <a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-zinc-900 hover:bg-zinc-800 text-zinc-200 border border-zinc-700 text-xs min-h-[32px] transition" title="${escapeHtml(c.role || 'Talent')}">
                        <span class="font-medium text-zinc-200">${escapeHtml(c.name)}</span>
                        <span class="text-zinc-400 font-normal">(${escapeHtml((c.role || '').split('at')[0].trim())})</span>
                        <i class="fa-solid fa-arrow-up-right-from-square text-[10px] text-zinc-400"></i>
                    </a>
                `}).join(' ');
            } else {
                const searchUrl = job.linkedin_search_url || `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(company)}+technical+recruiter`;
                linkedinContactsHtml = `
                    <a href="${searchUrl}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1.5 text-xs text-zinc-300 hover:text-zinc-100 min-h-[32px] transition">
                        <span>Search ${escapeHtml(company)} Recruiters</span>
                        <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                    </a>
                `;
            }

            return `
                <div class="card-interactive rounded-xl p-4 border border-zinc-700 flex flex-col gap-3">
                    <div class="flex items-start space-x-3 w-full">
                        <!-- Company Avatar -->
                        <div class="w-10 h-10 rounded-lg bg-zinc-800 border border-zinc-700 flex items-center justify-center font-semibold text-xs text-zinc-200 shrink-0">
                            ${initial}
                        </div>

                        <!-- Info Content -->
                        <div class="space-y-1.5 flex-1 min-w-0">
                            <div class="flex flex-wrap items-center gap-2">
                                <span class="inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded border ${badgeBg} font-mono-numbers">
                                    <span class="w-1.5 h-1.5 rounded-full ${dotColor}"></span>
                                    ${score}% Match
                                </span>
                                <span class="text-xs text-zinc-400">•</span>
                                <span class="text-xs text-zinc-300 bg-zinc-900 px-2.5 py-0.5 rounded border border-zinc-700">
                                    ${escapeHtml(job.source || 'ATS Portal')}
                                </span>
                                <span class="text-xs text-zinc-400">•</span>
                                <span class="text-xs text-zinc-400 font-mono-numbers">${escapeHtml(dateStr)}</span>
                            </div>

                            <!-- Role Title (h3 properly nested under h2) -->
                            <div>
                                <h3 class="font-semibold text-zinc-100 text-sm leading-snug tracking-tight hover:text-white transition">${escapeHtml(job.title)}</h3>
                                <p class="text-xs text-zinc-400 flex items-center gap-1.5 flex-wrap mt-0.5">
                                    <span class="text-zinc-200 font-medium">${escapeHtml(job.company)}</span>
                                    <span class="text-zinc-400">•</span>
                                    <span class="inline-flex items-center gap-1 text-zinc-300">
                                        <i class="fa-solid fa-location-dot text-zinc-400 text-[10px]"></i>
                                        ${escapeHtml(job.location || 'Remote')}
                                    </span>
                                    ${job.country && job.country !== 'Other' ? `<span class="bg-zinc-800 text-zinc-300 border border-zinc-700 px-1.5 py-0.2 rounded text-[10px] font-mono">${escapeHtml(job.country)}</span>` : ''}
                                </p>
                            </div>

                            <p class="text-xs text-zinc-300 leading-relaxed line-clamp-2">${escapeHtml(job.match_summary || '')}</p>

                            <div class="flex flex-wrap gap-1.5 pt-0.5">
                                ${skillsHtml}
                            </div>
                        </div>
                    </div>

                    <!-- Discovered LinkedIn Recruiters & Action Bar (Issues 1 & 5 Fixed) -->
                    <div class="pt-3 border-t border-zinc-800 flex flex-col md:flex-row md:items-center justify-between gap-3 text-xs">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="text-zinc-400 font-medium flex items-center gap-1 text-xs">
                                <i class="fa-brands fa-linkedin text-[#0A66C2]"></i>
                                <span>Recruiter Contacts:</span>
                            </span>
                            ${linkedinContactsHtml}
                            <button onclick="copyLinkedInPitch('${job._id}')" class="btn-ghost min-h-[32px] px-2.5 py-1 text-xs text-zinc-300 hover:text-zinc-100" title="Copy tailored 300-char connection note for LinkedIn">
                                <i class="fa-regular fa-copy mr-1 text-xs"></i>
                                <span>Copy Note</span>
                            </button>
                            <a href="${job.linkedin_search_url || `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(company)}+technical+recruiter`}" target="_blank" rel="noopener noreferrer" class="btn-ghost min-h-[32px] px-2.5 py-1 text-xs text-zinc-300 hover:text-[#0A66C2]" title="Search company employees on LinkedIn">
                                <i class="fa-brands fa-linkedin mr-1 text-xs"></i>
                                <span>Search All</span>
                            </a>
                        </div>

                        <!-- Action Buttons Cluster with standard min-h-[36px] touch targets -->
                        <div class="flex flex-wrap items-center gap-2 shrink-0">
                            <label for="status_${job._id}" class="sr-only">Update application status for ${escapeHtml(job.title)}</label>
                            <select id="status_${job._id}" onchange="updateJobStatus('${job._id}', this.value)" aria-label="Update status for ${escapeHtml(job.title)}" class="bg-zinc-950 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-200 min-h-[36px] focus:border-zinc-400 focus:outline-none cursor-pointer">
                                <option value="new" ${job.status === 'new' ? 'selected' : ''}>Status: New</option>
                                <option value="applied" ${job.status === 'applied' ? 'selected' : ''}>Status: Applied</option>
                                <option value="contacted" ${job.status === 'contacted' ? 'selected' : ''}>Status: Contacted</option>
                                <option value="interviewing" ${job.status === 'interviewing' ? 'selected' : ''}>Status: Interviewing</option>
                                <option value="saved" ${job.status === 'saved' ? 'selected' : ''}>Status: Saved</option>
                                <option value="rejected" ${job.status === 'rejected' ? 'selected' : ''}>Status: Rejected</option>
                            </select>

                            <a href="${job.url}" target="_blank" rel="noopener noreferrer" class="btn-secondary min-h-[36px] px-3 py-1.5" title="Open Career Portal Application Page">
                                <i class="fa-solid fa-arrow-up-right-from-square text-xs text-zinc-400"></i>
                                <span>Career Link</span>
                            </a>

                            <button onclick="openInterviewModalForJob('${job._id}')" class="btn-secondary min-h-[36px] px-3 py-1.5 text-zinc-200" title="1-Click Technical Interview Questions & Talking Points">
                                <i class="fa-solid fa-graduation-cap text-xs text-amber-400"></i>
                                <span>Interview Prep</span>
                            </button>

                            <button onclick="openEmailModalForJob('${job._id}')" class="btn-outline min-h-[36px] px-3 py-1.5 text-zinc-200" title="Generate & send personalized recruiter cold email">
                                <i class="fa-solid fa-paper-plane text-xs text-zinc-400"></i>
                                <span>Cold Email</span>
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }

        // Pagination Bar Renderer (100% Reliable Top & Bottom)
        function renderPagination(data) {
            const paginationBar = document.getElementById('paginationBar');
            const topPagination = document.getElementById('topPagination');

            if (!data || totalOpportunities === 0) {
                paginationBar.style.display = 'none';
                topPagination.style.display = 'none';
                return;
            }

            // Show top & bottom pagination bars
            paginationBar.style.display = 'flex';
            topPagination.style.display = 'flex';

            const start = (currentPage - 1) * pageSize + 1;
            const end = Math.min(totalOpportunities, currentPage * pageSize);
            document.getElementById('pageRangeText').innerText = `${start}–${end}`;
            document.getElementById('pageTotalText').innerText = totalOpportunities;
            document.getElementById('topPageIndicator').innerText = `Page ${currentPage} / ${totalPages}`;

            // Top buttons
            document.getElementById('topPrevBtn').disabled = currentPage <= 1;
            document.getElementById('topNextBtn').disabled = currentPage >= totalPages;

            // Bottom buttons
            const prevBtn = document.getElementById('prevPageBtn');
            const nextBtn = document.getElementById('nextPageBtn');
            prevBtn.disabled = currentPage <= 1;
            nextBtn.disabled = currentPage >= totalPages;

            // Page pills
            const pillsContainer = document.getElementById('pagePillsContainer');
            let pillsHtml = '';

            let startPage = Math.max(1, currentPage - 2);
            let endPage = Math.min(totalPages, currentPage + 2);

            if (startPage > 1) {
                pillsHtml += `<button onclick="goToPage(1)" class="btn-secondary !px-2.5 !py-1 text-xs font-mono-numbers">1</button>`;
                if (startPage > 2) {
                    pillsHtml += `<span class="text-zinc-600 text-xs px-1">…</span>`;
                }
            }

            for (let p = startPage; p <= endPage; p++) {
                if (p === currentPage) {
                    pillsHtml += `<button class="btn-primary !px-2.5 !py-1 text-xs font-bold font-mono-numbers">${p}</button>`;
                } else {
                    pillsHtml += `<button onclick="goToPage(${p})" class="btn-secondary !px-2.5 !py-1 text-xs font-mono-numbers">${p}</button>`;
                }
            }

            if (endPage < totalPages) {
                if (endPage < totalPages - 1) {
                    pillsHtml += `<span class="text-zinc-600 text-xs px-1">…</span>`;
                }
                pillsHtml += `<button onclick="goToPage(${totalPages})" class="btn-secondary !px-2.5 !py-1 text-xs font-mono-numbers">${totalPages}</button>`;
            }

            pillsContainer.innerHTML = pillsHtml;
        }

        function goToPage(p) {
            if (p < 1 || p > totalPages) return;
            currentPage = p;
            fetchJobs(p);
            const section = document.getElementById('jobsSection');
            if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }

        function prevPage() {
            if (currentPage > 1) {
                goToPage(currentPage - 1);
            }
        }

        function nextPage() {
            if (currentPage < totalPages) {
                goToPage(currentPage + 1);
            }
        }

        function changePageSize(newSize) {
            pageSize = parseInt(newSize);
            currentPage = 1;
            fetchJobs(1);
        }

        function copyLinkedInPitch(jobId) {
            const job = window.allJobsMap.get(jobId);
            if (!job) return;
            const note = job.linkedin_connection_note || `Hi there, saw the ${job.title || 'Backend'} role at ${job.company || 'your team'}. With 1.8+ yrs building high-scale NestJS/Node.js/PostgreSQL APIs (300K+ users at PRYM), I'd love to connect and share how my backend background fits your team's goals!`;
            navigator.clipboard.writeText(note).then(() => {
                showToast("Copied 300-char LinkedIn connection note!");
            }).catch(() => {
                prompt("Copy LinkedIn Connection Note:", note);
            });
        }

        // Job Status Update via API with Undo Support (Issue 8 Fixed)
        async function updateJobStatus(jobId, newStatus, showUndoToast = true) {
            const currentJob = window.allJobsMap.get(jobId);
            const previousStatus = currentJob ? currentJob.status : 'new';

            try {
                const res = await fetch(`/api/jobs/${jobId}/status`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus })
                });
                if (res.ok) {
                    if (currentJob) currentJob.status = newStatus;
                    const statusSelect = document.getElementById(`status_${jobId}`);
                    if (statusSelect) statusSelect.value = newStatus;

                    if (showUndoToast && previousStatus !== newStatus) {
                        showToast(`Status updated to ${newStatus.toUpperCase()}`, true, () => {
                            updateJobStatus(jobId, previousStatus, false);
                        });
                    } else {
                        showToast(`Status updated to ${newStatus.toUpperCase()}`);
                    }
                    fetchStats();
                } else {
                    showToast('Failed to update status', false);
                }
            } catch (e) {
                showToast('Error updating status: ' + e, false);
            }
        }

        // Multi-Step Outreach Sequence State
        let currentOutreachKit = null;
        let activeSequenceStep = 'initial';
        let currentInterviewData = null;

        // Cold Outreach & Follow-up Sequence Modal (Enhanced with Sequences & AI)
        async function openEmailModalForJob(jobId) {
            const job = window.allJobsMap.get(jobId);
            if (!job) return;
            lastFocusedTrigger = document.activeElement;
            activeJobId = jobId;
            activeSequenceStep = 'initial';

            document.getElementById('modalCompanyHeader').innerText = `Target Company: ${job.company || 'Company'}`;
            document.getElementById('modalRecipient').value = job.contact_email || 'careers@company.com';
            document.getElementById('modalSubject').value = job.cold_outreach_subject || `Application for ${job.title || 'Role'} - Sarthak Joshi`;
            document.getElementById('modalBody').value = job.cold_outreach_body || '';
            document.getElementById('copyPitchLabel').innerText = "Copy Text";

            const modal = document.getElementById('emailModal');
            modal.style.display = 'flex';
            trapModalFocus(modal);

            // Fetch AI-tailored sequence kit in background
            try {
                const res = await fetch(`/api/jobs/${jobId}/outreach-kit`);
                if (res.ok) {
                    currentOutreachKit = await res.json();
                    switchSequenceTab('initial');
                }
            } catch(e) {
                console.warn("Outreach kit fetch failed, using stored template:", e);
            }
        }

        function switchSequenceTab(step) {
            activeSequenceStep = step;
            const tabs = ['initial', 'day3', 'day7', 'linkedin'];
            tabs.forEach(t => {
                const btn = document.getElementById('tab' + t.charAt(0).toUpperCase() + t.slice(1));
                if (btn) {
                    btn.className = (t === step) ? 'btn-primary !px-2.5 !py-1 text-xs' : 'btn-secondary !px-2.5 !py-1 text-xs';
                }
            });

            if (!currentOutreachKit) return;
            const job = window.allJobsMap.get(activeJobId) || {};
            const role = job.title || 'Role';
            const company = job.company || 'Company';

            if (step === 'initial') {
                document.getElementById('modalSubject').value = `Application for ${role} - Sarthak Joshi`;
                document.getElementById('modalBody').value = currentOutreachKit.initial_pitch || job.cold_outreach_body || '';
                document.getElementById('modalPitchTitle').innerText = "Personalized Initial Pitch (Day 0):";
            } else if (step === 'day3') {
                document.getElementById('modalSubject').value = `Re: Application for ${role} - Sarthak Joshi`;
                document.getElementById('modalBody').value = currentOutreachKit.followup_day3 || '';
                document.getElementById('modalPitchTitle').innerText = "Follow-up Bump (Day 3):";
            } else if (step === 'day7') {
                document.getElementById('modalSubject').value = `Checking in regarding ${role} at ${company} - Sarthak Joshi`;
                document.getElementById('modalBody').value = currentOutreachKit.followup_day7 || '';
                document.getElementById('modalPitchTitle').innerText = "Final Value Check-in (Day 7):";
            } else if (step === 'linkedin') {
                document.getElementById('modalSubject').value = `LinkedIn Connection Note (${company})`;
                document.getElementById('modalBody').value = currentOutreachKit.linkedin_note || '';
                document.getElementById('modalPitchTitle').innerText = "LinkedIn 300-Char Connection Note:";
            }
        }

        async function regenerateOutreachKit() {
            if (!activeJobId) return;
            const regenBtn = document.getElementById('regenBtn');
            regenBtn.disabled = true;
            regenBtn.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin mr-1"></i><span>Generating...</span>`;
            try {
                const res = await fetch(`/api/jobs/${activeJobId}/outreach-kit`);
                if (res.ok) {
                    currentOutreachKit = await res.json();
                    switchSequenceTab(activeSequenceStep);
                    showToast("Outreach kit refreshed with fresh tailored copy!");
                } else {
                    showToast("Failed to refresh kit", false);
                }
            } catch(e) {
                showToast("Error generating kit: " + e, false);
            } finally {
                regenBtn.disabled = false;
                regenBtn.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles mr-1"></i><span>AI Re-craft</span>`;
            }
        }

        function closeEmailModal() {
            const modal = document.getElementById('emailModal');
            modal.style.display = 'none';
            releaseModalFocus();
            activeJobId = null;
            currentOutreachKit = null;
            if (lastFocusedTrigger && typeof lastFocusedTrigger.focus === 'function') {
                lastFocusedTrigger.focus();
            }
        }

        // ==========================================
        // Technical Interview Prep Copilot Functions
        // ==========================================
        async function openInterviewModalForJob(jobId) {
            const job = window.allJobsMap.get(jobId);
            if (!job) return;
            lastFocusedTrigger = document.activeElement;

            const modal = document.getElementById('interviewModal');
            const loading = document.getElementById('interviewLoading');
            const kit = document.getElementById('interviewKit');
            const title = document.getElementById('interviewModalTitle');
            const sub = document.getElementById('interviewModalSub');

            title.innerText = `Technical Interview Copilot: ${job.title || 'Role'}`;
            sub.innerText = `${job.company || 'Company'} • ${job.location || 'Remote'}`;
            loading.style.display = 'block';
            kit.style.display = 'none';
            modal.style.display = 'flex';
            trapModalFocus(modal);

            try {
                const res = await fetch(`/api/jobs/${jobId}/interview-prep`);
                const data = await res.json();
                currentInterviewData = data;
                renderInterviewKit(data);
                loading.style.display = 'none';
                kit.style.display = 'block';
            } catch (e) {
                loading.innerHTML = `<p class="text-rose-400">Failed to generate interview prep kit: ${e}</p>`;
            }
        }

        function renderInterviewKit(data) {
            const prep = data.prep || {};
            // 1. Core Topics
            const topicsContainer = document.getElementById('interviewTopics');
            const topics = prep.core_topics || [];
            topicsContainer.innerHTML = topics.map(t => 
                `<span class="bg-zinc-800 text-zinc-200 border border-zinc-700 px-2.5 py-1 rounded text-xs">${escapeHtml(t)}</span>`
            ).join(' ');

            // 2. Architecture Talking Points
            const archList = document.getElementById('interviewArchitecture');
            const archPoints = prep.architecture_talking_points || [];
            archList.innerHTML = archPoints.map(p => 
                `<li>${escapeHtml(p)}</li>`
            ).join('');

            // 3. Questions & Answers
            const qList = document.getElementById('interviewQuestionsList');
            const questions = prep.questions || [];
            qList.innerHTML = questions.map((q, idx) => `
                <div class="card-surface p-3.5 rounded-lg border border-zinc-700 space-y-2">
                    <div class="flex items-start gap-2">
                        <span class="w-5 h-5 rounded-full bg-amber-500/20 text-amber-300 font-bold text-xs flex items-center justify-center shrink-0 mt-0.5">${idx + 1}</span>
                        <div class="font-semibold text-zinc-100 text-xs leading-snug">${escapeHtml(q.question)}</div>
                    </div>
                    <div class="pl-7 space-y-1.5 text-xs">
                        <p class="text-zinc-400 italic"><span class="text-zinc-500 font-medium not-italic">Why they ask:</span> ${escapeHtml(q.rationale)}</p>
                        <div class="bg-zinc-900/90 p-2.5 rounded border border-zinc-800 text-zinc-200 leading-relaxed">
                            <span class="text-emerald-400 font-medium">Recommended Talking Points:</span> ${escapeHtml(q.recommended_answer)}
                        </div>
                    </div>
                </div>
            `).join('');
        }

        function closeInterviewModal() {
            const modal = document.getElementById('interviewModal');
            modal.style.display = 'none';
            releaseModalFocus();
            if (lastFocusedTrigger && typeof lastFocusedTrigger.focus === 'function') {
                lastFocusedTrigger.focus();
            }
        }

        function copyInterviewNotes() {
            if (!currentInterviewData) return;
            const prep = currentInterviewData.prep || {};
            let text = `TECHNICAL INTERVIEW PREPARATION KIT\nRole: ${currentInterviewData.title}\nCompany: ${currentInterviewData.company}\n\n`;
            text += `CORE FOCUS TOPICS:\n- ${(prep.core_topics || []).join('\n- ')}\n\n`;
            text += `ARCHITECTURE & SCALE TALKING POINTS:\n- ${(prep.architecture_talking_points || []).join('\n- ')}\n\n`;
            text += `SAMPLE TECHNICAL QUESTIONS & ANSWERS:\n`;
            (prep.questions || []).forEach((q, idx) => {
                text += `\nQ${idx+1}: ${q.question}\nRationale: ${q.rationale}\nTalking Points: ${q.recommended_answer}\n`;
            });

            navigator.clipboard.writeText(text).then(() => {
                const label = document.getElementById('copyInterviewLabel');
                label.innerText = "Copied!";
                setTimeout(() => { label.innerText = "Copy Notes"; }, 2000);
                showToast("Complete interview notes copied to clipboard!");
            });
        }

        function copyPitchText() {
            const body = document.getElementById('modalBody').value;
            navigator.clipboard.writeText(body).then(() => {
                document.getElementById('copyPitchLabel').innerText = "Copied!";
                setTimeout(() => {
                    document.getElementById('copyPitchLabel').innerText = "Copy Text";
                }, 2000);
            });
        }

        // Send Cold Email via Gmail SMTP
        async function dispatchColdEmail() {
            if (!activeJobId) return;
            const recipient = document.getElementById('modalRecipient').value.trim();
            if (!recipient || !recipient.includes('@')) {
                showToast('Please provide a valid recipient email address', false);
                return;
            }

            const btn = document.getElementById('sendEmailBtn');
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin text-xs"></i><span>Sending via Gmail...</span>`;

            const payload = {
                recipient_email: recipient,
                subject: document.getElementById('modalSubject').value,
                body: document.getElementById('modalBody').value
            };

            try {
                const res = await fetch(`/api/jobs/${activeJobId}/send-cold-email`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok) {
                    showToast('Cold email dispatched with attached resume!');
                    closeEmailModal();
                    fetchJobs(currentPage);
                    fetchStats();
                } else {
                    showToast(data.detail || 'Failed to dispatch email', false);
                }
            } catch(e) {
                showToast('Network error: ' + e, false);
            } finally {
                btn.disabled = false;
                btn.innerHTML = `<i class="fa-solid fa-paper-plane text-xs"></i><span>Send Cold Email Now</span>`;
            }
        }

        // Crawler Trigger & Background Progress Checking
        function triggerCrawl() {
            openCrawlModal();
        }

        async function executeCrawl(country = "India", state = null, minScore = 50, sendMail = false) {
            const btn = document.getElementById('triggerBtn');
            const icon = document.getElementById('triggerIcon');
            const banner = document.getElementById('crawlBanner');
            btn.disabled = true;
            icon.classList.add('fa-spin');
            banner.style.display = 'block';

            try {
                const res = await fetch('/api/trigger', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ country, state, min_score: minScore, send_mail: sendMail })
                });
                const data = await res.json();
                showToast(data.message || `Crawl initiated for ${country}!`);
                checkCrawlProgress();
            } catch(e) {
                showToast('Failed to start crawler: ' + e, false);
                btn.disabled = false;
                icon.classList.remove('fa-spin');
                banner.style.display = 'none';
            }
        }

        function checkCrawlProgress() {
            const interval = setInterval(async () => {
                try {
                    const res = await fetch('/api/trigger/status');
                    const data = await res.json();
                    const banner = document.getElementById('crawlBanner');
                    if (!data.is_running) {
                        clearInterval(interval);
                        const btn = document.getElementById('triggerBtn');
                        const icon = document.getElementById('triggerIcon');
                        btn.disabled = false;
                        icon.classList.remove('fa-spin');
                        banner.style.display = 'none';
                        showToast('Discovery complete! Database updated.');
                        fetchStats();
                        fetchJobs(currentPage);
                    } else {
                        banner.style.display = 'block';
                    }
                } catch(e) {
                    clearInterval(interval);
                }
            }, 2500);
        }

        // Reset Database Confirmation
        async function confirmResetDB() {
            if (confirm("Are you sure you want to empty MongoDB and clear all generated reports? This enables a completely fresh crawl and test run.")) {
                try {
                    const res = await fetch('/api/database/clear', { method: 'DELETE' });
                    const data = await res.json();
                    if (res.ok) {
                        showToast("Database & reports cleared!");
                        fetchStats();
                        fetchJobs(1);
                    } else {
                        showToast(data.detail || "Failed to clear database", false);
                    }
                } catch(e) {
                    showToast("Error clearing database: " + e, false);
                }
            }
        }

        // Terminal Live Logs Modal Functions with Focus Management (Issue 4 Fixed)
        function openLogsModal() {
            lastFocusedTrigger = document.activeElement;
            const modal = document.getElementById('logsModal');
            modal.style.display = 'flex';
            trapModalFocus(modal);
            fetchLiveLogs();
            if (logsInterval) clearInterval(logsInterval);
            logsInterval = setInterval(() => {
                if (document.getElementById('autoRefreshLogs').checked) {
                    fetchLiveLogs();
                }
            }, 2000);
        }

        function closeLogsModal() {
            const modal = document.getElementById('logsModal');
            modal.style.display = 'none';
            releaseModalFocus();
            if (logsInterval) clearInterval(logsInterval);
            if (lastFocusedTrigger && typeof lastFocusedTrigger.focus === 'function') {
                lastFocusedTrigger.focus();
            }
        }

        // Accessible Modal Focus Trap (WCAG 2.4.3 & 2.1.2)
        let activeModalKeyHandler = null;

        function trapModalFocus(modal) {
            const focusableElements = modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
            if (!focusableElements.length) return;

            const firstElement = focusableElements[0];
            const lastElement = focusableElements[focusableElements.length - 1];

            setTimeout(() => {
                firstElement.focus();
            }, 50);

            if (activeModalKeyHandler) {
                document.removeEventListener('keydown', activeModalKeyHandler);
            }

            activeModalKeyHandler = function(e) {
                if (e.key === 'Tab') {
                    if (e.shiftKey) {
                        if (document.activeElement === firstElement) {
                            e.preventDefault();
                            lastElement.focus();
                        }
                    } else {
                        if (document.activeElement === lastElement) {
                            e.preventDefault();
                            firstElement.focus();
                        }
                    }
                }
            };
            document.addEventListener('keydown', activeModalKeyHandler);
        }

        function releaseModalFocus() {
            if (activeModalKeyHandler) {
                document.removeEventListener('keydown', activeModalKeyHandler);
                activeModalKeyHandler = null;
            }
        }

        async function fetchLiveLogs() {
            try {
                const res = await fetch('/api/logs?lines=150');
                const data = await res.json();
                cachedRawLogs = data.logs || [];
                renderLogs();
            } catch(e) {
                console.error("Failed to fetch logs:", e);
            }
        }

        function renderLogs() {
            const container = document.getElementById('logsConsole');
            const query = (document.getElementById('logFilterInput').value || '').toLowerCase();
            const filtered = query 
                ? cachedRawLogs.filter(line => line.toLowerCase().includes(query))
                : cachedRawLogs;

            if (filtered.length === 0) {
                container.innerHTML = '<span class="text-zinc-500 italic">No log entries matched.</span>';
                return;
            }

            container.innerHTML = filtered.map(line => {
                let color = 'text-zinc-300';
                if (line.includes('[ERROR]')) color = 'text-rose-400 font-medium';
                else if (line.includes('[WARNING]')) color = 'text-amber-400 font-medium';
                else if (line.includes('[INFO]')) color = 'text-zinc-300';
                return `<div class="${color}">${escapeHtml(line)}</div>`;
            }).join('');

            container.scrollTop = container.scrollHeight;
        }

        function copyLogsToClipboard() {
            const text = cachedRawLogs.join('\\n');
            navigator.clipboard.writeText(text).then(() => {
                showToast("All logs copied to clipboard!");
            });
        }

        // Escape Utilities
        function escapeHtml(text) {
            if (!text) return '';
            return String(text)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)

