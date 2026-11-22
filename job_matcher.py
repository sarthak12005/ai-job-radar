import re
from typing import Dict, Any, List

class JobMatcher:
    def __init__(self, candidate_profile: Dict[str, Any]):
        self.profile = candidate_profile
        self.skills = [s.lower() for s in candidate_profile.get("core_skills", [])]
        self.candidate_exp = candidate_profile.get("experience_years", 1.8)

    def calculate_match(self, job: Dict[str, Any], target_country: str = "India", target_state: str = None) -> Dict[str, Any]:
        """
        Calculates match score (0-100%) between job posting and Sarthak's resume.
        Breakdown:
        - Title / Role fit: 30 pts
        - Skill overlap: 45 pts
        - Experience suitability: 15 pts
        - Location / Remote match: 10 pts (calibrated to target country and hub)
        """
        title = (job.get("title") or "").lower()
        desc = (job.get("description") or "").lower()
        location = (job.get("location") or "").lower()
        combined_text = f"{title} {desc} {location}"

        # 1. Title / Role Match (Max 30)
        title_score = 0
        role_keywords = ["backend", "node", "nest", "typescript", "full stack", "fullstack", "software engineer", "api developer", "web developer"]
        matched_roles = [kw for kw in role_keywords if kw in title]
        if "backend" in title or "node" in title or "nest" in title:
            title_score = 30
        elif "full stack" in title or "fullstack" in title:
            title_score = 28
        elif "software engineer" in title or "software developer" in title:
            title_score = 24
        elif matched_roles:
            title_score = 18

        # Seniority calibration: Sarthak has 1.8 years (Junior / Mid)
        senior_markers = ["senior", "staff", "principal", "lead", "director", "architect", "head of", "vp"]
        is_senior = any(sm in title for sm in senior_markers)
        junior_markers = ["junior", "jr", "associate", "entry", "sde 1", "sde-1", "sde i", "early career", "graduate"]
        is_junior = any(jm in title for jm in junior_markers)

        seniority_adj = 0
        if is_junior:
            seniority_adj = +10
        elif is_senior:
            seniority_adj = -15  # Senior roles penalized so junior/mid rank higher
        else:
            seniority_adj = +5   # Standard "Software Engineer" / "Backend Developer" fits well

        # 2. Skill Overlap (Max 45)
        matched_skills = []
        for skill in self.skills:
            pattern = r'\b' + re.escape(skill) + r'\b'
            if re.search(pattern, combined_text, re.IGNORECASE):
                # Capitalize nicely
                matched_skills.append(skill.title() if not skill.endswith("js") else skill)

        # High priority skills get weighted boost
        high_val = ["nestjs", "node.js", "nodejs", "typescript", "postgresql", "redis", "prisma", "bullmq", "rest api", "rest apis"]
        high_matches = [s for s in matched_skills if s.lower() in high_val]

        skill_ratio = len(matched_skills) / max(len(self.skills), 1)
        skill_score = min(45, int(skill_ratio * 40) + (len(high_matches) * 5))
        if len(high_matches) >= 3 and skill_score < 35:
            skill_score = 35

        # 3. Experience Match (Max 15)
        exp_score = 15
        # Look for required years
        exp_patterns = [
            r'(\d+)\+?\s*(?:to|-)\s*(\d+)\+?\s*years?',
            r'(\d+)\+?\s*years?(?:\s*of\s*experience)?'
        ]
        min_req = None
        max_req = None
        for pat in exp_patterns:
            m = re.search(pat, combined_text)
            if m:
                groups = m.groups()
                if len(groups) == 2 and groups[1]:
                    min_req = float(groups[0])
                    max_req = float(groups[1])
                elif len(groups) >= 1 and groups[0]:
                    min_req = float(groups[0])
                break

        if min_req is not None:
            if min_req <= 2.5:
                exp_score = 15  # Perfect for 1.8 years
            elif min_req <= 3.5:
                exp_score = 12  # Reachable mid-level
            elif min_req <= 5.0:
                exp_score = 6   # Stretch
            else:
                exp_score = 2   # Senior (5+ yrs)

        # 4. Location Match (Max 10)
        loc_score = 5
        loc_tag = "Remote / Flex"

        # Check geo restrictions
        is_geo_restricted_away = any(gr in location for gr in ["usa only", "us only", "europe only", "uk only", "americas only", "canada only", "latam only", "eu only"])

        if target_country and target_country.lower() == "india":
            india_hubs = ["pune", "bangalore", "bengaluru", "mumbai", "hyderabad", "noida", "gurgaon", "gurugram", "delhi", "chennai"]
            if any(h in location for h in india_hubs):
                loc_score = 10
                loc_tag = "India Tech Hub"
            elif "india" in location:
                loc_score = 10
                loc_tag = "India"
            elif any(rw in location for rw in ["worldwide", "anywhere", "global"]) or (location == "remote" and not is_geo_restricted_away):
                loc_score = 8
                loc_tag = "Worldwide Remote"
            elif is_geo_restricted_away or any(c in location for c in ["spain", "poland", "london", "costa rica", "canada", "toronto", "germany", "berlin", "us-remote"]):
                loc_score = 0
                seniority_adj -= 15  # Penalize non-India roles when user wants India
                loc_tag = "Non-India (Outside Scope)"
            else:
                loc_score = 4
                loc_tag = "Unspecified Location"

        # Check specific state/hub preference
        if target_state and target_state.lower() not in ["all", "all india", "all us", "any location", "any remote", ""]:
            clean_ts = target_state.split("(")[0].strip().lower()
            if clean_ts and clean_ts in location:
                loc_score = 10
                loc_tag = f"{clean_ts.title()} Hub Direct"

        total_score = max(0, min(100, title_score + skill_score + exp_score + loc_score + seniority_adj))

        # Summary bullet
        summary_parts = []
        if is_junior:
            summary_parts.append("Level: Junior/Associate (Direct Match)")
        elif is_senior:
            summary_parts.append("Level: Senior/Staff")
        else:
            summary_parts.append("Level: Mid/SWE (1-3 yrs fit)")

        if high_matches:
            summary_parts.append(f"Stack: {', '.join(high_matches[:4])}")
        if min_req is not None:
            summary_parts.append(f"Req Exp: {min_req}+ yrs (You: {self.candidate_exp} yrs)")
        summary_parts.append(f"📍 {loc_tag}")
        
        match_summary = " | ".join(summary_parts) if summary_parts else "Role aligns with Backend / Full Stack profile"

        # Generate Cold Outreach Icebreaker
        company = job.get("company", "the team")
        job_title = job.get("title", "Backend Developer")
        cold_subject = f"Application for {job_title} | Sarthak Joshi (1.8+ yrs Backend / NestJS / TypeScript)"
        cold_body = (
            f"Hi Hiring Team at {company},\n\n"
            f"I came across the {job_title} opening posted recently and wanted to directly express my strong interest. "
            f"With 1.8+ years of production experience building high-scale backend services at PRYM Solutions using "
            f"Node.js, NestJS, TypeScript, PostgreSQL, and Redis (supporting 300K+ registered users and 300+ REST APIs), "
            f"I am confident in hitting the ground running.\n\n"
            f"Would love to connect and share more on how I can contribute to {company}.\n\n"
            f"Best regards,\n"
            f"Sarthak Joshi\n"
            f"+91 9325619172 | sarthakjoshi12005@gmail.com\n"
            f"Portfolio: https://linkedin.com/in/sarthakjoshi"
        )

        return {
            "match_score": total_score,
            "matched_skills": matched_skills,
            "match_summary": match_summary,
            "is_recommended": total_score >= 50,
            "cold_outreach_subject": cold_subject,
            "cold_outreach_body": cold_body
        }
