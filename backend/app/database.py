import sqlite3

from app.config import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")

    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                category TEXT,
                order_id TEXT,
                product TEXT,
                urgency TEXT,
                requested_action TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                failure_reason TEXT,
                next_retry_at TEXT,
                claimed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );

            CREATE TABLE IF NOT EXISTS processing_checkpoints (
                message_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                stage_status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT,
                stage_attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

                PRIMARY KEY (message_id, stage),

                FOREIGN KEY (message_id)
                    REFERENCES messages(message_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS processing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT,
                event_type TEXT NOT NULL,
                stage TEXT,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );


            CREATE INDEX IF NOT EXISTS idx_messages_status
                ON messages(status);

            CREATE INDEX IF NOT EXISTS idx_messages_retry
                ON messages(status, next_retry_at);
            """
        )


def check_database_health() -> bool:
    try:
        with get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def record_event(
    connection: sqlite3.Connection,
    message_id: str | None,
    event_type: str,
    stage: str | None = None,
    details: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO processing_events (message_id, event_type, stage, details)
        VALUES (?, ?, ?, ?)
        """,
        (message_id, event_type, stage, details),
    )


def record_event(
    connection: sqlite3.Connection,
    message_id: str | None,
    event_type: str,
    stage: str | None = None,
    details: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO processing_events (message_id, event_type, stage, details)
        VALUES (?, ?, ?, ?)
        """,
        (message_id, event_type, stage, details),
    )
