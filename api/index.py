import sys
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import config

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Serves an interactive Web Dashboard with a 1-click trigger button."""
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Job Crawler &amp; Matcher Dashboard | Sarthak Joshi</title>
            <style>
                * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
                body {{ background: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }}
                .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 16px; max-width: 720px; width: 100%; padding: 32px; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5); }}
                .badge {{ display: inline-block; background: #0284c7; color: #ffffff; padding: 4px 12px; border-radius: 9999px; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; }}
                h1 {{ font-size: 26px; font-weight: 700; margin-bottom: 8px; color: #ffffff; }}
                p.subtitle {{ color: #94a3b8; font-size: 14px; margin-bottom: 24px; }}
                .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
                .metric {{ background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 16px; }}
                .metric-label {{ font-size: 12px; color: #64748b; text-transform: uppercase; font-weight: 600; margin-bottom: 4px; }}
                .metric-value {{ font-size: 18px; font-weight: 700; color: #38bdf8; }}
                .btn {{ display: inline-flex; align-items: center; justify-content: center; width: 100%; background: #2563eb; color: #ffffff; font-size: 16px; font-weight: 600; padding: 14px 24px; border-radius: 10px; border: none; cursor: pointer; transition: background 0.2s; text-decoration: none; }}
                .btn:hover {{ background: #1d4ed8; }}
                .btn:disabled {{ background: #475569; cursor: not-allowed; }}
                .endpoints {{ margin-top: 24px; padding-top: 20px; border-top: 1px solid #334155; }}
                .endpoints h3 {{ font-size: 14px; color: #cbd5e1; margin-bottom: 8px; }}
                .code-pill {{ display: flex; align-items: center; justify-content: space-between; background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 10px 14px; font-family: monospace; font-size: 13px; color: #38bdf8; margin-bottom: 8px; }}
                #output {{ margin-top: 16px; padding: 14px; border-radius: 8px; background: #0f172a; border: 1px solid #334155; font-family: monospace; font-size: 12px; color: #a5f3fc; display: none; white-space: pre-wrap; max-height: 250px; overflow-y: auto; }}
            </style>
        </head>
        <body>
            <div class="card">
                <span class="badge">Vercel &amp; Render Cloud Hub</span>
                <h1>🚀 Daily Job Matcher &amp; Crawler</h1>
                <p class="subtitle">Automated intelligence for <strong>{config.CANDIDATE_NAME}</strong> ({config.CANDIDATE_EMAIL})</p>

                <div class="grid">
                    <div class="metric">
                        <div class="metric-label">Automated Schedule</div>
                        <div class="metric-value">Everyday 8:00 AM IST</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Target Profile</div>
                        <div class="metric-value">Backend / Full Stack</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Core Tech Stack</div>
                        <div class="metric-value">NestJS, Node, TS, PG</div>
                    </div>
                </div>

                <button class="btn" id="runBtn" onclick="triggerRun()">⚡ Run Job Crawler Now (Manual Trigger)</button>
                <div id="output"></div>

                <div class="endpoints">
                    <h3>Available Trigger Endpoints:</h3>
                    <div class="code-pill">
                        <span>GET /api/cron</span>
                        <small style="color: #64748b;">(Vercel Daily Cron Trigger)</small>
                    </div>
                    <div class="code-pill">
                        <span>GET /api/trigger</span>
                        <small style="color: #64748b;">(Webhook / Direct API Trigger)</small>
                    </div>
                </div>
            </div>

            <script>
                async function triggerRun() {{
                    const btn = document.getElementById('runBtn');
                    const out = document.getElementById('output');
                    btn.disabled = true;
                    btn.innerText = '⏳ Crawling portals & scoring jobs...';
                    out.style.display = 'block';
                    out.innerText = 'Connecting to crawler engine...';

                    try {{
                        const res = await fetch('/api/trigger');
                        const data = await res.json();
                        out.innerText = JSON.stringify(data, null, 2);
                        btn.innerText = '✅ Crawler Run Completed!';
                    }} catch (err) {{
                        out.innerText = 'Error triggering run: ' + err;
                        btn.innerText = '❌ Failed (Retry)';
                    }} finally {{
                        setTimeout(() => {{
                            btn.disabled = false;
                            btn.innerText = '⚡ Run Job Crawler Now (Manual Trigger)';
                        }}, 4000);
                    }}
                }}
            </script>
        </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

