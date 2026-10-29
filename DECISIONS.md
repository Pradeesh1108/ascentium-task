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

---

## 6. Durable Job Claiming & Stale Job Recovery in SQLite

- **Decision**: Implement the durable work queue directly inside SQLite using atomic `UPDATE ... WHERE` conditional writes, without external queue infrastructure (Redis, RabbitMQ, Celery).
- **Rationale**:
  - The worker atomically claims an eligible job by setting `status = 'processing'` and `claimed_at = CURRENT_TIMESTAMP` in a single query.
  - If a worker process abruptly crashes while processing, other workers automatically recover the stuck job once its lease (`CLAIM_TIMEOUT_SECONDS`, default 60s) expires.
  - A slow or failing message never blocks other messages; the queue evaluates pending messages ordered by `created_at` whose retry backoff time has arrived.

---

## 7. Exponential Backoff & Non-Blocking Retry Scheduling

- **Decision**: Persist `next_retry_at` timestamps on messages and use an exponential backoff curve (`RETRY_BACKOFF_BASE ** attempts`).
- **Rationale**:
  - Prevents busy-waiting and flooding an already struggling or rate-limited LLM API.
  - After 3 consecutive processing failures, the message is marked `failed` with the exact error stored in `failure_reason`.
  - Manual retries (`POST /messages/{id}/retry` and `POST /messages/retry-failed`) reset the status to `pending` and clear failure metadata while **strictly preserving all valid extraction and classification checkpoints**.


---

## 8. REST API Contracts & Operational Transparency

- **Decision**: Keep HTTP handlers thin and delegating strictly to modular domain services. Expose operational and debugging states via `GET /messages/{id}`.
- **Rationale**:
  - Operators can inspect full checkpoint states (Stage A and Stage B results, errors, stage attempt counters) without logging sensitive raw message bodies to log aggregators.
  - Pagination (`page`, `limit`) and status filtering on `GET /messages` utilize SQLite index `idx_messages_status` to maintain predictable query latency.

---

## 9. Final Results Export Policy & Exact Contract Compliance

- **Decision**: Strict 7-field contract enforcement (`message_id`, `status`, `category`, `order_id`, `product`, `urgency`, `requested_action`) on `GET /results/export`.
- **Rationale**:
  - Excludes internal mechanics (`attempts`, `failure_reason`, timestamps, checkpoint JSON) to prevent leaking internal database schemas to downstream analytics consumers.
  - **Export Policy for Unfinished Records**:
    - `completed`: Exports resolved category, order_id, product, urgency, and requested_action.
    - `rejected`: Exports status `rejected` with `null` values for all 5 classification/extraction attributes.
    - `failed` or unfinished (`pending`/`processing`): Exports status `failed` with `null` values for attributes, ensuring unfinished tickets are never misrepresented as successfully processed.

---

## 10. Dashboard & Health Check Decoupling

- **Decision**: Decouple `GET /health` and `GET /stats` from live external LLM calls.
- **Rationale**:
  - Frequent polling by uptime monitors or frontend dashboards (such as the Ticket Desk) would incur significant costs, consume API rate limits, and cause artificial failures during upstream rate-limiting.
  - Database health is verified via a lightweight connection test (`SELECT 1`), while LLM health reports configuration state and logs errors asynchronously from processing attempts.

---

## 11. Server-Side Filtered Pagination & Live UI Realtime Synchronization

- **Decision**: Maintain a strict UI page size limit of 20 items (`limit=20`) and delegate all category, urgency, status (e.g. `rejected`, `failed`), and text search filtering directly to SQLite via parameterized `WHERE` clauses on `GET /messages`.
- **Rationale**:
  - Client-side filtering over an in-memory batch breaks down when the total dataset exceeds the page limit (e.g. ingesting 25 practice messages pushes older records such as validation-rejected messages like `T-1012` beyond the first 20 records).
  - Server-side filtering guarantees that selecting "Rejected" or any other filter immediately queries the entire database table and returns matching tickets within the 20-item viewport alongside Prev/Next pagination controls.
  - The "Failed messages" operator card independently monitors `GET /messages?status=failed` to ensure actionable failures are visible regardless of table filtering.

---

## 12. Message-Scoped Pipeline Inspection in Detail Pin Drawer

- **Decision**: Move the 8-stage processing pipeline stepper directly inside the slide-over pin drawer (`Detail` component) for each individual message, removing the detached global pipeline from the main overview screen.
- **Rationale**:
  - Operators inspect pipeline progress in the context of a specific message. Embedding the 8-stage visual pipeline inside the detail drawer allows immediate inspection of exactly how and where that specific ticket executed (e.g., Stage 1 Received ✓, Stage 2 Validated with `Rejected here` exclamation badge, or active stages with spinning amber ringlights).
  - De-clutters the top dashboard overview while expanding the drawer width to `sm:w-[32rem] md:w-[38rem]` for a clean, spacious 4-column responsive step track.

---

## 13. Delivery Idempotency vs. The Hazards of Semantic Deduplication

