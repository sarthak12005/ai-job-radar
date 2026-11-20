import os
import smtplib
import datetime
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Dict, Any
import config

class Mailer:
    def __init__(self):
        self.server = config.SMTP_SERVER
        self.port = config.SMTP_PORT
        self.sender_email = config.SENDER_EMAIL
        self.sender_password = config.SENDER_PASSWORD
        self.recipient_email = config.RECIPIENT_EMAIL

    def send_daily_report(self, excel_path: Path, jobs: List[Dict[str, Any]], candidate_name="Sarthak Joshi") -> bool:
        """
        Sends daily job report email with HTML summary and attached Excel workbook.
        """
        today_str = datetime.date.today().strftime("%d %b %Y")
        sorted_jobs = sorted(jobs, key=lambda j: j.get("match_score", 0), reverse=True)
        top_matches = [j for j in sorted_jobs if j.get("match_score", 0) >= 60][:10]

        subject = f"🎯 Daily Job Matches ({len(jobs)} Found) for {candidate_name} — {today_str}"

        # HTML Body
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #333; line-height: 1.6; background-color: #f7fafc; padding: 20px; }}
                .container {{ max-width: 800px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; }}
                .header {{ background: #1F4E79; color: #ffffff; padding: 24px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 22px; }}
                .header p {{ margin: 6px 0 0; font-size: 14px; opacity: 0.9; }}
                .kpi-row {{ display: flex; justify-content: space-around; padding: 16px; background: #ebf8fa; border-bottom: 1px solid #e2e8f0; text-align: center; }}
                .kpi-item {{ padding: 0 10px; }}
                .kpi-val {{ font-size: 22px; font-weight: bold; color: #1F4E79; }}
                .kpi-label {{ font-size: 12px; color: #4a5568; text-transform: uppercase; }}
                .content {{ padding: 24px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
                th {{ background: #f1f5f9; color: #334155; text-align: left; padding: 10px; font-size: 13px; border-bottom: 2px solid #cbd5e1; }}
                td {{ padding: 10px; font-size: 13px; border-bottom: 1px solid #f1f5f9; }}
                .badge-green {{ background: #def7ec; color: #03543f; padding: 3px 8px; border-radius: 12px; font-weight: bold; font-size: 12px; }}
                .badge-blue {{ background: #e1effe; color: #1e429f; padding: 3px 8px; border-radius: 12px; font-weight: bold; font-size: 12px; }}
                .btn-apply {{ background: #1F4E79; color: #ffffff; text-decoration: none; padding: 6px 12px; border-radius: 4px; font-size: 12px; font-weight: bold; display: inline-block; }}
                .footer {{ padding: 16px 24px; background: #f8fafc; color: #64748b; font-size: 12px; text-align: center; border-top: 1px solid #e2e8f0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🚀 Today's Curated Job Matches</h1>
                    <p>Automated Daily 8:00 AM Dispatch for <strong>{candidate_name}</strong></p>
                </div>
                
                <div class="kpi-row">
                    <div class="kpi-item">
                        <div class="kpi-val">{len(jobs)}</div>
                        <div class="kpi-label">Total Jobs Found</div>
                    </div>
                    <div class="kpi-item">
                        <div class="kpi-val">{len(top_matches)}</div>
                        <div class="kpi-label">High Matches (>=60%)</div>
                    </div>
                    <div class="kpi-item">
                        <div class="kpi-val">1.8+ Yrs</div>
                        <div class="kpi-label">Profile Fit</div>
                    </div>
                </div>

                <div class="content">
                    <p>Good morning {candidate_name},</p>
                    <p>Here are yesterday's jobs matching your experience in <strong>Node.js, NestJS, TypeScript, PostgreSQL, and Redis</strong>. The complete breakdown with <strong>Direct Career Links, HR Contact Emails, and Cold Outreach Pitch Messages</strong> is attached in the Excel sheet.</p>

                    <h3>🌟 Top Job Matches Today:</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Match</th>
                                <th>Role</th>
                                <th>Company</th>
                                <th>Location</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody>
        """

        for j in top_matches:
            score = j.get("match_score", 0)
            badge_class = "badge-green" if score >= 75 else "badge-blue"
            url = j.get("url", "#")
            html_content += f"""
                            <tr>
                                <td><span class="{badge_class}">{score}%</span></td>
                                <td><strong>{j.get('title', '')}</strong><br><small style="color: #64748b;">{j.get('match_summary', '')}</small></td>
                                <td>{j.get('company', '')}</td>
                                <td>{j.get('location', 'Remote')}</td>
                                <td><a href="{url}" class="btn-apply" target="_blank">Direct Apply</a></td>
                            </tr>
            """

        html_content += f"""
                        </tbody>
                    </table>

                    <div style="margin-top: 24px; padding: 14px; background: #fffbeb; border-left: 4px solid #f59e0b; border-radius: 4px;">
                        <strong>💡 Cold Outreach Tip:</strong> Check column <em>HR / Recruiter Email</em> and <em>Cold Outreach Message</em> in the attached Excel file to send cold introductions directly to hiring managers.
                    </div>
                </div>

                <div class="footer">
                    Attached File: <strong>{excel_path.name}</strong><br>
                    Automated by Sarthak's Daily Job Crawler &amp; Firecrawl Engine.
                </div>
            </div>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.sender_email
        msg["To"] = self.recipient_email

        # Attach HTML
        msg.attach(MIMEText(html_content, "html"))

        # Attach Excel file
        if excel_path and excel_path.exists():
            with open(excel_path, "rb") as f:
                part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={excel_path.name}")
            msg.attach(part)

        # Check if credentials exist
        if not self.sender_password or self.sender_password.strip() == "":
            preview_html = config.REPORTS_DIR / f"Email_Preview_{datetime.date.today().isoformat()}.html"
            preview_html.write_text(html_content, encoding="utf-8")
            print(f"\n[INFO] SMTP password not set in .env.")
            print(f"[INFO] Saved email preview to: {preview_html}")
            print(f"[INFO] Excel report attached: {excel_path}")
            print(f"[INFO] To send actual emails, add your 16-character Gmail App Password to SENDER_PASSWORD in .env\n")
            return False

    def send_cold_outreach(self, to_email: str, subject: str, body: str, candidate_name: str = "Sarthak Joshi") -> Dict[str, Any]:
        """
        Sends a cold outreach email directly to a recruiter or company contact,
        attaching Sarthak's resume.
        """
        if not self.sender_password:
            return {"success": False, "error": "SMTP password not configured in .env"}

        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = f"{candidate_name} <{self.sender_email}>"
        msg["To"] = to_email
        msg["Reply-To"] = self.sender_email

        # Clean text & HTML format
        html_body = body.replace("\n", "<br>")
        html_content = f"""
        <html>
        <body style="font-family: 'Segoe UI', Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #222;">
            {html_body}
        </body>
        </html>
        """
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        # Attach resume if available
        resume_p = Path(config.RESUME_PATH)
        if resume_p.exists():
            with open(resume_p, "rb") as f:
                part = MIMEBase("application", "pdf")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={resume_p.name}")
            msg.attach(part)

        try:
            print(f"Connecting to SMTP server {self.server}:{self.port} to send cold outreach to {to_email}...")
            with smtplib.SMTP(self.server, self.port) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(self.sender_email, self.sender_password)
                s.sendmail(self.sender_email, [to_email], msg.as_string())
            print(f"Cold outreach email successfully delivered to {to_email}!")
            return {"success": True, "message": f"Email delivered to {to_email}"}
        except Exception as e:
            print(f"Failed to send cold outreach email: {e}")
            return {"success": False, "error": str(e)}


