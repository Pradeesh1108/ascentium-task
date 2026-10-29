from fastapi import APIRouter

from app.config import LLM_BASE_URL
from app.database import check_database_health, get_connection
from app.models import EventType, MessageStatus
from app.schemas import HealthResponse, StatsResponse

router = APIRouter(tags=["dashboard"])


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Get operational message and LLM processing statistics",
)
def get_stats():
    with get_connection() as conn:
        # Message counts by status
        counts_cursor = conn.execute(
            """
            SELECT
                COUNT(*) AS total_unique,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS in_progress,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS rejected
            FROM messages
            """,
            (
                MessageStatus.PROCESSING.value,
                MessageStatus.COMPLETED.value,
                MessageStatus.FAILED.value,
                MessageStatus.REJECTED.value,
            ),
        )
        msg_counts = counts_cursor.fetchone()

        # Duplicates ignored from events
        dup_cursor = conn.execute(
            "SELECT COUNT(*) FROM processing_events WHERE event_type = ?",
            (EventType.DUPLICATE_DELIVERY_IGNORED.value,),
        )
        duplicates_ignored = dup_cursor.fetchone()[0]

        # Total and repeated LLM calls from checkpoints/events
        llm_calls_cursor = conn.execute(
            "SELECT COUNT(*) FROM processing_events WHERE event_type = ?",
            (EventType.CHECKPOINT_SAVED.value,),
        )
        total_llm_calls = llm_calls_cursor.fetchone()[0]

        repeated_cursor = conn.execute(
            """
            SELECT COALESCE(SUM(stage_attempts - 1), 0)
            FROM processing_checkpoints
            WHERE stage_attempts > 1
            """
        )
        repeated_llm_calls = repeated_cursor.fetchone()[0]

    total_unique = msg_counts["total_unique"] or 0
    total_calls = total_llm_calls or 0

    return StatsResponse(
        unique_messages=total_unique,
        received=total_unique,
        in_progress=msg_counts["in_progress"] or 0,
        completed=msg_counts["completed"] or 0,
        failed=msg_counts["failed"] or 0,
        rejected=msg_counts["rejected"] or 0,
        duplicates_ignored=duplicates_ignored or 0,
        total_llm_calls=total_calls,
        llm_calls=total_calls,
        repeated_llm_calls=repeated_llm_calls or 0,
    )


@router.get(
    "/health",
    summary="System and service health check",
)
def get_health():
    db_healthy = check_database_health()
    llm_status = "healthy" if bool(LLM_BASE_URL) else "unavailable"

    is_overall_healthy = db_healthy
    return {
        "status": "healthy" if is_overall_healthy else "degraded",
        "services": {
            "database": "healthy" if db_healthy else "unavailable",
            "llm": llm_status,
        },
        "storage": {
            "status": "healthy" if db_healthy else "unavailable",
        },
        "database": {
            "status": "healthy" if db_healthy else "unavailable",
        },
        "llm": {
            "status": llm_status,
        },
    }

