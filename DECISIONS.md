# Backend Architecture & Technical Decisions (DECISIONS.md)

This log records major architectural and technical decisions made in the CropCare FastAPI backend.

---

### TD-001 · Single-Gateway Architecture (FastAPI Mediating Supabase & Gemini)

**Date:** 2026-08-24  
**Decision:**
- The client (Flutter) communicates strictly with a single FastAPI gateway on Render.
- The client never receives or holds Supabase service-role keys or Gemini API keys.
- FastAPI acts as the centralized boundary for request validation, SlowAPI rate limiting, JWT verification, and audit logging.

---

### TD-002 · Supabase Auth Admin API Proxying & Information Hiding

**Date:** 2026-08-24  
**Decision:**
- `/auth/request-otp` and `/auth/verify-otp` proxy Supabase Auth Admin API using the server-side service-role key.
- **Information Hiding:**
  - `POST /auth/request-otp` unconditionally returns `200 OK` with a generic message even if an identifier is unregistered or Supabase throws an error. This prevents user-enumeration attacks.
  - `POST /auth/verify-otp` collapses all failure modes (wrong code, expired token, network latency) to a generic `401 Unauthorized` without distinguishing the specific reason.
- Request payload enforces exclusivity: exactly one of `email` or `phone` must be supplied (400 Bad Request otherwise).

---

### TD-003 · Phone OTP Cost-Control Gate (`PHONE_AUTH_ENABLED`)

**Date:** 2026-08-24  
**Decision:**
- Phone SMS authentication costs real money per SMS (via Twilio/telecom providers).
- Phone OTP is gated behind the `PHONE_AUTH_ENABLED` environment variable, defaulting to `False`.
- The gate executes before any SlowAPI consumption or Supabase network calls, immediately returning `403 Forbidden` ("Phone sign-in is not available right now — please use email.").

---

### TD-004 · JWT Verification & Algorithm Pinning (`HS256`)

**Date:** 2026-08-24  
**Decision:**
- JWT verification is implemented as a FastAPI dependency (`get_current_user_id`) using PyJWT.
- The decoding algorithm is **explicitly pinned to `HS256`** and token header `alg` is never trusted. This eliminates algorithm confusion and `alg: none` exploits.
- Tokens require valid `exp` and `sub` claims. Any signature, expiry, or claim defect returns a generic `401 Unauthorized`.
- Dependency injection (`Depends()`) is used instead of global middleware so routes can declare explicit authentication requirements.

---

### TD-005 · Optional Authentication for Guest AI Diagnosis (`get_optional_user_id`)

**Date:** 2026-08-24  
**Decision:**
- `POST /interpret-diagnosis` supports both guest farmers (offline-first, no account yet) and authenticated farmers.
- Implemented `get_optional_user_id` dependency:
  - If `Authorization: Bearer <token>` is present, it strictly validates the JWT and returns `user_id`.
  - If no `Authorization` header is sent, it returns `None` (guest mode) and uses client IP for rate limiting.
  - If a malformed or expired token is sent, it rejects with `401 Unauthorized`.

---

### TD-006 · Dynamic Rate Limiting with SlowAPI

**Date:** 2026-08-24  
**Decision:**
- Configured SlowAPI with dynamic key resolution via `_identifier_key`.
- Endpoints attach their rate-limiting key (`email`, `phone`, or `user_id`) to `request.state.rate_limit_key` during execution before SlowAPI's response-cycle hook evaluates the quota.
- Quotas:
  - `POST /auth/request-otp`: 3 requests per 10 minutes per identifier.
  - `POST /interpret-diagnosis`: 20 requests per minute per user/IP.

---

### TD-007 · Gemini 1.5 Flash Structured Agricultural Guidance (`POST /interpret-diagnosis`)

**Date:** 2026-08-24  
**Decision:**
- Integrates Google Gemini (`gemini-1.5-flash`) via `google-generativeai` with `response_mime_type="application/json"`.
- Enforces strict 4-part JSON response schema:
  - `summary` (concise disease overview)
  - `what_to_do` (actionable treatment steps)
  - `what_to_avoid` (crucial pitfalls/mistakes)
  - `recheck_after_days` (integer days before inspection)
- Supports trilingual prompting for English (`en`), Sinhala (`si`), and Tamil (`ta`).
- Generates a persistent UUID `interpretation_id` for tracking and client-side caching.

---

### TD-008 · Non-Blocking Best-Effort Audit Logging (`llm_interpretation`)

**Date:** 2026-08-24  
**Decision:**
- On successful diagnosis interpretation, the full request context, user ID, guidance fields, and generated `interpretation_id` are written to Supabase `llm_interpretation`.
- The audit insert is wrapped in a safe, non-blocking handler (`_log_to_supabase`). Any database connectivity issue logs a warning but never fails the farmer's response or blocks treatment delivery.

