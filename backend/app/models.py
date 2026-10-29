from enum import Enum


class MessageStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class StageName(str, Enum):
    EXTRACTION = "extraction"
    CLASSIFICATION = "classification"


class StageStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(str, Enum):
    MESSAGE_ACCEPTED = "message_accepted"
    MESSAGE_REJECTED = "message_rejected"
    DUPLICATE_DELIVERY_IGNORED = "duplicate_delivery_ignored"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    CHECKPOINT_SAVED = "checkpoint_saved"
    PROCESSING_FAILURE = "processing_failure"
    RETRY_SCHEDULED = "retry_scheduled"
    MANUAL_RETRY_REQUESTED = "manual_retry_requested"
    MESSAGE_COMPLETED = "message_completed"
    MESSAGE_FAILED = "message_failed"


PERMITTED_CATEGORIES = (
    "Billing",
    "Technical issue",
    "Account access",
    "Refund",
    "Other",
)

PERMITTED_URGENCIES = (
    "Low",
    "Medium",
    "High",
)

OFFICIAL_PRODUCTS = (
    "Aura Smart Watch",
    "Pulse Earbuds",
    "Nimbus Router X2",
    "Orbit Smart Speaker",
    "CloudSync Pro",
    "Zen Air Purifier",
    "Vista 4K Monitor",
    "Glide Laptop Stand",
)

