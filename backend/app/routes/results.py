from typing import List

from fastapi import APIRouter

from app.database import get_connection
from app.models import MessageStatus
from app.schemas import ExportResultItem

router = APIRouter(tags=["results"])


@router.get(
    "/results/export",
    response_model=List[ExportResultItem],
    summary="Export final message intelligence results with exact 7-field contract",
)
def export_results():
    """Generates the official final JSON export from persisted SQLite records.

    Contract guarantees:
    - Exactly one entry per unique message_id.
    - Exactly seven fields per item: message_id, status, category, order_id, product, urgency, requested_action.
    - Status values restricted to 'completed', 'failed', or 'rejected'.
    - Unfinished records ('pending'/'processing') default to 'failed' with null fields.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT
                message_id,
                status,
                category,
                order_id,
                product,
                urgency,
                requested_action
            FROM messages
            ORDER BY message_id ASC
            """
        )
        rows = cursor.fetchall()

    export_list: List[ExportResultItem] = []
    for row in rows:
        raw_status = row["status"]
        if raw_status == MessageStatus.COMPLETED.value:
            status = "completed"
            category = row["category"]
            order_id = row["order_id"]
            product = row["product"]
            urgency = row["urgency"]
            requested_action = row["requested_action"]
        elif raw_status == MessageStatus.REJECTED.value:
            status = "rejected"
            category = None
            order_id = None
            product = None
            urgency = None
            requested_action = None
        else:
            # 'failed', 'pending', or 'processing'
            status = "failed"
            category = None
            order_id = None
            product = None
            urgency = None
            requested_action = None

        export_list.append(
            ExportResultItem(
                message_id=row["message_id"],
                status=status,
                category=category,
                order_id=order_id,
                product=product,
                urgency=urgency,
                requested_action=requested_action,
            )
        )

    return export_list

