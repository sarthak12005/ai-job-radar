import os
import datetime
import logging
from typing import List, Dict, Any, Optional
from bson import ObjectId
import pymongo
from pymongo import MongoClient, UpdateOne

import config
from logger_config import get_logger

logger = get_logger("Database")

class Database:
    def __init__(self, uri: str = None, db_name: str = "job_crawler_db"):
        self.uri = uri or os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017")
        self.db_name = db_name or os.getenv("MONGODB_DB_NAME", "job_crawler_db")
        self.client = None
        self.db = None
        self.jobs_col = None
        self.runs_col = None
        self.outreach_col = None
        self.connect()

    def connect(self):
        try:
            self.client = MongoClient(self.uri, serverSelectionTimeoutMS=3000)
            self.db = self.client[self.db_name]
            self.jobs_col = self.db["jobs"]
            self.runs_col = self.db["crawler_runs"]
            self.outreach_col = self.db["outreach_logs"]
            
            # Create indexes
            self.jobs_col.create_index([("url", pymongo.ASCENDING)], unique=True)
            self.jobs_col.create_index([("match_score", pymongo.DESCENDING)])
            self.jobs_col.create_index([("company", pymongo.ASCENDING)])
            self.jobs_col.create_index([("status", pymongo.ASCENDING)])
            self.jobs_col.create_index([("created_at", pymongo.DESCENDING)])
            logger.info(f"Connected to MongoDB database '{self.db_name}' successfully.")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")

    def is_connected(self) -> bool:
        if not self.client:
            return False
        try:
            self.client.admin.command("ping")
            return True
        except:
            return False

    def clear_all_data(self) -> Dict[str, int]:
        """Clears all jobs, runs, and outreach logs from the database."""
        deleted = {"jobs": 0, "runs": 0, "outreach": 0}
        if self.jobs_col is not None:
            deleted["jobs"] = self.jobs_col.delete_many({}).deleted_count
        if self.runs_col is not None:
            deleted["runs"] = self.runs_col.delete_many({}).deleted_count
        if self.outreach_col is not None:
            deleted["outreach"] = self.outreach_col.delete_many({}).deleted_count
        logger.info(f"Database reset: cleared {deleted['jobs']} jobs, {deleted['runs']} runs, {deleted['outreach']} outreach logs.")
        return deleted

    def upsert_jobs(self, jobs: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Upserts jobs into MongoDB based on unique URL.
        Preserves user-updated fields (status, notes) if the job already exists.
        """
        if self.jobs_col is None:
            return {"inserted": 0, "updated": 0}

        now = datetime.datetime.now()
        inserted_count = 0
        updated_count = 0

        operations = []
        for job in jobs:
            url = job.get("url")
            if not url:
                continue

            # Update document fields
            set_fields = {
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location", "Remote"),
                "match_score": job.get("match_score", 0),
                "match_summary": job.get("match_summary", ""),
                "matched_skills": job.get("matched_skills", []),
                "contact_email": job.get("contact_email", "careers@company.com"),
                "contact_type": job.get("contact_type", "Careers Desk"),
                "cold_outreach_subject": job.get("cold_outreach_subject", ""),
                "cold_outreach_body": job.get("cold_outreach_body", ""),
                "source": job.get("source", "Web"),
                "date_posted": job.get("date_posted", datetime.date.today().isoformat()),
                "description": job.get("description", "")[:1000],
                "last_scraped_at": now
            }

            # On insert only
            set_on_insert = {
                "created_at": now,
                "status": "new",
                "notes": "",
                "applied_at": None,
                "contacted_at": None
            }

            op = UpdateOne(
                {"url": url},
                {"$set": set_fields, "$setOnInsert": set_on_insert},
                upsert=True
            )
            operations.append(op)

        if operations:
            result = self.jobs_col.bulk_write(operations, ordered=False)
            inserted_count = result.upserted_count
            updated_count = result.modified_count

        logger.info(f"MongoDB Upsert: {inserted_count} new jobs inserted, {updated_count} updated.")
        return {"inserted": inserted_count, "updated": updated_count, "total_processed": len(jobs)}

    def get_jobs(
        self,
        search: Optional[str] = None,
        status: Optional[str] = None,
        min_score: int = 0,
        sort_by: str = "match_score",
        sort_order: int = -1,
        limit: int = 10,
        skip: int = 0
    ) -> Dict[str, Any]:
        """Fetches jobs with flexible search, filtering, and structured pagination."""
        if self.jobs_col is None:
            return {
                "jobs": [],
                "total": 0,
                "returned": 0,
                "page": 1,
                "limit": limit,
                "total_pages": 1,
                "has_next": False,
                "has_prev": False
            }

        query = {}

        if min_score > 0:
            query["match_score"] = {"$gte": min_score}

        if status and status.lower() != "all":
            query["status"] = status.lower()

        if search and search.strip():
            s = search.strip()
            regex = {"$regex": s, "$options": "i"}
            query["$or"] = [
                {"title": regex},
                {"company": regex},
                {"location": regex},
                {"matched_skills": regex},
                {"match_summary": regex}
            ]

        # Sorting
        sort_field = "match_score" if sort_by == "match_score" else ("created_at" if sort_by == "date" else "company")
        
        total_count = self.jobs_col.count_documents(query)
        cursor = self.jobs_col.find(query).sort(sort_field, sort_order).skip(skip).limit(limit)

        jobs = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            if isinstance(doc.get("created_at"), datetime.datetime):
                doc["created_at"] = doc["created_at"].isoformat()
            if isinstance(doc.get("last_scraped_at"), datetime.datetime):
                doc["last_scraped_at"] = doc["last_scraped_at"].isoformat()
            if isinstance(doc.get("applied_at"), datetime.datetime):
                doc["applied_at"] = doc["applied_at"].isoformat()
            if isinstance(doc.get("contacted_at"), datetime.datetime):
                doc["contacted_at"] = doc["contacted_at"].isoformat()
            jobs.append(doc)

        safe_limit = max(1, limit)
        total_pages = max(1, (total_count + safe_limit - 1) // safe_limit)
        page = (skip // safe_limit) + 1
        has_next = page < total_pages
        has_prev = page > 1

        return {
            "jobs": jobs,
            "total": total_count,
            "returned": len(jobs),
            "page": page,
            "limit": safe_limit,
            "total_pages": total_pages,
            "has_next": has_next,
            "has_prev": has_prev
        }

    def get_job_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.jobs_col is None:
            return None
        try:
            doc = self.jobs_col.find_one({"_id": ObjectId(job_id)})
            if doc:
                doc["_id"] = str(doc["_id"])
            return doc
        except Exception:
            return None

    def update_job_status(self, job_id: str, status: str, notes: Optional[str] = None) -> bool:
        if self.jobs_col is None:
            return False
        try:
            update_data = {"status": status.lower()}
            if notes is not None:
                update_data["notes"] = notes

            now = datetime.datetime.now()
            if status.lower() == "applied":
                update_data["applied_at"] = now
            elif status.lower() == "contacted":
                update_data["contacted_at"] = now

            res = self.jobs_col.update_one({"_id": ObjectId(job_id)}, {"$set": update_data})
            return res.modified_count > 0 or res.matched_count > 0
        except Exception as e:
            logger.error(f"Failed to update job status: {e}")
            return False

    def log_crawler_run(self, run_data: Dict[str, Any]):
        if self.runs_col is None:
            return
        try:
            doc = run_data.copy()
            doc["timestamp"] = datetime.datetime.now()
            self.runs_col.insert_one(doc)
        except Exception as e:
            logger.error(f"Failed to log crawler run: {e}")

    def log_outreach(self, outreach_data: Dict[str, Any]):
        if self.outreach_col is None:
            return
        try:
            doc = outreach_data.copy()
            doc["sent_at"] = datetime.datetime.now()
            self.outreach_col.insert_one(doc)
        except Exception as e:
            logger.error(f"Failed to log outreach: {e}")

    def get_stats(self) -> Dict[str, Any]:
        if self.jobs_col is None:
            return {}
        try:
            total_jobs = self.jobs_col.count_documents({})
            high_matches = self.jobs_col.count_documents({"match_score": {"$gte": 70}})
            new_jobs = self.jobs_col.count_documents({"status": "new"})
            applied_jobs = self.jobs_col.count_documents({"status": "applied"})
            contacted_jobs = self.jobs_col.count_documents({"status": "contacted"})
            interviewing_jobs = self.jobs_col.count_documents({"status": "interviewing"})
            saved_jobs = self.jobs_col.count_documents({"status": "saved"})

            # Get distinct companies
            companies_count = len(self.jobs_col.distinct("company"))

            return {
                "total_jobs": total_jobs,
                "high_matches": high_matches,
                "new_jobs": new_jobs,
                "applied_jobs": applied_jobs,
                "contacted_jobs": contacted_jobs,
                "interviewing_jobs": interviewing_jobs,
                "saved_jobs": saved_jobs,
                "distinct_companies": companies_count
            }
        except Exception as e:
            logger.error(f"Failed to compute stats: {e}")
            return {}

# Global singleton
db_instance = Database()
