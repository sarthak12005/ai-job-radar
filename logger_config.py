import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from collections import deque

import config

LOG_FILE = config.LOGS_DIR / "app.log"
config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Custom Formatter
FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Root Logger Setup
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Clear existing handlers to prevent duplicates
if root_logger.hasHandlers():
    root_logger.handlers.clear()

# 1. Console Handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(FORMATTER)
console_handler.setLevel(logging.INFO)
root_logger.addHandler(console_handler)

# 2. Rotating File Handler (Max 5MB per file, 5 backup files)
file_handler = RotatingFileHandler(
    filename=str(LOG_FILE),
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8"
)
file_handler.setFormatter(FORMATTER)
file_handler.setLevel(logging.INFO)
root_logger.addHandler(file_handler)

def get_logger(name: str) -> logging.Logger:
    """Returns a named logger inheriting the centralized configuration."""
    return logging.getLogger(name)

def get_recent_logs(max_lines: int = 150) -> list:
    """Reads the last N lines from the application log file."""
    if not LOG_FILE.exists():
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = deque(f, maxlen=max_lines)
            return [line.rstrip() for line in lines]
    except Exception as e:
        return [f"Error reading log file: {e}"]

