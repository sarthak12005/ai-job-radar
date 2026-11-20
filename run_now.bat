@echo off
REM ==============================================================
REM Run Job Crawler & Emailer Immediately
REM ==============================================================
title Running Daily Job Crawler

echo --------------------------------------------------------------
echo Starting Job Crawler and Matcher...
echo --------------------------------------------------------------

cd /d "%~dp0"
python main.py

echo.
echo --------------------------------------------------------------
echo Finished! Check the reports/ folder for the Excel report.
echo --------------------------------------------------------------
pause

