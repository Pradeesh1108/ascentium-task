import json
import math
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response

from app.config import PROJECT_DIR
from app.database import get_connection
from app.schemas import (
    CheckpointInfo,
    MessageDetailResponse,
    MessageIngestRequest,
    MessageIngestResponse,
    MessageListItem,
    PaginatedMessagesResponse,
    RetryResponse,
)
from app.services.ingestion import ingest_message
from app.services.retry import manual_retry_all_failed, manual_retry_message

router = APIRouter(tags=["messages"])


@router.get(
    "/practice-messages",
    summary="Get candidate practice messages dataset",
)
def get_practice_messages():
    practice_path = PROJECT_DIR / "for_candidates" / "practice_messages.json"
    if not practice_path.exists():
        practice_path = PROJECT_DIR / "practice_messages.json"
    if not practice_path.exists():
        raise HTTPException(status_code=404, detail="practice_messages.json not found")
    with open(practice_path, "r", encoding="utf-8") as f:
        return json.load(f)


@router.post(
    "/messages",

    response_model=MessageIngestResponse,
    summary="Ingest a customer message",
    status_code=202,
)
def create_message(payload: MessageIngestRequest, response: Response):
    status_code, result = ingest_message(payload)
    response.status_code = status_code
    return result


@router.get(
    "/messages",
    response_model=PaginatedMessagesResponse,
    summary="List paginated messages with optional status filter",
)
def list_messages(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status (pending, processing, completed, failed, rejected)"),
    category: Optional[str] = Query(None, description="Filter by category"),
    urgency: Optional[str] = Query(None, description="Filter by urgency"),
    q: Optional[str] = Query(None, description="Search ID or text"),
):
    offset = (page - 1) * limit
    where_clauses = []
    params = []

    if status:
        where_clauses.append("status = ?")
        params.append(status)
    if category:
        where_clauses.append("category = ?")
        params.append(category)
    if urgency:
        where_clauses.append("urgency = ?")
        params.append(urgency)
    if q and q.strip():
        where_clauses.append("(message_id LIKE ? OR text LIKE ?)")
        search_pattern = f"%{q.strip()}%"
        params.extend([search_pattern, search_pattern])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""


    with get_connection() as conn:
        count_cursor = conn.execute(f"SELECT COUNT(*) FROM messages {where_sql}", params)
        total = count_cursor.fetchone()[0]

        query = f"""
            SELECT
                message_id, text, status, category, order_id, product,
                urgency, requested_action, attempts, failure_reason,
                created_at, updated_at
            FROM messages
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(query, params + [limit, offset]).fetchall()

    items = [
        MessageListItem(
            message_id=row["message_id"],
            text=row["text"],
            status=row["status"],
            category=row["category"],
            order_id=row["order_id"],
            product=row["product"],
            urgency=row["urgency"],
            requested_action=row["requested_action"],
            attempts=row["attempts"],
            failure_reason=row["failure_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


    total_pages = math.ceil(total / limit) if total > 0 else 0

    return PaginatedMessagesResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@router.get(
    "/messages/{message_id}",
    response_model=MessageDetailResponse,
    summary="Get single message details and checkpoint state",
)
def get_message(message_id: str):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                message_id, text, status, category, order_id, product,
                urgency, requested_action, attempts, failure_reason,
                next_retry_at, claimed_at, created_at, updated_at
            FROM messages
            WHERE message_id = ?
            """,
            (message_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Message {message_id} not found")

        ckpt_rows = conn.execute(
            """
            SELECT stage, stage_status, result_json, stage_attempts, error, updated_at
            FROM processing_checkpoints
            WHERE message_id = ?
            ORDER BY stage ASC
            """,
            (message_id,),
        ).fetchall()

    checkpoints = []
    extraction_dict = None
    classification_dict = None

    for c in ckpt_rows:
        raw_json = c["result_json"]
        parsed = None
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except Exception:
                pass

        ckpt_obj = CheckpointInfo(
            stage=c["stage"],
            stage_status=c["stage_status"],
            result=parsed,
            stage_attempts=c["stage_attempts"],
            error=c["error"],
            updated_at=c["updated_at"],
        )
        checkpoints.append(ckpt_obj)

        if c["stage"] == "extraction":
            extraction_dict = {"status": c["stage_status"], "result": parsed}
        elif c["stage"] == "classification":
            classification_dict = {"status": c["stage_status"], "result": parsed}

    return MessageDetailResponse(
        message_id=row["message_id"],
        text=row["text"],
        status=row["status"],
        category=row["category"],
        order_id=row["order_id"],
        product=row["product"],
        urgency=row["urgency"],
        requested_action=row["requested_action"],
        attempts=row["attempts"],
        failure_reason=row["failure_reason"],
        next_retry_at=row["next_retry_at"],
        claimed_at=row["claimed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        checkpoints=checkpoints,
        extraction=extraction_dict,
        classification=classification_dict,
    )



@router.post(
    "/messages/{message_id}/retry",
    response_model=RetryResponse,
    summary="Retry a specific failed message",
)
def retry_message(message_id: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Message {message_id} not found")

    if row["status"] != "failed":
        raise HTTPException(
            status_code=400,
            detail=f"Message {message_id} is in status '{row['status']}', only 'failed' messages can be retried",
        )

    success = manual_retry_message(message_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to re-queue message for retry")

    return RetryResponse(
        requeued_count=1,
        details=f"Message {message_id} has been re-queued for processing",
    )


@router.post(
    "/messages/retry-failed",
    response_model=RetryResponse,
    summary="Retry all eligible failed messages",
)
def retry_all_failed():
    count = manual_retry_all_failed()
    return RetryResponse(
        requeued_count=count,
        details=f"Re-queued {count} failed message(s) for processing",
    )
