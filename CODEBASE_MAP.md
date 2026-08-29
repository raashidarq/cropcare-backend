# CODEBASE_MAP.md — CropCare Backend

> **Purpose:** Gives a fresh AI coding agent an accurate, compact understanding of the **current** CropCare backend.
> **Source-of-truth hierarchy:** Actual source code → `CropCare_System_Architecture.md` → `CropCare_Build_Checklist.md`.
> **Last updated:** 2026-08-29

---

## 1. Current Status

| Area | Status | Notes |
|------|--------|-------|
| FastAPI application | **Implemented** | `main.py`, `uvicorn`-compatible, deployable to Render |
| Health endpoint `GET /health` | **Implemented** | Returns `{"status": "ok"}` |
| Auth router `/auth/*` | **Implemented** | Email OTP, phone OTP (gated), password reset, account deletion, and email/phone updates; see §7 |
| `POST /auth/request-otp` | **Implemented** | Email always active; phone gated by `PHONE_AUTH_ENABLED` flag |
| `POST /auth/verify-otp` | **Implemented** | Relays Supabase `access_token`, `refresh_token`, `expires_at` |
| `POST /auth/forgot-password` | **Implemented** | Password reset request via Supabase Auth with rate limiting & anti-enumeration |
| `DELETE /auth/account` | **Implemented** | Account deletion via Supabase Auth Admin & cascaded sync record cleanup |
| `POST /auth/change-email` | **Implemented** | Updates user email via Supabase Auth Admin (with conflict detection) |
| `POST /auth/change-phone/request-otp` | **Implemented** | Sends SMS OTP to new phone number before update (gated by `PHONE_AUTH_ENABLED`) |
| `POST /auth/change-phone/verify-otp` | **Implemented** | Verifies OTP and updates user phone number via Supabase Auth Admin |
| `POST /feedback` | **Implemented** | User feedback, bug reporting, and suggestion submission (guest + authenticated) |
| Treatment Guidance `POST /interpret-diagnosis` | **Implemented** | Google Gemini 1.5 Flash structured guidance + Supabase audit logging |
| Sync router `/scans`, `/diagnoses`, `/escalations` | **Implemented** | `routers/sync.py` — idempotent upserts strictly scoped to JWT `user_id` |
| `POST /scans/{id}/upload-url` | **Implemented** | Signed upload URL generation for Supabase Storage (`scan-images` bucket) |
| `GET /reference-data` | **Implemented** | Versioned pull of crops, diseases, guidelines, and models (`since` filter) |
| JWT auth dependency | **Implemented** | `dependencies/jwt_auth.py` (`get_current_user_id`, `get_optional_user_id`) — tries Supabase's ES256 signing keys first, falls back to legacy HS256 shared secret; see TD-017 |
| AI provider abstraction | **Implemented** | `dependencies/ai/` — routers call `service.generate()`, never Gemini/NVIDIA directly; automatic fallback between providers; see TD-018 |
| Rate limiting | **Implemented** | SlowAPI on `/auth/request-otp` (3/10min), `/auth/forgot-password` (3/10min), `/auth/change-phone/request-otp` (3/10min), and `/interpret-diagnosis` (20/min) |
| CORS middleware | **Not present** | Not configured in `main.py` |
| Supabase Postgres access | **Implemented** | Service-role client used for Auth Admin, sync upserts, and audit table |
| Database schema & RLS policies | **Applied** | DDL and RLS security policies applied directly in Supabase Cloud |
| Repository/service/use-case layers | **Not present** | Flat structure only |
| ML / on-device inference | **Not present in backend** | Stays on Flutter client |
| Gemini integration | **Implemented** | `dependencies/gemini.py`, wrapped by `dependencies/ai/gemini_provider.py`; model-name AND API-key fallback, quota/timeout retry other models on same key first; see TD-019 |
| NVIDIA NIM integration | **Implemented** | `dependencies/ai/nvidia_provider.py`; fallback provider, `meta/llama-3.1-8b-instruct` by default |
| Image upload / storage | **Implemented** | Direct-to-Storage upload pattern via signed upload URLs |
| Settings/profile endpoints | **Not present** | — |
| CI pipeline | **Implemented** | GitHub Actions: `pytest` + `ruff check` on push/PR to `main` |
| Render keep-alive | **Implemented** | `.github/workflows/keep-alive.yml`, pings `/health` every 10 min; see TD-021 |

