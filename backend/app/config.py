import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent

load_dotenv(BACKEND_DIR / ".env")

DATABASE_PATH = Path(
    os.getenv(
        "DATABASE_PATH",
        str(BACKEND_DIR / "data" / "tickets.db"),
    )
)

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
LLM_TIMEOUT_SECONDS = float(
    os.getenv("LLM_TIMEOUT_SECONDS", "30")
)
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "5000"))
CLAIM_TIMEOUT_SECONDS = int(os.getenv("CLAIM_TIMEOUT_SECONDS", "60"))
RETRY_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "2.0"))
WORKER_POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "1.0"))
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "5000"))
CLAIM_TIMEOUT_SECONDS = int(os.getenv("CLAIM_TIMEOUT_SECONDS", "60"))
RETRY_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "2.0"))
WORKER_POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "1.0"))
