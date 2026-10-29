import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from app.database import get_connection, record_event
from app.models import EventType, OFFICIAL_PRODUCTS, StageName, StageStatus
from app.schemas import ExtractionResult
from app.services.llm_client import LLMClient, get_llm_client

logger = logging.getLogger(__name__)

ORDER_ID_REGEX = re.compile(r"^ORD-\d{5}$")

EXTRACTION_SYSTEM_PROMPT = """You are an accurate data extraction assistant for customer support messages.
Your task is to extract two specific fields from the customer message:
1. "order_id": An order ID matching the format ORD- followed by exactly 5 digits (e.g. ORD-48213).
   - Prefer an explicitly corrected order ID if the customer corrected an earlier one.
   - If multiple order IDs are mentioned, choose the one relevant to the primary issue.
   - Do NOT confuse invoice numbers (e.g. INV-...), tracking numbers, or transaction IDs with order IDs.
   - If no valid order ID is present, return null.

2. "product": The product mentioned in the message, normalized to the official catalogue name if identifiable.
   The official catalogue names are:
   - Aura Smart Watch
   - Pulse Earbuds
   - Nimbus Router X2
   - Orbit Smart Speaker
   - CloudSync Pro
   - Zen Air Purifier
   - Vista 4K Monitor
   - Glide Laptop Stand
   - If the customer received the wrong item, identify the product that was ordered.
   - If not recognizable or unmentioned, return null.
   - Never invent product names.

Respond ONLY with a valid JSON object with keys "order_id" and "product". Example:
{"order_id": "ORD-48213", "product": "Aura Smart Watch"}
"""


def run_extraction_stage(
    message_id: str,
    text: str,
    client: Optional[LLMClient] = None,
) -> ExtractionResult:
    """Executes Stage A extraction, validates fields in Python, and persists durable checkpoint."""
    llm = client or get_llm_client()

    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": f"Customer message:\n{text}"},
    ]

    raw_result = llm.chat_completion(messages=messages, temperature=0.0)
    validated = validate_and_normalize_extraction(raw_result)

    # Persist durable checkpoint
    save_extraction_checkpoint(message_id, validated)

    return validated


def validate_and_normalize_extraction(raw_data: Dict[str, Any]) -> ExtractionResult:
    """Performs rigorous Python-side validation and normalization of extraction output."""
    raw_order_id = raw_data.get("order_id")
    order_id: Optional[str] = None
    if isinstance(raw_order_id, str):
        cleaned_order_id = raw_order_id.strip()
        if ORDER_ID_REGEX.match(cleaned_order_id):
            order_id = cleaned_order_id
        else:
            # Fallback: search for pattern ORD-\d{5} inside string
            match = re.search(r"ORD-\d{5}", cleaned_order_id)
            if match:
                order_id = match.group(0)

    raw_product = raw_data.get("product")
    product: Optional[str] = None
    if isinstance(raw_product, str):
        cleaned_product = raw_product.strip().lower()
        for official in OFFICIAL_PRODUCTS:
            if official.lower() == cleaned_product or official.lower() in cleaned_product:
                product = official
                break

    return ExtractionResult(order_id=order_id, product=product)


def save_extraction_checkpoint(message_id: str, result: ExtractionResult) -> None:
    result_json = json.dumps(result.model_dump())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO processing_checkpoints (
                message_id, stage, stage_status, result_json, stage_attempts, updated_at
            )
            VALUES (?, ?, ?, ?, 1, (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))
            ON CONFLICT(message_id, stage) DO UPDATE SET
                stage_status = excluded.stage_status,
                result_json = excluded.result_json,
                stage_attempts = stage_attempts + 1,
                error = NULL,
                updated_at = (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))

            """,
            (
                message_id,
                StageName.EXTRACTION.value,
                StageStatus.COMPLETED.value,
                result_json,
            ),
        )
        record_event(
            conn,
            message_id,
            EventType.CHECKPOINT_SAVED.value,
            stage=StageName.EXTRACTION.value,
            details=f"Extraction checkpoint saved: order_id={result.order_id}, product={result.product}",
        )