---

## 2. Repository Structure

```
cropcare-backend/
├── main.py                     # FastAPI app entry point (wires auth, diagnosis, feedback, sync routers)
├── config.py                   # Settings singleton (env var reader)
├── requirements.txt            # Python dependencies (includes supabase, pyjwt, google-generativeai)
├── pytest.ini                  # Test runner config
├── DECISIONS.md                # Architectural and technical decisions log (TD-001 to TD-021)
├── .gitignore                  # Ignores .env, .venv, __pycache__
├── routers/
│   ├── __init__.py
│   ├── auth.py                 # /auth router (OTP, password reset, delete account), limiter, Pydantic schemas
│   ├── diagnosis.py            # /interpret-diagnosis endpoint (calls dependencies/ai/service.py)
│   ├── chat.py                 # /chat-about-diagnosis endpoint (calls dependencies/ai/service.py)
│   ├── feedback.py             # /feedback endpoint (user feedback collection)
│   └── sync.py                 # Sync endpoints (/scans, /diagnoses, /escalations, /reference-data, upload-url)
├── dependencies/
│   ├── __init__.py
│   ├── jwt_auth.py             # get_current_user_id & get_optional_user_id — ES256 (Supabase signing keys) first, HS256 fallback
│   ├── gemini.py                # Gemini-specific retry: model-name AND API-key fallback axes; see TD-019
│   └── ai/
│       ├── __init__.py
│       ├── base.py              # AIProvider interface
│       ├── errors.py            # AIConfigurationError / AIProviderUnavailable / AIQuotaExceeded / AIRequestFailed
│       ├── gemini_provider.py   # Wraps dependencies/gemini.py, translates its errors to the shared types
│       ├── nvidia_provider.py   # NVIDIA NIM via plain httpx, OpenAI-compatible endpoint
│       └── service.py           # generate() — routes AI_PROVIDER -> AI_FALLBACK_PROVIDER; see TD-018
├── tests/
│   ├── conftest.py             # autouse env-var, limiter reset, JWKS stub & NVIDIA-key-clear fixtures
│   ├── integration/
│   │   └── test_ai_providers_live.py  # opt-in real provider calls, gated by RUN_AI_INTEGRATION_TESTS
│   ├── test_auth.py            # Auth endpoint, JWT (HS256 + ES256), and config-guard tests
│   ├── test_auth_email.py      # /auth/register, /auth/login tests
│   ├── test_diagnosis.py       # Diagnosis interpretation tests
│   ├── test_chat.py            # Chat endpoint tests
│   ├── test_feedback.py        # Feedback endpoint tests
│   ├── test_sync.py            # Sync engine, JWT scoping & reference data tests
│   ├── test_sync_restore_delete.py  # Restore & delete-scan tests
│   ├── test_gemini_model_fallback.py  # Gemini model-name + API-key retry axis tests
│   ├── test_ai_service.py      # Provider routing/fallback tests
│   ├── test_gemini_provider.py # GeminiProvider error-classification tests
│   ├── test_nvidia_provider.py # NvidiaProvider tests
│   └── test_health.py          # Health endpoint test
└── .github/
    └── workflows/
        ├── ci.yml              # GitHub Actions: pytest + ruff
        └── keep-alive.yml      # Pings /health every 10 min to reduce Render free-tier cold starts
```

---

## 3. Important Files

### `DECISIONS.md`
- **Responsibility:** Persistent record of all major architectural and technical decisions (TD-001 through TD-014).
- **Layer:** Documentation & architectural governance

### `main.py`
- **Responsibility:** Application factory; wires SlowAPI middleware + exception handler; includes `auth_router`, `diagnosis_router`, `feedback_router`, and `sync_router`; defines `/health`.
- **Layer:** Application entry point

### `config.py`
- **Responsibility:** Reads env vars once at startup into a singleton `settings` object (`supabase_url`, `supabase_service_role_key`, `supabase_jwt_secret`, `gemini_api_key`, `phone_auth_enabled`).
- **Layer:** Configuration

