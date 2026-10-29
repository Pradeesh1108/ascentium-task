import json
import logging
from typing import Any, Dict, Optional, Tuple

from app.database import get_connection, record_event
from app.models import EventType, MessageStatus, StageName, StageStatus
from app.schemas import ClassificationResult, ExtractionResult
from app.services.classification import run_classification_stage
from app.services.extraction import run_extraction_stage
from app.services.llm_client import LLMClient
from app.services.retry import handle_processing_failure

logger = logging.getLogger(__name__)


def process_claimed_job(
    message_id: str,
    client: Optional[LLMClient] = None,
) -> bool:
    """Processes a claimed job, handling failures via retry backoff or marking as failed."""
    try:
        return process_message_stages(message_id, client=client)
    except Exception as exc:
        logger.warning(f"Failure during processing of message {message_id}: {exc}")
        handle_processing_failure(message_id, str(exc))
        return False



def get_checkpoints_for_message(message_id: str) -> Dict[str, Tuple[str, Optional[Dict[str, Any]]]]:
    """Retrieves all persisted checkpoints for a given message ID."""
    checkpoints: Dict[str, Tuple[str, Optional[Dict[str, Any]]]] = {}
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT stage, stage_status, result_json
            FROM processing_checkpoints
            WHERE message_id = ?
            """,
            (message_id,),
        )
        for row in cursor.fetchall():
            stage = row["stage"]
            status = row["stage_status"]
            raw_json = row["result_json"]
            parsed_data = None
            if raw_json:
                try:
                    parsed_data = json.loads(raw_json)
                except Exception:
                    pass
            checkpoints[stage] = (status, parsed_data)
    return checkpoints


def process_message_stages(
    message_id: str,
    client: Optional[LLMClient] = None,
) -> bool:
    """Executes the two-stage pipeline with durable checkpoint resumption and atomic finalization.

    Resume semantics:
    - If extraction is missing: execute Call 1.
    - If extraction is valid and classification is missing: skip Call 1, execute Call 2.
    - If both checkpoints are valid: skip both LLM calls and execute finalization.
    """
    # 1. Fetch message record
    with get_connection() as conn:
        row = conn.execute(
            "SELECT text, status FROM messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()

    if not row:
        logger.error(f"Cannot process unknown message_id: {message_id}")
        return False

    if row["status"] in (MessageStatus.COMPLETED.value, MessageStatus.REJECTED.value):
        logger.info(f"Message {message_id} is already {row['status']}; skipping processing.")
        return True

    text = row["text"]
    checkpoints = get_checkpoints_for_message(message_id)

    # 2. Stage A: Extraction
    extraction_entry = checkpoints.get(StageName.EXTRACTION.value)
    if extraction_entry and extraction_entry[0] == StageStatus.COMPLETED.value and extraction_entry[1]:
        logger.info(f"Skipping Stage A for {message_id}; valid extraction checkpoint found.")
        extraction_result = ExtractionResult(**extraction_entry[1])
    else:
        with get_connection() as conn:
            record_event(
                conn,
                message_id,
                EventType.STAGE_STARTED.value,
                stage=StageName.EXTRACTION.value,
                details="Starting Stage A extraction",
            )

        extraction_result = run_extraction_stage(message_id, text, client=client)

        with get_connection() as conn:
            record_event(
                conn,
                message_id,
                EventType.STAGE_COMPLETED.value,
                stage=StageName.EXTRACTION.value,
                details=f"Completed Stage A: order_id={extraction_result.order_id}, product={extraction_result.product}",
            )

    # 3. Stage B: Classification
    classification_entry = checkpoints.get(StageName.CLASSIFICATION.value)
    if classification_entry and classification_entry[0] == StageStatus.COMPLETED.value and classification_entry[1]:
        logger.info(f"Skipping Stage B for {message_id}; valid classification checkpoint found.")
        classification_result = ClassificationResult(**classification_entry[1])
    else:
        with get_connection() as conn:
            record_event(
                conn,
                message_id,
                EventType.STAGE_STARTED.value,
                stage=StageName.CLASSIFICATION.value,
                details="Starting Stage B classification with extraction context",
            )

        classification_result = run_classification_stage(
            message_id,
            text,
            extraction_result,
            client=client,
        )

        with get_connection() as conn:
            record_event(
                conn,
                message_id,
                EventType.STAGE_COMPLETED.value,
                stage=StageName.CLASSIFICATION.value,
                details=f"Completed Stage B: category={classification_result.category}, urgency={classification_result.urgency}",
            )

    # 4. Atomic Finalization
    finalize_message(message_id, extraction_result, classification_result)
    return True


def finalize_message(
    message_id: str,
    extraction: ExtractionResult,
    classification: ClassificationResult,
) -> None:
    """Atomically commits all final fields and marks message status as 'completed' in a single transaction."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE messages
            SET
                status = ?,
                category = ?,
                order_id = ?,
                product = ?,
                urgency = ?,
                requested_action = ?,
                failure_reason = NULL,
                next_retry_at = NULL,
                updated_at = (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            WHERE message_id = ?

            """,
            (
                MessageStatus.COMPLETED.value,
                classification.category,
                extraction.order_id,
                extraction.product,
                classification.urgency,
                classification.requested_action,
                message_id,
            ),
        )
        record_event(
            conn,
            message_id,
            EventType.MESSAGE_COMPLETED.value,
            details=f"Message finalized successfully: category={classification.category}, urgency={classification.urgency}",
        )

