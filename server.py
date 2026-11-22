import os
import sys
import time
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

import config
from logger_config import get_logger, get_recent_logs
from database import db_instance
from main import run_pipeline
from mailer import Mailer
from excel_reporter import ExcelReporter

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    recipient_email: str
    subject: str
    body: str

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

@app.post("/api/jobs/{job_id}/send-cold-email", tags=["Cold Outreach"], summary="Send Cold Email to Recruiter")
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

@app.get("/api/logs", tags=["Logs & Monitoring"], summary="Live Application Logs")
def get_logs(lines: int = Query(100, ge=10, le=500, description="Number of recent log lines to fetch")):
    """Returns recent log entries from the rotating log file for live frontend streaming."""
    return {"logs": get_recent_logs(lines)}

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

        /* Unified Button System (5 distinct styles max) */
        .btn-primary {
            background-color: #FAFAFA;
            color: #09090B;
            font-weight: 500;
            font-size: 0.75rem;
            padding: 0.375rem 0.875rem;
            border-radius: 0.5rem;
            transition: all 150ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.375rem;
            cursor: pointer;
            box-shadow: 0 1px 2px rgba(0,0,0,0.2);
        }
        .btn-primary:hover { background-color: #E4E4E7; }
        .btn-primary:active { transform: scale(0.97); }

        .btn-secondary {
            background-color: #18181B;
            color: #E4E4E7;
            border: 1px solid #27272A;
            font-weight: 500;
            font-size: 0.75rem;
            padding: 0.375rem 0.75rem;
            border-radius: 0.5rem;
            transition: all 150ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.375rem;
            cursor: pointer;
        }
        .btn-secondary:hover { background-color: #27272A; color: #FFFFFF; }
        .btn-secondary:active { transform: scale(0.97); }

        .btn-outline {
            background-color: transparent;
            color: #A1A1AA;
            border: 1px solid #27272A;
            font-weight: 500;
            font-size: 0.75rem;
            padding: 0.375rem 0.75rem;
            border-radius: 0.5rem;
            transition: all 150ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.375rem;
            cursor: pointer;
        }
        .btn-outline:hover { background-color: #18181B; color: #FAFAFA; border-color: #3F3F46; }
        .btn-outline:active { transform: scale(0.97); }

        .btn-ghost {
            background-color: transparent;
            color: #A1A1AA;
            padding: 0.375rem 0.5rem;
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
            color: #71717A;
            padding: 0.375rem 0.5rem;
            border-radius: 0.5rem;
            transition: all 150ms ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
        }
        .btn-destructive:hover { background-color: rgba(239, 68, 68, 0.1); color: #F87171; }

        /* Crisp card containers */
        .card-surface {
            background-color: #121215;
            border: 1px solid #27272A;
        }
        .card-interactive {
            background-color: #121215;
            border: 1px solid #27272A;
            transition: border-color 150ms ease, background-color 150ms ease;
        }
        .card-interactive:hover {
            border-color: #3F3F46;
            background-color: #18181B;
        }

        button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
            outline: 2px solid #52525B;
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

        <!-- Notification Toast -->
        <div id="toast" role="status" aria-live="polite" style="display: none;" class="fixed bottom-5 right-5 z-50 card-surface border border-zinc-700 text-zinc-100 px-4 py-3 rounded-lg shadow-xl flex items-center space-x-3 transition-all max-w-md">
            <i id="toastIcon" class="fa-solid fa-circle-check text-emerald-400 text-sm shrink-0"></i>
            <span id="toastMsg" class="text-xs font-medium">Notification</span>
            <button onclick="hideToast()" class="btn-ghost ml-2" aria-label="Close notification">
                <i class="fa-solid fa-xmark text-xs"></i>
            </button>
        </div>

        <!-- Candidate Profile Header Bar -->
        <div class="card-surface rounded-xl p-4 border border-zinc-800 flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-lg bg-zinc-800 border border-zinc-700 flex items-center justify-center text-zinc-100 font-semibold text-sm shrink-0">
                    SJ
                </div>
                <div>
                    <div class="flex items-center space-x-2">
                        <h1 class="text-sm font-semibold text-zinc-100 tracking-tight">Sarthak Joshi — Candidate Profile</h1>
                        <span class="bg-zinc-800 text-zinc-300 border border-zinc-700 text-xs font-medium px-2 py-0.5 rounded-full flex items-center gap-1.5">
                            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Active
                        </span>
                    </div>
                    <p class="text-xs text-zinc-400 mt-0.5">
                        <span class="text-zinc-300 font-medium">Stack:</span> NestJS, Node.js, TypeScript, PostgreSQL, Redis, BullMQ, Docker • 
                        <span class="text-zinc-300 font-medium">Experience:</span> 1.8+ Years (Target: Mid/Junior 1–3 Yrs)
                    </p>
                </div>
            </div>

            <!-- Resume & Schedule Metadata -->
            <div class="flex flex-wrap items-center gap-2 text-xs">
                <span class="bg-zinc-900 border border-zinc-800 px-2.5 py-1 rounded-md text-zinc-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-file-pdf text-zinc-400 text-xs"></i>
                    <span>Nov 2026 PDF</span>
                </span>
                <span class="bg-zinc-900 border border-zinc-800 px-2.5 py-1 rounded-md text-zinc-300 flex items-center gap-1.5">
                    <i class="fa-regular fa-clock text-zinc-400 text-xs"></i>
                    <span>Daily 08:30 AM IST</span>
                </span>
                <span class="bg-zinc-900 border border-zinc-800 px-2.5 py-1 rounded-md text-zinc-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-database text-zinc-400 text-xs"></i>
                    <span>job_crawler_db</span>
                </span>
            </div>
        </div>

        <!-- KPI Metrics Grid (Data-Dense Dashboard - Issue 4 & 5 Fixed) -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <!-- Total Tracked -->
            <div class="card-surface rounded-xl p-4 border border-zinc-800">
                <div class="flex items-center justify-between text-zinc-400 mb-1.5">
                    <span class="text-xs font-medium text-zinc-400">Total Tracked</span>
                    <i class="fa-solid fa-briefcase text-zinc-500 text-xs"></i>
                </div>
                <div id="statTotal" class="text-2xl font-bold text-zinc-100 font-mono-numbers">0</div>
                <!-- Issue 4 Fixed: Body copy raised from 11px to 12px (text-xs) -->
                <p class="text-xs text-zinc-400 mt-1 flex items-center justify-between">
                    <span>MongoDB Database</span>
                    <span id="statDistinctCompanies" class="text-zinc-300 font-medium">0 Companies</span>
                </p>
            </div>

            <!-- High Matches (≥70%) -->
            <div class="card-surface rounded-xl p-4 border border-zinc-800">
                <div class="flex items-center justify-between text-zinc-400 mb-1.5">
                    <span class="text-xs font-medium text-emerald-400">High Matches</span>
                    <i class="fa-solid fa-bullseye text-emerald-400 text-xs"></i>
                </div>
                <div id="statHigh" class="text-2xl font-bold text-emerald-400 font-mono-numbers">0</div>
                <!-- Issue 4 Fixed: Body copy raised from 11px to 12px (text-xs) -->
                <p class="text-xs text-zinc-400 mt-1">≥ 70% Score Calibration</p>
            </div>

            <!-- Cold Outreach Sent -->
            <div class="card-surface rounded-xl p-4 border border-zinc-800">
                <div class="flex items-center justify-between text-zinc-400 mb-1.5">
                    <span class="text-xs font-medium text-zinc-300">Outreach Sent</span>
                    <i class="fa-solid fa-paper-plane text-zinc-400 text-xs"></i>
                </div>
                <div id="statContacted" class="text-2xl font-bold text-zinc-100 font-mono-numbers">0</div>
                <!-- Issue 4 Fixed: Body copy raised from 11px to 12px (text-xs) -->
                <p class="text-xs text-zinc-400 mt-1">Direct Recruiter Emails</p>
            </div>

            <!-- Pipeline / Interviews (Issue 5 Fixed: Removed all-caps, used sentence/title case) -->
            <div class="card-surface rounded-xl p-4 border border-zinc-800">
                <div class="flex items-center justify-between text-zinc-400 mb-1.5">
                    <span class="text-xs font-medium text-zinc-300">Pipeline &amp; Interviews</span>
                    <i class="fa-solid fa-handshake text-zinc-400 text-xs"></i>
                </div>
                <div id="statInterviewing" class="text-2xl font-bold text-zinc-100 font-mono-numbers">0</div>
                <!-- Issue 4 Fixed: Body copy raised from 11px to 12px (text-xs) -->
                <p class="text-xs text-zinc-400 mt-1">Active Conversations</p>
            </div>
        </div>

        <!-- Search & Filter Controls -->
        <div class="card-surface rounded-xl p-4 border border-zinc-800 space-y-3">
            <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
                <!-- Search Box -->
                <div class="relative flex-1">
                    <i class="fa-solid fa-magnifying-glass absolute left-3 top-3 text-zinc-500 text-xs"></i>
                    <input type="text" id="searchInput" oninput="debounceFetch()" placeholder="Filter by role, company, or tech stack (e.g. NestJS, Stripe, Redis)..." class="w-full bg-zinc-950 border border-zinc-800 rounded-lg pl-8 pr-8 py-2 text-xs text-zinc-100 placeholder-zinc-500 focus:border-zinc-600 focus:outline-none">
                    <button id="clearSearchBtn" onclick="clearSearch()" style="display: none;" class="btn-ghost absolute right-2 top-2 text-xs" aria-label="Clear search">
                        <i class="fa-solid fa-circle-xmark"></i>
                    </button>
                </div>

                <!-- Country & State / Hub Selectors -->
                <div class="flex flex-wrap items-center gap-2 bg-zinc-950 p-1.5 rounded-lg border border-zinc-800">
                    <div class="flex items-center space-x-1.5">
                        <i class="fa-solid fa-earth-americas text-zinc-400 text-xs ml-1"></i>
                        <span class="text-xs text-zinc-400 font-medium">Country:</span>
                        <select id="countryFilter" onchange="onCountryChange()" class="bg-zinc-900 border border-zinc-700/80 rounded-md px-2 py-1 text-xs text-zinc-100 font-medium focus:border-zinc-500 focus:outline-none cursor-pointer">
                            <option value="India" selected>India 🇮🇳</option>
                            <option value="United States">United States 🇺🇸</option>
                            <option value="Europe">Europe / UK 🇪🇺</option>
                            <option value="Remote">Worldwide Remote 🌐</option>
                            <option value="All">All Countries</option>
                        </select>
                    </div>

                    <div class="flex items-center space-x-1.5 border-l border-zinc-800 pl-2" id="stateFilterContainer">
                        <i class="fa-solid fa-location-dot text-zinc-400 text-xs"></i>
                        <span class="text-xs text-zinc-400 font-medium">Hub:</span>
                        <select id="stateFilter" onchange="onStateChange()" class="bg-zinc-900 border border-zinc-700/80 rounded-md px-2 py-1 text-xs text-zinc-100 font-medium focus:border-zinc-500 focus:outline-none cursor-pointer">
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

                <!-- Match Score Presets -->
                <div class="flex flex-wrap items-center gap-2 bg-zinc-950 p-1.5 rounded-lg border border-zinc-800">
                    <span class="text-xs text-zinc-400 font-medium ml-1">Score:</span>
                    <button onclick="setScorePreset(0)" class="score-btn btn-secondary text-xs" data-score="0">All</button>
                    <button onclick="setScorePreset(50)" class="score-btn btn-primary text-xs" data-score="50">50%+</button>
                    <button onclick="setScorePreset(70)" class="score-btn btn-secondary text-xs" data-score="70">70%+ High</button>
                    <button onclick="setScorePreset(80)" class="score-btn btn-secondary text-xs" data-score="80">80%+ Prime</button>
                    <input type="range" id="scoreRange" min="0" max="100" value="50" step="5" oninput="updateScoreFilter(this.value)" class="w-20 accent-zinc-200 cursor-pointer ml-1" title="Adjust minimum match score">
                    <span id="scoreLabel" class="text-xs font-semibold text-zinc-200 font-mono-numbers w-10 text-right">50%</span>
                </div>

                <!-- Sort Order Selector -->
                <div class="flex items-center space-x-2 shrink-0">
                    <select id="sortBy" onchange="fetchJobs(1)" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2.5 py-2 text-xs font-medium text-zinc-200 cursor-pointer focus:border-zinc-600 focus:outline-none">
                        <option value="match_score">Sort: Match Score</option>
                        <option value="date">Sort: Date Scraped</option>
                        <option value="company">Sort: Company Name</option>
                    </select>
                </div>
            </div>

            <!-- Application Status Tabs with Live Count Badges -->
            <div class="flex items-center space-x-2 overflow-x-auto pb-1 text-xs border-t border-zinc-800/80 pt-3" id="statusTabs">
                <button onclick="setStatusFilter('all')" class="status-tab btn-primary shrink-0" data-status="all">
                    <span>All Opportunities</span>
                    <span id="tabCountAll" class="bg-zinc-800 text-zinc-200 text-xs px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('new')" class="status-tab btn-secondary shrink-0" data-status="new">
                    <span>New</span>
                    <span id="tabCountNew" class="bg-zinc-800 text-zinc-400 text-xs px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('applied')" class="status-tab btn-secondary shrink-0" data-status="applied">
                    <span>Applied</span>
                    <span id="tabCountApplied" class="bg-zinc-800 text-zinc-400 text-xs px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('contacted')" class="status-tab btn-secondary shrink-0" data-status="contacted">
                    <span>Cold Outreach</span>
                    <span id="tabCountContacted" class="bg-zinc-800 text-zinc-400 text-xs px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('interviewing')" class="status-tab btn-secondary shrink-0" data-status="interviewing">
                    <span>Interviewing</span>
                    <span id="tabCountInterviewing" class="bg-zinc-800 text-zinc-400 text-xs px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('saved')" class="status-tab btn-secondary shrink-0" data-status="saved">
                    <span>Saved</span>
                    <span id="tabCountSaved" class="bg-zinc-800 text-zinc-400 text-xs px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
            </div>
        </div>

        <!-- Matched Job Opportunities Header & Results List -->
        <!-- Issue 6 Fixed: Proper Heading Hierarchy (H1 -> H2 -> H3) -->
        <div class="space-y-3" id="jobsSection">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between text-xs text-zinc-400 px-1 gap-2">
                <div class="flex items-center space-x-2">
                    <h2 class="text-sm font-semibold text-zinc-100">Discovered Opportunities</h2>
                    <span class="text-xs text-zinc-500 font-mono-numbers">(<span id="jobsCount">0</span> total)</span>
                </div>

                <!-- Top Pagination Toolbar (Quick Navigation) -->
                <div class="flex items-center space-x-3">
                    <div class="flex items-center space-x-1.5 text-xs text-zinc-400">
                        <span>Show:</span>
                        <select id="pageSizeSelect" onchange="changePageSize(this.value)" class="bg-zinc-950 border border-zinc-800 rounded-md px-2 py-1 text-zinc-200 text-xs font-medium focus:border-zinc-600 focus:outline-none cursor-pointer">
                            <option value="10" selected>10 per page</option>
                            <option value="20">20 per page</option>
                            <option value="50">50 per page</option>
                        </select>
                    </div>

                    <!-- Top Mini Pagination Controls -->
                    <div id="topPagination" style="display: none;" class="flex items-center space-x-1">
                        <button onclick="prevPage()" id="topPrevBtn" class="btn-secondary !p-1 !px-2 text-xs" title="Previous page">
                            <i class="fa-solid fa-chevron-left text-xs"></i>
                        </button>
                        <span id="topPageIndicator" class="text-xs text-zinc-300 font-mono-numbers px-2">Page 1 / 1</span>
                        <button onclick="nextPage()" id="topNextBtn" class="btn-secondary !p-1 !px-2 text-xs" title="Next page">
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
    <!-- Live Application Logs Modal -->
    <!-- ========================================== -->
    <div id="logsModal" style="display: none;" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="card-surface border border-zinc-700 max-w-4xl w-full rounded-xl shadow-2xl overflow-hidden flex flex-col h-[80vh]">
            <!-- Terminal Header -->
            <div class="px-4 py-3 border-b border-zinc-800 flex items-center justify-between bg-zinc-900">
                <div class="flex items-center space-x-2">
                    <i class="fa-solid fa-terminal text-zinc-400 text-xs"></i>
                    <span class="font-semibold text-zinc-100 text-xs">Application Runtime &amp; Crawler Logs</span>
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse ml-1"></span>
                </div>
                <div class="flex items-center space-x-2 text-xs">
                    <input type="text" id="logFilterInput" oninput="renderLogs()" placeholder="Filter log text..." class="bg-zinc-950 border border-zinc-800 rounded-md px-2 py-1 text-zinc-200 text-xs placeholder-zinc-500 w-36 focus:outline-none">
                    <label class="flex items-center space-x-1.5 text-zinc-400 cursor-pointer">
                        <input type="checkbox" id="autoRefreshLogs" checked class="accent-zinc-400 cursor-pointer">
                        <span>Stream</span>
                    </label>
                    <button onclick="copyLogsToClipboard()" class="btn-ghost" title="Copy all logs">
                        <i class="fa-regular fa-copy text-xs"></i>
                    </button>
                    <button onclick="closeLogsModal()" class="btn-ghost" aria-label="Close logs">
                        <i class="fa-solid fa-xmark text-xs"></i>
                    </button>
                </div>
            </div>

            <!-- Terminal Output Console -->
            <div id="logsConsole" class="flex-1 bg-[#050507] p-4 font-mono text-xs overflow-y-auto space-y-1 text-zinc-300 leading-relaxed"></div>

            <!-- Terminal Footer -->
            <div class="px-4 py-2 border-t border-zinc-800 bg-zinc-900 flex items-center justify-between text-xs text-zinc-500">
                <span>File destination: <code class="text-zinc-400 font-mono">logs/app.log</code> (Rotating)</span>
                <button onclick="closeLogsModal()" class="btn-secondary !py-1">Close</button>
            </div>
        </div>
    </div>

    <!-- ========================================== -->
    <!-- Cold Outreach Modal -->
    <!-- ========================================== -->
    <div id="emailModal" style="display: none;" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="card-surface border border-zinc-700 max-w-2xl w-full rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
            <!-- Modal Header -->
            <div class="px-5 py-3.5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900">
                <div class="flex items-center space-x-2.5">
                    <i class="fa-solid fa-paper-plane text-zinc-400 text-xs"></i>
                    <div>
                        <h3 class="font-semibold text-zinc-100 text-sm">Direct Recruiter Outreach</h3>
                        <p class="text-xs text-zinc-400" id="modalCompanyHeader">Target: Recruiter</p>
                    </div>
                </div>
                <button onclick="closeEmailModal()" class="btn-ghost" aria-label="Close modal">
                    <i class="fa-solid fa-xmark text-xs"></i>
                </button>
            </div>

            <!-- Modal Content Form -->
            <div class="p-5 overflow-y-auto space-y-3.5 text-xs">
                <div>
                    <label class="block text-zinc-300 font-medium mb-1">Recipient Email:</label>
                    <input type="email" id="modalRecipient" placeholder="e.g. careers@company.com" class="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-zinc-100 text-xs focus:border-zinc-600 focus:outline-none">
                </div>

                <div>
                    <label class="block text-zinc-300 font-medium mb-1">Subject Line:</label>
                    <input type="text" id="modalSubject" class="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-zinc-100 text-xs focus:border-zinc-600 focus:outline-none">
                </div>

                <div>
                    <div class="flex items-center justify-between mb-1">
                        <label class="block text-zinc-300 font-medium">Personalized Cold Pitch:</label>
                        <button onclick="copyPitchText()" id="copyPitchBtn" class="btn-ghost !p-1 text-xs text-zinc-400 hover:text-zinc-200">
                            <i class="fa-regular fa-copy mr-1"></i>
                            <span id="copyPitchLabel">Copy Text</span>
                        </button>
                    </div>
                    <textarea id="modalBody" rows="7" class="w-full bg-zinc-950 border border-zinc-800 rounded-lg p-3 text-zinc-100 text-xs font-sans leading-relaxed focus:border-zinc-600 focus:outline-none"></textarea>
                </div>

                <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-3 flex items-center space-x-3 text-zinc-300">
                    <i class="fa-solid fa-file-pdf text-zinc-400 text-xl shrink-0"></i>
                    <div>
                        <div class="font-medium text-xs text-zinc-200">Verified Resume Attachment:</div>
                        <div class="text-xs text-zinc-400 font-mono">sarthak_joshi_fullstack_developer_nov_2026.pdf will be attached automatically.</div>
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
    <!-- Targeted Crawl Discovery Modal -->
    <!-- ========================================== -->
    <div id="crawlModal" style="display: none;" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="card-surface border border-zinc-700 max-w-lg w-full rounded-xl shadow-2xl overflow-hidden flex flex-col">
            <!-- Modal Header -->
            <div class="px-5 py-3.5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900">
                <div class="flex items-center space-x-2.5">
                    <i class="fa-solid fa-radar text-zinc-400 text-xs"></i>
                    <div>
                        <h3 class="font-semibold text-zinc-100 text-sm">Targeted Job Discovery</h3>
                        <p class="text-xs text-zinc-400">Search by Country, State / Tech Hub &amp; Role</p>
                    </div>
                </div>
                <button onclick="closeCrawlModal()" class="btn-ghost" aria-label="Close modal">
                    <i class="fa-solid fa-xmark text-xs"></i>
                </button>
            </div>

            <!-- Modal Content Form -->
            <div class="p-5 space-y-4 text-xs">
                <div>
                    <label class="block text-zinc-300 font-medium mb-1.5">Target Country:</label>
                    <select id="crawlCountry" onchange="onCrawlCountryChange()" class="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-zinc-100 text-xs focus:border-zinc-600 focus:outline-none cursor-pointer">
                        <option value="India" selected>India 🇮🇳 (Target Hubs &amp; Local Tech Opportunities)</option>
                        <option value="United States">United States 🇺🇸</option>
                        <option value="Europe">Europe / UK 🇪🇺</option>
                        <option value="Remote">Worldwide Remote 🌐</option>
                        <option value="All">All Countries / Unrestricted</option>
                    </select>
                </div>

                <div>
                    <label class="block text-zinc-300 font-medium mb-1.5">Target Tech Hub / State:</label>
                    <select id="crawlState" class="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-zinc-100 text-xs focus:border-zinc-600 focus:outline-none cursor-pointer">
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
                    <label class="block text-zinc-300 font-medium mb-1.5">Minimum Match Score Threshold:</label>
                    <div class="flex items-center space-x-3 bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2">
                        <input type="range" id="crawlScoreRange" min="20" max="80" value="50" step="5" oninput="document.getElementById('crawlScoreVal').innerText = this.value + '%'" class="flex-1 accent-zinc-200 cursor-pointer">
                        <span id="crawlScoreVal" class="text-xs font-semibold text-zinc-200 font-mono-numbers w-10 text-right">50%</span>
                    </div>
                </div>

                <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-3 space-y-1.5">
                    <label class="flex items-center space-x-2 text-zinc-300 cursor-pointer">
                        <input type="checkbox" id="crawlSendMail" class="accent-zinc-400 cursor-pointer">
                        <span class="font-medium text-xs">Email Daily Briefing &amp; Excel report upon completion</span>
                    </label>
                    <p class="text-xs text-zinc-500 pl-5">Automatically dispatches the candidate summary and workbook to recipient email.</p>
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
        let logsInterval = null;
        let cachedRawLogs = [];
        let totalJobsCount = 0;
        let currentPage = 1;
        let pageSize = 10;
        let totalPages = 1;
        let totalOpportunities = 0;
        window.allJobsMap = new Map();

        document.addEventListener('DOMContentLoaded', () => {
            fetchStats();
            fetchJobs(1);
            checkCrawlProgress();

            window.addEventListener('keydown', (e) => {
                if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
                    e.preventDefault();
                    document.getElementById('searchInput').focus();
                } else if (e.key === 'Escape') {
                    closeEmailModal();
                    closeLogsModal();
                }
            });
        });

        // Toast Feedback System
        function showToast(msg, isSuccess = true) {
            const toast = document.getElementById('toast');
            const toastMsg = document.getElementById('toastMsg');
            const toastIcon = document.getElementById('toastIcon');
            toastMsg.innerText = msg;
            toastIcon.className = isSuccess 
                ? 'fa-solid fa-circle-check text-emerald-400 text-sm shrink-0' 
                : 'fa-solid fa-circle-exclamation text-rose-400 text-sm shrink-0';
            toast.style.display = 'flex';
            setTimeout(() => {
                toast.style.display = 'none';
            }, 4500);
        }

        function hideToast() {
            document.getElementById('toast').style.display = 'none';
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
                if (btn.dataset.status === status) {
                    btn.className = 'status-tab btn-primary shrink-0';
                } else {
                    btn.className = 'status-tab btn-secondary shrink-0';
                }
            });
            currentPage = 1;
            fetchJobs(1);
        }

        // Score Filtering
        function setScorePreset(val) {
            currentScore = val;
            document.getElementById('scoreRange').value = val;
            document.getElementById('scoreLabel').innerText = val + '%';
            document.querySelectorAll('.score-btn').forEach(btn => {
                if (parseInt(btn.dataset.score) === val) {
                    btn.className = 'score-btn btn-primary text-xs';
                } else {
                    btn.className = 'score-btn btn-secondary text-xs';
                }
            });
            currentPage = 1;
            fetchJobs(1);
        }

        function updateScoreFilter(val) {
            currentScore = parseInt(val);
            document.getElementById('scoreLabel').innerText = val + '%';
            document.querySelectorAll('.score-btn').forEach(btn => {
                if (parseInt(btn.dataset.score) === currentScore) {
                    btn.className = 'score-btn btn-primary text-xs';
                } else {
                    btn.className = 'score-btn btn-secondary text-xs';
                }
            });
            currentPage = 1;
            fetchJobs(1);
        }

        function clearSearch() {
            document.getElementById('searchInput').value = '';
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
            document.getElementById('crawlModal').style.display = 'flex';
        }

        function closeCrawlModal() {
            document.getElementById('crawlModal').style.display = 'none';
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
                            <div class="card-surface rounded-xl p-10 text-center text-zinc-400 border border-zinc-800">
                                <div class="w-12 h-12 rounded-lg bg-zinc-800 text-zinc-400 flex items-center justify-center text-xl mx-auto mb-3">
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
                        container.innerHTML = `
                            <div class="card-surface rounded-xl p-8 text-center text-zinc-400 border border-zinc-800">
                                <h3 class="text-sm font-semibold text-zinc-100">No jobs matched your current criteria</h3>
                                <p class="text-xs text-zinc-400 mt-1">Try lowering the minimum score or clearing search keywords.</p>
                                <button onclick="setScorePreset(0); clearSearch(); setStatusFilter('all');" class="btn-secondary mt-3">
                                    Reset Filters
                                </button>
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

        // Render Single Job Card with High Polish (Issue 6 Fixed: h3 properly nested under h2)
        function renderJobCard(job) {
            const score = job.match_score || 0;
            let badgeBg = 'bg-zinc-800 text-zinc-400 border-zinc-700';
            let dotColor = 'bg-zinc-400';
            if (score >= 80) {
                badgeBg = 'bg-emerald-950/60 text-emerald-400 border-emerald-800/80';
                dotColor = 'bg-emerald-400';
            } else if (score >= 65) {
                badgeBg = 'bg-zinc-800 text-zinc-200 border-zinc-700';
                dotColor = 'bg-zinc-300';
            } else if (score >= 50) {
                badgeBg = 'bg-amber-950/50 text-amber-400 border-amber-800/80';
                dotColor = 'bg-amber-400';
            }

            const company = job.company || 'Company';
            const initial = company.charAt(0).toUpperCase();

            // Highlight primary core skills
            const coreStack = ['nestjs', 'node.js', 'nodejs', 'typescript', 'postgresql', 'redis', 'bullmq', 'docker', 'mongodb'];
            const skillsHtml = (job.matched_skills || []).map(s => {
                const isCore = coreStack.includes(s.toLowerCase());
                if (isCore) {
                    return `<span class="bg-zinc-800 text-zinc-200 border border-zinc-600 font-medium px-2 py-0.5 rounded text-xs">${s}</span>`;
                }
                return `<span class="bg-zinc-900 text-zinc-400 border border-zinc-800 px-2 py-0.5 rounded text-xs">${s}</span>`;
            }).join(' ');

            const dateStr = job.date_posted || 'Recently posted';

            return `
                <div class="card-interactive rounded-xl p-4 border border-zinc-800 flex flex-col md:flex-row md:items-center justify-between gap-3">
                    <div class="flex items-start space-x-3 flex-1 min-w-0">
                        <!-- Company Avatar -->
                        <div class="w-9 h-9 rounded-lg bg-zinc-800 border border-zinc-700 flex items-center justify-center font-semibold text-xs text-zinc-200 shrink-0">
                            ${initial}
                        </div>

                        <!-- Info Content -->
                        <div class="space-y-1 flex-1 min-w-0">
                            <div class="flex flex-wrap items-center gap-2">
                                <span class="inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-0.5 rounded border ${badgeBg} font-mono-numbers">
                                    <span class="w-1.5 h-1.5 rounded-full ${dotColor}"></span>
                                    ${score}% Match
                                </span>
                                <span class="text-xs text-zinc-600">•</span>
                                <span class="text-xs text-zinc-400 bg-zinc-900 px-2 py-0.5 rounded border border-zinc-800">
                                    ${job.source || 'ATS Portal'}
                                </span>
                                <span class="text-xs text-zinc-600">•</span>
                                <span class="text-xs text-zinc-500 font-mono-numbers">${dateStr}</span>
                            </div>

                            <!-- Role Title (h3 under h2) -->
                            <div>
                                <h3 class="font-semibold text-zinc-100 text-sm leading-snug tracking-tight hover:text-white transition">${escapeHtml(job.title)}</h3>
                                <p class="text-xs text-zinc-400 flex items-center gap-1.5 flex-wrap">
                                    <span class="text-zinc-300 font-medium">${escapeHtml(job.company)}</span>
                                    <span class="text-zinc-600">•</span>
                                    <span class="inline-flex items-center gap-1 text-zinc-300">
                                        <i class="fa-solid fa-location-dot text-zinc-400 text-[10px]"></i>
                                        ${escapeHtml(job.location || 'Remote')}
                                    </span>
                                    ${job.country && job.country !== 'Other' ? `<span class="bg-zinc-800 text-zinc-400 border border-zinc-700/80 px-1.5 py-0.2 rounded text-[10px] font-mono">${escapeHtml(job.country)}</span>` : ''}
                                </p>
                            </div>

                            <p class="text-xs text-zinc-400 leading-relaxed line-clamp-2">${escapeHtml(job.match_summary || '')}</p>

                            <div class="flex flex-wrap gap-1 pt-0.5">
                                ${skillsHtml}
                            </div>
                        </div>
                    </div>

                    <!-- Actions -->
                    <div class="flex flex-col sm:flex-row md:flex-col lg:flex-row items-stretch sm:items-center gap-2 border-t md:border-t-0 pt-3 md:pt-0 border-zinc-800 shrink-0">
                        <select onchange="updateJobStatus('${job._id}', this.value)" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs font-medium text-zinc-200 focus:border-zinc-600 focus:outline-none cursor-pointer" title="Update Application Status">
                            <option value="new" ${job.status === 'new' ? 'selected' : ''}>Status: New</option>
                            <option value="applied" ${job.status === 'applied' ? 'selected' : ''}>Status: Applied</option>
                            <option value="contacted" ${job.status === 'contacted' ? 'selected' : ''}>Status: Contacted</option>
                            <option value="interviewing" ${job.status === 'interviewing' ? 'selected' : ''}>Status: Interviewing</option>
                            <option value="saved" ${job.status === 'saved' ? 'selected' : ''}>Status: Saved</option>
                            <option value="rejected" ${job.status === 'rejected' ? 'selected' : ''}>Status: Rejected</option>
                        </select>

                        <a href="${job.url}" target="_blank" rel="noopener noreferrer" class="btn-secondary" title="Open Career Portal Application Page">
                            <i class="fa-solid fa-arrow-up-right-from-square text-xs text-zinc-400"></i>
                            <span>Career Link</span>
                        </a>

                        <button onclick="openEmailModalForJob('${job._id}')" class="btn-outline text-zinc-200" title="Generate & send personalized recruiter cold email">
                            <i class="fa-solid fa-paper-plane text-xs text-zinc-400"></i>
                            <span>Cold Email</span>
                        </button>
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

        // Job Status Update via API
        async function updateJobStatus(jobId, newStatus) {
            try {
                const res = await fetch(`/api/jobs/${jobId}/status`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus })
                });
                if (res.ok) {
                    showToast(`Status updated to ${newStatus.toUpperCase()}`);
                    fetchStats();
                } else {
                    showToast('Failed to update status', false);
                }
            } catch (e) {
                showToast('Error updating status: ' + e, false);
            }
        }

        // Cold Outreach Modal
        function openEmailModalForJob(jobId) {
            const job = window.allJobsMap.get(jobId);
            if (!job) return;
            activeJobId = jobId;
            document.getElementById('modalCompanyHeader').innerText = `Target Company: ${job.company || 'Company'}`;
            document.getElementById('modalRecipient').value = job.contact_email || 'careers@company.com';
            document.getElementById('modalSubject').value = job.cold_outreach_subject || `Application for ${job.title || 'Role'} - Sarthak Joshi`;
            document.getElementById('modalBody').value = job.cold_outreach_body || '';
            document.getElementById('copyPitchLabel').innerText = "Copy Text";
            document.getElementById('emailModal').style.display = 'flex';
        }

        function closeEmailModal() {
            document.getElementById('emailModal').style.display = 'none';
            activeJobId = null;
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

        // Terminal Live Logs Modal Functions
        function openLogsModal() {
            document.getElementById('logsModal').style.display = 'flex';
            fetchLiveLogs();
            if (logsInterval) clearInterval(logsInterval);
            logsInterval = setInterval(() => {
                if (document.getElementById('autoRefreshLogs').checked) {
                    fetchLiveLogs();
                }
            }, 2000);
        }

        function closeLogsModal() {
            document.getElementById('logsModal').style.display = 'none';
            if (logsInterval) clearInterval(logsInterval);
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

