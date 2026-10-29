import sqlite3
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test_tickets.db"
    monkeypatch.setattr("app.config.DATABASE_PATH", test_db)
    monkeypatch.setattr("app.database.DATABASE_PATH", test_db)
    initialize_database()

    with TestClient(app) as test_client:
        yield test_client


def test_ingest_valid_message(client, tmp_path):
    response = client.post(
        "/messages",
        json={"message_id": "T-1001", "text": "My router stopped working."},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["message_id"] == "T-1001"
    assert data["status"] == "accepted"

    # Verify directly in SQLite
    test_db = tmp_path / "test_tickets.db"
    conn = sqlite3.connect(test_db)
    row = conn.execute("SELECT status, text FROM messages WHERE message_id = 'T-1001'").fetchone()
    assert row is not None
    assert row[0] == "pending"
    assert row[1] == "My router stopped working."
    conn.close()


def test_ingest_duplicate_message_ignored(client):
    res1 = client.post(
        "/messages",
        json={"message_id": "T-1002", "text": "First delivery"},
    )
    assert res1.status_code == 202
    assert res1.json()["status"] == "accepted"

    # Second delivery with same ID
    res2 = client.post(
        "/messages",
        json={"message_id": "T-1002", "text": "Second delivery"},
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "duplicate"


def test_ingest_empty_text_rejected(client, tmp_path):
    response = client.post(
        "/messages",
        json={"message_id": "T-1003", "text": ""},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"
    assert "Empty" in data["details"]

    # Verify persisted as rejected
    test_db = tmp_path / "test_tickets.db"
    conn = sqlite3.connect(test_db)
    row = conn.execute("SELECT status, failure_reason FROM messages WHERE message_id = 'T-1003'").fetchone()
    assert row is not None
    assert row[0] == "rejected"
    assert "Empty" in row[1]
    conn.close()


def test_ingest_whitespace_text_rejected(client, tmp_path):
    response = client.post(
        "/messages",
        json={"message_id": "T-1004", "text": "   \n\t  "},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"


def test_ingest_oversized_message_rejected(client, monkeypatch):
    monkeypatch.setattr("app.services.ingestion.MAX_MESSAGE_LENGTH", 100)
    response = client.post(
        "/messages",
        json={"message_id": "T-1005", "text": "x" * 150},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"
    assert "exceeds maximum allowed length" in data["details"]


def test_ingest_malformed_payload(client):
    # Missing text
    res1 = client.post("/messages", json={"message_id": "T-1006"})
    assert res1.status_code == 422

    # Missing message_id
    res2 = client.post("/messages", json={"text": "hello"})
    assert res2.status_code == 422

    # Extra fields forbidden
    res3 = client.post("/messages", json={"message_id": "T-1007", "text": "hi", "extra": "forbidden"})
    assert res3.status_code == 422

    # Empty message_id
    res4 = client.post("/messages", json={"message_id": "   ", "text": "hi"})
    assert res4.status_code == 400


def test_database_error_does_not_claim_success(client):
    with patch("app.services.ingestion.get_connection", side_effect=sqlite3.OperationalError("Disk full")):
        res = client.post("/messages", json={"message_id": "T-1008", "text": "Test"})
        assert res.status_code == 503
        assert "Database unavailable" in res.json()["detail"]


def test_list_messages_filtered_and_paginated(client):
    # Ingest 25 valid messages + 1 rejected
    for i in range(1, 26):
        client.post("/messages", json={"message_id": f"T-VALID-{i:03d}", "text": f"Valid ticket {i}"})
    client.post("/messages", json={"message_id": "T-REJ-001", "text": "   "})

    # Query with default limit=20
    res_page1 = client.get("/messages?limit=20&page=1")
    assert res_page1.status_code == 200
    data_page1 = res_page1.json()
    assert len(data_page1["items"]) == 20
    assert data_page1["total"] == 26
    assert data_page1["total_pages"] == 2

    # Query status=rejected
    res_rejected = client.get("/messages?status=rejected&limit=20")
    assert res_rejected.status_code == 200
    data_rejected = res_rejected.json()
    assert data_rejected["total"] == 1
    assert len(data_rejected["items"]) == 1
    assert data_rejected["items"][0]["message_id"] == "T-REJ-001"
    assert data_rejected["items"][0]["status"] == "rejected"


