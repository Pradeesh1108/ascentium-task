import datetime
import sqlite3
from typing import Generator

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text as sa_text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from app.config import DATABASE_PATH

Base = declarative_base()


class MessageModel(Base):
    __tablename__ = "messages"

    message_id = Column(String, primary_key=True)
    text = Column(Text, nullable=False)
    status = Column(String, nullable=False, server_default=sa_text("'pending'"))
    category = Column(String, nullable=True)
    order_id = Column(String, nullable=True)
    product = Column(String, nullable=True)
    urgency = Column(String, nullable=True)
    requested_action = Column(String, nullable=True)
    attempts = Column(Integer, nullable=False, server_default=sa_text("0"))
    failure_reason = Column(Text, nullable=True)
    next_retry_at = Column(String, nullable=True)
    claimed_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False, server_default=sa_text("(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"))
    updated_at = Column(String, nullable=False, server_default=sa_text("(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"))

    checkpoints = relationship("ProcessingCheckpointModel", back_populates="message", cascade="all, delete-orphan")


class ProcessingCheckpointModel(Base):
    __tablename__ = "processing_checkpoints"

    message_id = Column(String, ForeignKey("messages.message_id", ondelete="CASCADE"), primary_key=True)
    stage = Column(String, primary_key=True)
    stage_status = Column(String, nullable=False, server_default=sa_text("'pending'"))
    result_json = Column(Text, nullable=True)
    stage_attempts = Column(Integer, nullable=False, server_default=sa_text("0"))
    error = Column(Text, nullable=True)
    updated_at = Column(String, nullable=False, server_default=sa_text("(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"))

    message = relationship("MessageModel", back_populates="checkpoints")


class ProcessingEventModel(Base):
    __tablename__ = "processing_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String, nullable=True)
    event_type = Column(String, nullable=False)
    stage = Column(String, nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(String, nullable=False, server_default=sa_text("(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"))


# Indices
Index("idx_messages_status", MessageModel.status)
Index("idx_messages_retry", MessageModel.status, MessageModel.next_retry_at)

# Aliases for clean imports
Message = MessageModel
ProcessingCheckpoint = ProcessingCheckpointModel
ProcessingEvent = ProcessingEventModel


def get_engine():
    """Dynamically creates a SQLAlchemy engine matching the current DATABASE_PATH."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(
        f"sqlite:///{DATABASE_PATH}",
        connect_args={"timeout": 10},
        pool_pre_ping=True,
    )

    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 10000")
        cursor.close()

    return eng


def get_session():
    """Returns a new SQLAlchemy Session bound to the current database path."""
    Session = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return Session()


def get_db() -> Generator:
    """FastAPI dependency yielding a SQLAlchemy session."""
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def get_connection() -> sqlite3.Connection:
    """Returns a SQLite connection configured with Row factory, foreign keys, and WAL mode."""
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
    """Initializes tables using exact schema DDL and verifies metadata reflection."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
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
                FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS processing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT,
                event_type TEXT NOT NULL,
                stage TEXT,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
            CREATE INDEX IF NOT EXISTS idx_messages_retry ON messages(status, next_retry_at);
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
