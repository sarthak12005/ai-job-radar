import sys
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler
import datetime

# Add root directory to python path for imports
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import config
from main import run_pipeline

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """
        Triggered automatically by Vercel Cron every day at 08:30 AM IST (03:00 UTC).
        """
        # SEC-05 Fix: Fail closed when CRON_SECRET is missing; use constant-time comparison
        import hmac
        cron_secret = os.getenv("CRON_SECRET", "").strip()
        auth_header = self.headers.get("Authorization", "").strip()
        if not cron_secret or not hmac.compare_digest(auth_header, f"Bearer {cron_secret}"):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized: valid CRON_SECRET required"}).encode("utf-8"))
            return

        try:
            excel_path, matched_jobs = run_pipeline(min_score=50, send_mail=True)
            top_jobs = [
                {
                    "title": j.get("title"),
                    "company": j.get("company"),
                    "match_score": j.get("match_score"),
                    "location": j.get("location"),
                    "url": j.get("url"),
                    "hr_email": j.get("contact_email")
                }
                for j in matched_jobs[:10]
            ]

            response_data = {
                "success": True,
                "triggered_by": "Vercel Cron (08:00 AM IST)",
                "timestamp": datetime.datetime.now().isoformat(),
                "candidate": config.CANDIDATE_NAME,
                "recipient_email": config.RECIPIENT_EMAIL,
                "total_matched_jobs": len(matched_jobs),
                "excel_report": excel_path.name if excel_path else None,
                "top_10_matches": top_jobs
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response_data, indent=2).encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))

