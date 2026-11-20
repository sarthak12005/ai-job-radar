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

import threading

# In-memory crawl and scheduler status
crawl_state = {
    "is_running": False,
    "last_run": None,
    "last_result": None
}

scheduler_state = {
    "target_time": "08:30 AM IST",
    "last_crawl_date": None,
    "active": True
}

def _background_crawl(send_mail: bool = False):
    crawl_state["is_running"] = True
    logger.info(f"Background crawl task started (send_mail={send_mail}).")
    try:
        excel_path, matched_jobs = run_pipeline(min_score=50, send_mail=send_mail)
        crawl_state["last_run"] = datetime.datetime.now().isoformat()
        crawl_state["last_result"] = {
            "success": True,
            "jobs_count": len(matched_jobs),
            "report": excel_path.name if excel_path else None
        }
        logger.info(f"Background crawl completed successfully. Found {len(matched_jobs)} matched jobs.")
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

@app.get("/api/jobs", tags=["Jobs"], summary="List & Filter Matched Jobs with Pagination")
def get_jobs(
    search: Optional[str] = Query(None, description="Search term matching title, company, skills, or summary"),
    status: Optional[str] = Query(None, description="Status filter: all, new, applied, contacted, interviewing, saved, rejected"),
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
def trigger_crawl(background_tasks: BackgroundTasks):
    """Triggers the job crawler asynchronously across Firecrawl, ATS, and remote portals."""
    if crawl_state["is_running"]:
        return {"status": "busy", "message": "Job crawl is already currently executing in background."}

    background_tasks.add_task(_background_crawl)
    return {"status": "started", "message": "Job crawler triggered in background. Database will update automatically."}

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
# Frontend Dashboard (HTML / Tailwind / JS) - Elevated with UI/UX Pro Max
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
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
    <!-- FontAwesome 6 -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
                        mono: ['JetBrains Mono', 'Fira Code', 'monospace']
                    },
                    colors: {
                        brand: {
                            50: '#f0f9ff',
                            100: '#e0f2fe',
                            200: '#bae6fd',
                            500: '#0284c7',
                            600: '#0369a1',
                            700: '#075985',
                            800: '#0c4a6e',
                            900: '#082f49'
                        },
                        surface: {
                            DEFAULT: '#0B1120',
                            card: '#0F172A',
                            hover: '#1E293B',
                            border: '#1E293B'
                        }
                    }
                }
            }
        }
    </script>
    <style>
        :root {
            color-scheme: dark;
        }
        body {
            font-family: 'Inter', system-ui, sans-serif;
            background-color: #070B14;
            color: #F8FAFC;
        }
        .font-mono-numbers {
            font-family: 'JetBrains Mono', monospace;
            font-variant-numeric: tabular-nums;
        }
        /* Custom scrollbar */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #070B14; }
        ::-webkit-scrollbar-thumb { background: #1E293B; border-radius: 9999px; }
        ::-webkit-scrollbar-thumb:hover { background: #334155; }
        /* Glassmorphism */
        .glass-panel {
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        .glass-card {
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.06);
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .glass-card:hover {
            border-color: rgba(56, 189, 248, 0.35);
            box-shadow: 0 10px 25px -5px rgba(2, 132, 199, 0.12), 0 8px 10px -6px rgba(0, 0, 0, 0.3);
            transform: translateY(-1.5px);
        }
        /* Accessible focus rings */
        button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
            outline: 2px solid #0284C7;
            outline-offset: 2px;
        }
        /* Animated gradient text */
        .gradient-text {
            background: linear-gradient(135deg, #38BDF8 0%, #818CF8 50%, #C084FC 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        /* Pulse glow */
        .pulse-glow {
            box-shadow: 0 0 15px rgba(2, 132, 199, 0.35);
        }
        @media (prefers-reduced-motion: reduce) {
            * {
                animation-duration: 0.01ms !important;
                transition-duration: 0.01ms !important;
            }
        }
    </style>
</head>
<body class="min-h-screen flex flex-col antialiased selection:bg-sky-500/30 selection:text-sky-200">

    <!-- Top Sticky Header Navigation -->
    <header class="sticky top-0 z-40 border-b border-slate-800/80 bg-[#070B14]/85 backdrop-blur-md">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between gap-4">
            <!-- Brand & Candidate Identity -->
            <div class="flex items-center space-x-3 min-w-0">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-sky-500 via-indigo-600 to-purple-600 flex items-center justify-center font-bold text-white text-lg shadow-lg shadow-sky-500/20 shrink-0">
                    <i class="fa-solid fa-radar text-white text-base"></i>
                </div>
                <div class="truncate">
                    <div class="flex items-center space-x-2">
                        <span class="font-extrabold text-base sm:text-lg tracking-tight text-white">Job Radar</span>
                        <span class="text-[11px] font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30 px-2 py-0.5 rounded-full">Sarthak Joshi</span>
                    </div>
                    <p class="text-[11px] text-slate-400 font-medium truncate hidden sm:block">1.8+ Yrs Exp • Full Stack / Backend: Node.js, NestJS, TypeScript, PostgreSQL, Redis, BullMQ</p>
                </div>
            </div>

            <!-- Header Action Controls -->
            <div class="flex items-center space-x-2 shrink-0">
                <!-- Swagger OpenAPI Docs -->
                <a href="/docs" target="_blank" rel="noopener noreferrer" class="cursor-pointer inline-flex items-center space-x-1.5 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold transition active:scale-95" title="Interactive Swagger UI & OpenAPI Documentation">
                    <i class="fa-solid fa-book-bookmark text-indigo-400 text-xs"></i>
                    <span class="hidden md:inline">Swagger API</span>
                </a>

                <!-- Live Logs Button -->
                <button onclick="openLogsModal()" class="cursor-pointer inline-flex items-center space-x-1.5 bg-slate-800/90 hover:bg-slate-700/90 text-slate-200 border border-slate-700 px-3 py-1.5 rounded-lg text-xs font-semibold transition active:scale-95" title="Inspect live rotating application logs">
                    <i class="fa-solid fa-terminal text-sky-400 text-xs"></i>
                    <span class="hidden sm:inline">Live Logs</span>
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse hidden sm:inline-block"></span>
                </button>

                <!-- 1-Click Job Crawler Button -->
                <button onclick="triggerCrawl()" id="triggerBtn" class="cursor-pointer inline-flex items-center space-x-2 bg-gradient-to-r from-sky-500 to-indigo-600 hover:from-sky-400 hover:to-indigo-500 text-white px-3.5 py-1.5 rounded-lg text-xs font-semibold shadow-md shadow-sky-500/20 transition active:scale-95">
                    <i class="fa-solid fa-arrows-rotate text-xs" id="triggerIcon"></i>
                    <span>Crawl Jobs</span>
                </button>

                <!-- Export Excel -->
                <a href="/api/export-excel" class="cursor-pointer inline-flex items-center space-x-1.5 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold transition active:scale-95" title="Download formatted Excel (.xlsx) report with hyperlinks">
                    <i class="fa-solid fa-file-excel text-emerald-400 text-xs"></i>
                    <span class="hidden lg:inline">Excel</span>
                </a>

                <!-- Database Reset / Clean Button -->
                <button onclick="confirmResetDB()" class="cursor-pointer text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/30 p-2 rounded-lg text-xs transition active:scale-95" title="Reset/Empty Database & Reports for a clean run" aria-label="Reset Database">
                    <i class="fa-solid fa-trash-can text-sm"></i>
                </button>
            </div>
        </div>

        <!-- Crawl Progress Alert Strip (Dynamic) -->
        <div id="crawlBanner" class="hidden border-t border-sky-500/30 bg-gradient-to-r from-sky-950/80 via-indigo-950/80 to-sky-950/80 px-4 py-2">
            <div class="max-w-7xl mx-auto flex items-center justify-between text-xs text-sky-200">
                <div class="flex items-center space-x-2.5">
                    <i class="fa-solid fa-circle-notch fa-spin text-sky-400 text-sm"></i>
                    <span class="font-medium">Active multi-source crawl in progress: Scanning Greenhouse ATS boards, Firecrawl live search &amp; remote portals...</span>
                </div>
                <span class="text-sky-300/80 text-[11px] font-mono-numbers">Background Task Active</span>
            </div>
        </div>
    </header>

    <!-- Main Content Area -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-7 space-y-6 flex-1 w-full">

        <!-- Notification Toast (UX Compliant) -->
        <div id="toast" role="status" aria-live="polite" class="hidden fixed bottom-6 right-6 z-50 glass-panel border border-slate-700 text-white px-4 py-3 rounded-xl shadow-2xl flex items-center space-x-3 transition-all duration-300 max-w-md">
            <i id="toastIcon" class="fa-solid fa-circle-check text-emerald-400 text-lg shrink-0"></i>
            <span id="toastMsg" class="text-xs sm:text-sm font-medium leading-snug">Notification</span>
            <button onclick="hideToast()" class="cursor-pointer text-slate-400 hover:text-white ml-2 text-xs" aria-label="Close notification">
                <i class="fa-solid fa-xmark"></i>
            </button>
        </div>

        <!-- Profile & Target Header Banner -->
        <div class="glass-panel rounded-2xl p-5 border border-slate-800 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
            <div class="flex items-center space-x-4">
                <div class="w-12 h-12 rounded-xl bg-gradient-to-tr from-sky-500 to-indigo-600 flex items-center justify-center text-white font-bold text-xl shadow-md shadow-sky-500/20 shrink-0">
                    SJ
                </div>
                <div>
                    <div class="flex items-center space-x-2">
                        <h1 class="text-lg font-bold text-white tracking-tight">Sarthak Joshi — Candidate Intelligence Profile</h1>
                        <span class="bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-[11px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1">
                            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Ready to Apply
                        </span>
                    </div>
                    <p class="text-xs text-slate-400 mt-0.5">
                        <span class="text-slate-300 font-medium">Core Stack:</span> NestJS, Node.js, TypeScript, PostgreSQL, Redis, BullMQ, Docker, Microservices • 
                        <span class="text-slate-300 font-medium">Exp:</span> 1.8+ Years (Target: 1-3 Yrs Roles)
                    </p>
                </div>
            </div>

            <!-- Resume & Automation Badges -->
            <div class="flex flex-wrap items-center gap-2 self-stretch md:self-auto justify-end text-xs">
                <span class="bg-slate-900 border border-slate-700/80 px-2.5 py-1 rounded-lg text-slate-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-file-pdf text-rose-400"></i>
                    <span class="font-medium">Resume: Nov 2026 PDF</span>
                </span>
                <span class="bg-slate-900 border border-slate-700/80 px-2.5 py-1 rounded-lg text-slate-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-clock text-sky-400"></i>
                    <span class="font-medium">Scheduled: Daily 8:30 AM IST</span>
                </span>
                <span class="bg-slate-900 border border-slate-700/80 px-2.5 py-1 rounded-lg text-slate-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-database text-emerald-400"></i>
                    <span class="font-medium">MongoDB: job_crawler_db</span>
                </span>
            </div>
        </div>

        <!-- KPI Metrics Grid (Data-Dense Dashboard) -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <!-- Total Discovered -->
            <div class="glass-card rounded-2xl p-4 sm:p-5 border border-slate-800">
                <div class="flex items-center justify-between text-slate-400 mb-2">
                    <span class="text-[11px] font-bold uppercase tracking-wider text-slate-400">Total Tracked</span>
                    <span class="w-7 h-7 rounded-lg bg-sky-500/10 text-sky-400 flex items-center justify-center text-xs">
                        <i class="fa-solid fa-briefcase"></i>
                    </span>
                </div>
                <div id="statTotal" class="text-2xl sm:text-3xl font-extrabold text-white font-mono-numbers">0</div>
                <div class="text-[11px] text-slate-400 mt-1 flex items-center justify-between">
                    <span>MongoDB Collection</span>
                    <span id="statDistinctCompanies" class="font-medium text-slate-300">0 Companies</span>
                </div>
            </div>

            <!-- High Matches (≥70%) -->
            <div class="glass-card rounded-2xl p-4 sm:p-5 border border-slate-800">
                <div class="flex items-center justify-between text-slate-400 mb-2">
                    <span class="text-[11px] font-bold uppercase tracking-wider text-emerald-400">High Matches</span>
                    <span class="w-7 h-7 rounded-lg bg-emerald-500/10 text-emerald-400 flex items-center justify-center text-xs">
                        <i class="fa-solid fa-bullseye"></i>
                    </span>
                </div>
                <div id="statHigh" class="text-2xl sm:text-3xl font-extrabold text-emerald-400 font-mono-numbers">0</div>
                <div class="text-[11px] text-slate-400 mt-1">
                    <span>≥ 70% Match Score Calibration</span>
                </div>
            </div>

            <!-- Cold Outreach Sent -->
            <div class="glass-card rounded-2xl p-4 sm:p-5 border border-slate-800">
                <div class="flex items-center justify-between text-slate-400 mb-2">
                    <span class="text-[11px] font-bold uppercase tracking-wider text-amber-400">Outreach Sent</span>
                    <span class="w-7 h-7 rounded-lg bg-amber-500/10 text-amber-400 flex items-center justify-center text-xs">
                        <i class="fa-solid fa-paper-plane"></i>
                    </span>
                </div>
                <div id="statContacted" class="text-2xl sm:text-3xl font-extrabold text-amber-400 font-mono-numbers">0</div>
                <div class="text-[11px] text-slate-400 mt-1">
                    <span>Direct Recruiter Cold Emails</span>
                </div>
            </div>

            <!-- Pipeline / Interviewing -->
            <div class="glass-card rounded-2xl p-4 sm:p-5 border border-slate-800">
                <div class="flex items-center justify-between text-slate-400 mb-2">
                    <span class="text-[11px] font-bold uppercase tracking-wider text-purple-400">Pipeline / Interviews</span>
                    <span class="w-7 h-7 rounded-lg bg-purple-500/10 text-purple-400 flex items-center justify-center text-xs">
                        <i class="fa-solid fa-handshake"></i>
                    </span>
                </div>
                <div id="statInterviewing" class="text-2xl sm:text-3xl font-extrabold text-purple-400 font-mono-numbers">0</div>
                <div class="text-[11px] text-slate-400 mt-1">
                    <span>Active Candidate Conversations</span>
                </div>
            </div>
        </div>

        <!-- Filter & Search Command Center -->
        <div class="glass-panel rounded-2xl p-4 sm:p-5 border border-slate-800 space-y-4">
            <!-- Search & Primary Filter Row -->
            <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
                <!-- Search Box with Clear Button -->
                <div class="relative flex-1">
                    <i class="fa-solid fa-magnifying-glass absolute left-3.5 top-3.5 text-slate-400 text-xs"></i>
                    <input type="text" id="searchInput" oninput="debounceFetch()" placeholder="Search by role, company, or stack (e.g. NestJS, Stripe, Bangalore, Redis)..." class="w-full bg-[#070B14] border border-slate-700/80 rounded-xl pl-9 pr-8 py-2.5 text-xs sm:text-sm text-slate-100 placeholder-slate-500 transition focus:border-sky-500">
                    <button id="clearSearchBtn" onclick="clearSearch()" class="hidden cursor-pointer absolute right-3 top-3 text-slate-400 hover:text-white text-xs" aria-label="Clear search">
                        <i class="fa-solid fa-circle-xmark"></i>
                    </button>
                </div>

                <!-- Match Score Presets & Slider -->
                <div class="flex flex-wrap items-center gap-2.5 bg-[#070B14] p-2 rounded-xl border border-slate-700/80">
                    <span class="text-xs text-slate-400 font-medium ml-1">Score:</span>
                    <!-- Preset pills -->
                    <button onclick="setScorePreset(0)" class="score-pill px-2 py-1 rounded text-[11px] font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition" data-score="0">All</button>
                    <button onclick="setScorePreset(50)" class="score-pill px-2 py-1 rounded text-[11px] font-semibold bg-sky-500 text-white transition" data-score="50">50%+</button>
                    <button onclick="setScorePreset(70)" class="score-pill px-2 py-1 rounded text-[11px] font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition" data-score="70">70%+ High</button>
                    <button onclick="setScorePreset(80)" class="score-pill px-2 py-1 rounded text-[11px] font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition" data-score="80">80%+ Prime</button>
                    <input type="range" id="scoreRange" min="0" max="100" value="50" step="5" oninput="updateScoreFilter(this.value)" class="w-20 accent-sky-500 cursor-pointer ml-1" title="Adjust minimum match score">
                    <span id="scoreLabel" class="text-xs font-bold text-sky-400 font-mono-numbers w-10 text-right">50%</span>
                </div>

                <!-- Sort Order Selector -->
                <div class="flex items-center space-x-2 shrink-0">
                    <select id="sortBy" onchange="fetchJobs()" class="bg-[#070B14] border border-slate-700/80 rounded-xl px-3 py-2.5 text-xs font-medium text-slate-200 cursor-pointer transition focus:border-sky-500">
                        <option value="match_score">Sort: Match Score (Highest)</option>
                        <option value="date">Sort: Date Scraped</option>
                        <option value="company">Sort: Company Name</option>
                    </select>
                </div>
            </div>

            <!-- Application Status Tabs with Live Count Badges -->
            <div class="flex items-center space-x-2 overflow-x-auto pb-1 text-xs border-t border-slate-800/80 pt-3" id="statusTabs">
                <button onclick="setStatusFilter('all')" class="status-tab cursor-pointer px-3 py-1.5 rounded-lg font-semibold transition bg-sky-500 text-white shrink-0 flex items-center gap-1.5" data-status="all">
                    <span>All Jobs</span>
                    <span id="tabCountAll" class="bg-white/20 text-white text-[10px] px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('new')" class="status-tab cursor-pointer px-3 py-1.5 rounded-lg font-semibold transition bg-slate-800/80 text-slate-400 hover:text-white hover:bg-slate-700 shrink-0 flex items-center gap-1.5" data-status="new">
                    <span>New / Unreviewed</span>
                    <span id="tabCountNew" class="bg-slate-700 text-slate-300 text-[10px] px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('applied')" class="status-tab cursor-pointer px-3 py-1.5 rounded-lg font-semibold transition bg-slate-800/80 text-slate-400 hover:text-white hover:bg-slate-700 shrink-0 flex items-center gap-1.5" data-status="applied">
                    <span>Applied</span>
                    <span id="tabCountApplied" class="bg-slate-700 text-slate-300 text-[10px] px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('contacted')" class="status-tab cursor-pointer px-3 py-1.5 rounded-lg font-semibold transition bg-slate-800/80 text-slate-400 hover:text-white hover:bg-slate-700 shrink-0 flex items-center gap-1.5" data-status="contacted">
                    <span>Cold Outreach Sent</span>
                    <span id="tabCountContacted" class="bg-slate-700 text-slate-300 text-[10px] px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('interviewing')" class="status-tab cursor-pointer px-3 py-1.5 rounded-lg font-semibold transition bg-slate-800/80 text-slate-400 hover:text-white hover:bg-slate-700 shrink-0 flex items-center gap-1.5" data-status="interviewing">
                    <span>Interviewing</span>
                    <span id="tabCountInterviewing" class="bg-slate-700 text-slate-300 text-[10px] px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
                <button onclick="setStatusFilter('saved')" class="status-tab cursor-pointer px-3 py-1.5 rounded-lg font-semibold transition bg-slate-800/80 text-slate-400 hover:text-white hover:bg-slate-700 shrink-0 flex items-center gap-1.5" data-status="saved">
                    <span>Saved</span>
                    <span id="tabCountSaved" class="bg-slate-700 text-slate-300 text-[10px] px-1.5 py-0.2 rounded-full font-mono-numbers">0</span>
                </button>
            </div>
        </div>

        <!-- Matched Job Opportunities Header & Results List -->
        <div class="space-y-4" id="jobsSection">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between text-xs text-slate-400 px-1 gap-2">
                <span>Displaying <strong id="jobsCount" class="font-bold text-white font-mono-numbers">0</strong> matching opportunities</span>
                <div class="flex items-center space-x-3">
                    <div class="flex items-center space-x-1.5 text-xs text-slate-400">
                        <span>Per Page:</span>
                        <select id="pageSizeSelect" onchange="changePageSize(this.value)" class="cursor-pointer bg-[#070B14] border border-slate-700/80 rounded-lg px-2 py-1 text-slate-200 text-xs font-semibold focus:border-sky-500">
                            <option value="10" selected>10</option>
                            <option value="20">20</option>
                            <option value="50">50</option>
                        </select>
                    </div>
                    <span id="syncStatus" class="flex items-center gap-1.5 text-slate-400">
                        <span class="w-2 h-2 rounded-full bg-emerald-400"></span> Synced with MongoDB
                    </span>
                </div>
            </div>

            <!-- Job Cards Container -->
            <div id="jobsList" class="space-y-3.5">
                <!-- Dynamically rendered job cards -->
            </div>

            <!-- Pagination Bar (UI/UX Pro Max) -->
            <div id="paginationBar" class="hidden glass-panel rounded-2xl p-4 border border-slate-800 flex flex-col sm:flex-row items-center justify-between gap-4">
                <div class="text-xs text-slate-400">
                    Showing <span id="pageRangeText" class="font-bold text-white font-mono-numbers">0-0</span> of <span id="pageTotalText" class="font-bold text-white font-mono-numbers">0</span> opportunities
                </div>

                <div class="flex items-center space-x-1.5">
                    <button id="prevPageBtn" onclick="prevPage()" class="cursor-pointer px-3 py-1.5 rounded-lg bg-slate-800 text-slate-300 hover:text-white hover:bg-slate-700 text-xs font-semibold disabled:opacity-30 disabled:cursor-not-allowed transition flex items-center gap-1" disabled>
                        <i class="fa-solid fa-chevron-left text-[10px]"></i>
                        <span>Prev</span>
                    </button>

                    <div id="pagePillsContainer" class="flex items-center space-x-1"></div>

                    <button id="nextPageBtn" onclick="nextPage()" class="cursor-pointer px-3 py-1.5 rounded-lg bg-slate-800 text-slate-300 hover:text-white hover:bg-slate-700 text-xs font-semibold disabled:opacity-30 disabled:cursor-not-allowed transition flex items-center gap-1" disabled>
                        <span>Next</span>
                        <i class="fa-solid fa-chevron-right text-[10px]"></i>
                    </button>
                </div>
            </div>
        </div>

    </main>

    <!-- Footer -->
    <footer class="border-t border-slate-800/80 bg-[#070B14] py-6 text-center text-xs text-slate-500">
        <p>Built for <strong class="text-slate-300">Sarthak Joshi</strong> — Full Stack &amp; Backend Engineer • Powered by Firecrawl &amp; FastAPI</p>
    </footer>

    <!-- ========================================== -->
    <!-- Live Application Logs Modal (UX Enhanced) -->
    <!-- ========================================== -->
    <div id="logsModal" class="hidden fixed inset-0 z-50 bg-[#070B14]/85 backdrop-blur-md flex items-center justify-center p-4">
        <div class="glass-panel border border-slate-700 max-w-4xl w-full rounded-2xl shadow-2xl overflow-hidden flex flex-col h-[82vh]">
            <!-- Terminal Header -->
            <div class="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between bg-slate-900/80">
                <div class="flex items-center space-x-2.5">
                    <div class="flex space-x-1.5 mr-2">
                        <span class="w-3 h-3 rounded-full bg-rose-500/80 inline-block"></span>
                        <span class="w-3 h-3 rounded-full bg-amber-500/80 inline-block"></span>
                        <span class="w-3 h-3 rounded-full bg-emerald-500/80 inline-block"></span>
                    </div>
                    <i class="fa-solid fa-terminal text-sky-400 text-xs"></i>
                    <h3 class="font-bold text-white text-sm">Application Runtime &amp; Crawler Logs</h3>
                    <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse ml-1.5"></span>
                </div>
                <div class="flex items-center space-x-3 text-xs">
                    <input type="text" id="logFilterInput" oninput="filterLogs()" placeholder="Filter logs (e.g. ERROR, INFO)..." class="bg-slate-950 border border-slate-700 rounded-lg px-2.5 py-1 text-slate-200 text-xs placeholder-slate-500 w-44 focus:outline-none focus:border-sky-500">
                    <label class="flex items-center space-x-1.5 text-slate-400 cursor-pointer">
                        <input type="checkbox" id="autoRefreshLogs" checked class="accent-sky-500 cursor-pointer">
                        <span>Auto-stream</span>
                    </label>
                    <button onclick="copyLogsToClipboard()" class="cursor-pointer text-slate-300 hover:text-white px-2 py-1 rounded bg-slate-800 hover:bg-slate-700" title="Copy all logs">
                        <i class="fa-solid fa-copy"></i>
                    </button>
                    <button onclick="closeLogsModal()" class="cursor-pointer text-slate-400 hover:text-white text-base ml-2" aria-label="Close logs">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </div>
            </div>

            <!-- Terminal Output Console -->
            <div id="logsConsole" class="flex-1 bg-[#050811] p-4 font-mono text-xs overflow-y-auto space-y-1 text-slate-300 leading-relaxed">
                <!-- Log entries dynamically appended -->
            </div>

            <!-- Terminal Footer -->
            <div class="px-5 py-2.5 border-t border-slate-800 bg-slate-900/60 flex items-center justify-between text-xs text-slate-400">
                <span>File destination: <code class="text-sky-400 font-mono">logs/app.log</code> (Rotating)</span>
                <button onclick="closeLogsModal()" class="cursor-pointer px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg font-medium">Close Console</button>
            </div>
        </div>
    </div>

    <!-- ========================================== -->
    <!-- Cold Outreach Modal (UX Pro Max Enhanced) -->
    <!-- ========================================== -->
    <div id="emailModal" class="hidden fixed inset-0 z-50 bg-[#070B14]/85 backdrop-blur-md flex items-center justify-center p-4">
        <div class="glass-panel border border-slate-700 max-w-2xl w-full rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh]">
            <!-- Modal Header -->
            <div class="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/70">
                <div class="flex items-center space-x-3">
                    <div class="w-9 h-9 rounded-xl bg-sky-500/15 text-sky-400 flex items-center justify-center font-bold">
                        <i class="fa-solid fa-paper-plane text-sm"></i>
                    </div>
                    <div>
                        <h3 class="font-bold text-white text-base">Direct Recruiter Cold Outreach</h3>
                        <p class="text-xs text-slate-400" id="modalCompanyHeader">Target: Recruiter</p>
                    </div>
                </div>
                <button onclick="closeEmailModal()" class="cursor-pointer text-slate-400 hover:text-white text-base" aria-label="Close modal">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>

            <!-- Modal Content Form -->
            <div class="p-6 overflow-y-auto space-y-4 text-xs">
                <div>
                    <label class="block text-slate-300 font-semibold mb-1">Recipient Email (HR / Hiring Manager / Recruiter):</label>
                    <input type="email" id="modalRecipient" placeholder="e.g. careers@company.com or recruiter@company.com" class="w-full bg-[#070B14] border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:border-sky-500">
                </div>

                <div>
                    <label class="block text-slate-300 font-semibold mb-1">Subject Line:</label>
                    <input type="text" id="modalSubject" class="w-full bg-[#070B14] border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:border-sky-500">
                </div>

                <div>
                    <div class="flex items-center justify-between mb-1">
                        <label class="block text-slate-300 font-semibold">Personalized Pitch Message:</label>
                        <button onclick="copyPitchText()" id="copyPitchBtn" class="cursor-pointer text-sky-400 hover:text-sky-300 font-medium flex items-center gap-1 text-[11px]">
                            <i class="fa-regular fa-copy"></i>
                            <span id="copyPitchLabel">Copy Pitch Text</span>
                        </button>
                    </div>
                    <textarea id="modalBody" rows="8" class="w-full bg-[#070B14] border border-slate-700 rounded-lg p-3 text-slate-100 text-sm font-sans leading-relaxed focus:border-sky-500"></textarea>
                </div>

                <!-- Verified Resume Attachment Notification -->
                <div class="bg-gradient-to-r from-sky-950/40 to-indigo-950/40 border border-sky-500/25 rounded-xl p-3 flex items-center space-x-3 text-sky-200">
                    <i class="fa-solid fa-file-pdf text-rose-400 text-2xl shrink-0"></i>
                    <div>
                        <div class="font-semibold text-xs text-white">Attached Resume (Nov 2026 Edition):</div>
                        <div class="text-[11px] text-slate-400 font-mono">sarthak_joshi_fullstack_developer_nov_2026.pdf will be automatically delivered with this email.</div>
                    </div>
                </div>
            </div>

            <!-- Modal Footer Actions -->
            <div class="px-6 py-4 border-t border-slate-800 bg-slate-900/50 flex items-center justify-between">
                <button onclick="closeEmailModal()" class="cursor-pointer px-4 py-2 rounded-lg text-slate-400 hover:text-white transition font-medium">Cancel</button>
                <button id="sendEmailBtn" onclick="dispatchColdEmail()" class="cursor-pointer inline-flex items-center space-x-2 bg-gradient-to-r from-sky-500 to-indigo-600 hover:from-sky-400 hover:to-indigo-500 text-white px-5 py-2 rounded-lg font-bold text-xs shadow-lg shadow-sky-500/25 transition active:scale-95">
                    <i class="fa-solid fa-paper-plane"></i>
                    <span>Send Cold Email Now</span>
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

        document.addEventListener('DOMContentLoaded', () => {
            fetchStats();
            fetchJobs(1);
            checkCrawlProgress();

            // Keyboard shortcut '/' to focus search input
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
                ? 'fa-solid fa-circle-check text-emerald-400 text-lg shrink-0' 
                : 'fa-solid fa-circle-exclamation text-rose-400 text-lg shrink-0';
            toast.classList.remove('hidden');
            setTimeout(() => {
                toast.classList.add('hidden');
            }, 4500);
        }

        function hideToast() {
            document.getElementById('toast').classList.add('hidden');
        }

        // Stats & Application Funnel Retrieval
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

                // Update tab count badges
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
                    btn.className = 'status-tab cursor-pointer px-3 py-1.5 rounded-lg font-semibold transition bg-sky-500 text-white shrink-0 flex items-center gap-1.5';
                } else {
                    btn.className = 'status-tab cursor-pointer px-3 py-1.5 rounded-lg font-semibold transition bg-slate-800/80 text-slate-400 hover:text-white hover:bg-slate-700 shrink-0 flex items-center gap-1.5';
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
            document.querySelectorAll('.score-pill').forEach(btn => {
                if (parseInt(btn.dataset.score) === val) {
                    btn.className = 'score-pill px-2 py-1 rounded text-[11px] font-semibold bg-sky-500 text-white transition';
                } else {
                    btn.className = 'score-pill px-2 py-1 rounded text-[11px] font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition';
                }
            });
            currentPage = 1;
            fetchJobs(1);
        }

        function updateScoreFilter(val) {
            currentScore = parseInt(val);
            document.getElementById('scoreLabel').innerText = val + '%';
            document.querySelectorAll('.score-pill').forEach(btn => {
                if (parseInt(btn.dataset.score) === currentScore) {
                    btn.className = 'score-pill px-2 py-1 rounded text-[11px] font-semibold bg-sky-500 text-white transition';
                } else {
                    btn.className = 'score-pill px-2 py-1 rounded text-[11px] font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition';
                }
            });
            currentPage = 1;
            fetchJobs(1);
        }

        function clearSearch() {
            document.getElementById('searchInput').value = '';
            document.getElementById('clearSearchBtn').classList.add('hidden');
            currentPage = 1;
            fetchJobs(1);
        }

        function debounceFetch() {
            const val = document.getElementById('searchInput').value;
            const clearBtn = document.getElementById('clearSearchBtn');
            if (val.trim()) {
                clearBtn.classList.remove('hidden');
            } else {
                clearBtn.classList.add('hidden');
            }
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                currentPage = 1;
                fetchJobs(1);
            }, 280);
        }

        // Fetch and Render Job Listings with Pagination
        async function fetchJobs(page = 1) {
            currentPage = page;
            const search = document.getElementById('searchInput').value;
            const sortBy = document.getElementById('sortBy').value;
            const container = document.getElementById('jobsList');
            const paginationBar = document.getElementById('paginationBar');

            let url = `/api/jobs?page=${currentPage}&limit=${pageSize}&min_score=${currentScore}&sort_by=${sortBy}`;
            if (currentStatus !== 'all') url += `&status=${currentStatus}`;
            if (search.trim()) url += `&search=${encodeURIComponent(search.trim())}`;

            try {
                const res = await fetch(url);
                const data = await res.json();
                const jobs = data.jobs || [];
                totalOpportunities = data.total !== undefined ? data.total : jobs.length;
                totalPages = data.total_pages || 1;
                document.getElementById('jobsCount').innerText = totalOpportunities;

                if (jobs.length === 0) {
                    paginationBar.classList.add('hidden');
                    if (totalJobsCount === 0) {
                        // Empty database state
                        container.innerHTML = `
                            <div class="glass-panel rounded-2xl p-12 text-center text-slate-400 border border-slate-800">
                                <div class="w-16 h-16 rounded-2xl bg-sky-500/10 text-sky-400 flex items-center justify-center text-2xl mx-auto mb-4">
                                    <i class="fa-solid fa-radar"></i>
                                </div>
                                <h3 class="text-base font-bold text-white">Database is clean and ready!</h3>
                                <p class="text-xs text-slate-400 mt-1 max-w-md mx-auto">Click below to start live automated discovery across Firecrawl, Greenhouse ATS, and remote developer job boards.</p>
                                <button onclick="triggerCrawl()" class="cursor-pointer mt-5 inline-flex items-center space-x-2 bg-gradient-to-r from-sky-500 to-indigo-600 hover:from-sky-400 hover:to-indigo-500 text-white px-5 py-2.5 rounded-xl font-bold text-xs shadow-lg shadow-sky-500/20 transition active:scale-95">
                                    <i class="fa-solid fa-arrows-rotate"></i>
                                    <span>Run Job Discovery Now</span>
                                </button>
                            </div>
                        `;
                    } else {
                        // Filter returned no results state
                        container.innerHTML = `
                            <div class="glass-panel rounded-2xl p-10 text-center text-slate-400 border border-slate-800">
                                <div class="w-12 h-12 rounded-xl bg-slate-800 text-slate-400 flex items-center justify-center text-xl mx-auto mb-3">
                                    <i class="fa-solid fa-filter-circle-xmark"></i>
                                </div>
                                <h3 class="text-sm font-bold text-white">No jobs matched your current criteria</h3>
                                <p class="text-xs text-slate-400 mt-1">Try lowering the minimum match score or clearing your search keywords.</p>
                                <div class="mt-4 flex items-center justify-center gap-3">
                                    <button onclick="setScorePreset(0); clearSearch(); setStatusFilter('all');" class="cursor-pointer px-4 py-2 bg-slate-800 hover:bg-slate-700 text-sky-400 text-xs font-semibold rounded-lg transition">
                                        Reset All Filters
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
                paginationBar.classList.add('hidden');
            }
        }

        // Pagination Bar Renderer (UI/UX Pro Max)
        function renderPagination(data) {
            const paginationBar = document.getElementById('paginationBar');
            if (!data || totalOpportunities === 0) {
                paginationBar.classList.add('hidden');
                return;
            }
            paginationBar.classList.remove('hidden');

            const start = (currentPage - 1) * pageSize + 1;
            const end = Math.min(totalOpportunities, currentPage * pageSize);
            document.getElementById('pageRangeText').innerText = `${start}–${end}`;
            document.getElementById('pageTotalText').innerText = totalOpportunities;

            const prevBtn = document.getElementById('prevPageBtn');
            const nextBtn = document.getElementById('nextPageBtn');
            prevBtn.disabled = currentPage <= 1;
            nextBtn.disabled = currentPage >= totalPages;

            const pillsContainer = document.getElementById('pagePillsContainer');
            let pillsHtml = '';

            let startPage = Math.max(1, currentPage - 2);
            let endPage = Math.min(totalPages, currentPage + 2);

            if (startPage > 1) {
                pillsHtml += `<button onclick="goToPage(1)" class="cursor-pointer px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">1</button>`;
                if (startPage > 2) {
                    pillsHtml += `<span class="text-slate-500 text-xs px-1">...</span>`;
                }
            }

            for (let p = startPage; p <= endPage; p++) {
                if (p === currentPage) {
                    pillsHtml += `<button class="px-2.5 py-1 rounded-lg text-xs font-bold bg-sky-500 text-white shadow-sm transition font-mono-numbers">${p}</button>`;
                } else {
                    pillsHtml += `<button onclick="goToPage(${p})" class="cursor-pointer px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white transition font-mono-numbers">${p}</button>`;
                }
            }

            if (endPage < totalPages) {
                if (endPage < totalPages - 1) {
                    pillsHtml += `<span class="text-slate-500 text-xs px-1">...</span>`;
                }
                pillsHtml += `<button onclick="goToPage(${totalPages})" class="cursor-pointer px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition font-mono-numbers">${totalPages}</button>`;
            }

            pillsContainer.innerHTML = pillsHtml;
        }

        function goToPage(p) {
            if (p < 1 || p > totalPages || p === currentPage) return;
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

        // Render Single Job Card with High Polish
        function renderJobCard(job) {
            const score = job.match_score || 0;
            let badgeBg = 'bg-slate-800 text-slate-300 border-slate-700';
            let dotColor = 'bg-slate-400';
            if (score >= 80) {
                badgeBg = 'bg-emerald-500/15 text-emerald-300 border-emerald-500/35';
                dotColor = 'bg-emerald-400';
            } else if (score >= 65) {
                badgeBg = 'bg-sky-500/15 text-sky-300 border-sky-500/35';
                dotColor = 'bg-sky-400';
            } else if (score >= 50) {
                badgeBg = 'bg-amber-500/15 text-amber-300 border-amber-500/35';
                dotColor = 'bg-amber-400';
            }

            // Company initial avatar color
            const company = job.company || 'Company';
            const initial = company.charAt(0).toUpperCase();

            // Skill Badges: Highlight Sarthak's primary stack
            const coreStack = ['nestjs', 'node.js', 'nodejs', 'typescript', 'postgresql', 'redis', 'bullmq', 'docker', 'mongodb'];
            const skillsHtml = (job.matched_skills || []).map(s => {
                const isCore = coreStack.includes(s.toLowerCase());
                if (isCore) {
                    return `<span class="bg-sky-500/15 text-sky-200 border border-sky-500/30 font-medium px-2 py-0.5 rounded text-[11px]">${s}</span>`;
                }
                return `<span class="bg-slate-800/90 text-slate-300 border border-slate-700/60 px-2 py-0.5 rounded text-[11px]">${s}</span>`;
            }).join(' ');

            const dateStr = job.date_posted || 'Recently posted';

            return `
                <div class="glass-card rounded-2xl p-5 border border-slate-800 flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div class="flex items-start space-x-3.5 flex-1 min-w-0">
                        <!-- Company Avatar -->
                        <div class="w-11 h-11 rounded-xl bg-gradient-to-tr from-slate-800 to-slate-700 border border-slate-700/80 flex items-center justify-center font-bold text-base text-white shrink-0 shadow-sm">
                            ${initial}
                        </div>

                        <!-- Info Content -->
                        <div class="space-y-1.5 flex-1 min-w-0">
                            <!-- Metatags row -->
                            <div class="flex flex-wrap items-center gap-2">
                                <span class="inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-0.5 rounded-full border ${badgeBg} font-mono-numbers">
                                    <span class="w-1.5 h-1.5 rounded-full ${dotColor}"></span>
                                    ${score}% Match
                                </span>
                                <span class="text-xs text-slate-400">•</span>
                                <span class="text-[11px] font-semibold text-slate-300 bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700/70">
                                    <i class="fa-solid fa-layer-group text-[10px] text-slate-400 mr-1"></i>${job.source || 'ATS Career Portal'}
                                </span>
                                <span class="text-xs text-slate-400">•</span>
                                <span class="text-[11px] text-slate-400 font-mono-numbers">${dateStr}</span>
                            </div>

                            <!-- Role & Company -->
                            <div>
                                <h3 class="font-bold text-white text-base leading-snug tracking-tight hover:text-sky-300 transition">${escapeHtml(job.title)}</h3>
                                <p class="text-xs text-slate-300 font-medium">
                                    ${escapeHtml(job.company)} 
                                    <span class="text-slate-500">•</span> 
                                    <span class="text-slate-400"><i class="fa-solid fa-location-dot text-[10px] mr-1"></i>${escapeHtml(job.location || 'Remote')}</span>
                                </p>
                            </div>

                            <!-- Match Summary -->
                            <p class="text-xs text-slate-400 leading-relaxed line-clamp-2">${escapeHtml(job.match_summary || '')}</p>

                            <!-- Matched Skills Chips -->
                            <div class="flex flex-wrap gap-1.5 pt-1">
                                ${skillsHtml}
                            </div>
                        </div>
                    </div>

                    <!-- Action Controls Right Column -->
                    <div class="flex flex-col sm:flex-row md:flex-col lg:flex-row items-stretch sm:items-center gap-2 border-t md:border-t-0 pt-3 md:pt-0 border-slate-800/80 shrink-0">
                        <!-- Quick Status Dropdown -->
                        <select onchange="updateJobStatus('${job._id}', this.value)" class="cursor-pointer bg-[#070B14] border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-slate-200 transition focus:border-sky-500" title="Update Application Status">
                            <option value="new" ${job.status === 'new' ? 'selected' : ''}>Status: New</option>
                            <option value="applied" ${job.status === 'applied' ? 'selected' : ''}>Status: Applied</option>
                            <option value="contacted" ${job.status === 'contacted' ? 'selected' : ''}>Status: Cold Contacted</option>
                            <option value="interviewing" ${job.status === 'interviewing' ? 'selected' : ''}>Status: Interviewing</option>
                            <option value="saved" ${job.status === 'saved' ? 'selected' : ''}>Status: Saved</option>
                            <option value="rejected" ${job.status === 'rejected' ? 'selected' : ''}>Status: Rejected</option>
                        </select>

                        <!-- Direct Career Link -->
                        <a href="${job.url}" target="_blank" rel="noopener noreferrer" class="cursor-pointer inline-flex items-center justify-center space-x-1.5 bg-slate-800/90 hover:bg-slate-700 text-slate-200 border border-slate-700 px-3 py-1.5 rounded-lg text-xs font-semibold transition active:scale-95" title="Open Career Portal Application Page">
                            <i class="fa-solid fa-arrow-up-right-from-square text-sky-400 text-xs"></i>
                            <span>Career Link</span>
                        </a>

                        <!-- Direct Cold Email Button -->
                        <button onclick="openEmailModal('${job._id}', '${escapeAttr(job.company)}', '${escapeAttr(job.contact_email || 'careers@company.com')}', '${encodeURIComponent(job.cold_outreach_subject || '')}', '${encodeURIComponent(job.cold_outreach_body || '')}')" class="cursor-pointer inline-flex items-center justify-center space-x-1.5 bg-sky-500/20 hover:bg-sky-500/30 text-sky-200 border border-sky-500/40 px-3.5 py-1.5 rounded-lg text-xs font-bold transition active:scale-95" title="Generate & send personalized recruiter cold email with attached resume">
                            <i class="fa-solid fa-paper-plane text-xs text-sky-300"></i>
                            <span>Cold Email</span>
                        </button>
                    </div>
                </div>
            `;
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

        // Cold Outreach Modal Open / Close
        function openEmailModal(jobId, company, contactEmail, encodedSubject, encodedBody) {
            activeJobId = jobId;
            document.getElementById('modalCompanyHeader').innerText = `Target Company: ${company}`;
            document.getElementById('modalRecipient').value = contactEmail;
            document.getElementById('modalSubject').value = decodeURIComponent(encodedSubject);
            document.getElementById('modalBody').value = decodeURIComponent(encodedBody);
            document.getElementById('copyPitchLabel').innerText = "Copy Pitch Text";
            document.getElementById('emailModal').classList.remove('hidden');
        }

        function closeEmailModal() {
            document.getElementById('emailModal').classList.add('hidden');
            activeJobId = null;
        }

        function copyPitchText() {
            const body = document.getElementById('modalBody').value;
            navigator.clipboard.writeText(body).then(() => {
                document.getElementById('copyPitchLabel').innerText = "Copied to Clipboard!";
                setTimeout(() => {
                    document.getElementById('copyPitchLabel').innerText = "Copy Pitch Text";
                }, 2500);
            }).catch(e => {
                console.error("Copy failed:", e);
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
            btn.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i><span>Sending via Gmail SMTP...</span>`;

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
                    showToast('🎉 Cold email dispatched successfully with attached resume!');
                    closeEmailModal();
                    fetchJobs();
                    fetchStats();
                } else {
                    showToast(data.detail || 'Failed to dispatch email', false);
                }
            } catch(e) {
                showToast('Network error sending email: ' + e, false);
            } finally {
                btn.disabled = false;
                btn.innerHTML = `<i class="fa-solid fa-paper-plane"></i><span>Send Cold Email Now</span>`;
            }
        }

        // Crawler Trigger & Background Progress Checking
        async function triggerCrawl() {
            const btn = document.getElementById('triggerBtn');
            const icon = document.getElementById('triggerIcon');
            const banner = document.getElementById('crawlBanner');
            btn.disabled = true;
            icon.classList.add('fa-spin');
            banner.classList.remove('hidden');

            try {
                const res = await fetch('/api/trigger', { method: 'POST' });
                const data = await res.json();
                showToast(data.message || 'Crawl initiated across ATS portals & Firecrawl!');
                checkCrawlProgress();
            } catch(e) {
                showToast('Failed to start crawler: ' + e, false);
                btn.disabled = false;
                icon.classList.remove('fa-spin');
                banner.classList.add('hidden');
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
                        banner.classList.add('hidden');
                        showToast('Discovery complete! Database updated.');
                        fetchStats();
                        fetchJobs();
                    } else {
                        banner.classList.remove('hidden');
                    }
                } catch(e) {
                    clearInterval(interval);
                }
            }, 2500);
        }

        // Reset Database Confirmation
        async function confirmResetDB() {
            if (confirm("Are you sure you want to empty MongoDB and clear all generated Excel reports? This enables a completely fresh crawl and test run.")) {
                try {
                    const res = await fetch('/api/database/clear', { method: 'DELETE' });
                    const data = await res.json();
                    if (res.ok) {
                        showToast("Database & reports successfully cleared!");
                        fetchStats();
                        fetchJobs();
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
            document.getElementById('logsModal').classList.remove('hidden');
            fetchLiveLogs();
            if (logsInterval) clearInterval(logsInterval);
            logsInterval = setInterval(() => {
                if (document.getElementById('autoRefreshLogs').checked) {
                    fetchLiveLogs();
                }
            }, 2000);
        }

        function closeLogsModal() {
            document.getElementById('logsModal').classList.add('hidden');
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

        function filterLogs() {
            renderLogs();
        }

        function renderLogs() {
            const container = document.getElementById('logsConsole');
            const query = (document.getElementById('logFilterInput').value || '').toLowerCase();
            const filtered = query 
                ? cachedRawLogs.filter(line => line.toLowerCase().includes(query))
                : cachedRawLogs;

            if (filtered.length === 0) {
                container.innerHTML = '<span class="text-slate-500 italic">No log entries matched.</span>';
                return;
            }

            container.innerHTML = filtered.map(line => {
                let color = 'text-slate-300';
                if (line.includes('[ERROR]')) color = 'text-rose-400 font-semibold';
                else if (line.includes('[WARNING]')) color = 'text-amber-400 font-medium';
                else if (line.includes('[INFO]')) color = 'text-sky-300';
                return `<div class="${color}">${escapeHtml(line)}</div>`;
            }).join('');

            container.scrollTop = container.scrollHeight;
        }

        function copyLogsToClipboard() {
            const text = cachedRawLogs.join('\\n');
            navigator.clipboard.writeText(text).then(() => {
                showToast("All log entries copied to clipboard!");
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

        function escapeAttr(text) {
            if (!text) return '';
            return String(text).replace(/'/g, "\\\\'").replace(/"/g, '&quot;');
        }
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)

