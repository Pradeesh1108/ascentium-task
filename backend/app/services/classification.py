import json
import logging
from typing import Any, Dict, Optional

from app.database import get_connection, record_event
from app.models import (
    EventType,
    PERMITTED_CATEGORIES,
    PERMITTED_URGENCIES,
    StageName,
    StageStatus,
)
from app.schemas import ClassificationResult, ExtractionResult
from app.services.llm_client import LLMClient, get_llm_client

logger = logging.getLogger(__name__)

CLASSIFICATION_SYSTEM_PROMPT = """You are an accurate customer ticket classification assistant.
Your task is to determine category, urgency, and requested_action based strictly on the business rules below.

Permitted Categories (choose EXACTLY ONE):
1. "Refund": Customer requests money back, a reversed charge, store credit, or price difference.
   - If a charge complaint explicitly asks for money back, use Refund.
   - A pending refund enquiry remains a Refund issue.
2. "Billing": Questions about charges, invoices, payments, subscriptions, prices, fees, or plans when NOT asking for money back.
3. "Technical issue": A device, product, or application is malfunctioning or not working as expected.
4. "Account access": Login problems, passwords, OTPs, verification codes, account locks, security concerns.
5. "Other": Delivery enquiries, general product questions, feature requests, compliments, feedback, unrelated messages.
If multiple issues appear, choose the category representing the PRIMARY requested action.

Permitted Urgency Levels (choose EXACTLY ONE: "Low", "Medium", "High"):
- "High":
  * Customer cannot use product or account.
  * Money was wrongly taken or a duplicate charge has occurred.
  * Security concern or unauthorized account access.
- "Medium":
  * Problem affects customer, but they can still manage.
  * Billing questions and pending refunds.
  * Meaningful service issues not meeting High criteria.
- "Low":
  * General enquiries, compliments, feature suggestions, and feedback.
Base urgency strictly on business facts, NOT on exclamation marks, ALL CAPS, or angry language.

"requested_action": A short, specific action the support team should take (e.g. "Refund duplicate payment", "Troubleshoot Wi-Fi disconnection", "Unlock customer account"). Do NOT invent promises or long explanations.

Respond ONLY with valid JSON in this exact structure:
{"category": "...", "urgency": "...", "requested_action": "..."}
"""


def run_classification_stage(
    message_id: str,
    text: str,
    extraction: ExtractionResult,
    client: Optional[LLMClient] = None,
) -> ClassificationResult:
    """Executes Stage B classification with extraction context, validates fields, and saves checkpoint."""
    llm = client or get_llm_client()

    context_str = f"Extracted Context:\n- Order ID: {extraction.order_id}\n- Product: {extraction.product}\n\nCustomer Message:\n{text}"

    messages = [
        {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
        {"role": "user", "content": context_str},
    ]

    raw_result = llm.chat_completion(messages=messages, temperature=0.0)
    validated = validate_and_normalize_classification(raw_result)

    # Persist durable checkpoint
    save_classification_checkpoint(message_id, validated)

    return validated


def validate_and_normalize_classification(raw_data: Dict[str, Any]) -> ClassificationResult:
    """Validates and normalizes category, urgency, and requested_action in Python."""
    raw_category = raw_data.get("category")
    category: Optional[str] = None
    if isinstance(raw_category, str):
        cleaned_cat = raw_category.strip().title()
        for permitted in PERMITTED_CATEGORIES:
            if permitted.lower() == raw_category.strip().lower():
                category = permitted
                break
    if not category:
        category = "Other"

    raw_urgency = raw_data.get("urgency")
    urgency: Optional[str] = None
    if isinstance(raw_urgency, str):
        cleaned_urg = raw_urgency.strip().capitalize()
        for permitted in PERMITTED_URGENCIES:
            if permitted.lower() == raw_urgency.strip().lower():
                urgency = permitted
                break
    if not urgency:
        urgency = "Medium"

    raw_action = raw_data.get("requested_action")
    requested_action: Optional[str] = None
    if isinstance(raw_action, str) and raw_action.strip():
        requested_action = raw_action.strip()

    return ClassificationResult(
        category=category,  # type: ignore
        urgency=urgency,    # type: ignore
        requested_action=requested_action,
    )


def save_classification_checkpoint(message_id: str, result: ClassificationResult) -> None:
    result_json = json.dumps(result.model_dump())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO processing_checkpoints (
                message_id, stage, stage_status, result_json, stage_attempts, updated_at
            )
            VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(message_id, stage) DO UPDATE SET
                stage_status = excluded.stage_status,
                result_json = excluded.result_json,
                stage_attempts = stage_attempts + 1,
                error = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                message_id,
                StageName.CLASSIFICATION.value,
                StageStatus.COMPLETED.value,
                result_json,
            ),
        )
        record_event(
            conn,
            message_id,
            EventType.CHECKPOINT_SAVED.value,
            stage=StageName.CLASSIFICATION.value,
            details=f"Classification checkpoint saved: category={result.category}, urgency={result.urgency}",
        )