### `routers/feedback.py`
- **Responsibility:** `POST /feedback` endpoint for bug reports, suggestions, and user feedback; supports guest and authenticated submissions with best-effort persistence.
- **Layer:** Router / presentation & feedback

### `routers/sync.py`
- **Responsibility:** User-scoped sync endpoints (`POST /scans`, `POST /diagnoses`, `POST /escalations`), signed upload URL generation (`POST /scans/{id}/upload-url`), and reference data retrieval (`GET /reference-data`).
- **Layer:** Router / persistence & sync
- **Key objects:**
  - `ScanSyncItem`, `DiagnosisSyncItem`, `EscalationSyncItem`, `SyncResponse`, `UploadUrlResponse`

### `routers/diagnosis.py`
- **Responsibility:** `POST /interpret-diagnosis` endpoint; Gemini prompt formulation; multi-language output generation; Supabase audit logging to `llm_interpretation`.
- **Layer:** Router / AI integration

### `routers/auth.py`
- **Responsibility:** `/auth/request-otp`, `/auth/verify-otp`, `/auth/forgot-password`, and `/auth/account` endpoints; Pydantic schemas; SlowAPI limiter instance; Supabase client factory.
- **Layer:** Router / presentation & auth

### `dependencies/jwt_auth.py`
- **Responsibility:** FastAPI dependencies for JWT verification. `get_current_user_id` tries Supabase's asymmetric signing keys first (`ES256`, via `PyJWKClient` against `SUPABASE_URL/auth/v1/.well-known/jwks.json`, no shared secret), falling back to the legacy shared-secret path (`SUPABASE_JWT_SECRET`, `HS256`) only if the JWKS lookup itself fails. `get_optional_user_id` for guest workflows. See TD-004/TD-017.
- **Layer:** Dependency / security

### `dependencies/gemini.py`
- **Responsibility:** Gemini-specific call logic and its own two-axis retry: candidate model names (`GEMINI_MODEL`, then a fallback list) and candidate API keys (`GEMINI_API_KEY`, then `GEMINI_API_KEY_FALLBACK`). A missing-model error, quota error, or timeout all retry the next model on the SAME key first — quota and timeout both turned out to say nothing reliable about the other models on that key. See TD-019.
- **Layer:** Dependency / AI integration (Gemini-specific)

### `dependencies/ai/`
- **Responsibility:** Provider-independent AI layer. `service.py` is the only thing `routers/diagnosis.py` and `routers/chat.py` call — it routes to `AI_PROVIDER` and falls back to `AI_FALLBACK_PROVIDER` on anything in `errors.py` worth a fallback. `gemini_provider.py` wraps `dependencies/gemini.py`; `nvidia_provider.py` calls NVIDIA's NIM API directly via `httpx`. See TD-018.
- **Layer:** Dependency / AI integration (provider-independent)

---

## 4. Backend Architecture in Practice

| Layer | Status | Notes |
|-------|--------|-------|
| FastAPI application layer | **Implemented** | `main.py` |
| API routers | **Implemented** | `/auth`, `/interpret-diagnosis`, `/feedback`, and sync routers (`/scans`, `/diagnoses`, `/escalations`, `/reference-data`) |
| FastAPI dependency injection | **Implemented** | `get_current_user_id` and `get_optional_user_id` in `dependencies/jwt_auth.py` |
| SlowAPI rate limiting | **Implemented** | On `/auth/request-otp` (3/10min), `/auth/forgot-password` (3/10min), and `/interpret-diagnosis` (20/min) |
| Database persistence layer | **Implemented** | Service-role client direct query/upsert in routers |
| Storage signed URLs | **Implemented** | In `routers/sync.py` for `scan-images` bucket |
| Supabase Auth integration | **Implemented** | Via auth router; Auth Admin API (OTP, reset password, delete user) |
| Gemini integration | **Implemented** | `google-generativeai` with JSON mode in `routers/diagnosis.py` |
| RLS security policies | **Implemented** | Maintained in `sql/schema_and_rls.sql` |
| JWT verification | **Implemented** | ES256 (Supabase signing keys) first, HS256 shared-secret fallback; per-route dependency |
| CORS middleware | **Not present** | Not configured |
| Configuration | **Implemented** | `config.py` singleton |
| Error handling | **Implemented** | Explicit HTTPExceptions; standard `{ "detail": "..." }` responses |

