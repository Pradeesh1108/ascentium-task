import sqlite3
from typing import Tuple

from fastapi import HTTPException

from app.config import MAX_MESSAGE_LENGTH
from app.database import get_connection, record_event
from app.models import EventType, MessageStatus
from app.schemas import MessageIngestRequest, MessageIngestResponse


def ingest_message(payload: MessageIngestRequest) -> Tuple[int, MessageIngestResponse]:
    # Validate message identifier
    message_id = payload.message_id.strip() if payload.message_id else ""
    if not message_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid message_id: must not be empty or whitespace only",
        )

    # Business rule 1: Empty or whitespace-only messages are rejected
    if not payload.text or not payload.text.strip():
        rejection_reason = "Empty or whitespace-only message"
        try:
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO messages (message_id, text, status, failure_reason, attempts)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (message_id, payload.text, MessageStatus.REJECTED.value, rejection_reason, 0),
                )
                record_event(
                    conn,
                    message_id,
                    EventType.MESSAGE_REJECTED.value,
                    details=rejection_reason,
                )
            return 200, MessageIngestResponse(
                message_id=message_id,
                status="rejected",
                details=rejection_reason,
            )
        except sqlite3.IntegrityError:
            _record_duplicate_delivery(message_id)
            return 200, MessageIngestResponse(
                message_id=message_id,
                status="duplicate",
                details="Duplicate message_id received; previous record preserved",
            )
        except sqlite3.Error as e:
            raise HTTPException(
                status_code=503,
                detail=f"Database unavailable: unable to persist rejection record ({e})",
            )

    # Business rule 2: Oversized messages are rejected safely
    if len(payload.text) > MAX_MESSAGE_LENGTH:
        rejection_reason = (
            f"Message exceeds maximum allowed length of {MAX_MESSAGE_LENGTH} characters "
            f"(received {len(payload.text)})"
        )
        try:
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO messages (message_id, text, status, failure_reason, attempts)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (message_id, payload.text, MessageStatus.REJECTED.value, rejection_reason, 0),
                )
                record_event(
                    conn,
                    message_id,
                    EventType.MESSAGE_REJECTED.value,
                    details=rejection_reason,
                )
            return 200, MessageIngestResponse(
                message_id=message_id,
                status="rejected",
                details=rejection_reason,
            )
        except sqlite3.IntegrityError:
            _record_duplicate_delivery(message_id)
            return 200, MessageIngestResponse(
                message_id=message_id,
                status="duplicate",
                details="Duplicate message_id received; previous record preserved",
            )
        except sqlite3.Error as e:
            raise HTTPException(
                status_code=503,
                detail=f"Database unavailable: unable to persist rejection record ({e})",
            )

    # Valid message: persist with 'pending' status
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO messages (message_id, text, status, attempts)
                VALUES (?, ?, ?, ?)
                """,
                (message_id, payload.text, MessageStatus.PENDING.value, 0),
            )
            record_event(
                conn,
                message_id,
                EventType.MESSAGE_ACCEPTED.value,
                details="Message accepted for processing",
            )
        return 202, MessageIngestResponse(
            message_id=message_id,
            status="accepted",
            details="Message accepted for processing",
        )
    except sqlite3.IntegrityError:
        _record_duplicate_delivery(message_id)
        return 200, MessageIngestResponse(
            message_id=message_id,
            status="duplicate",
            details="Duplicate message_id received; previous record preserved",
        )
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: unable to persist message ({e})",
        )


def _record_duplicate_delivery(message_id: str) -> None:
    try:
        with get_connection() as conn:
            record_event(
                conn,
                message_id,
                EventType.DUPLICATE_DELIVERY_IGNORED.value,
                details="Duplicate message delivery ignored",
            )
    except sqlite3.Error:
        # Best effort logging for duplicate events if DB allows
        pass

