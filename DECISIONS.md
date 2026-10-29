# Architecture & Design Decisions

This document details key architectural decisions, trade-offs, and operational guarantees made in the Ticket Intelligence System.

---

## 1. Storage & Concurrency Model: SQLite with WAL Mode

- **Decision**: Use SQLite in WAL (Write-Ahead Logging) mode with `PRAGMA foreign_keys = ON` and a 10,000ms `busy_timeout`.
- **Rationale**:
  - Eliminates the operational complexity of external infrastructure (Postgres, Redis, Kafka) while fulfilling the single-node durable work queue requirement.
  - WAL mode allows concurrent readers (e.g., FastAPI read queries, dashboard statistics) without blocking the single writer (worker updates or ingestion).
- **Trade-offs**: SQLite writes are serialized. For massive multi-node scale, a migration to PostgreSQL would be required. Within single-node constraints, WAL provides exceptional throughput and durability.

---

## 2. Ingestion & Idempotency Strategy

- **Decision**: Enforce idempotency using SQLite database-level `PRIMARY KEY (message_id)` rather than `SELECT` before `INSERT`.
- **Rationale**:
  - Eliminates race conditions under concurrent deliveries. If two requests with the same ID arrive simultaneously, SQLite guarantees exactly one succeeds and the other triggers an `IntegrityError`.
  - Duplicate deliveries are not counted as new messages; instead, an explicit `duplicate_delivery_ignored` event is logged in `processing_events`, and the API returns a predictable `200 OK` with status `"duplicate"`.
- **Business Rejections vs JSON Schema**:
  - The input JSON schema allows empty string text. We separate schema validation (valid JSON structure) from business policy (rejecting empty, whitespace-only, or oversized messages).
  - Rejected messages are persisted with status `rejected` and a clear `failure_reason` so they appear in reports and metrics, without consuming LLM resources.

---

## 3. Two-Stage Decomposition: Extraction and Classification

- **Decision**: Split ticket understanding into two sequential LLM calls:
  1. **Stage A (Extraction)**: Extracts `order_id` (enforcing `^ORD-\d{5}$`) and normalizes `product` to the official catalogue.
  2. **Stage B (Classification)**: Receives the customer message along with the validated extraction context to determine `category`, `urgency`, and `requested_action`.
- **Rationale**:
  - Keeps prompts focused on a single responsibility. Large multi-task prompts suffer from lower accuracy, hallucinations, and non-deterministic schema violations.
  - Feeding extracted entity context into classification resolves ambiguities (e.g., distinguishing a hardware breakdown from a delivery inquiry).
- **Catalogue Normalization**:
  - Even if the model outputs a slightly varied name, Python-level normalization strictly maps to the 8 official products or returns `null`.

---

## 4. Durable Checkpointing & Resumption Guarantees

- **Decision**: Persist intermediate stage results in a dedicated `processing_checkpoints` table with a composite primary key `(message_id, stage)`.
- **Rationale**:
  - **No Repeated LLM Calls**: If the worker crashes or Stage B fails, the system recovers by loading the persisted Stage A checkpoint, skipping Call 1 completely.
  - **Atomic Finalization**: The final transition to `completed` and all structured fields are written in a single database transaction. If the process crashes during finalization, the message remains eligible for retry, resumes without calling the LLM, and retries only finalization.
- **Important Boundary**: An external LLM call can succeed while a machine crashes before the response reaches disk. The system guarantees that once a checkpoint is written to SQLite, that stage will never be re-executed.

---

## 5. Shared LLM Client and Resilience

- **Decision**: Use a shared HTTPX client instance with configurable timeout (`LLM_TIMEOUT_SECONDS`) and explicit error categorization.
- **Rationale**:
  - Prevents socket exhaustion from creating new HTTP connections on every request.
  - Includes `ngrok-skip-browser-warning: true` header to bypass ngrok free-tier interstitials.
  - Distinguishes transient errors (timeouts, connection resets, 429 rate limits, 5xx server errors) from permanent configuration/auth errors (401/403). Only transient errors are eligible for retry.
  - Parses JSON defensively, stripping potential markdown fences (` ```json `) before validating schemas in Python.
