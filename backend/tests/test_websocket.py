import json
import pytest
from fastapi.testclient import TestClient

from app.database import get_connection, initialize_database, record_event
from app.main import app
from app.models import EventType


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test_ws.db"
    monkeypatch.setattr("app.config.DATABASE_PATH", test_db)
    monkeypatch.setattr("app.database.DATABASE_PATH", test_db)
    initialize_database()

    with TestClient(app) as test_client:
        yield test_client


def test_websocket_connection_and_ping(client):
    with client.websocket_connect("/ws") as websocket:
        init_data = websocket.receive_json()
        assert init_data["type"] == "connected"
        assert "WebSocket" in init_data["message"]

        websocket.send_text("ping")
        pong_data = websocket.receive_json()
        assert pong_data["type"] == "pong"

