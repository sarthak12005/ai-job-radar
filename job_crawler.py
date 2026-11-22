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
        """Fetches software-dev jobs from Remotive, filtering geo-restrictions."""
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
                loc = item.get("candidate_required_location", "Remote")
                loc_l = loc.lower()

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
                        "description": BeautifulSoup(desc, "html.parser").get_text()[:600],
                        "source": "Remotive Career Board"
                    })
        return jobs

    def _crawl_jobicy(self, target_country: str = "India") -> list:
        """Fetches jobs from Jobicy API, filtering geo-restrictions."""
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
                loc = item.get("jobGeo", "Remote")
                loc_l = loc.lower()

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
                        "description": BeautifulSoup(desc, "html.parser").get_text()[:600],
                        "source": "Jobicy Career Hub"
                    })
        return jobs

    def _crawl_remoteok(self, target_country: str = "India") -> list:
        """Fetches jobs from RemoteOK API, filtering out non-target locations."""
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
                    loc = item.get("location", "Remote")
                    loc_l = loc.lower()

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
                            "date_posted": date_str or datetime.date.today().isoformat(),
                            "description": BeautifulSoup(desc, "html.parser").get_text()[:600],
                            "source": "RemoteOK"
                        })
        return jobs

    def _crawl_ats_career_pages(self, target_country: str = "India", target_state: str = None) -> list:
        """
        Crawls popular company career pages using Greenhouse ATS endpoints,
        filtering locations according to user preferences.
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
                        loc_l = loc.lower()

                        if target_country and target_country.lower() == "india":
                            # Skip if explicit foreign city without India / Remote option
                            if any(c in loc_l for c in ["san francisco", "new york", "seattle", "austin", "london", "dublin", "paris", "berlin", "tokyo", "sydney", "madrid", "toronto"]) and "india" not in loc_l and "remote" not in loc_l:
                                continue

                        if any(k in t_lower for k in ["backend", "node", "nest", "typescript", "full stack", "fullstack", "software engineer", "api"]):
                            target_jobs.append({
                                "title": title,
                                "company": target["company"],
                                "location": loc,
                                "country": config.detect_country(loc),
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

    def _crawl_with_firecrawl(self, target_country: str = "India", target_state: str = None) -> list:
        """
        Uses Firecrawl API to search for live career postings
        matching user's stack and targeted location (country & state/hub).
        """
        jobs = []
        headers = {
            "Authorization": f"Bearer {self.firecrawl_key}",
            "Content-Type": "application/json"
        }

        # Build dynamic location queries
        if target_country and target_country.lower() == "india":
            hub_kw = "India OR Pune OR Bangalore OR Bengaluru OR Mumbai OR Hyderabad OR Remote"
            if target_state and target_state.lower() not in ["all", "all india", "any location", "any remote"]:
                clean_s = target_state.split("(")[0].strip()
                if clean_s:
                    hub_kw = f'"{clean_s}" OR India'
            queries = [
                f'site:lever.co ("Node.js" OR "NestJS" OR "Backend Developer") ({hub_kw})',
                f'site:greenhouse.io ("Backend Developer" OR "TypeScript") ({hub_kw})'
            ]
        elif target_country and target_country.lower() in ["united states", "us"]:
            queries = [
                'site:lever.co ("Node.js" OR "NestJS") ("United States" OR "Remote US")',
                'site:greenhouse.io ("Backend Developer" OR "TypeScript") ("United States" OR "Remote")'
            ]
        elif target_country and target_country.lower() in ["europe", "eu", "uk"]:
            queries = [
                'site:lever.co ("Node.js" OR "NestJS") ("Europe" OR "UK" OR "London")',
                'site:greenhouse.io ("Backend Developer" OR "TypeScript") ("Europe" OR "UK")'
            ]
        else:
            queries = [
                'site:lever.co "Node.js" OR "NestJS" developer',
                'site:greenhouse.io "Backend Developer" "TypeScript" Remote'
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
                            loc_display = f"{target_state or 'India'} / Remote" if (target_country == "India") else "Remote / Global"
                            jobs.append({
                                "title": title or "Backend Developer",
                                "company": company,
                                "location": loc_display,
                                "country": target_country if target_country != "All" else config.detect_country(loc_display),
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
