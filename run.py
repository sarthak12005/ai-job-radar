import sys
import os
import time
import webbrowser
import threading
import argparse

def check_environment():
    import config
    from database import db_instance

    print("==================================================")
    print("🚀 Sarthak Joshi — Job Radar & Outreach Hub")
    print("==================================================")

    # 1. MongoDB Check
    mongo_ok = db_instance.is_connected()
    mongo_status = "✅ Connected (job_crawler_db)" if mongo_ok else "❌ Offline (Start MongoDB on 27017)"
    print(f"• MongoDB:   {mongo_status}")

    # 2. Firecrawl Check
    fc_ok = bool(config.FIRECRAWL_API_KEY)
    fc_status = "✅ Active (API Key Configured)" if fc_ok else "⚠️ Keyless Mode"
    print(f"• Firecrawl: {fc_status}")

    # 3. SMTP Check
    smtp_ok = bool(config.SENDER_PASSWORD)
    smtp_status = f"✅ Ready ({config.SENDER_EMAIL})" if smtp_ok else "⚠️ Password not set in .env"
    print(f"• Email/SMTP: {smtp_status}")

    # 4. Resume Check
    resume_p = config.RESUME_PATH
    resume_ok = os.path.exists(resume_p)
    resume_status = f"✅ Loaded ({os.path.basename(resume_p)})" if resume_ok else "❌ File not found"
    print(f"• Resume:    {resume_status}")

    print("--------------------------------------------------")
    print("🌐 Dashboard URL: http://127.0.0.1:8000")
    print("📖 API Docs:      http://127.0.0.1:8000/docs")
    print("==================================================\n")

def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8000")

def main():
    parser = argparse.ArgumentParser(description="Start Sarthak's Job Radar System")
    parser.add_argument("--crawl", action="store_true", help="Run crawl and exit")
    parser.add_argument("--mail", action="store_true", help="Send daily email briefing and exit")
    parser.add_argument("--port", type=int, default=8000, help="Port to run server on (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    if args.crawl:
        from main import run_pipeline
        run_pipeline(min_score=50, send_mail=False)
        return

    if args.mail:
        from main import run_pipeline
        run_pipeline(min_score=50, send_mail=True)
        return

    # Normal mode: Start Web Application
    check_environment()

    if not args.no_browser:
        threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=args.port, reload=True)

if __name__ == "__main__":
    main()

