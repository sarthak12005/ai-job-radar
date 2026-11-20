import sys
import argparse
import logging
import datetime
from pathlib import Path

import config
from logger_config import get_logger
from resume_parser import ResumeParser
from job_crawler import JobCrawler
from job_matcher import JobMatcher
from excel_reporter import ExcelReporter
from mailer import Mailer
from database import db_instance

logger = get_logger("DailyJobRunner")

def run_pipeline(min_score: int = 50, send_mail: bool = True):
    logger.info("==================================================")
    logger.info("🚀 Starting Daily Job Match Pipeline")
    logger.info(f"Candidate: {config.CANDIDATE_NAME} ({config.CANDIDATE_EMAIL})")
    logger.info("==================================================")

    # 1. Parse Resume
    logger.info("Step 1: Reading Candidate Resume & Profile...")
    parser = ResumeParser()
    profile = parser.get_profile()
    logger.info(f"Targeting: {profile.get('title')} with {len(profile.get('core_skills', []))} core skills")

    # 2. Crawl Jobs
    logger.info("Step 2: Crawling recent jobs from ATS, APIs, and portals...")
    crawler = JobCrawler()
    raw_jobs = crawler.crawl_all()
    logger.info(f"Total raw jobs retrieved: {len(raw_jobs)}")

    if not raw_jobs:
        logger.warning("No jobs returned. Retrying with broad discovery...")

    # 3. Match & Score
    logger.info("Step 3: Scoring jobs against candidate resume...")
    matcher = JobMatcher(profile)
    matched_jobs = []
    
    for job in raw_jobs:
        match_info = matcher.calculate_match(job)
        job.update(match_info)
        if job.get("match_score", 0) >= min_score:
            matched_jobs.append(job)

    logger.info(f"Jobs matching threshold (>={min_score}%): {len(matched_jobs)}")

    # 3.5 Upsert into MongoDB
    logger.info("Step 3.5: Upserting jobs into MongoDB database...")
    try:
        db_res = db_instance.upsert_jobs(matched_jobs)
        logger.info(f"MongoDB Sync: {db_res.get('inserted', 0)} new jobs, {db_res.get('updated', 0)} updated.")
        db_instance.log_crawler_run({
            "jobs_crawled": len(raw_jobs),
            "jobs_matched": len(matched_jobs),
            "new_inserted": db_res.get("inserted", 0),
            "updated": db_res.get("updated", 0),
            "min_score": min_score,
            "email_sent": send_mail
        })
    except Exception as e:
        logger.warning(f"MongoDB sync error: {e}")

    # 4. Generate Excel Report
    logger.info("Step 4: Generating styled Excel Workbook...")
    reporter = ExcelReporter(config.REPORTS_DIR)
    excel_path = reporter.generate_report(matched_jobs, config.CANDIDATE_NAME)
    logger.info(f"Excel report created: {excel_path}")

    # 5. Email Dispatch
    if send_mail:
        logger.info(f"Step 5: Dispatching Daily Email to {config.RECIPIENT_EMAIL}...")
        mailer = Mailer()
        sent = mailer.send_daily_report(excel_path, matched_jobs, config.CANDIDATE_NAME)
        if sent:
            logger.info("Email delivered successfully!")
        else:
            logger.info("Email dispatch saved locally as preview.")
    else:
        logger.info("Email sending skipped as requested.")

    logger.info("==================================================")
    logger.info(f"🎉 Pipeline Completed Successfully! {len(matched_jobs)} jobs ready in Excel.")
    logger.info(f"Report location: {excel_path}")
    logger.info("==================================================")
    return excel_path, matched_jobs

def main():
    parser = argparse.ArgumentParser(description="Automated Daily Job Matcher & Emailer")
    parser.add_argument("--min-score", type=int, default=50, help="Minimum match score (0-100)")
    parser.add_argument("--no-mail", action="store_true", help="Skip sending email")
    parser.add_argument("--test", action="store_true", help="Run quick test cycle")
    args = parser.parse_args()

    run_pipeline(min_score=args.min_score, send_mail=not args.no_mail)

if __name__ == "__main__":
    main()

