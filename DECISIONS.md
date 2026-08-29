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

### TD-004 · JWT Verification & Algorithm Pinning — SUPERSEDED 2026-08-29, see TD-017

**Date:** 2026-08-24 (superseded 2026-08-29)  
**Original decision:**
- JWT verification is implemented as a FastAPI dependency (`get_current_user_id`) using PyJWT.
- The decoding algorithm was pinned to `HS256` only, verified against a single shared secret (`SUPABASE_JWT_SECRET`).
- Tokens require valid `exp` and `sub` claims. Any signature, expiry, or claim defect returns a generic `401 Unauthorized`.
- Dependency injection (`Depends()`) is used instead of global middleware so routes can declare explicit authentication requirements.

**Why superseded:** this project's Supabase instance signs tokens with its
asymmetric JWT signing keys (`ES256`), not `HS256` — confirmed by decoding a
live token's header, not assumed. HS256-only verification against a shared
secret could never succeed against an ES256 token regardless of how correct
the secret was, so this was a live, total production outage: every
authenticated request from every farmer failed as "session expired,"
including immediately after a successful sign-in. See TD-017 for the fix.
The "never trust the token's `alg` header" principle survives unchanged —
what changed is that there are now correctly two explicitly-allowlisted
algorithms tried in a fixed order, not one.

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

---

### TD-017 · JWT Verification Tries Supabase's Signing Keys (`ES256`) First, Falls Back to the Legacy Shared Secret

**Date:** 2026-08-29  
**Decision:**
- `dependencies/jwt_auth.py` now tries Supabase's asymmetric JWT signing keys
  first: fetched via `PyJWKClient` against
  `SUPABASE_URL/auth/v1/.well-known/jwks.json`, algorithm `ES256`, no shared
  secret involved. Only if that lookup itself fails (no matching key,
  endpoint unreachable) does it fall back to the legacy shared-secret path
  (`SUPABASE_JWT_SECRET`, `HS256`).
- Both decode calls now also pass `audience="authenticated"` explicitly —
  every real Supabase token carries that exact claim, and PyJWT enforces the
  `aud` claim once a token carries one unless told what to expect.
- The algorithm allowlist stays explicit at every `jwt.decode()` call site;
  the token's own `alg` header still never single-handedly decides what gets
  trusted.

