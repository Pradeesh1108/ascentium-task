from fastapi import APIRouter, Response

from app.schemas import MessageIngestRequest, MessageIngestResponse
from app.services.ingestion import ingest_message

router = APIRouter(tags=["messages"])


@router.post(
    "/messages",
    response_model=MessageIngestResponse,
    summary="Ingest a customer message",
    status_code=202,
)
def create_message(payload: MessageIngestRequest, response: Response):
    status_code, result = ingest_message(payload)
    response.status_code = status_code
    return result

