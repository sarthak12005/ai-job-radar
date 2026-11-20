@echo off
REM ==============================================================
REM Start Sarthak's Job Radar & Cold Outreach Hub (FastAPI + MongoDB)
REM ==============================================================
title Job Radar & Outreach Hub - Sarthak Joshi

echo ==============================================================
echo 🚀 Launching Job Radar & Cold Outreach Dashboard
echo Connecting to local MongoDB & Firecrawl Engine...
echo ==============================================================

cd /d "%~dp0"

REM Open browser after 2 seconds
start "" http://127.0.0.1:8000

REM Run the FastAPI web application
python -m uvicorn server:app --host 127.0.0.1 --port 8000 --reload

pause