**Rationale:** found live, not planned. See TD-004 for the full incident.
Two bugs stacked: signature verification failed outright (wrong algorithm,
TD-004's original issue), and once that was fixed, a SECOND bug became
visible for the first time — `InvalidAudienceError` — because the old
HS256-only code never got far enough to reach that check. Fixing only the
algorithm was necessary but not sufficient; both had to be found by testing
a real Supabase-issued token end-to-end, not by reasoning about the code in
isolation.

**Test-safety note:** every existing test mints its own token and expects
the legacy HS256 path; without intervention each one would trigger a real
HTTP call to the fake test JWKS URL before falling through. `tests/conftest.py`
has an autouse fixture (`_stub_jwks_client`) that makes that lookup fail
instantly and deterministically instead — no network, no flakiness.

---

### TD-018 · Provider-Independent AI Layer (`dependencies/ai/`), Gemini and NVIDIA Behind One Interface

**Date:** 2026-08-29  
**Decision:**
- `routers/diagnosis.py` and `routers/chat.py` no longer import Gemini
  directly. Both call `dependencies/ai/service.py`, which routes to whichever
  provider `AI_PROVIDER` names (default `gemini`) and falls back to
  `AI_FALLBACK_PROVIDER` (default `nvidia`) if the primary can't serve the
  request.
- `dependencies/ai/errors.py` defines the only exception types a provider is
  allowed to raise: `AIConfigurationError` / `AIProviderUnavailable` /
  `AIQuotaExceeded` (all worth a fallback attempt) and `AIRequestFailed` (a
  bug, not a provider problem — never retried against the fallback, since a
  different provider would fail on the same bad input).
- `dependencies/gemini.py` (see TD-019) is wrapped by `GeminiProvider`, not
  rewritten — its own model/key retry logic is reused as-is.
- `NvidiaProvider` calls `meta/llama-3.1-8b-instruct` by default via NVIDIA's
  OpenAI-compatible endpoint (plain `httpx`, no SDK). Chosen over a larger or
  "thinking"/reasoning model deliberately: this app's prompts are short
  treatment guidance and short chat replies, where extra latency and token
  cost from a reasoning model buys nothing, and the image diagnosis already
  happened on-device — this layer only ever sees text.

**Rationale:** a single provider's free-tier quota turned out to be a real,
live blocker — automated testing and normal development traffic drain the
same daily allowance a farmer's actual usage draws from, and once it's gone,
waiting for a midnight reset isn't viable mid-demo. A second, independent
provider with its own quota is sturdier than trying to stretch one further.

**Known limitation, stated plainly:** neither NVIDIA's default model nor
most other free/fast NIM options have officially documented Sinhala or Tamil
support the way Gemini does. Accepted trade-off for quota resilience at zero
cost — the on-device seeded guidance remains the real safety net for
language quality regardless of which provider answers.

**Test-safety:** `tests/conftest.py` force-clears `NVIDIA_API_KEY` on every
test regardless of what happens to be set in the developer's own shell, so
an unmocked test can't reach the network by accident. Opt-in real-provider
integration tests live in `tests/integration/`, gated behind
`RUN_AI_INTEGRATION_TESTS=true`.

---

### TD-019 · Gemini Retry: Two Axes (Model Name, API Key), and Quota/Timeout Retry the SAME Key First

**Date:** 2026-08-29 (evolved from the original two-fallback-model design)  
**Decision:**
- `dependencies/gemini.py` tries each configured key (`GEMINI_API_KEY`, then
  `GEMINI_API_KEY_FALLBACK` if set) in order; for each key, tries each
  candidate model name in order (`GEMINI_MODEL` first if set, then
  `gemini-flash-latest`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`).
- A missing-model error, a quota error, OR a timeout all move to the next
  MODEL on the SAME key first. Only an auth error (bad/revoked key) moves
  straight to the next key.
- Every call is bounded by a 15s timeout (`request_options={"timeout": 15}`).

**Rationale, in the order these were actually found live, not designed
upfront:**
1. No timeout at all used to hang `/interpret-diagnosis` and
   `/chat-about-diagnosis` past three minutes with zero response — found via
   a live check against the deployed service, not a code review.
2. A quota error originally moved straight to the next key. A live
   rate-limit dashboard then showed one model over its 20/day cap while
   THREE OTHER candidate models on the exact same key sat at 0/20,
   completely unused — Google's free-tier quota is scoped per model per
   project, not per key. Jumping keys on the first 429 wasted most of a
   key's daily budget for no reason.
3. A timeout originally moved straight to the next key too (reasoning at
   the time: a stalled connection is a property of the network path, not
   one model). Live evidence then contradicted that: every request was
   timing out on the SAME first model (the `gemini-flash-latest` alias,
   likely resolving to whatever Google's newest, most in-demand model
   currently is) on BOTH keys, while the same dashboard showed the other
   three models still sitting at 0/20 — never reached, because timeout
   jumped keys before ever trying them.
4. The three concrete fallback model names (`gemini-2.5-flash`,
   `gemini-2.0-flash`, `gemini-2.5-flash-lite`) were retired by Google;
   live logs showed all three 404ing on every request. Google's own error
   text named the replacements directly (`gemini-3.6-flash`,
   `gemini-3.5-flash-lite`) — not a guess.

**Net effect:** only a genuine auth/key-level failure ever gives up on a key
before exhausting every candidate model on it — because quota and timeouts
turned out, with live evidence both times, to say nothing reliable about
the other models on the same key.

---

### TD-020 · Missing Supabase Config Surfaces as a Clear 500, Consistently, on Every Router

**Date:** 2026-08-29  
**Decision:**
- Every router's `_get_supabase()` now guards `if not settings.supabase_url
  or not settings.supabase_service_role_key` and raises a clear 500 before
  calling `create_client()`. `sync.py`, `chat.py`, and `feedback.py` already
  did this; `auth.py` didn't.
- All 9 call sites in `auth.py` now call `_get_supabase()` BEFORE their
  try/except block, not inside it — so a config error can't be swallowed by
  the same deliberately-broad `except Exception` blocks that intentionally
  hide *Supabase auth* error details (wrong password, expired OTP, etc. —
  that hiding is intentional, documented in `routers/auth.py`'s own header).

**Rationale:** found live. `SUPABASE_SERVICE_ROLE_KEY` was unset on Render
(a Render env var was actually named `SUPABASE_SECRET_KEY`, a mismatch, not
a code bug), and `_get_supabase()` had no guard — the raw supabase-py error
("supabase_key is required") reached `register()`'s broad `except
Exception`, which had no way to tell "Supabase isn't configured" apart from
"this specific registration attempt failed," so it surfaced as a generic
"could not create that account" with no signal the server itself was
misconfigured. The fix means the NEXT misconfiguration (a typo'd env var, a
rotated key) fails loudly and specifically instead of masquerading as a
user-facing auth failure.

---

### TD-021 · Render Free-Tier Keep-Alive, and Supabase Reference Tables Are Not Auto-Seeded

**Date:** 2026-08-29  
**Decision:**
- `.github/workflows/keep-alive.yml` pings `GET /health` every 10 minutes
  (plus a manual `workflow_dispatch` trigger for pre-demo warming), to
  reduce how often a farmer or a demo hits Render free tier's ~20-30s
  cold-start.
- Supabase's `crop` and `disease` reference tables are NOT seeded by any
  application code path — only the on-device SQLite copy is
  (`CropRepositoryImpl`/`DiseaseRepositoryImpl` in the Flutter app). This
  was a live blocker: `POST /scans` and `POST /diagnoses` both enforce a
  foreign key against these tables, so every sync failed with a `23503`
  foreign key violation until they were seeded manually via the Supabase SQL
  editor, from the exact same source data the app already seeds locally.

**Outstanding:** no migration/seed script for `crop`/`disease` (or
`treatment_guideline`/`model_version`, which `GET /reference-data` also
reads and were never seeded either) is committed to this repo — the schema
and its reference rows were both created directly against the live Supabase
project. A fresh Supabase project stood up from this repo alone would hit
the exact same FK violation on its first sync. Worth turning into a
committed, idempotent seed script rather than a one-off SQL session, before
this surprises someone else the way it surprised this one.

