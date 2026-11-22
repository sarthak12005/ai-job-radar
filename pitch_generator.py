"""
pitch_generator.py
==================
Generates tailored cold outreach emails, automated follow-up sequences,
LinkedIn connection notes, and technical interview preparation kits.

Supports:
1. Gemini 1.5/2.0 REST API (if GEMINI_API_KEY is configured).
2. Intelligent Heuristic Rule Engine (zero-dependency offline fallback).
"""

import os
import json
import logging
import requests
from typing import Dict, Any, List, Optional
import config

logger = logging.getLogger("PitchGenerator")


class PitchGenerator:
    def __init__(self):
        self.candidate_name = config.CANDIDATE_NAME
        self.candidate_email = config.CANDIDATE_EMAIL
        self.candidate_phone = config.CANDIDATE_PHONE
        self.api_key = getattr(config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

    def generate_full_kit(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates complete outreach kit:
        - initial_pitch (Day 0)
        - followup_day3 (Day 3 bump)
        - followup_day7 (Day 7 final value check-in)
        - linkedin_note (<= 300 chars)
        - interview_prep (technical questions & talking points)
        """
        if self.api_key:
            try:
                result = self._generate_with_gemini(job)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Gemini API generation failed ({e}), falling back to heuristic engine.")

        return self._generate_heuristic(job)

    def generate_interview_prep(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates targeted technical interview questions, architecture discussion points,
        and project talking points for a specific job.
        """
        kit = self.generate_full_kit(job)
        return kit.get("interview_prep", {})

    def _generate_with_gemini(self, job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}

        prompt = f"""
You are an expert tech recruiter and hiring manager.
Candidate: {self.candidate_name}
Experience: 1.8+ years in Backend & Full Stack engineering
Core Skills: Node.js, NestJS, TypeScript, PostgreSQL, Redis, BullMQ, Docker, MongoDB
Key Achievement: Architected & scaled backend APIs handling 300K+ concurrent users with BullMQ message queues and Redis caching at PRYM.

Target Job:
Role: {job.get('title', 'Backend Developer')}
Company: {job.get('company', 'Company')}
Location: {job.get('location', 'Remote')}
Matched Skills: {', '.join(job.get('matched_skills', []))}
Summary: {job.get('match_summary', '')}

Generate a JSON object with EXACTLY this structure:
{{
  "initial_pitch": "3-paragraph concise, persuasive cold email pitch. Paragraph 1: hook + role reference. Paragraph 2: specific match between candidate's NestJS/Node/PostgreSQL/BullMQ scale accomplishments and their needs. Paragraph 3: call to action + resume attachment reference.",
  "followup_day3": "Short, polite 2-paragraph follow-up email for Day 3 after initial outreach.",
  "followup_day7": "High-value final follow-up email for Day 7 mentioning willingness to discuss system architecture.",
  "linkedin_note": "A polite, high-impact connection request note under 280 characters.",
  "interview_prep": {{
    "core_topics": ["3-5 key technical focus areas for this role"],
    "questions": [
      {{
        "question": "Realistic technical interview question",
        "rationale": "Why this company asks it based on their stack",
        "recommended_answer": "Key talking points from candidate background (PRYM 300K users, Redis, NestJS, BullMQ)"
      }}
    ],
    "architecture_talking_points": ["2-3 system design points Sarthak should emphasize"]
  }}
}}
Return ONLY raw JSON, without markdown code fences or backticks.
"""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048}
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=12)
        if resp.status_code == 200:
            text = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            # Strip backticks if present
            clean_text = text.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            if clean_text.startswith("```"):
                clean_text = clean_text[3:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
            return json.loads(clean_text.strip())

        return None

    def _generate_heuristic(self, job: Dict[str, Any]) -> Dict[str, Any]:
        role = job.get("title", "Backend Developer")
        company = job.get("company", "your team")
        matched = job.get("matched_skills", [])
        skills_str = ", ".join(matched[:4]) if matched else "Node.js, NestJS, TypeScript, and PostgreSQL"

        # 1. Initial Pitch (Day 0)
        initial_pitch = f"""Hi {company} Hiring Team,

I came across your open {role} position and wanted to reach out directly. With 1.8+ years of hands-on backend and full-stack engineering experience, my background aligns closely with your tech stack in {skills_str}.

In my previous role at PRYM, I designed and scaled core microservices serving 300K+ concurrent users. My focus areas include architecting resilient REST APIs with NestJS and TypeScript, optimizing complex PostgreSQL relational queries, and implementing distributed queue processing with BullMQ and Redis to maintain sub-100ms response latencies.

I would welcome the opportunity to connect for 10 minutes to discuss how my backend scaling background can support {company}'s engineering goals. I have attached my resume for your review.

Best regards,
{self.candidate_name}
{self.candidate_phone} | {self.candidate_email}"""

        # 2. Day 3 Follow-up Bump
        followup_day3 = f"""Hi {company} Hiring Team,

Following up on my note from earlier this week regarding the {role} opportunity. 

I understand your team is busy reviewing applications, but I wanted to reiterate my strong enthusiasm for {company}. Given my hands-on background scaling high-throughput APIs with NestJS, TypeScript, and Redis, I am confident I can make an immediate positive impact on your team.

Would you be open to a brief introductory chat sometime this week?

Best regards,
{self.candidate_name}
{self.candidate_email}"""

        # 3. Day 7 Final Follow-up
        followup_day7 = f"""Hi {company} Hiring Team,

I am checking in one final time regarding the {role} position at {company}.

If you have already moved forward with other candidates, I completely understand and appreciate your consideration. If the role remains open, I would love to share architectural diagrams and benchmark data from my previous work scaling Node.js/PostgreSQL services to 300K+ users.

Either way, thank you for your time, and I wish {company} continued success!

Best regards,
{self.candidate_name}"""

        # 4. LinkedIn Connection Note (Strictly under 290 characters)
        linkedin_note = f"Hi! Saw the {role[:25]} role at {company[:20]}. With 1.8+ yrs building NestJS, Node.js & PostgreSQL APIs for 300K+ users at PRYM, I'd love to connect and share how my backend background fits your team's roadmap!"
        if len(linkedin_note) > 295:
            linkedin_note = f"Hi! Saw the {role[:20]} opening at {company[:18]}. With 1.8+ yrs in NestJS, Node & PostgreSQL (300K+ users at PRYM), I'd love to connect regarding your engineering needs!"

        # 5. Targeted Technical Interview Prep Kit
        questions = [
            {
                "question": "How do you handle asynchronous job queues and ensure idempotency under high concurrency?",
                "rationale": f"{company}'s stack relies on distributed background workers and queue processing.",
                "recommended_answer": "Discuss BullMQ with Redis backing: using unique job IDs for deduplication, exponential backoff retries, and dead-letter queues (DLQ) to ensure no lost tasks."
            },
            {
                "question": "How do you optimize slow PostgreSQL queries in an active production database?",
                "rationale": "High-scale backend roles require query profiling and index management.",
                "recommended_answer": "Explain running EXPLAIN (ANALYZE, BUFFERS), adding composite/partial B-tree indexes, mitigating N+1 query patterns with DataLoader, and leveraging read-replicas or Redis caching."
            },
            {
                "question": "What architectural patterns do you implement in NestJS to maintain separation of concerns?",
                "rationale": "Evaluates domain-driven structure, module boundaries, and dependency injection.",
                "recommended_answer": "Highlight NestJS modular architecture: custom service providers, repository pattern with TypeORM/Prisma, DTO validation pipes, and custom interceptors for timing and logging."
            },
            {
                "question": "How do you prevent cache stampedes and manage cache invalidation with Redis?",
                "rationale": "Critical for microservices serving hundreds of thousands of users.",
                "recommended_answer": "Discuss probabilistic early expiration (XFetch), mutex locks for cold-cache warming, and cache-aside with TTL jitter."
            }
        ]

        interview_prep = {
            "core_topics": [
                f"{role} system design & API reliability",
                "NestJS / TypeScript modular patterns",
                "PostgreSQL indexing & transaction isolation",
                "BullMQ & Redis distributed task queuing"
            ],
            "questions": questions,
            "architecture_talking_points": [
                "Scaling PRYM backend to 300K+ users using Redis cache-aside and BullMQ background workers.",
                "Designing strict TypeScript types and Pydantic/Zod/NestJS DTO validation for zero-crash endpoints.",
                "Database connection pooling and connection tuning under high peak loads."
            ]
        }

        return {
            "initial_pitch": initial_pitch,
            "followup_day3": followup_day3,
            "followup_day7": followup_day7,
            "linkedin_note": linkedin_note,
            "interview_prep": interview_prep
        }
