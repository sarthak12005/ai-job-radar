import re
import json
import html
import logging
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import config
from logger_config import get_logger

logger = get_logger("JobCrawler")

def parse_date_str(val) -> str:
    """Extracts YYYY-MM-DD string from various date formats (ISO, timestamps, strings)."""
    if not val:
        return ""
    val_str = str(val).strip()
    m = re.search(r'(\d{4}-\d{2}-\d{2})', val_str)
    if m:
        return m.group(1)
    return ""

def is_job_fresh(date_str: str, max_days: int = None) -> bool:
    """
    Returns True if the date is within max_days, or if date is unstated/unparseable.
    Filters out stale roles older than cutoff.
    """
    days = max_days if max_days is not None else getattr(config, "MAX_JOB_AGE_DAYS", 14)
    clean_d = parse_date_str(date_str)
    if not clean_d:
        return True  # Fallback if source provides no date
    try:
        job_d = datetime.date.fromisoformat(clean_d)
        cutoff = datetime.date.today() - datetime.timedelta(days=days)
        return job_d >= cutoff
    except Exception:
        return True

def clean_job_description(raw_desc: str, max_chars: int = None) -> str:
    """
    Strips HTML tags, collapses excessive whitespace, and preserves full technical content.
    Prevents truncation of skills/requirements while capping at max_chars.
    """
    limit = max_chars if max_chars is not None else getattr(config, "MAX_JD_CHAR_LIMIT", 5000)
    if not raw_desc:
        return ""
    try:
        if "<" in raw_desc and ">" in raw_desc:
            soup = BeautifulSoup(raw_desc, "html.parser")
            for s in soup(["script", "style", "noscript", "iframe"]):
                s.decompose()
            text = soup.get_text(separator=" ")
        else:
            text = raw_desc
    except Exception:
        text = raw_desc

    clean = re.sub(r'\s+', ' ', text).strip()
    return clean[:limit]

