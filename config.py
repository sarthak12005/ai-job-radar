import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Candidate Profile Defaults
CANDIDATE_NAME = os.getenv("CANDIDATE_NAME", "Sarthak Joshi")
CANDIDATE_EMAIL = os.getenv("CANDIDATE_EMAIL", "sarthakjoshi12005@gmail.com")
CANDIDATE_PHONE = os.getenv("CANDIDATE_PHONE", "+91 9325619172")
RESUME_PATH = os.getenv("RESUME_PATH", str(BASE_DIR / "sarthak_joshi_fullstack_developer_nov_2026.pdf"))

# Vercel / Serverless Environment Detection
IS_VERCEL = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))
CRON_SECRET = os.getenv("CRON_SECRET", "")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")  # Security: required for destructive endpoints

# Email / SMTP Settings
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SENDER_EMAIL = os.getenv("SENDER_EMAIL", CANDIDATE_EMAIL)
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "")  # Gmail App Password (16 chars)
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", CANDIDATE_EMAIL)

# Firecrawl Settings
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_API_URL = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev")

# Match Score Threshold
MIN_MATCH_SCORE = int(os.getenv("MIN_MATCH_SCORE", "70"))

# Freshness & Text Extraction Thresholds
MAX_JOB_AGE_DAYS = int(os.getenv("MAX_JOB_AGE_DAYS", "14"))
MAX_JD_CHAR_LIMIT = int(os.getenv("MAX_JD_CHAR_LIMIT", "5000"))

# AI & LLM Pitch Generation (Gemini API)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Real-Time Mobile Push Alerts (Telegram Bot)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Crawl & Filter Settings
TARGET_ROLES = [
    "Backend Developer",
    "Node.js Developer",
    "NestJS Developer",
    "Full Stack Developer",
    "Software Engineer - Backend",
    "API Developer",
    "TypeScript Developer",
    "Software Engineer"
]

DEFAULT_TARGET_COUNTRY = "India"
DEFAULT_TARGET_STATE = "All India"

SUPPORTED_LOCATIONS = {
    "India": {
        "code": "IN",
        "name": "India 🇮🇳",
        "states": [
            "All India",
            "Pune (Maharashtra)",
            "Bangalore (Karnataka)",
            "Mumbai (Maharashtra)",
            "Hyderabad (Telangana)",
            "Delhi NCR (Noida / Gurgaon)",
            "Chennai (Tamil Nadu)",
            "Remote (India)"
        ],
        "keywords": ["india", "pune", "bangalore", "bengaluru", "mumbai", "hyderabad", "noida", "gurgaon", "gurugram", "delhi", "chennai"]
    },
    "United States": {
        "code": "US",
        "name": "United States 🇺🇸",
        "states": [
            "All US",
            "San Francisco / Bay Area",
            "New York",
            "Austin (Texas)",
            "Seattle (Washington)",
            "Remote (US)"
        ],
        "keywords": ["united states", "usa", "us-remote", "remote in us", "san francisco", "chicago", "seattle", "austin", "new york", "california", "texas"]
    },
    "Europe": {
        "code": "EU",
        "name": "Europe / UK 🇪🇺",
        "states": [
            "All Europe",
            "London (UK)",
            "Berlin (Germany)",
            "Amsterdam (Netherlands)",
            "Spain / Barcelona",
            "Poland",
            "Remote (Europe)"
        ],
        "keywords": ["europe", "uk", "united kingdom", "london", "berlin", "germany", "amsterdam", "netherlands", "spain", "barcelona", "poland", "costa rica"]
    },
    "Remote": {
        "code": "REMOTE",
        "name": "Worldwide Remote 🌐",
        "states": [
            "Any Remote",
            "Global / Worldwide",
            "APAC",
            "EMEA",
            "Americas"
        ],
        "keywords": ["worldwide", "anywhere", "global", "telecommute", "work from home"]
    }
}

def detect_country(location_str: str) -> str:
    """Classifies a raw location string into a normalized country category."""
    loc = (location_str or "").lower()
    if any(k in loc for k in ["india", "pune", "bangalore", "bengaluru", "mumbai", "hyderabad", "noida", "gurgaon", "gurugram", "delhi", "chennai"]):
        return "India"
    if any(k in loc for k in ["united states", "usa", "us-remote", "remote in us", "san francisco", "chicago", "seattle", "austin", "new york", "canada", "toronto"]):
        return "United States"
    if any(k in loc for k in ["uk", "united kingdom", "london", "germany", "berlin", "spain", "barcelona", "poland", "amsterdam", "netherlands", "europe", "costa rica"]):
        return "Europe"
    if any(k in loc for k in ["worldwide", "anywhere", "remote", "global"]):
        return "Remote"
    return "Other"

TARGET_LOCATIONS = [
    "Pune",
    "Bangalore",
    "Bengaluru",
    "Mumbai",
    "Hyderabad",
    "Noida",
    "Gurgaon",
    "Gurugram",
    "Delhi NCR",
    "Remote",
    "India",
    "Hybrid"
]

CORE_SKILLS = [
    "Node.js", "NestJS", "TypeScript", "JavaScript", "Express.js",
    "PostgreSQL", "MongoDB", "MySQL", "Prisma", "Prisma ORM",
    "Redis", "BullMQ", "REST APIs", "REST API", "WebSockets",
    "Docker", "AWS", "Azure", "Git", "System Design", "Microservices",
    "React.js", "Next.js", "Nginx", "Docker Compose", "GitLab CI/CD", "Razorpay"
]

MIN_EXPERIENCE_YEARS = 0
MAX_EXPERIENCE_YEARS = 3.5

# Paths (Serverless uses /tmp for writeable storage)
if IS_VERCEL:
    REPORTS_DIR = Path("/tmp/reports")
    LOGS_DIR = Path("/tmp/logs")
else:
    REPORTS_DIR = BASE_DIR / "reports"
    LOGS_DIR = BASE_DIR / "logs"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