---

### TD-009 · Flat Repository Architecture for MVP

**Date:** 2026-08-24  
**Decision:**
- Kept the backend structure clean and flat (`routers/`, `dependencies/`, `config.py`, `tests/`) rather than introducing prematurely complex multi-layer abstractions (e.g. redundant service or repository interfaces) before database sync models are implemented.

---

### TD-010 · Idempotent Sync Architecture & User-Scoped Upsert Enforcement

**Date:** 2026-08-24  
**Decision:**
- Sync endpoints (`POST /scans`, `POST /diagnoses`, `POST /escalations`) are protected with `get_current_user_id`.
- The server forcibly injects `user_id = jwt_user_id` into every row payload, ignoring any client-provided or spoofed `user_id`.
- Idempotency is enforced using `on_conflict="user_id,local_scan_id"`, `on_conflict="user_id,local_diagnosis_id"`, and `on_conflict="user_id,local_escalation_id"`.
- Network retries from client background sync workers will safely update existing records without creating duplicates.

---

### TD-011 · Supabase Signed Upload URLs for Direct Storage Ingestion

**Date:** 2026-08-24  
**Decision:**
- Binary image payloads are never proxied through the Render FastAPI instance, preserving bandwidth and preventing timeouts.
- `POST /scans/{id}/upload-url` generates a short-lived signed upload URL via Supabase Storage for bucket `scan-images`, strictly scoped to `{user_id}/{scan_id}.jpg`.
- The Flutter client uploads JPEG bytes directly to Supabase Storage using the signed URL.

---

### TD-012 · Defense-in-Depth Row-Level Security (RLS) Policy Matrix

**Date:** 2026-08-24  
**Decision:**
- While FastAPI acts as the primary query-scoping boundary via the service-role key, RLS is enabled on all tables in Supabase Postgres.
- User-scoped tables (`profile`, `scan`, `diagnosis`, `escalation`, `sync_log`, `llm_interpretation`) enforce `auth.uid() = user_id`.
- Reference tables (`crop`, `disease`, `treatment_guideline`, `model_version`) allow public read (`USING (true)`) and restrict writes to service role / admin.
- DDL and RLS definitions are applied directly to Supabase Cloud via SQL Editor.

---

### TD-013 · Password Reset (`POST /auth/forgot-password`) & Anti-Enumeration

**Date:** 2026-08-25  
**Decision:**
- Added `POST /auth/forgot-password` accepting `{"email": "farmer@example.com"}`.
- Relays to Supabase Auth (`supabase.auth.reset_password_for_email`).
- **Anti-Enumeration & Security:** Unconditionally returns HTTP `200 OK` with `{"message": "If an account exists with this email, password reset instructions have been sent.", "status": "success"}` even if the email does not exist in Supabase or an error is thrown.
- **Rate-Limiting:** Enforces 3 requests / 10 minutes per email address using SlowAPI.

---

### TD-014 · Account Deletion & User Sync Cleanup (`DELETE /auth/account`)

**Date:** 2026-08-25  
**Decision:**
- Added `DELETE /auth/account` protected by `get_current_user_id` Bearer JWT dependency.
- Cascades user-scoped data deletions across `scan`, `diagnosis`, `escalation`, `profile`, and `llm_interpretation` tables.
- Invokes Supabase Auth Admin API (`supabase.auth.admin.delete_user(user_id)`) to remove authentication record.
- Returns HTTP `200 OK` with `{"status": "success", "message": "Account successfully deleted"}`.

---

### TD-015 · Resilient In-App User Feedback (`POST /feedback`)

**Date:** 2026-08-25  
**Decision:**
- Added `POST /feedback` supporting bug reports, feature suggestions, and general user feedback.
- Dual authentication support: accepts guest feedback as well as authenticated submissions (via `get_optional_user_id`), automatically binding the JWT `user_id` when present.
- Non-blocking persistence: records are inserted into Supabase `feedback` table with resilient error handling ensuring client responses never crash due to database latency.

---

### TD-016 · Authenticated Email & Phone Modification (`POST /auth/change-email`, `/change-phone/*`)

**Date:** 2026-08-25  
**Decision:**
- Added `POST /auth/change-email` allowing authenticated users to update their email address via `supabase.auth.admin.update_user_by_id`, returning `409 Conflict` if the email is already registered.
- Added `POST /auth/change-phone/request-otp` and `POST /auth/change-phone/verify-otp` to safely verify ownership of a new phone number via SMS OTP before persisting the phone update to the user record.
- Both phone change endpoints strictly respect the `PHONE_AUTH_ENABLED` feature flag and SlowAPI rate limiting (3 requests / 10 min).




