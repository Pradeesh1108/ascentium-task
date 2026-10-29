import sqlite3
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database, get_connection
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test_api_results.db"
    monkeypatch.setattr("app.config.DATABASE_PATH", test_db)
    monkeypatch.setattr("app.database.DATABASE_PATH", test_db)
    initialize_database()

    with TestClient(app) as test_client:
        yield test_client


def test_list_messages_pagination_and_filter(client):
    # Ingest several test messages
    client.post("/messages", json={"message_id": "T-4001", "text": "First message"})
    client.post("/messages", json={"message_id": "T-4002", "text": "Second message"})
    client.post("/messages", json={"message_id": "T-4003", "text": ""})  # rejected

    # List all
    res = client.get("/messages?page=1&limit=2")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["total_pages"] == 2

    # Filter by rejected status
    res_rejected = client.get("/messages?status=rejected")
    assert res_rejected.status_code == 200
    rejected_data = res_rejected.json()
    assert rejected_data["total"] == 1
    assert rejected_data["items"][0]["message_id"] == "T-4003"


def test_get_message_detail_and_404(client):
    client.post("/messages", json={"message_id": "T-4004", "text": "Detail test"})

    # Found
    res = client.get("/messages/T-4004")
    assert res.status_code == 200
    data = res.json()
    assert data["message_id"] == "T-4004"
    assert data["text"] == "Detail test"
    assert "checkpoints" in data

    # 404 for unknown ID
    res_404 = client.get("/messages/NON_EXISTENT")
    assert res_404.status_code == 404


def test_manual_retry_endpoints(client):
    # Setup failed message directly in SQLite
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, text, status, attempts, failure_reason) VALUES (?, ?, ?, ?, ?)",
            ("T-4005", "Failed message", "failed", 3, "Network timeout"),
        )
        conn.execute(
            "INSERT INTO messages (message_id, text, status) VALUES (?, ?, ?)",
            ("T-4006", "Pending message", "pending"),
        )

    # Retry non-failed message should fail (400)
    res_invalid = client.post("/messages/T-4006/retry")
    assert res_invalid.status_code == 400

    # Retry failed message succeeds
    res_retry = client.post("/messages/T-4005/retry")
    assert res_retry.status_code == 200
    assert res_retry.json()["requeued_count"] == 1

    # Bulk retry failed messages
    res_bulk = client.post("/messages/retry-failed")
    assert res_bulk.status_code == 200


def test_stats_and_health_reporting(client):
    client.post("/messages", json={"message_id": "T-4007", "text": "Valid message"})
    client.post("/messages", json={"message_id": "T-4007", "text": "Duplicate delivery"})
    client.post("/messages", json={"message_id": "T-4008", "text": ""})  # rejected

    res = client.get("/stats")
    assert res.status_code == 200
    stats = res.json()
    assert stats["unique_messages"] == 2
    assert stats["rejected"] == 1
    assert stats["duplicates_ignored"] == 1

    # Health check
    res_health = client.get("/health")
    assert res_health.status_code == 200
    health_data = res_health.json()
    assert health_data["status"] == "healthy"
    assert health_data["services"]["database"] == "healthy"


def test_health_reports_degraded_on_db_error(client):
    with patch("app.routes.dashboard.check_database_health", return_value=False):
        res = client.get("/health")
        assert res.status_code == 200
        health_data = res.json()
        assert health_data["status"] == "degraded"
        assert health_data["services"]["database"] == "unavailable"


def test_export_contract_exact_seven_fields(client):
    # Setup test rows: completed, rejected, failed
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO messages (message_id, text, status, category, order_id, product, urgency, requested_action)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("T-5001", "Text 1", "completed", "Refund", "ORD-12345", "Pulse Earbuds", "High", "Refund payment"),
        )
        conn.execute(
            """
            INSERT INTO messages (message_id, text, status)
            VALUES (?, ?, ?)
            """,
            ("T-5002", "Text 2", "rejected"),
        )
        conn.execute(
            """
            INSERT INTO messages (message_id, text, status, attempts, failure_reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("T-5003", "Text 3", "failed", 3, "Timeout"),
        )

    res = client.get("/results/export")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 3

    expected_keys = {
        "message_id",
        "status",
        "category",
        "order_id",
        "product",
        "urgency",
        "requested_action",
    }

    for item in data:
        # EXACTLY seven required fields
        assert set(item.keys()) == expected_keys

    # Check specific row formatting
    completed_item = next(x for x in data if x["message_id"] == "T-5001")
    assert completed_item["status"] == "completed"
    assert completed_item["category"] == "Refund"
    assert completed_item["order_id"] == "ORD-12345"
    assert completed_item["product"] == "Pulse Earbuds"
    assert completed_item["urgency"] == "High"
    assert completed_item["requested_action"] == "Refund payment"

    rejected_item = next(x for x in data if x["message_id"] == "T-5002")
    assert rejected_item["status"] == "rejected"
    assert rejected_item["category"] is None
    assert rejected_item["order_id"] is None

    failed_item = next(x for x in data if x["message_id"] == "T-5003")
    assert failed_item["status"] == "failed"
    assert failed_item["category"] is None


def test_practice_dataset_ingestion_and_export_integrity(client):
    import json
    from app.config import PROJECT_DIR

    practice_path = PROJECT_DIR / "for_candidates" / "practice_messages.json"
    if not practice_path.exists():
        practice_path = PROJECT_DIR / "practice_messages.json"

    with open(practice_path) as f:
        messages = json.load(f)

    accepted_count = 0
    duplicate_count = 0
    rejected_count = 0

    for msg in messages:
        res = client.post("/messages", json=msg)
        assert res.status_code in (200, 202)
        body = res.json()
        if body["status"] == "accepted":
            accepted_count += 1
        elif body["status"] == "duplicate":
            duplicate_count += 1
        elif body["status"] == "rejected":
            rejected_count += 1

    # 25 total deliveries: 23 accepted + 1 duplicate (T-1007) + 1 rejected (T-1012)
    assert len(messages) == 25
    assert accepted_count == 23
    assert duplicate_count == 1
    assert rejected_count == 1

    # Verify stats
    stats_res = client.get("/stats")
    stats = stats_res.json()
    assert stats["unique_messages"] == 24
    assert stats["rejected"] == 1
    assert stats["duplicates_ignored"] == 1

    # Verify export contract
    export_res = client.get("/results/export")
    assert export_res.status_code == 200
    export_data = export_res.json()
    assert len(export_data) == 24

    # Ensure duplicate T-1007 is exported exactly once
    t1007_items = [x for x in export_data if x["message_id"] == "T-1007"]
    assert len(t1007_items) == 1

    # Ensure empty text T-1012 is rejected with nulls
    t1012_item = next(x for x in export_data if x["message_id"] == "T-1012")
    assert t1012_item["status"] == "rejected"
    assert t1012_item["category"] is None

