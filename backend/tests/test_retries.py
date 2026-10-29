from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

from app.database import initialize_database, get_connection
from app.models import MessageStatus, StageName, StageStatus
from app.services.llm_client import LLMClient, LLMClientError
from app.services.processor import process_claimed_job
from app.services.retry import (
    claim_next_job,
    manual_retry_all_failed,
    manual_retry_message,
)


@pytest.fixture
def setup_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_retries.db"
    monkeypatch.setattr("app.config.DATABASE_PATH", test_db)
    monkeypatch.setattr("app.database.DATABASE_PATH", test_db)
    initialize_database()
    return test_db


def test_three_failed_attempts_leads_to_failed_status(setup_db):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, text, status, attempts) VALUES (?, ?, ?, ?)",
            ("T-3001", "Failing message", "pending", 0),
        )

    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.chat_completion.side_effect = LLMClientError("LLM timed out", is_transient=True)

    # Attempt 1: should be scheduled for retry (pending, attempts=1)
    job_id = claim_next_job()
    assert job_id == "T-3001"
    success = process_claimed_job("T-3001", client=mock_llm)
    assert success is False

    with get_connection() as conn:
        row = conn.execute("SELECT status, attempts, next_retry_at, failure_reason FROM messages WHERE message_id = 'T-3001'").fetchone()
        assert row["status"] == MessageStatus.PENDING.value
        assert row["attempts"] == 1
        assert row["next_retry_at"] is not None
        assert "LLM timed out" in row["failure_reason"]

        # Advance next_retry_at to past so it can be claimed again
        conn.execute("UPDATE messages SET next_retry_at = NULL WHERE message_id = 'T-3001'")

    # Attempt 2:
    job_id = claim_next_job()
    assert job_id == "T-3001"
    process_claimed_job("T-3001", client=mock_llm)

    with get_connection() as conn:
        row = conn.execute("SELECT status, attempts FROM messages WHERE message_id = 'T-3001'").fetchone()
        assert row["status"] == MessageStatus.PENDING.value
        assert row["attempts"] == 2
        conn.execute("UPDATE messages SET next_retry_at = NULL WHERE message_id = 'T-3001'")

    # Attempt 3: should transition to FAILED
    job_id = claim_next_job()
    assert job_id == "T-3001"
    process_claimed_job("T-3001", client=mock_llm)

    with get_connection() as conn:
        row = conn.execute("SELECT status, attempts, failure_reason FROM messages WHERE message_id = 'T-3001'").fetchone()
        assert row["status"] == MessageStatus.FAILED.value
        assert row["attempts"] == 3
        assert "LLM timed out" in row["failure_reason"]


def test_other_messages_continue_while_one_is_waiting_for_retry(setup_db):
    future_time = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    with get_connection() as conn:
        # Message 1 is backed off into future
        conn.execute(
            "INSERT INTO messages (message_id, text, status, next_retry_at, attempts) VALUES (?, ?, ?, ?, ?)",
            ("T-3002", "Message backed off", "pending", future_time, 1),
        )
        # Message 2 is immediately eligible
        conn.execute(
            "INSERT INTO messages (message_id, text, status) VALUES (?, ?, ?)",
            ("T-3003", "Eligible message", "pending"),
        )

    # Next claimed job MUST be T-3003
    job = claim_next_job()
    assert job == "T-3003"


def test_stale_processing_job_is_recovered(setup_db):
    old_claim_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, text, status, claimed_at) VALUES (?, ?, ?, ?)",
            ("T-3004", "Worker died midway", "processing", old_claim_time),
        )

    # With lease_seconds=60, a 5-minute old job is stale and recovered!
    claimed = claim_next_job(lease_seconds=60)
    assert claimed == "T-3004"


def test_manual_retry_preserves_checkpoints(setup_db):
    with get_connection() as conn:
        # Setup failed message with an existing extraction checkpoint
        conn.execute(
            "INSERT INTO messages (message_id, text, status, attempts, failure_reason) VALUES (?, ?, ?, ?, ?)",
            ("T-3005", "Failed message", "failed", 3, "Previous error"),
        )
        conn.execute(
            "INSERT INTO processing_checkpoints (message_id, stage, stage_status, result_json) VALUES (?, ?, ?, ?)",
            ("T-3005", StageName.EXTRACTION.value, StageStatus.COMPLETED.value, '{"order_id": "ORD-12345", "product": "Pulse Earbuds"}'),
        )

    success = manual_retry_message("T-3005")
    assert success is True

    with get_connection() as conn:
        msg = conn.execute("SELECT status, attempts, failure_reason FROM messages WHERE message_id = 'T-3005'").fetchone()
        assert msg["status"] == MessageStatus.PENDING.value
        assert msg["attempts"] == 0
        assert msg["failure_reason"] is None

        # Checkpoint MUST be preserved!
        ckpt = conn.execute("SELECT stage_status, result_json FROM processing_checkpoints WHERE message_id = 'T-3005' AND stage = 'extraction'").fetchone()
        assert ckpt is not None
        assert ckpt["stage_status"] == StageStatus.COMPLETED.value


def test_bulk_manual_retry(setup_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO messages (message_id, text, status, attempts) VALUES (?, ?, ?, ?)", ("T-3006", "Fail 1", "failed", 3))
        conn.execute("INSERT INTO messages (message_id, text, status, attempts) VALUES (?, ?, ?, ?)", ("T-3007", "Fail 2", "failed", 3))
        conn.execute("INSERT INTO messages (message_id, text, status) VALUES (?, ?, ?)", ("T-3008", "Pending", "pending"))

    requeued = manual_retry_all_failed()
    assert requeued == 2

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'pending'").fetchone()[0]
        assert count == 3

