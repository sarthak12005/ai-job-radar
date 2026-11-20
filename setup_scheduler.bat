@echo off
REM ==============================================================
REM Windows Task Scheduler Setup for Daily 8:30 AM Job Crawler
REM ==============================================================
title Setup Daily 8:30 AM Job Crawler Scheduler

echo --------------------------------------------------------------
echo Setting up Daily Job Crawler at 8:30 AM for Sarthak Joshi...
echo --------------------------------------------------------------

set TASK_NAME=DailyJobCrawler_Sarthak
set SCRIPT_DIR=%~dp0
set PYTHON_EXE=python
set SCRIPT_PATH=%SCRIPT_DIR%main.py

echo Script Directory: %SCRIPT_DIR%
echo Target Execution Time: 08:30 AM Everyday

REM Create or update Scheduled Task in Windows Task Scheduler
schtasks /create /tn "%TASK_NAME%" /tr "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\"" /sc daily /st 08:30 /f /rl HIGHEST

if %ERRORLEVEL% equ 0 (
    echo.
    echo ==============================================================
    echo [SUCCESS] Scheduled Task '%TASK_NAME%' created successfully!
    echo It will trigger automatically every day at 08:30 AM.
    echo ==============================================================
) else (
    echo.
    echo [NOTICE] Running without elevated administrator privileges.
    echo Creating task in user context...
    schtasks /create /tn "%TASK_NAME%" /tr "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\"" /sc daily /st 08:30 /f
)

pause

