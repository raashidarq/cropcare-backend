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