---

## 5. API Surface

| Method | Path | Purpose | Request Body | Response Body | Auth | Status |
|--------|------|---------|-------------|--------------|------|--------|
| `GET` | `/health` | Liveness check | — | `{"status":"ok"}` | None | Implemented |
| `POST` | `/auth/request-otp` | Send OTP | `{email?,phone?}` | `{"message":"..."}` | None | Implemented |
| `POST` | `/auth/verify-otp` | Verify OTP, return tokens | `{email?,phone?,code}` | `{access_token,refresh_token,expires_at}` | None | Implemented |
| `POST` | `/auth/forgot-password` | Request password reset | `{email}` | `{"message":"...","status":"success"}` | None | Implemented |
| `DELETE` | `/auth/account` | Delete user account & sync data | — | `{"status":"success","message":"..."}` | Required Bearer JWT | Implemented |
| `POST` | `/auth/change-email` | Update user email | `{new_email}` | `{"success":true,"message":"...","user":{...}}` | Required Bearer JWT | Implemented |
| `POST` | `/auth/change-phone/request-otp` | Request phone change OTP | `{new_phone_number}` | `{"success":true,"message":"..."}` | Required Bearer JWT | Implemented |
| `POST` | `/auth/change-phone/verify-otp` | Verify phone change OTP | `{new_phone_number,otp_code}` | `{"success":true,"message":"...","user":{...}}` | Required Bearer JWT | Implemented |
| `POST` | `/feedback` | Submit in-app feedback | `{category?,message,user_id?,timestamp?}` | `{"status":"success","message":"..."}` | Optional Bearer JWT | Implemented |
| `POST` | `/interpret-diagnosis` | AI Treatment guidance | `{crop_id,disease_id,confidence,severity,language_code,user_observations?}` | `{summary,what_to_do,what_to_avoid,recheck_after_days,interpretation_id}` | Optional Bearer JWT | Implemented |
| `POST` | `/scans` | Sync scan row | `{local_scan_id,crop_id?,image_url?,status?,captured_at?}` | `{status,remote_id,local_entity_id}` | Required Bearer JWT | Implemented |
| `POST` | `/diagnoses` | Sync diagnosis row | `{local_diagnosis_id,scan_id?,disease_id?,confidence,severity?,result_state?,treatment_source?,diagnosed_at?}` | `{status,remote_id,local_entity_id}` | Required Bearer JWT | Implemented |
| `POST` | `/escalations` | Sync escalation row | `{local_escalation_id,scan_id?,diagnosis_id?,channel?,recipient_contact?,notes?,shared_at?}` | `{status,remote_id,local_entity_id}` | Required Bearer JWT | Implemented |
| `POST` | `/scans/{id}/upload-url` | Get signed upload URL | — | `{upload_url,path,token?}` | Required Bearer JWT | Implemented |
| `GET` | `/reference-data` | Pull reference data | Query: `since?` | `{crops,diseases,treatment_guidelines,model_versions}` | Required Bearer JWT | Implemented |

---

## 6. Implemented Request / Runtime Flows

### Sync engine flow
```
POST /scans | /diagnoses | /escalations  (Bearer JWT)
  → get_current_user_id validates token and extracts user_id (401 if missing/invalid)
  → Pydantic validation (422 if invalid)
  → Server stamps record["user_id"] = user_id (ignoring payload user_id)
  → Supabase table upsert on conflict (user_id, local_entity_id)
  → return SyncResponse(status="synced", remote_id=..., local_entity_id=...)
```

### Storage signed URL flow
```
POST /scans/{id}/upload-url  (Bearer JWT)
  → get_current_user_id validates token (401 if invalid)
  → Path generated: {user_id}/{scan_id}.jpg
  → Supabase Storage create_signed_upload_url called
  → return UploadUrlResponse(upload_url=..., path=...)
  → Flutter client uploads image bytes directly to Supabase Storage
```

