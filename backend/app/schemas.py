from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class MessageIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(..., description="Unique ID of the message")
    text: str = Field(..., description="Customer message text (can be empty at schema level)")


class MessageIngestResponse(BaseModel):
    message_id: str
    status: Literal["accepted", "duplicate", "rejected"]
    details: Optional[str] = None


class CheckpointInfo(BaseModel):
    stage: str
    stage_status: str
    result: Optional[Dict[str, Any]] = None
    stage_attempts: int = 0
    error: Optional[str] = None
    updated_at: Optional[str] = None


class MessageDetailResponse(BaseModel):
    message_id: str
    text: str
    status: str
    category: Optional[str] = None
    order_id: Optional[str] = None
    product: Optional[str] = None
    urgency: Optional[str] = None
    requested_action: Optional[str] = None
    attempts: int = 0
    failure_reason: Optional[str] = None
    next_retry_at: Optional[str] = None
    claimed_at: Optional[str] = None
    created_at: str
    updated_at: str
    checkpoints: List[CheckpointInfo] = Field(default_factory=list)


class MessageListItem(BaseModel):
    message_id: str
    status: str
    category: Optional[str] = None
    order_id: Optional[str] = None
    product: Optional[str] = None
    urgency: Optional[str] = None
    requested_action: Optional[str] = None
    attempts: int = 0
    failure_reason: Optional[str] = None
    created_at: str
    updated_at: str


class ExtractionResult(BaseModel):
    order_id: Optional[str] = None
    product: Optional[str] = None


class ClassificationResult(BaseModel):
    category: Optional[Literal["Billing", "Technical issue", "Account access", "Refund", "Other"]] = None
    urgency: Optional[Literal["Low", "Medium", "High"]] = None
    requested_action: Optional[str] = None


class ExportResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    status: Literal["completed", "failed", "rejected"]
    category: Optional[str] = None
    order_id: Optional[str] = None
    product: Optional[str] = None
    urgency: Optional[str] = None
    requested_action: Optional[str] = None


class StatsResponse(BaseModel):
    unique_messages: int
    in_progress: int
    completed: int
    failed: int
    rejected: int
    duplicates_ignored: int
    total_llm_calls: int
    repeated_llm_calls: int


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    services: Dict[str, str]

