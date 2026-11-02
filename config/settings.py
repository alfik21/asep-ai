import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("ASEP_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.getenv("ASEP_DB_PATH", DATA_DIR / "asep.db"))

# Timezone & Localization
TIMEZONE = os.getenv("ASEP_TIMEZONE", "Asia/Jakarta")

# Security & WhatsApp Allowlist
OWNER_PHONE = os.getenv("ASEP_OWNER_PHONE", "")
raw_allowed = os.getenv("ASEP_ALLOWED_NUMBERS", OWNER_PHONE)
ALLOWED_NUMBERS = [num.strip() for num in raw_allowed.split(",") if num.strip()]

# API & Runtime Limits (Optimized for 1GB VPS)
API_TIMEOUT_SECONDS = int(os.getenv("ASEP_API_TIMEOUT", "20"))
BUSY_TIMEOUT_MS = int(os.getenv("ASEP_DB_BUSY_TIMEOUT_MS", "5000"))
LOG_LEVEL = os.getenv("ASEP_LOG_LEVEL", "INFO")