### Treatment guidance flow
```
POST /interpret-diagnosis  {crop_id, disease_id, confidence, severity, language_code, user_observations}
  → Optional Bearer JWT validated via get_optional_user_id (extracts user_id or None)
  → DiagnosisInterpretationRequest validation (422 if invalid)
  → SlowAPI rate-limit check (429 if >20/min)
  → Google Gemini 1.5 Flash called with structured JSON mode
  → Best-effort Supabase insert to llm_interpretation table
  → return DiagnosisInterpretationResponse
```

### OTP auth flow
```
POST /auth/request-otp → Rate-limited (3/10min) → Supabase Auth OTP sent
POST /auth/verify-otp → Supabase verify → returns access_token + refresh_token
```

---

## 7. Authentication & Authorization

| Aspect | Current implementation |
|--------|----------------------|
| Auth method | Supabase OTP (email always active; phone gated via `PHONE_AUTH_ENABLED`) |
| JWT verification | `dependencies/jwt_auth.py` with `get_current_user_id` and `get_optional_user_id` |
| JWT algorithm | `ES256` (Supabase's own signing keys, via JWKS) tried first, `HS256` shared-secret as fallback; token header `alg` never single-handedly decides trust — see TD-017 |
| Protected routes | `/scans`, `/diagnoses`, `/escalations`, `/scans/{id}/upload-url`, `/reference-data` |
| Scoping guarantee | Server overwrites `record["user_id"] = jwt_user_id` on every upsert |
| Defense-in-Depth | Row-Level Security (RLS) enabled on all user tables in `sql/schema_and_rls.sql` |

---

## 8. Database & Persistence

| Aspect | Status |
|--------|--------|
| Database | Supabase Postgres & Storage |
| Client | `supabase` client initialized lazily with service-role key |
| Tables accessed | `scan`, `diagnosis`, `escalation`, `crop`, `disease`, `treatment_guideline`, `model_version`, `llm_interpretation` |
| Storage Bucket | `scan-images` (scoped paths: `{user_id}/{scan_id}.jpg`) |
| Schema & RLS Script | `sql/schema_and_rls.sql` |

---

## 9. Data Models & Schemas

| Model | File | Layer | Used by |
|-------|------|-------|---------|
| `OtpRequestBody`, `OtpVerifyBody`, `ForgotPasswordRequestBody`, `ForgotPasswordResponse`, `DeleteAccountResponse`, `ChangeEmailRequestBody`, `ChangeEmailResponse`, `ChangePhoneRequestOtpBody`, `ChangePhoneRequestOtpResponse`, `ChangePhoneVerifyOtpBody`, `ChangePhoneVerifyOtpResponse`, `UserProfileSummary` | `routers/auth.py` | API schema | Auth endpoints |
| `FeedbackRequestBody`, `FeedbackResponse` | `routers/feedback.py` | API schema | Feedback endpoint |
| `DiagnosisInterpretationRequest`, `DiagnosisInterpretationResponse` | `routers/diagnosis.py` | API schema | Treatment guidance |
| `ScanSyncItem`, `DiagnosisSyncItem`, `EscalationSyncItem` | `routers/sync.py` | API schema | Sync endpoints |
| `SyncResponse`, `UploadUrlResponse` | `routers/sync.py` | API schema | Sync & Upload URL endpoints |

---

## 10. External Services & Integrations

| Service | Purpose | File | Status | Notes |
|---------|---------|------|--------|-------|
| Supabase Auth (Admin API) | Send/verify OTP, reset password, delete user, update email/phone | `routers/auth.py` | **Implemented** | Service-role key |
| Supabase Postgres | Sync, audit, and feedback persistence | `routers/sync.py`, `routers/diagnosis.py`, `routers/feedback.py` | **Implemented** | Idempotent upserts & inserts |
| Supabase Storage | Direct image uploads | `routers/sync.py` | **Implemented** | Signed upload URLs |
| Gemini API | Treatment interpretation, chat | `dependencies/gemini.py` (via `dependencies/ai/gemini_provider.py`) | **Implemented** | Model + key fallback; see TD-019 |
| NVIDIA NIM API | Fallback treatment interpretation, chat | `dependencies/ai/nvidia_provider.py` | **Implemented** | `meta/llama-3.1-8b-instruct` default; see TD-018 |

---

## 11. Configuration & Environment

| Variable | Purpose | Read in | Default |
|----------|---------|---------|---------|
| `SUPABASE_URL` | Supabase project URL | `config.py` | `""` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key | `config.py` | `""` |
| `SUPABASE_JWT_SECRET` | Legacy HS256 fallback only — the primary path verifies against Supabase's own published public key (JWKS), no secret needed | `config.py` | `""` |
| `AI_PROVIDER` | Primary AI provider (`gemini` \| `nvidia`) | `config.py` | `gemini` |
| `AI_FALLBACK_PROVIDER` | Fallback AI provider, tried if the primary can't serve the request | `config.py` | `nvidia` |
| `GEMINI_API_KEY` | Google Gemini API key | `config.py` | `""` |
| `GEMINI_API_KEY_FALLBACK` | A second Gemini key (e.g. a separate Google account), tried when the primary is out of quota — independent of `AI_FALLBACK_PROVIDER` | `config.py` | `""` |
| `GEMINI_MODEL` | Overrides the Gemini model name without a redeploy | `config.py` | `""` (falls back to candidate list) |
| `NVIDIA_API_KEY` | NVIDIA NIM API key, from build.nvidia.com | `config.py` | `""` |
| `NVIDIA_MODEL` | Overrides the NVIDIA model name | `config.py` | `meta/llama-3.1-8b-instruct` |
| `PHONE_AUTH_ENABLED` | Enable phone OTP (`"true"` exact lowercase only) | `config.py` | `False` |

---

## 12. Testing

### Structure
```
tests/
├── conftest.py                     # autouse env-var, limiter reset, JWKS stub & NVIDIA-key-clear fixtures
├── integration/
│   └── test_ai_providers_live.py   # opt-in real provider calls, gated by RUN_AI_INTEGRATION_TESTS
├── test_health.py                  # GET /health
├── test_auth.py                    # OTP, password reset, account deletion, change email/phone, phone flag, rate limit, JWT (HS256 + ES256), config-guard tests
├── test_auth_email.py              # /auth/register, /auth/login
├── test_diagnosis.py               # Diagnosis interpretation, JWT user scoping, validation, 500 handling
├── test_chat.py                    # Chat endpoint
├── test_feedback.py                # Guest/auth feedback, validation, best-effort resilience
├── test_sync.py                    # Auth enforcement, user scoping, idempotent upserts, upload URL, reference data
├── test_sync_restore_delete.py     # Restore & delete-scan
├── test_gemini_model_fallback.py   # Gemini model-name + API-key retry axes
├── test_ai_service.py              # Provider routing/fallback (service.generate())
├── test_gemini_provider.py         # GeminiProvider error classification
└── test_nvidia_provider.py         # NvidiaProvider
```

**Total active passing tests:** 204 passed, 2 skipped (opt-in integration tests) — see `README.md`'s Testing section for the exact command.

---

## 13. Known Gaps / Discrepancies

| # | Discrepancy |
|---|-------------|
| 1 | Architecture: CORS not explicitly described → Current: CORS not configured in `main.py` |
| 2 | `verify_otp` has no rate limiting (per design) |

---

## 14. AI Implementation Rules

1. **Treat the actual backend source code as the only source of truth for what currently exists.**
2. Treat `CropCare_System_Architecture.md` as the intended architectural direction.
3. Treat `CropCare_Build_Checklist.md` as the planned implementation sequence.
4. Preserve accepted architectural decisions: JWT verified via Supabase's ES256 signing keys first, HS256 shared secret as fallback (TD-017); user_id stamped server-side on sync; phone-auth feature flag; signed upload URLs for storage; routers call `dependencies/ai/service.py`, never a provider SDK directly (TD-018).
5. When adding new endpoints, use `get_current_user_id` for protected routes.
6. Supabase's `crop`/`disease`/`treatment_guideline`/`model_version` reference tables are NOT seeded by any code path in this repo — see TD-021. Don't assume a fresh Supabase project has them populated.
7. **Never expose secrets, API keys, or env var values in documentation, logs, or responses.**
8. Update `CODEBASE_MAP.md` and `DECISIONS.md` when a future implementation materially changes the documented backend structure.
