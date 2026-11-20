import sys
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler
import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import config
from main import run_pipeline

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def _handle_request(self):
        """Manual trigger point accessible via browser, curl, webhook, or Postman."""
        try:
            # Run pipeline
            excel_path, matched_jobs = run_pipeline(min_score=50, send_mail=True)
            
            top_jobs = [
                {
                    "title": j.get("title"),
                    "company": j.get("company"),
                    "match_score": f"{j.get('match_score')}%",
                    "location": j.get("location"),
                    "apply_url": j.get("url"),
                    "hr_email": j.get("contact_email")
                }
                for j in matched_jobs[:10]
            ]

            response = {
                "status": "success",
                "message": "Job crawler triggered successfully!",
                "triggered_at": datetime.datetime.now().isoformat(),
                "candidate": config.CANDIDATE_NAME,
                "jobs_found": len(matched_jobs),
                "report_generated": excel_path.name if excel_path else "None",
                "email_dispatched_to": config.RECIPIENT_EMAIL,
                "sample_top_matches": top_jobs
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(response, indent=2).encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))