class JobCrawler:
    def __init__(self, firecrawl_key=None):
        self.firecrawl_key = firecrawl_key or config.FIRECRAWL_API_KEY
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/html, */*"
        })
        self.company_linkedin_cache = {}

    def crawl_all(self, target_roles=None, days_back=2, target_country="India", target_state=None) -> list:
        """Crawls all sources concurrently for jobs matching target country, state, and roles."""
        roles = target_roles or config.TARGET_ROLES
        all_jobs = []

        logger.info(f"Starting concurrent job crawl: country={target_country}, state={target_state or 'All'} across sources...")
        tasks = [
            ("Remotive", lambda: self._crawl_remotive(target_country=target_country)),
            ("Jobicy", lambda: self._crawl_jobicy(target_country=target_country)),
            ("RemoteOK", lambda: self._crawl_remoteok(target_country=target_country)),
            ("ATS Career Pages", lambda: self._crawl_ats_career_pages(target_country=target_country, target_state=target_state))
        ]

        if self.firecrawl_key:
            tasks.append(("Firecrawl", lambda: self._crawl_with_firecrawl(target_country=target_country, target_state=target_state)))

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_source = {executor.submit(func): name for name, func in tasks}
            for future in as_completed(future_to_source):
                src_name = future_to_source[future]
                try:
                    res = future.result()
                    all_jobs.extend(res)
                    logger.info(f"{src_name} crawled: {len(res)} candidate jobs")
                except Exception as e:
                    logger.warning(f"{src_name} crawl error: {e}")

        # Deduplicate and enrich
        seen = set()
        deduped = []
        for j in all_jobs:
            raw_comp = html.unescape(j.get("company", "").strip())
            j["company"] = raw_comp
            key = (raw_comp.lower(), j.get("title", "").strip().lower())
            url_key = j.get("url", "").strip().lower()
            if key not in seen and url_key not in seen:
                seen.add(key)
                if url_key:
                    seen.add(url_key)
                
                # Assign normalized country if not present
                if not j.get("country"):
                    j["country"] = config.detect_country(j.get("location", ""))
                
                # Enrich with HR / Recruiter email contact if missing
                self._enrich_contact_email(j)

                # Enrich with key company contacts & recruiters on LinkedIn
                self._enrich_linkedin_contacts(j)

                deduped.append(j)

        logger.info(f"Total unique recent jobs collected: {len(deduped)}")
        return deduped

    def _crawl_remotive(self, target_country: str = "India") -> list:
        """Fetches software-dev jobs from Remotive, filtering geo-restrictions and stale postings."""
        jobs = []
        url = "https://remotive.com/api/remote-jobs?category=software-dev&limit=50"
        resp = self.session.get(url, timeout=12)
        if resp.status_code == 200:
            data = resp.json().get("jobs", [])
            for item in data:
                title = item.get("title", "")
                tags = [t.lower() for t in item.get("tags", [])]
                desc = item.get("description", "")
                company = item.get("company_name", "")
                pub_date = parse_date_str(item.get("publication_date", ""))
                loc = item.get("candidate_required_location", "Remote")
                loc_l = loc.lower()

                # Filter out stale jobs older than MAX_JOB_AGE_DAYS
                if pub_date and not is_job_fresh(pub_date):
                    continue

                # If user targets India, exclude roles strictly restricted to non-India regions
                if target_country and target_country.lower() == "india":
                    if any(c in loc_l for c in ["usa only", "us only", "europe only", "uk only", "americas only", "canada only", "latam only", "eu only"]):
                        continue
                
                # Check keyword relevance
                t_lower = title.lower()
                if any(k in t_lower or k in tags for k in ["node", "nest", "backend", "typescript", "fullstack", "full stack", "api", "postgres"]):
                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": loc,
                        "country": config.detect_country(loc),
                        "url": item.get("url", ""),
                        "date_posted": pub_date or datetime.date.today().isoformat(),
                        "description": clean_job_description(desc),
                        "source": "Remotive Career Board"
                    })
        return jobs

    def _crawl_jobicy(self, target_country: str = "India") -> list:
        """Fetches jobs from Jobicy API, filtering geo-restrictions and stale postings."""
        jobs = []
        url = "https://jobicy.com/api/v2/remote-jobs?count=50&industry=engineering"
        resp = self.session.get(url, timeout=12)
        if resp.status_code == 200:
            data = resp.json().get("jobs", [])
            for item in data:
                title = item.get("jobTitle", "")
                desc = item.get("jobDescription", "")
                company = item.get("companyName", "")
                pub_date = parse_date_str(item.get("pubDate", ""))
                loc = item.get("jobGeo", "Remote")
                loc_l = loc.lower()

                # Filter out stale jobs older than MAX_JOB_AGE_DAYS
                if pub_date and not is_job_fresh(pub_date):
                    continue

                if target_country and target_country.lower() == "india":
                    if any(c in loc_l for c in ["usa", "europe", "uk", "canada", "emea", "latam"]) and "anywhere" not in loc_l:
                        continue

                t_lower = title.lower()
                if any(k in t_lower for k in ["node", "nest", "backend", "typescript", "full stack", "fullstack", "software engineer", "developer"]):
                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": loc,
                        "country": config.detect_country(loc),
                        "url": item.get("url", ""),
                        "date_posted": pub_date or datetime.date.today().isoformat(),
                        "description": clean_job_description(desc),
                        "source": "Jobicy Career Hub"
                    })
        return jobs

    def _crawl_remoteok(self, target_country: str = "India") -> list:
        """Fetches jobs from RemoteOK API, filtering out non-target locations and stale postings."""
        jobs = []
        url = "https://remoteok.com/api"
        resp = self.session.get(url, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                for item in data[1:50]:
                    title = item.get("position", "")
                    tags = [t.lower() for t in item.get("tags", [])]
                    desc = item.get("description", "")
                    company = item.get("company", "")
                    pub_date = parse_date_str(item.get("date", ""))
                    loc = item.get("location", "Remote")
                    loc_l = loc.lower()

                    # Filter out stale jobs older than MAX_JOB_AGE_DAYS
                    if pub_date and not is_job_fresh(pub_date):
                        continue

                    if target_country and target_country.lower() == "india":
                        if any(c in loc_l for c in ["spain", "poland", "london", "costa rica", "canada", "toronto", "germany", "berlin", "us-remote", "chicago", "seattle"]):
                            continue

                    t_lower = title.lower()
                    if any(k in t_lower or k in tags for k in ["node", "nest", "backend", "typescript", "fullstack", "postgres"]):
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": loc,
                            "country": config.detect_country(loc),
                            "url": item.get("url", ""),
                            "date_posted": pub_date or datetime.date.today().isoformat(),
                            "description": clean_job_description(desc),
                            "source": "RemoteOK"
                        })
        return jobs

    def _crawl_ats_career_pages(self, target_country: str = "India", target_state: str = None) -> list:
        """
        Crawls popular company career pages using Greenhouse and Ashby ATS endpoints,
        prioritizing Pune engineering centers, India tech hubs, and remote startups.
        """
        ats_targets = [
            # Pune Tech Centers & Enterprise Engineering
            {"type": "greenhouse", "company": "Druva", "url": "https://boards-api.greenhouse.io/v1/boards/druva/jobs?content=true"},
            {"type": "greenhouse", "company": "PubMatic", "url": "https://boards-api.greenhouse.io/v1/boards/pubmatic/jobs?content=true"},
            {"type": "greenhouse", "company": "NICE", "url": "https://boards-api.greenhouse.io/v1/boards/nice/jobs?content=true"},
            {"type": "greenhouse", "company": "SymphonyAI", "url": "https://boards-api.greenhouse.io/v1/boards/symphony/jobs?content=true"},
            {"type": "lever", "company": "Mindtickle", "url": "https://api.lever.co/v0/postings/mindtickle?mode=json"},
            
            # Top Indian Tech Unicorns & Startups
            {"type": "lever", "company": "Paytm", "url": "https://api.lever.co/v0/postings/paytm?mode=json"},
            {"type": "lever", "company": "CRED", "url": "https://api.lever.co/v0/postings/cred?mode=json"},
            {"type": "lever", "company": "Meesho", "url": "https://api.lever.co/v0/postings/meesho?mode=json"},
            {"type": "lever", "company": "Zeta", "url": "https://api.lever.co/v0/postings/zeta?mode=json"},
            {"type": "greenhouse", "company": "InMobi", "url": "https://boards-api.greenhouse.io/v1/boards/inmobi/jobs?content=true"},
            {"type": "greenhouse", "company": "Slice", "url": "https://boards-api.greenhouse.io/v1/boards/slice/jobs?content=true"},
            {"type": "greenhouse", "company": "Groww", "url": "https://boards-api.greenhouse.io/v1/boards/groww/jobs?content=true"},
            {"type": "ashby", "company": "Atlan", "url": "https://api.ashbyhq.com/posting-api/job-board/atlan"},
            {"type": "ashby", "company": "Navi", "url": "https://api.ashbyhq.com/posting-api/job-board/navi"},

            # Top Global DevTools & High-Scale Cloud Companies
            {"type": "greenhouse", "company": "Branch", "url": "https://boards-api.greenhouse.io/v1/boards/branch/jobs?content=true"},
            {"type": "greenhouse", "company": "Elastic", "url": "https://boards-api.greenhouse.io/v1/boards/elastic/jobs?content=true"},
            {"type": "greenhouse", "company": "Stripe", "url": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true"},
            {"type": "greenhouse", "company": "Cloudflare", "url": "https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs?content=true"},
            {"type": "greenhouse", "company": "GitLab", "url": "https://boards-api.greenhouse.io/v1/boards/gitlab/jobs?content=true"},
            {"type": "greenhouse", "company": "Datadog", "url": "https://boards-api.greenhouse.io/v1/boards/datadog/jobs?content=true"},
            {"type": "greenhouse", "company": "Coinbase", "url": "https://boards-api.greenhouse.io/v1/boards/coinbase/jobs?content=true"},
            {"type": "greenhouse", "company": "Affirm", "url": "https://boards-api.greenhouse.io/v1/boards/affirm/jobs?content=true"},
            {"type": "greenhouse", "company": "Figma", "url": "https://boards-api.greenhouse.io/v1/boards/figma/jobs?content=true"},
            {"type": "greenhouse", "company": "Brex", "url": "https://boards-api.greenhouse.io/v1/boards/brex/jobs?content=true"},
            {"type": "ashby", "company": "Linear", "url": "https://api.ashbyhq.com/posting-api/job-board/linear"},
            {"type": "ashby", "company": "Supabase", "url": "https://api.ashbyhq.com/posting-api/job-board/supabase"},
            {"type": "ashby", "company": "PostHog", "url": "https://api.ashbyhq.com/posting-api/job-board/posthog"},
            {"type": "ashby", "company": "Replit", "url": "https://api.ashbyhq.com/posting-api/job-board/replit"},
            {"type": "ashby", "company": "Perplexity", "url": "https://api.ashbyhq.com/posting-api/job-board/perplexity"},
            {"type": "ashby", "company": "Resend", "url": "https://api.ashbyhq.com/posting-api/job-board/resend"}
        ]

        is_pune_targeted = target_state and "pune" in target_state.lower()

        def _fetch_target(target):
            target_jobs = []
            try:
                resp = self.session.get(target["url"], timeout=10)
                if resp.status_code == 200:
                    raw_data = resp.json()
                    t_type = target.get("type", "greenhouse")

                    if t_type == "lever":
                        job_items = raw_data if isinstance(raw_data, list) else []
                    else:
                        job_items = raw_data.get("jobs", []) if isinstance(raw_data, dict) else []

                    for item in job_items:
                        if t_type == "greenhouse":
                            title = item.get("title", "")
                            loc = item.get("location", {}).get("name", "India / Remote")
                            job_url = item.get("absolute_url", "")
                            raw_desc = item.get("content", "")
                            date_posted = parse_date_str(item.get("updated_at") or item.get("first_published"))
                        elif t_type == "ashby":
                            title = item.get("title", "")
                            loc = item.get("location", "Remote")
                            job_url = item.get("jobUrl", "")
                            raw_desc = item.get("descriptionPlain") or item.get("descriptionHtml") or ""
                            date_posted = parse_date_str(item.get("publishedAt"))
                        elif t_type == "lever":
                            title = item.get("text", "")
                            loc = item.get("categories", {}).get("location", "India / Remote")
                            job_url = item.get("hostedUrl", "")
                            raw_desc = item.get("descriptionPlain") or item.get("description") or ""
                            created_ms = item.get("createdAt")
                            date_posted = ""
                            if created_ms:
                                try:
                                    date_posted = datetime.date.fromtimestamp(created_ms / 1000).isoformat()
                                except:
                                    date_posted = ""
                        else:
                            title = item.get("title", "")
                            loc = "India / Remote"
                            job_url = ""
                            raw_desc = ""
                            date_posted = ""

                        # Filter out stale jobs older than MAX_JOB_AGE_DAYS
                        if date_posted and not is_job_fresh(date_posted):
                            continue

                        loc_l = (loc or "").lower()
                        t_lower = title.lower()

                        if target_country and target_country.lower() == "india":
                            if any(c in loc_l for c in ["san francisco", "new york", "seattle", "austin", "london", "dublin", "paris", "berlin", "tokyo", "sydney", "madrid", "toronto"]) and "india" not in loc_l and "remote" not in loc_l:
                                continue

                        if is_pune_targeted:
                            pune_terms = ["pune", "maharashtra", "hinjewadi", "baner", "magarpatta", "kharadi", "remote", "india"]
                            if not any(pt in loc_l for pt in pune_terms) and not any(pt in t_lower for pt in ["pune", "remote"]):
                                if any(other_c in loc_l for other_c in ["bangalore", "delhi", "gurgaon", "noida", "hyderabad", "chennai"]):
                                    continue

                        if any(k in t_lower for k in ["backend", "node", "nest", "typescript", "full stack", "fullstack", "software engineer", "api developer", "engineer"]):
                            clean_desc = clean_job_description(raw_desc) or f"Position: {title} at {target['company']} ({loc})"
                            target_jobs.append({
                                "title": title,
                                "company": target["company"],
                                "location": loc,
                                "country": config.detect_country(loc),
                                "url": job_url,
                                "date_posted": date_posted or datetime.date.today().isoformat(),
                                "description": clean_desc,
                                "source": f"{target['company']} Career {t_type.capitalize()}"
                            })
            except Exception as ex:
                logger.debug(f"ATS crawl {target['company']} skipped: {ex}")
            return target_jobs

        all_ats = []
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = [ex.submit(_fetch_target, t) for t in ats_targets]
            for f in as_completed(futures):
                all_ats.extend(f.result())
        return all_ats

    def _crawl_with_firecrawl(self, target_country: str = "India", target_state: str = None) -> list:
        """
        Uses Firecrawl API to deeply search across ATS systems (Lever, Greenhouse, Ashby)
        and startup career portals specifically targeting Pune, Maharashtra, and high-match stacks.
        """
        jobs = []
        headers = {
            "Authorization": f"Bearer {self.firecrawl_key}",
            "Content-Type": "application/json"
        }

        is_pune = (target_state and "pune" in target_state.lower()) or (not target_state and target_country == "India")

        if is_pune:
            queries = [
                'site:lever.co ("Node.js" OR "NestJS" OR "Backend Developer") ("Pune" OR "Maharashtra")',
                'site:greenhouse.io ("Node.js" OR "NestJS" OR "Backend Developer") ("Pune" OR "Maharashtra")',
                'site:ashbyhq.com ("Backend" OR "Node.js" OR "Software Engineer") ("Pune" OR "India" OR "Remote")',
                'site:jobs.lever.co "Pune" ("Backend" OR "Software Engineer" OR "Full Stack")',
                'site:job-boards.greenhouse.io "Pune" ("Backend" OR "Software Engineer" OR "Node")',
                'site:instahire.com/jobs ("Pune") ("Node.js" OR "NestJS" OR "Backend Developer")',
                'site:wellfound.com ("Pune" OR "Remote, India") ("Node.js" OR "NestJS" OR "Backend")',
                'site:cutshort.io ("Pune") ("Node.js" OR "NestJS" OR "Backend" OR "Full Stack")',
                '("careers" OR "hiring") ("Node.js" OR "NestJS" OR "Backend Developer") ("Pune" OR "Pune, India") (startup OR tech)',
                '("Pune" OR "Pune, Maharashtra") ("Backend Developer" OR "Full Stack Developer") ("NestJS" OR "Node.js" OR "BullMQ" OR "PostgreSQL")',
                'site:lever.co ("NestJS" OR "Node.js" OR "TypeScript") ("Pune" OR "Hinjewadi" OR "Baner" OR "Magarpatta")',
                'site:greenhouse.io ("NestJS" OR "Node.js" OR "TypeScript") ("Pune" OR "Kharadi" OR "Wakad" OR "Hadapsar")'
            ]
        elif target_country and target_country.lower() == "india":
            hub_kw = "India OR Pune OR Bangalore OR Bengaluru OR Mumbai OR Hyderabad OR Remote"
            if target_state and target_state.lower() not in ["all", "all india", "any location", "any remote"]:
                clean_s = target_state.split("(")[0].strip()
                if clean_s:
                    hub_kw = f'"{clean_s}" OR India'
            queries = [
                f'site:lever.co ("Node.js" OR "NestJS" OR "Backend Developer") ({hub_kw})',
                f'site:greenhouse.io ("Backend Developer" OR "TypeScript" OR "NestJS") ({hub_kw})',
                f'site:ashbyhq.com ("Backend" OR "Software Engineer" OR "Node.js") ({hub_kw})',
                f'site:wellfound.com ("Backend Developer" OR "Node.js") ({hub_kw})',
                f'("careers" OR "hiring") ("Node.js" OR "NestJS" OR "Backend") ({hub_kw}) startup'
            ]
        elif target_country and target_country.lower() in ["united states", "us"]:
            queries = [
                'site:lever.co ("Node.js" OR "NestJS") ("United States" OR "Remote US")',
                'site:greenhouse.io ("Backend Developer" OR "TypeScript") ("United States" OR "Remote")',
                'site:ashbyhq.com ("Backend" OR "Node.js") ("United States" OR "Remote")'
            ]
        elif target_country and target_country.lower() in ["europe", "eu", "uk"]:
            queries = [
                'site:lever.co ("Node.js" OR "NestJS") ("Europe" OR "UK" OR "London")',
                'site:greenhouse.io ("Backend Developer" OR "TypeScript") ("Europe" OR "UK")',
                'site:ashbyhq.com ("Backend" OR "Node.js") ("Europe" OR "Remote")'
            ]
        else:
            queries = [
                'site:lever.co "Node.js" OR "NestJS" developer',
                'site:greenhouse.io "Backend Developer" "TypeScript" Remote',
                'site:ashbyhq.com "Backend Developer" Remote'
            ]

        def _execute_query(q):
            local_jobs = []
            payload = {"query": q, "limit": 20}
            try:
                resp = self.session.post(f"{config.FIRECRAWL_API_URL}/v1/search", json=payload, headers=headers, timeout=25)
                if resp.status_code == 200:
                    results = resp.json().get("data", [])
                    for item in results:
                        raw_title = item.get("title", "")
                        url = item.get("url", "")
                        desc = item.get("description", "")
                        
                        # Clean title
                        title = re.sub(r'^(Job Application for |Careers at |Apply for )', '', raw_title, flags=re.IGNORECASE)
                        title = title.split(" | ")[0].split(" - ")[0].strip() or "Backend Developer"
                        
                        company = self._extract_company_from_url(url, raw_title)
                        
                        # Normalize URL (remove /apply trailing)
                        clean_url = re.sub(r'/apply/?$', '', url)
                        
                        if clean_url and any(k in (title + " " + desc).lower() for k in ["node", "nest", "typescript", "backend", "full stack", "fullstack", "software", "developer", "engineer"]):
                            clean_desc = clean_job_description(desc)
                            
                            # Parse date if available in metadata or snippet
                            item_date = parse_date_str(item.get("date") or item.get("published_date") or item.get("date_posted"))
                            if not item_date and desc:
                                m_date = re.search(r'\b(202[4-9]-\d{2}-\d{2})\b', desc)
                                if m_date:
                                    item_date = m_date.group(1)

                            # Filter out confirmed stale postings older than MAX_JOB_AGE_DAYS
                            if item_date and not is_job_fresh(item_date):
                                continue

                            loc_display = "Pune, Maharashtra / Remote" if is_pune else (f"{target_state or 'India'} / Remote" if target_country == "India" else "Remote / Global")
                            local_jobs.append({
                                "title": title,
                                "company": company,
                                "location": loc_display,
                                "country": "India" if is_pune or target_country == "India" else config.detect_country(loc_display),
                                "url": clean_url,
                                "date_posted": item_date or datetime.date.today().isoformat(),
                                "description": clean_desc,
                                "source": "Firecrawl Startup Radar"
                            })
            except Exception as e:
                logger.warning(f"Firecrawl query error for '{q[:40]}...': {e}")
            return local_jobs

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_execute_query, q) for q in queries]
            for f in as_completed(futures):
                jobs.extend(f.result())

        return jobs

    def _extract_company_from_url(self, url: str, raw_title: str = "") -> str:
        try:
            parsed = urlparse(url)
            parts = parsed.path.strip("/").split("/")
            
            # Check title for "at Company" or "@ Company" pattern first
            if raw_title:
                m = re.search(r'(?:at|@)\s+([A-Za-z0-9\s\.\-_]+)(?:$|\||\-|\()', raw_title)
                if m and len(m.group(1).strip()) > 1:
                    clean_c = m.group(1).strip()
                    if clean_c.lower() not in ["lever", "greenhouse", "ashby", "jobs", "careers"]:
                        return clean_c.title()
            
            if "lever.co" in parsed.netloc and len(parts) >= 1:
                return parts[0].replace("-", " ").title()
            if "greenhouse.io" in parsed.netloc and len(parts) >= 1:
                return parts[0].replace("-", " ").title()
            if "ashbyhq.com" in parsed.netloc and len(parts) >= 1:
                return parts[0].replace("-", " ").title()
            if "wellfound.com" in parsed.netloc and len(parts) >= 2:
                return parts[1].replace("-", " ").title()
            if "instahire.com" in parsed.netloc and len(parts) >= 2:
                return parts[1].replace("-", " ").title()
            
            domain = parsed.netloc.replace("www.", "").split(".")[0]
            if domain and domain.lower() not in ["jobs", "careers", "apply"]:
                return domain.title()
            return "Tech Company"
        except:
            return "Tech Company"

    def _enrich_contact_email(self, job: dict):
        """
        Extracts HR / Recruiter email if found in job description,
        or generates the high-probability cold outreach email address.
        """
        desc = job.get("description", "")
        # Find explicit emails in description
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        found_emails = re.findall(email_pattern, desc)
        valid_emails = [
            e for e in found_emails 
            if not any(skip in e.lower() for skip in ["example", "sentry", "github", "w3.org", "support@"])
        ]

        if valid_emails:
            job["contact_email"] = valid_emails[0]
            job["contact_type"] = "Direct HR/Recruiter Email"
            return

        # Infer company domain from URL
        url = job.get("url", "")
        parsed = urlparse(url)
        domain = ""
        company = job.get("company", "").strip().lower()

        if "lever.co" in parsed.netloc:
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 1:
                domain = f"{path_parts[0]}.com"
        elif "greenhouse.io" in parsed.netloc:
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 1:
                domain = f"{path_parts[0]}.com"
        elif parsed.netloc and not any(p in parsed.netloc for p in ["remotive", "jobicy", "remoteok", "linkedin", "naukri"]):
            domain = parsed.netloc.replace("www.", "")
        
        if not domain and company:
            clean_company = re.sub(r'[^a-zA-Z0-9]', '', company)
            domain = f"{clean_company}.com"

        if domain:
            job["contact_email"] = f"careers@{domain}"
            job["contact_type"] = f"Standard Company Careers Desk (careers@{domain})"
        else:
            job["contact_email"] = "careers@company.com"
            job["contact_type"] = "Careers Desk"

    def _enrich_linkedin_contacts(self, job: dict):
        """
        Crawls and discovers key LinkedIn recruiter and engineering lead contacts
        for the job's company, generating a tailored 300-char LinkedIn pitch.
        """
        company = job.get("company", "").strip()
        if not company or company.lower() in ["tech company", "stealth", "confidential"]:
            search_query_url = "https://www.linkedin.com/search/results/people/?keywords=technical+recruiter"
            job["linkedin_contacts"] = [{
                "name": "Technical Recruiter",
                "role": "Talent Acquisition",
                "url": search_query_url
            }]
            job["linkedin_search_url"] = search_query_url
            job["linkedin_connection_note"] = self._generate_linkedin_note(job, [])
            return

        # Check in-memory cache to prevent duplicate queries for the same company
        if company in self.company_linkedin_cache:
            cached = self.company_linkedin_cache[company]
            job["linkedin_contacts"] = cached.get("contacts", [])
            job["linkedin_search_url"] = cached.get("search_url", "")
            job["linkedin_connection_note"] = self._generate_linkedin_note(job, cached.get("contacts", []))
            return

        search_query_url = f"https://www.linkedin.com/search/results/people/?keywords={requests.utils.quote(company)}+technical+recruiter"
        discovered_contacts = []

        if self.firecrawl_key:
            # Query Firecrawl for live LinkedIn profiles at this company
            query = f'site:linkedin.com/in/ "{company}" ("Technical Recruiter" OR "Talent Acquisition" OR "Engineering Manager" OR "CTO")'
            headers = {
                "Authorization": f"Bearer {self.firecrawl_key}",
                "Content-Type": "application/json"
            }
            try:
                resp = self.session.post(
                    f"{config.FIRECRAWL_API_URL}/v1/search",
                    json={"query": query, "limit": 3},
                    headers=headers,
                    timeout=8
                )
                if resp.status_code == 200:
                    results = resp.json().get("data", [])
                    for item in results:
                        u = item.get("url", "")
                        t = item.get("title", "")
                        if "linkedin.com/in/" in u and t:
                            name, role = self._parse_linkedin_title(t, company)
                            if name:
                                discovered_contacts.append({
                                    "name": name,
                                    "role": role,
                                    "url": u
                                })
            except Exception as e:
                logger.debug(f"LinkedIn search error for {company}: {e}")

        # If no specific profile was found, provide default team search entry
        if not discovered_contacts:
            discovered_contacts.append({
                "name": f"{company} Recruiting Team",
                "role": "Technical Recruiting & Talent Acquisition",
                "url": search_query_url
            })

        self.company_linkedin_cache[company] = {
            "contacts": discovered_contacts,
            "search_url": search_query_url
        }

        job["linkedin_contacts"] = discovered_contacts
        job["linkedin_search_url"] = search_query_url
        job["linkedin_connection_note"] = self._generate_linkedin_note(job, discovered_contacts)

    def _parse_linkedin_title(self, raw_title: str, company: str) -> tuple:
        """Parses LinkedIn title string into (Name, Role)."""
        clean = raw_title.replace(" | LinkedIn", "").replace(" - LinkedIn", "").strip()
        parts = re.split(r'\s*[-–—|]\s*', clean)
        name = parts[0].strip() if len(parts) >= 1 else ""
        role = parts[1].strip() if len(parts) >= 2 else f"Talent Acquisition at {company}"
        # Avoid generic or invalid titles
        if len(name.split()) > 4 or any(w in name.lower() for w in ["jobs", "careers", "linkedin", "hiring"]):
            name = f"{company} Recruiter"
        return name, role

    def _generate_linkedin_note(self, job: dict, contacts: list = None) -> str:
        """Generates a high-conversion 300-char LinkedIn connection request note."""
        company = job.get("company", "the team")
        role = job.get("title", "Backend Developer")
        first_name = "there"
        if contacts and len(contacts) > 0:
            first_raw = contacts[0].get("name", "").split()[0]
            if first_raw and "Team" not in first_raw and "Recruiter" not in first_raw:
                first_name = first_raw

        note = (
            f"Hi {first_name}, saw the {role} opening at {company}. "
            f"With 1.8+ yrs building high-scale NestJS/Node.js/PostgreSQL APIs (300K+ users at PRYM), "
            f"I'd love to connect and share how my backend background fits your team's goals!"
        )
        return note[:298]
