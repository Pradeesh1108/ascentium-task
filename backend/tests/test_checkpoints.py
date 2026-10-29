import json
import sqlite3
from unittest.mock import MagicMock
import pytest

from app.database import initialize_database, get_connection
from app.models import MessageStatus, StageName, StageStatus
from app.schemas import ExtractionResult, ClassificationResult
from app.services.llm_client import LLMClient
from app.services.processor import process_message_stages, get_checkpoints_for_message


@pytest.fixture
def setup_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_checkpoints.db"
    monkeypatch.setattr("app.config.DATABASE_PATH", test_db)
    monkeypatch.setattr("app.database.DATABASE_PATH", test_db)
    initialize_database()
    return test_db


def test_pipeline_checkpoints_persisted(setup_db):
    # Insert test message
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, text, status) VALUES (?, ?, ?)",
            ("T-2001", "Order ORD-12345 for Pulse Earbuds was charged twice.", "pending"),
        )

    mock_llm = MagicMock(spec=LLMClient)
    # Stage A: Classification, Stage B: Extraction
    mock_llm.chat_completion.side_effect = [
        {"category": "Refund", "urgency": "High", "requested_action": "Refund duplicate charge"},
        {"order_id": "ORD-12345", "product": "Pulse Earbuds"},
    ]

    success = process_message_stages("T-2001", client=mock_llm)
    assert success is True
    assert mock_llm.chat_completion.call_count == 2

    # Verify classification checkpoint in DB
    checkpoints = get_checkpoints_for_message("T-2001")
    assert StageName.CLASSIFICATION.value in checkpoints
    status, data = checkpoints[StageName.CLASSIFICATION.value]
    assert status == StageStatus.COMPLETED.value
    assert data["category"] == "Refund"
    assert data["urgency"] == "High"

    # Verify extraction checkpoint in DB
    assert StageName.EXTRACTION.value in checkpoints
    status, data = checkpoints[StageName.EXTRACTION.value]
    assert status == StageStatus.COMPLETED.value
    assert data["order_id"] == "ORD-12345"
    assert data["product"] == "Pulse Earbuds"

    # Verify message completed
    with get_connection() as conn:
        row = conn.execute("SELECT status, category, order_id, product, urgency FROM messages WHERE message_id = 'T-2001'").fetchone()
        assert row["status"] == MessageStatus.COMPLETED.value
        assert row["category"] == "Refund"
        assert row["order_id"] == "ORD-12345"
        assert row["product"] == "Pulse Earbuds"
        assert row["urgency"] == "High"


def test_other_category_skips_extraction(setup_db):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, text, status) VALUES (?, ?, ?)",
            ("T-2002", "Love your products! Great job team.", "pending"),
        )

    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.chat_completion.return_value = {
        "category": "Other",
        "urgency": "Low",
        "requested_action": "Log customer compliment",
    }

    success = process_message_stages("T-2002", client=mock_llm)
    assert success is True
    # Exactly ONE call made: extraction was completely skipped!
    assert mock_llm.chat_completion.call_count == 1

    checkpoints = get_checkpoints_for_message("T-2002")
    assert StageName.CLASSIFICATION.value in checkpoints
    assert StageName.EXTRACTION.value in checkpoints
    assert checkpoints[StageName.EXTRACTION.value][1]["order_id"] is None
    assert checkpoints[StageName.EXTRACTION.value][1]["product"] is None

    with get_connection() as conn:
        row = conn.execute("SELECT status, category, order_id, product FROM messages WHERE message_id = 'T-2002'").fetchone()
        assert row["status"] == MessageStatus.COMPLETED.value
        assert row["category"] == "Other"
        assert row["order_id"] is None
        assert row["product"] is None


def test_extraction_failure_does_not_repeat_classification(setup_db):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, text, status) VALUES (?, ?, ?)",
            ("T-2003", "Order ORD-99999 is broken", "pending"),
        )

    mock_llm = MagicMock(spec=LLMClient)
    # Stage A (Classification) succeeds, Stage B (Extraction) fails
    mock_llm.chat_completion.side_effect = [
        {"category": "Technical issue", "urgency": "Medium", "requested_action": "Fix broken product"},
        RuntimeError("LLM extraction failed!"),
    ]

    with pytest.raises(RuntimeError):
        process_message_stages("T-2003", client=mock_llm)

    # Verify Stage A (Classification) is saved
    checkpoints = get_checkpoints_for_message("T-2003")
    assert StageName.CLASSIFICATION.value in checkpoints
    assert checkpoints[StageName.CLASSIFICATION.value][0] == StageStatus.COMPLETED.value
    assert StageName.EXTRACTION.value not in checkpoints

    # Now resume with a new LLM call where Stage B succeeds
    mock_llm_resume = MagicMock(spec=LLMClient)
    mock_llm_resume.chat_completion.return_value = {
        "order_id": "ORD-99999",
        "product": None,
    }

    process_message_stages("T-2003", client=mock_llm_resume)

    # Crucial check: mock_llm_resume should be called only ONCE (for Stage B Extraction), Stage A was skipped!
    assert mock_llm_resume.chat_completion.call_count == 1


def test_both_saved_checkpoints_allow_finalization_without_llm_call(setup_db):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, text, status) VALUES (?, ?, ?)",
            ("T-2004", "I want a refund for ORD-11111", "pending"),
        )
        # Pre-populate both checkpoints
        conn.execute(
            "INSERT INTO processing_checkpoints (message_id, stage, stage_status, result_json) VALUES (?, ?, ?, ?)",
            ("T-2004", StageName.EXTRACTION.value, StageStatus.COMPLETED.value, json.dumps({"order_id": "ORD-11111", "product": None})),
        )
        conn.execute(
            "INSERT INTO processing_checkpoints (message_id, stage, stage_status, result_json) VALUES (?, ?, ?, ?)",
            ("T-2004", StageName.CLASSIFICATION.value, StageStatus.COMPLETED.value, json.dumps({"category": "Refund", "urgency": "High", "requested_action": "Process refund"})),
        )

    mock_llm = MagicMock(spec=LLMClient)
    success = process_message_stages("T-2004", client=mock_llm)

    assert success is True
    # Zero LLM calls should have occurred!
    assert mock_llm.chat_completion.call_count == 0

    with get_connection() as conn:
        row = conn.execute("SELECT status, category, order_id FROM messages WHERE message_id = 'T-2004'").fetchone()
        assert row["status"] == MessageStatus.COMPLETED.value
        assert row["category"] == "Refund"
        assert row["order_id"] == "ORD-11111"