- **Decision**: Enforce message uniqueness strictly at the platform delivery boundary (`message_id` as primary key) rather than applying naive text embeddings or semantic similarity matching across incoming messages.
- **Rationale & Analysis**:
  - In customer support systems, two distinct customers often send word-for-word identical complaints (e.g. "My internet is down" or "Where is my parcel?"). Conflating these via text similarity would silently drop one customer's ticket.
  - Furthermore, template-like enquiries (e.g. Customer A: *"Need help with ORD-12345"* vs. Customer B: *"Need help with ORD-12346"*) have an extraordinarily high embedding cosine similarity (~0.98), yet they reference completely distinct orders and customer accounts. Deduplicating by similarity would falsely merge separate order issues.
  - **Production Entity-Aware Policy**: True deduplication must be delivery-idempotent by `message_id` first. Cross-message deduplication is only valid when scoped to the same authenticated customer ID, within a tight time window (e.g. 15 minutes), and after verifying that extracted entities (`order_id`, `product`) match.

---

## 14. Realtime Database Polling Mitigation & WebSocket Live Event Stream

- **Decision**: Implement a native WebSocket real-time event stream (`/ws`) powered by an asynchronous background broadcaster scanning `processing_events` in SQLite WAL mode, eliminating the need for aggressive interval polling.
- **Rationale & Implementation**:
  - In a multi-tenant evaluation environment where external evaluators submit messages from their own machines (via `curl`, Postman, or automated testing scripts), client-side polling introduces lag (up to several seconds) and burns unnecessary database reads.
  - **WebSocket Architecture (`backend/app/websocket.py`)**:
    - FastAPI runs an asynchronous background broadcaster task (`event_broadcaster_task`) monitoring auto-incrementing `id` in `processing_events`.
    - When an evaluator ingests a message or the worker completes a stage/checkpoint, the event is immediately pushed across all active WebSocket connections within ~250ms.
    - The frontend (`useDash`) subscribes to `/ws` and renders live updates instantly without requiring manual page reloads.
    - Includes automatic heartbeat, graceful reconnection backoff, and transparent fallback polling if the socket connection is interrupted.


---

## 15. Classification-First Pipeline & Cost-Optimized Conditional Extraction

- **Decision**: Invert the pipeline sequence so that Stage A executes **Classification** first, followed conditionally by **Extraction** as Stage B only when `category != "Other"`.
- **Rationale & Impact**:
  - Messages classified as `"Other"` typically encompass promotional spam, general compliments, or questions containing neither an order ID nor an identifiable product catalogue item.
  - By classifying first, the system safely bypasses Stage B (Extraction) whenever `category == "Other"`, setting `order_id = null` and `product = null` with an explicit `skipped` checkpoint.
  - **Efficiency Gain**: Eliminates 50% of the expensive LLM calls for general and spam messages, dramatically lowering token costs, preventing hallucinations, and maximizing overall pipeline throughput.
  - **Resume Guarantee**: Classification checkpoints remain fully durable. If an extraction attempt fails on an eligible ticket, retries resume directly at extraction without repeating classification.

---

## 16. Service Decoupling & Blast-Radius Isolation Roadmap

- **Architecture Strategy for Stage Isolation**:
  - To prevent a classifier outage or upstream LLM rate-limit from bringing down the entire pipeline, the architecture cleanly isolates ingestion, classification, and extraction into decoupled execution boundaries:
    1. **Ingestion Boundary**: `POST /messages` commits incoming payloads into the persistent queue in SQLite immediately, completely isolated from worker health.
    2. **Independent Stage Workers / Asynchronous Hand-off**: Classification and extraction can be deployed as discrete worker pools communicating via task queues or checkpoint state transitions. If the classification service experiences degraded latency, extraction workers continue processing previously classified tickets, and ingestion never blocks.
    3. **Fault-Specific Healing**: Because errors are logged per stage in `processing_checkpoints`, operators can target retries to specifically failed stages without touching successful checkpoints.

---

## 17. SQLAlchemy ORM Integration & Docker Compose Multi-Container Architecture

- **Decision**: Introduce standard SQLAlchemy declarative models (`MessageModel`, `ProcessingCheckpointModel`, `ProcessingEventModel`), engine pooling with PRAGMA connection listeners, and multi-container Docker Compose deployment decoupling API and Worker services.
- **SQLite Concurrency & Multi-Container Persistence Guarantees**:
  1. **Persistent Docker Volume**: Defined named volume `sqlite-data` mounted at `/app/data` across container lifecycles, ensuring database records, intermediate checkpoints, and events survive container restarts and rebuilds.
  2. **Shared Database File**: Both the `ticket-api` and `ticket-worker` services mount the exact same volume at `/app/data/tickets.db`.
  3. **Safe Concurrent Access**:
     - **WAL Mode (`PRAGMA journal_mode = WAL`)**: Enables non-blocking concurrent readers (API listing tickets, checking `/health` and `/stats`) alongside the writer.
     - **Busy Timeout (`PRAGMA busy_timeout = 10000`)**: Configures SQLite engine and DBAPI connections to wait up to 10 seconds for locks rather than throwing immediate busy exceptions.
     - **Atomic Job Claims**: Worker claims pending tasks using an atomic conditional update query (`UPDATE messages SET status='processing', claimed_at=? WHERE message_id=? AND status='pending'`), ensuring zero duplicate claims even under concurrent worker scaling.
     - **Strict Unique Constraint**: Enforced on `message_id` as PRIMARY KEY to guarantee delivery idempotency.
  4. **Docker Compose Execution**: Single-command startup (`docker compose up --build`) launches the API server, frontend UI, WebSocket stream, and background worker with shared volume persistence.






