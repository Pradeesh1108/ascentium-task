import asyncio
import logging
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect

from app.database import get_connection

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, data: dict) -> None:
        if not self.active_connections:
            return
        dead_connections = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_json(data)
            except Exception as e:
                logger.debug(f"Error broadcasting to WebSocket client: {e}")
                dead_connections.add(connection)
        for dead in dead_connections:
            self.disconnect(dead)


ws_manager = ConnectionManager()


async def event_broadcaster_task() -> None:
    """Continuously monitors processing_events in SQLite WAL mode and broadcasts new events."""
    last_event_id = 0
    try:
        with get_connection() as conn:
            cursor = conn.execute("SELECT COALESCE(MAX(id), 0) FROM processing_events")
            row = cursor.fetchone()
            if row:
                last_event_id = row[0]
    except Exception as e:
        logger.warning(f"Could not initialize event broadcaster watermark: {e}")

    logger.info(f"WebSocket event broadcaster active. Starting watermark: event_id={last_event_id}")

    while True:
        try:
            if ws_manager.active_connections:
                with get_connection() as conn:
                    cursor = conn.execute(
                        """
                        SELECT id, message_id, event, stage, details, created_at
                        FROM processing_events
                        WHERE id > ?
                        ORDER BY id ASC
                        LIMIT 50
                        """,
                        (last_event_id,),
                    )
                    rows = cursor.fetchall()

                if rows:
                    for row in rows:
                        last_event_id = row["id"]
                        event_payload = {
                            "type": "event",
                            "event_id": row["id"],
                            "message_id": row["message_id"],
                            "event": row["event"],
                            "stage": row["stage"],
                            "details": row["details"],
                            "created_at": row["created_at"],
                        }
                        await ws_manager.broadcast(event_payload)

            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            logger.info("Event broadcaster task cancelled.")
            break
        except Exception as e:
            logger.debug(f"Event broadcaster loop warning: {e}")
            await asyncio.sleep(1.0)

