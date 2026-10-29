import pytest
from app.database import (
    Base,
    MessageModel,
    ProcessingCheckpointModel,
    ProcessingEventModel,
    get_engine,
    get_session,
    initialize_database,
)


@pytest.fixture
def setup_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_sa.db"
    monkeypatch.setattr("app.config.DATABASE_PATH", test_db)
    monkeypatch.setattr("app.database.DATABASE_PATH", test_db)
    initialize_database()
    return test_db


def test_sqlalchemy_orm_crud(setup_db):
    session = get_session()
    try:
        # Create message via SQLAlchemy ORM
        msg = MessageModel(
            message_id="T-SA-001",
            text="SQLAlchemy integration test",
            status="pending",
        )
        session.add(msg)
        session.commit()

        # Query back via SQLAlchemy ORM
        saved = session.query(MessageModel).filter_by(message_id="T-SA-001").first()
        assert saved is not None
        assert saved.message_id == "T-SA-001"
        assert saved.status == "pending"
        assert saved.attempts == 0

        # Add checkpoint via ORM
        checkpoint = ProcessingCheckpointModel(
            message_id="T-SA-001",
            stage="classification",
            stage_status="completed",
            result_json='{"category": "Billing"}',
        )
        session.add(checkpoint)
        session.commit()

        # Verify relationship
        session.refresh(saved)
        assert len(saved.checkpoints) == 1
        assert saved.checkpoints[0].stage == "classification"
    finally:
        session.close()

