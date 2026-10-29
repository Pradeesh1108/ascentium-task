from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from app.config import CLAIM_TIMEOUT_SECONDS, MAX_ATTEMPTS, RETRY_BACKOFF_BASE
from app.database import get_connection, record_event
from app.models import EventType, MessageStatus

logger = logging.getLogger(__name__)


def calculate_backoff_delay(attempts: int) -> float:
    """Calculates exponential backoff delay in seconds for retry scheduling."""
    return float(RETRY_BACKOFF_BASE ** attempts)


def claim_next_job(lease_seconds: int = CLAIM_TIMEOUT_SECONDS) -> Optional[str]:
    """Atomically claims one eligible pending or stale processing job.

    Eligible jobs:
    1. status == 'pending' AND (next_retry_at IS NULL OR next_retry_at <= now)
    2. status == 'processing' AND claimed_at <= (now - lease_seconds) [Stale Recovery]
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    stale_cutoff = (now - timedelta(seconds=lease_seconds)).isoformat()

    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT message_id
            FROM messages
            WHERE (status = ? AND (next_retry_at IS NULL OR next_retry_at <= ?))
               OR (status = ? AND claimed_at <= ?)
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (
                MessageStatus.PENDING.value,
                now_iso,
                MessageStatus.PROCESSING.value,
                stale_cutoff,
            ),
        )
        row = cursor.fetchone()
        if not row:
            return None

        message_id = row["message_id"]

        # Attempt atomic claim
        update_cursor = conn.execute(
            """
            UPDATE messages
            SET
                status = ?,
                claimed_at = ?,
                updated_at = (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))

            WHERE message_id = ?
              AND (
                  (status = ? AND (next_retry_at IS NULL OR next_retry_at <= ?))
                  OR (status = ? AND claimed_at <= ?)
              )
            """,
            (
                MessageStatus.PROCESSING.value,
                now_iso,
                message_id,
                MessageStatus.PENDING.value,
                now_iso,
                MessageStatus.PROCESSING.value,
                stale_cutoff,
            ),
        )

        if update_cursor.rowcount > 0:
            record_event(
                conn,
                message_id,
                EventType.STAGE_STARTED.value,
                details=f"Job claimed for processing at {now_iso}",
            )
            return message_id

    return None


def handle_processing_failure(message_id: str, error_message: str) -> None:
    """Handles job failure: increments attempt count, schedules exponential retry or marks failed."""
    now = datetime.now(timezone.utc)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT attempts FROM messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()

        if not row:
            return

        current_attempts = row["attempts"] + 1

        if current_attempts >= MAX_ATTEMPTS:
            # Mark permanently failed
            conn.execute(
                """
                UPDATE messages
                SET
                    status = ?,
                    attempts = ?,
                    failure_reason = ?,
                    claimed_at = NULL,
                    next_retry_at = NULL,
                    updated_at = (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                WHERE message_id = ?
                """,
                (
                    MessageStatus.FAILED.value,
                    current_attempts,
                    error_message,
                    message_id,
                ),
            )
            record_event(
                conn,
                message_id,
                EventType.MESSAGE_FAILED.value,
                details=f"Permanently failed after {current_attempts} attempts: {error_message}",
            )
        else:
            # Schedule retry with exponential backoff
            delay = calculate_backoff_delay(current_attempts)
            next_retry_at = (now + timedelta(seconds=delay)).isoformat()

            conn.execute(
                """
                UPDATE messages
                SET
                    status = ?,
                    attempts = ?,
                    failure_reason = ?,
                    claimed_at = NULL,
                    next_retry_at = ?,
                    updated_at = (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                WHERE message_id = ?
                """,
                (
                    MessageStatus.PENDING.value,
                    current_attempts,
                    error_message,
                    next_retry_at,
                    message_id,
                ),
            )
            record_event(
                conn,
                message_id,
                EventType.RETRY_SCHEDULED.value,
                details=f"Retry attempt {current_attempts}/{MAX_ATTEMPTS} scheduled for {next_retry_at} (backoff: {delay}s)",
            )


def manual_retry_message(message_id: str) -> bool:
    """Manually re-queues a failed message, preserving existing checkpoints."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()

        if not row:
            return False

        if row["status"] != MessageStatus.FAILED.value:
            # Only failed messages are eligible for retry
            return False

        conn.execute(
            """
            UPDATE messages
            SET
                status = ?,
                attempts = 0,
                failure_reason = NULL,
                claimed_at = NULL,
                next_retry_at = NULL,
                updated_at = (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            WHERE message_id = ?
            """,
            (MessageStatus.PENDING.value, message_id),
        )
        record_event(
            conn,
            message_id,
            EventType.MANUAL_RETRY_REQUESTED.value,
            details="Manual retry requested; preserved existing checkpoints",
        )
        return True


def manual_retry_all_failed() -> int:
    """Manually re-queues all failed messages, preserving their checkpoints."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT message_id
            FROM messages
            WHERE status = ?
            """,
            (MessageStatus.FAILED.value,),
        )
        failed_ids = [row["message_id"] for row in cursor.fetchall()]

        if not failed_ids:
            return 0

        conn.execute(
            """
            UPDATE messages
            SET
                status = ?,
                attempts = 0,
                failure_reason = NULL,
                claimed_at = NULL,
                next_retry_at = NULL,
                updated_at = (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            WHERE status = ?
            """,
            (MessageStatus.PENDING.value, MessageStatus.FAILED.value),
        )


        for mid in failed_ids:
            record_event(
                conn,
                mid,
                EventType.MANUAL_RETRY_REQUESTED.value,
                details="Bulk manual retry requested; preserved existing checkpoints",
            )

        return len(failed_ids)

