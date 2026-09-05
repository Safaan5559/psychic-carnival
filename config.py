import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
BUILD_DIR = Path(os.getenv("BUILD_DIR", DATA_DIR / "builds"))
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "py2apk.sqlite3"))
BUILDER_IMAGE = os.getenv("BUILDER_IMAGE", "py2apk-builder:latest")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_BUILD_SECONDS = int(os.getenv("MAX_BUILD_SECONDS", "900"))
BUILD_CPU = os.getenv("BUILD_CPU", "2")
BUILD_MEMORY = os.getenv("BUILD_MEMORY", "2g")
BUILD_PIDS = os.getenv("BUILD_PIDS", "256")
MAX_LOG_BYTES = int(os.getenv("MAX_LOG_BYTES", str(2 * 1024 * 1024)))
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
APP_TITLE = os.getenv("APP_TITLE", "Py2APK")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))
MAX_CONCURRENT_BUILDS = int(os.getenv("MAX_CONCURRENT_BUILDS", "2"))
REGISTRATION_ENABLED = os.getenv("REGISTRATION_ENABLED", "1") == "1"

for directory in (DATA_DIR, UPLOAD_DIR, BUILD_DIR):
    directory.mkdir(parents=True, exist_ok=True)
