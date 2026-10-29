import logging
import signal
import sys
import time

from app.config import WORKER_POLL_INTERVAL
from app.database import initialize_database
from app.services.llm_client import close_llm_client
from app.services.processor import process_claimed_job
from app.services.retry import claim_next_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ticket_worker")

_is_running = True


def handle_shutdown_signal(signum, frame):
    global _is_running
    logger.info(f"Received termination signal ({signum}). Initiating graceful shutdown...")
    _is_running = False


def run_worker_loop(poll_interval: float = WORKER_POLL_INTERVAL) -> None:
    global _is_running
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    logger.info("Initializing database schema...")
    initialize_database()
    logger.info(f"Worker started. Polling interval: {poll_interval}s. Waiting for jobs...")

    try:
        while _is_running:
            job_id = claim_next_job()
            if job_id:
                logger.info(f"Claimed job: {job_id}")
                success = process_claimed_job(job_id)
                logger.info(f"Job {job_id} processing result: {'SUCCESS' if success else 'FAILED/RETRY'}")
            else:
                # Idle sleep when no jobs are eligible
                time.sleep(poll_interval)
    finally:
        logger.info("Cleaning up resources...")
        close_llm_client()
        logger.info("Worker stopped cleanly.")


def main():
    run_worker_loop()


if __name__ == "__main__":
    main()

