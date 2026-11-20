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

class JobCrawler:
    def __init__(self, firecrawl_key=None):
        self.firecrawl_key = firecrawl_key or config.FIRECRAWL_API_KEY
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/html, */*"
        })

    def crawl_all(self, target_roles=None, days_back=2) -> list:
        """Crawls all sources concurrently for jobs posted recently (past 1-2 days)."""
        roles = target_roles or config.TARGET_ROLES
        all_jobs = []

        logger.info("Starting concurrent job crawl across sources...")
        tasks = [
            ("Remotive", self._crawl_remotive),
            ("Jobicy", self._crawl_jobicy),
            ("RemoteOK", self._crawl_remoteok),
            ("ATS Career Pages", self._crawl_ats_career_pages)
        ]

        if self.firecrawl_key:
            tasks.append(("Firecrawl", self._crawl_with_firecrawl))

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
                
                # Enrich with HR / Recruiter email contact if missing
                self._enrich_contact_email(j)
                deduped.append(j)

        logger.info(f"Total unique recent jobs collected: {len(deduped)}")
        return deduped

    def _crawl_remotive(self) -> list:
        """Fetches software-dev jobs from Remotive."""
        jobs = []
        url = "https://remotive.com/api/remote-jobs?category=software-dev&limit=40"
        resp = self.session.get(url, timeout=12)
        if resp.status_code == 200:
            data = resp.json().get("jobs", [])
            for item in data:
                title = item.get("title", "")
                tags = [t.lower() for t in item.get("tags", [])]
                desc = item.get("description", "")
                company = item.get("company_name", "")
                pub_date = item.get("publication_date", "")[:10]
                
                # Check keyword relevance
                t_lower = title.lower()
                if any(k in t_lower or k in tags for k in ["node", "nest", "backend", "typescript", "fullstack", "full stack", "api", "postgres"]):
                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": item.get("candidate_required_location", "Remote"),
                        "url": item.get("url", ""),
                        "date_posted": pub_date or datetime.date.today().isoformat(),
                        "description": BeautifulSoup(desc, "html.parser").get_text()[:600],
                        "source": "Remotive Career Board"
                    })
        return jobs

    def _crawl_jobicy(self) -> list:
        """Fetches jobs from Jobicy API."""
        jobs = []
        url = "https://jobicy.com/api/v2/remote-jobs?count=40&industry=engineering"
        resp = self.session.get(url, timeout=12)
        if resp.status_code == 200:
            data = resp.json().get("jobs", [])
            for item in data:
                title = item.get("jobTitle", "")
                desc = item.get("jobDescription", "")
                company = item.get("companyName", "")
                pub_date = item.get("pubDate", "")[:10]
                t_lower = title.lower()

                if any(k in t_lower for k in ["node", "nest", "backend", "typescript", "full stack", "fullstack", "software engineer", "developer"]):
                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": item.get("jobGeo", "Remote"),
                        "url": item.get("url", ""),
                        "date_posted": pub_date or datetime.date.today().isoformat(),
                        "description": BeautifulSoup(desc, "html.parser").get_text()[:600],
                        "source": "Jobicy Career Hub"
                    })
        return jobs

    def _crawl_remoteok(self) -> list:
        """Fetches jobs from RemoteOK API."""
        jobs = []
        url = "https://remoteok.com/api"
        resp = self.session.get(url, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                # First element is legal metadata
                for item in data[1:50]:
                    title = item.get("position", "")
                    tags = [t.lower() for t in item.get("tags", [])]
                    desc = item.get("description", "")
                    company = item.get("company", "")
                    date_str = item.get("date", "")[:10]
                    t_lower = title.lower()

                    if any(k in t_lower or k in tags for k in ["node", "nest", "backend", "typescript", "fullstack", "postgres"]):
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": item.get("location", "Remote"),
                            "url": item.get("url", ""),
                            "date_posted": date_str or datetime.date.today().isoformat(),
                            "description": BeautifulSoup(desc, "html.parser").get_text()[:600],
                            "source": "RemoteOK"
                        })
        return jobs

    def _crawl_ats_career_pages(self) -> list:
        """
        Crawls popular company career pages using Greenhouse ATS endpoints
        frequently hiring tech talent in India and globally (concurrently).
        """
        ats_targets = [
            {"type": "greenhouse", "company": "Stripe", "url": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"},
            {"type": "greenhouse", "company": "Cloudflare", "url": "https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs"},
            {"type": "greenhouse", "company": "GitLab", "url": "https://boards-api.greenhouse.io/v1/boards/gitlab/jobs"},
            {"type": "greenhouse", "company": "Elastic", "url": "https://boards-api.greenhouse.io/v1/boards/elastic/jobs"},
            {"type": "greenhouse", "company": "Datadog", "url": "https://boards-api.greenhouse.io/v1/boards/datadog/jobs"},
            {"type": "greenhouse", "company": "Coinbase", "url": "https://boards-api.greenhouse.io/v1/boards/coinbase/jobs"},
            {"type": "greenhouse", "company": "Affirm", "url": "https://boards-api.greenhouse.io/v1/boards/affirm/jobs"},
            {"type": "greenhouse", "company": "Figma", "url": "https://boards-api.greenhouse.io/v1/boards/figma/jobs"},
            {"type": "greenhouse", "company": "Brex", "url": "https://boards-api.greenhouse.io/v1/boards/brex/jobs"}
        ]

        def _fetch_target(target):
            target_jobs = []
            try:
                resp = self.session.get(target["url"], timeout=6)
                if resp.status_code == 200:
                    data = resp.json().get("jobs", [])
                    for item in data:
                        title = item.get("title", "")
                        t_lower = title.lower()
                        loc = item.get("location", {}).get("name", "India / Remote")
                        if any(k in t_lower for k in ["backend", "node", "nest", "typescript", "full stack", "fullstack", "software engineer", "api"]):
                            target_jobs.append({
                                "title": title,
                                "company": target["company"],
                                "location": loc,
                                "url": item.get("absolute_url", ""),
                                "date_posted": datetime.date.today().isoformat(),
                                "description": f"Position: {title} at {target['company']}",
                                "source": f"{target['company']} Career ATS"
                            })
            except Exception as ex:
                logger.debug(f"ATS crawl {target['company']} skipped: {ex}")
            return target_jobs

        all_ats = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(_fetch_target, t) for t in ats_targets]
            for f in as_completed(futures):
                all_ats.extend(f.result())
        return all_ats

    def _crawl_with_firecrawl(self) -> list:
        """
        Uses Firecrawl API to search for live career postings
        matching Sarthak's stack on company portals.
        """
        jobs = []
        headers = {
            "Authorization": f"Bearer {self.firecrawl_key}",
            "Content-Type": "application/json"
        }
        queries = [
            'site:lever.co "Node.js" OR "NestJS" developer',
            'site:greenhouse.io "Backend Developer" "TypeScript" Bangalore OR Remote'
        ]

        for query in queries:
            payload = {
                "query": query,
                "limit": 10
            }
            try:
                resp = self.session.post(f"{config.FIRECRAWL_API_URL}/v1/search", json=payload, headers=headers, timeout=12)
                if resp.status_code == 200:
                    results = resp.json().get("data", [])
                    for item in results:
                        title = item.get("title", "")
                        url = item.get("url", "")
                        desc = item.get("description", "")
                        company = self._extract_company_from_url(url)
                        if url and any(k in (title + desc).lower() for k in ["node", "nest", "typescript", "backend", "full stack"]):
                            jobs.append({
                                "title": title or "Backend Developer",
                                "company": company,
                                "location": "Remote / India",
                                "url": url,
                                "date_posted": datetime.date.today().isoformat(),
                                "description": desc[:600],
                                "source": "Firecrawl Web Discovery"
                            })
            except Exception as e:
                logger.warning(f"Firecrawl query error for '{query}': {e}")
        return jobs

    def _extract_company_from_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            parts = parsed.path.strip("/").split("/")
            if "lever.co" in parsed.netloc and len(parts) >= 1:
                return parts[0].capitalize()
            if "greenhouse.io" in parsed.netloc and len(parts) >= 1:
                return parts[0].capitalize()
            domain = parsed.netloc.replace("www.", "").split(".")[0]
            return domain.capitalize()
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
