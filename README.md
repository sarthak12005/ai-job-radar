# ai-job-radar

# 🚀 Automated Daily 8:30 AM Job Crawler, Matcher & Emailer

Custom built for **Sarthak Joshi** (Full Stack / Backend Developer — Node.js, NestJS, TypeScript, PostgreSQL, Redis, REST APIs, BullMQ).

Every day right at **8:30 AM IST**, this pipeline:
1. Crawls fresh jobs posted yesterday across direct company career pages (Greenhouse, Lever ATS) and job platforms (Remotive, Jobicy, RemoteOK, Firecrawl).
2. Evaluates and scores every job against your resume (1.8+ yrs experience, backend/fullstack tech stack).
3. Discovers direct application links and HR / recruiter contact emails.
4. Generates a tailored cold outreach pitch message for each company.
5. Builds a styled Excel spreadsheet report (`.xlsx`) with clickable links and KPI metrics.
6. Dispatches an HTML email briefing with the Excel spreadsheet and resume attached to `sarthakjoshi12005@gmail.com`.

---

## 🎯 Trigger Points & Cron Architecture

### 1. Automated Cron Trigger (8:30 AM IST)
- **Vercel Cron Expression**: `0 3 * * *`
  *(Vercel cron executes in UTC time. `03:00 UTC` = **`08:30 AM IST`** every single day)*.
- **Cron Endpoint**: `GET /api/cron`
  Configured in `vercel.json`. When triggered by Vercel, it runs the crawler, creates the Excel report in `/tmp`, dispatches your email, and returns a JSON summary.

### 2. Manual HTTP Trigger Point (Webhooks, Curl, Browser)
- **Endpoint**: `GET /api/trigger` or `POST /api/trigger`
- Trigger anytime on-demand from any browser, Postman, or webhook:
  ```bash
  curl -X GET https://your-app.vercel.app/api/trigger
  ```

### 3. Interactive Web Dashboard & Pagination
- **Endpoint**: `GET /`
- Access your deployment URL (e.g. `http://127.0.0.1:8000`) in your browser to view your live candidate status, browse paginated opportunities (10/20/50 per page), filter by match score, and send 1-click recruiter cold emails!

### 4. Local One-Command Runner
Start the full web application, connect to MongoDB, and automatically open the Dashboard:
```powershell
python run.py
# or
py run.py
# or
npm start
```
- **CLI Crawl Only**: `python run.py --crawl`
- **CLI Email Only**: `python run.py --mail`
- **Instant 1-Click Batch**: Double-click `start_app.bat` or `run_now.bat`.
- **Windows Scheduled Task**: Runs daily at 08:30 AM IST via `DailyJobCrawler_Sarthak`.

---

## ☁️ Deploying to Vercel (Step-by-Step)

### Option 1: Via Vercel CLI (Quickest)
1. Open PowerShell in `D:\ImpPDF\sarthakResume\crwaler_job`:
   ```powershell
   npm install -g vercel
   vercel
   ```
2. Follow the on-screen prompts (accept defaults).
3. Deploy to production:
   ```powershell
   vercel --prod
   ```
4. Set your Environment Variables in Vercel Project Settings:
   - `SENDER_EMAIL`: `sarthakjoshi12005@gmail.com`
   - `SENDER_PASSWORD`: Your 16-character Gmail App Password
   - `RECIPIENT_EMAIL`: `sarthakjoshi12005@gmail.com`
   - `CANDIDATE_NAME`: `Sarthak Joshi`
   - `FIRECRAWL_API_KEY`: Your Firecrawl API key
5. Vercel automatically detects `vercel.json` and activates the **8:30 AM IST Cron Job** under **Project Settings > Cron Jobs**!

---

## ☁️ Deploying to Render (Free Web Service + Cron)

1. Push your repository to GitHub.
2. In Render Dashboard, click **New +** -> **Blueprint**.
3. Connect your repository.
4. Render automatically reads `render.yaml` and sets up a free **Cron Job** (`daily-job-crawler-cron`) that executes `python main.py` every day at `08:30 AM IST` (`0 3 * * *`).
5. Add your `SENDER_PASSWORD` and `FIRECRAWL_API_KEY` under Environment Variables in the Render dashboard.

---

## 📁 Project Structure

```text
├── .env.example            # Environment template
├── .gitignore              # Keeps credentials and reports private
├── config.py               # Central configuration loader
├── database.py             # MongoDB persistence & structured pagination
├── job_crawler.py          # Concurrent ATS & Firecrawl job crawler
├── job_matcher.py          # ATS matching & cold outreach generator
├── excel_reporter.py       # Styled Excel (.xlsx) generator with hyperlinks
├── mailer.py               # Gmail SMTP HTML email dispatcher with resume
├── main.py                 # Pipeline orchestrator
├── server.py               # FastAPI server, Swagger UI (/docs) & Web Dashboard
├── run.py                  # One-command startup runner
├── package.json            # Node.js scripts (npm start, npm run crawl)
├── render.yaml             # Render Blueprint specification
├── vercel.json             # Vercel Cron (8:30 AM IST) & routing config
├── requirements.txt        # Python production dependencies
└── sarthak_joshi_fullstack_developer_nov_2026.pdf # Official candidate resume
```

---

## 🔑 Email Password Setup (Gmail)
To send emails to your inbox:
1. Visit [Google App Passwords](https://myaccount.google.com/apppasswords).
2. Generate an app password for **JobCrawler**.
3. Paste the 16 characters into `.env` (locally) or into your Vercel/Render Environment Variables (`SENDER_PASSWORD`).
