"""
notifier.py
===========
Real-time push alert notification engine for AI Job Radar.
Supports Telegram Bot alerts when prime job opportunities (match score >= 75%)
are discovered during automated morning crawls.
"""

import os
import logging
import requests
from typing import Dict, Any, List
import config

logger = logging.getLogger("Notifier")


class Notifier:
    def __init__(self):
        self.bot_token = getattr(config, "TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = getattr(config, "TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @property
    def telegram_enabled(self) -> bool:
        return self.is_configured()

    def send_high_match_alert(self, job: Dict[str, Any]) -> bool:
        """
        Sends an instant mobile notification via Telegram when a high-match job is found.
        """
        if not self.is_configured():
            logger.debug("Telegram alerts not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing).")
            return False

        score = job.get("match_score", 0)
        title = job.get("title", "Software Engineer")
        company = job.get("company", "Company")
        location = job.get("location", "Remote")
        url = job.get("url", "#")
        skills = ", ".join(job.get("matched_skills", [])[:4])

        message = (
            f"🎯 *New Prime Job Match ({score}%) Found!*\n\n"
            f"💼 *Role:* {title}\n"
            f"🏢 *Company:* {company}\n"
            f"📍 *Location:* {location}\n"
            f"⚡ *Key Skills:* {skills}\n\n"
            f"🔗 [Direct Application Link]({url})"
        )

        return self._send_telegram_message(message)

    def send_daily_summary_alert(self, total_jobs: int, high_matches: int, candidate_name: str = "Sarthak Joshi") -> bool:
        """
        Sends a morning summary alert to Telegram after crawl completion.
        """
        if not self.is_configured():
            return False

        message = (
            f"🚀 *Daily Job Radar Complete for {candidate_name}*\n\n"
            f"📊 *Total Jobs Crawled:* {total_jobs}\n"
            f"🌟 *High Matches (≥70%):* {high_matches}\n\n"
            f"Access your dashboard to review leads and send 1-click cold emails."
        )

        return self._send_telegram_message(message)

    def _send_telegram_message(self, text: str) -> bool:
        try:
            api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            }
            res = requests.post(api_url, json=payload, timeout=8)
            if res.status_code == 200:
                logger.info("Telegram alert delivered successfully.")
                return True
            else:
                logger.warning(f"Telegram API responded with {res.status_code}: {res.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to dispatch Telegram notification: {e}")
            return False
