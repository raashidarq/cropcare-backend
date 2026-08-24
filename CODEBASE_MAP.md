# CODEBASE_MAP.md — CropCare Backend

> **Purpose:** Gives a fresh AI coding agent an accurate, compact understanding of the **current** CropCare backend.
> **Source-of-truth hierarchy:** Actual source code → `CropCare_System_Architecture.md` → `CropCare_Build_Checklist.md`.
> **Last updated:** 2026-08-25

---

## 1. Current Status

| Area | Status | Notes |
|------|--------|-------|
| FastAPI application | **Implemented** | `main.py`, `uvicorn`-compatible, deployable to Render |
| Health endpoint `GET /health` | **Implemented** | Returns `{"status": "ok"}` |
| Auth router `/auth/*` | **Implemented** | Email OTP and phone OTP (gated); see §7 |
| `POST /auth/request-otp` | **Implemented** | Email always active; phone gated by `PHONE_AUTH_ENABLED` flag |
| `POST /auth/verify-otp` | **Implemented** | Relays Supabase `access_token`, `refresh_token`, `expires_at` |
| Treatment Guidance `POST /interpret-diagnosis` | **Implemented** | Google Gemini 1.5 Flash structured guidance + Supabase audit logging |
| Sync router `/scans`, `/diagnoses`, `/escalations` | **Implemented** | `routers/sync.py` — idempotent upserts strictly scoped to JWT `user_id` |
| `POST /scans/{id}/upload-url` | **Implemented** | Signed upload URL generation for Supabase Storage (`scan-images` bucket) |
| `GET /reference-data` | **Implemented** | Versioned pull of crops, diseases, guidelines, and models (`since` filter) |
| JWT auth dependency | **Implemented** | `dependencies/jwt_auth.py` (`get_current_user_id`, `get_optional_user_id`) |
| Rate limiting | **Implemented** | SlowAPI on `/auth/request-otp` (3/10min) and `/interpret-diagnosis` (20/min) |
| CORS middleware | **Not present** | Not configured in `main.py` |
| Supabase Postgres access | **Implemented** | Service-role client used for Auth Admin, sync upserts, and audit table |
| Database schema & RLS policies | **Applied** | DDL and RLS security policies applied directly in Supabase Cloud |
| Repository/service/use-case layers | **Not present** | Flat structure only |
| ML / on-device inference | **Not present in backend** | Stays on Flutter client |
| Gemini integration | **Implemented** | `google-generativeai` with JSON mode in `routers/diagnosis.py` |
| Image upload / storage | **Implemented** | Direct-to-Storage upload pattern via signed upload URLs |
| Settings/profile endpoints | **Not present** | — |
| CI pipeline | **Implemented** | GitHub Actions: `pytest` + `ruff check` on push/PR to `main` |

---

## 2. Repository Structure

```
cropcare-backend/
├── main.py                     # FastAPI app entry point (wires auth, diagnosis, sync routers)
├── config.py                   # Settings singleton (env var reader)
├── requirements.txt            # Python dependencies (includes supabase, pyjwt, google-generativeai)
├── pytest.ini                  # Test runner config
├── DECISIONS.md                # Architectural and technical decisions log (TD-001 to TD-012)
├── .gitignore                  # Ignores .env, .venv, __pycache__
├── routers/
│   ├── __init__.py
│   ├── auth.py                 # /auth router, limiter, Pydantic schemas
│   ├── diagnosis.py            # /interpret-diagnosis endpoint (Gemini integration)
│   └── sync.py                 # Sync endpoints (/scans, /diagnoses, /escalations, /reference-data, upload-url)
├── dependencies/
│   ├── __init__.py
│   └── jwt_auth.py             # get_current_user_id & get_optional_user_id FastAPI dependencies
├── tests/
│   ├── conftest.py             # autouse env-var & limiter reset fixture
│   ├── test_auth.py            # Auth endpoint and JWT tests (13 tests)
│   ├── test_diagnosis.py       # Diagnosis interpretation & Gemini tests (8 tests)
│   ├── test_sync.py            # Sync engine, JWT scoping & reference data tests (12 tests)
│   └── test_health.py         # Health endpoint test (1 test)
└── .github/
    └── workflows/
        └── ci.yml              # GitHub Actions: pytest + ruff
```

---

## 3. Important Files

### `DECISIONS.md`
- **Responsibility:** Persistent record of all major architectural and technical decisions (TD-001 through TD-012).
- **Layer:** Documentation & architectural governance

### `main.py`
- **Responsibility:** Application factory; wires SlowAPI middleware + exception handler; includes `auth_router`, `diagnosis_router`, and `sync_router`; defines `/health`.
- **Layer:** Application entry point

### `config.py`
- **Responsibility:** Reads env vars once at startup into a singleton `settings` object (`supabase_url`, `supabase_service_role_key`, `supabase_jwt_secret`, `gemini_api_key`, `phone_auth_enabled`).
- **Layer:** Configuration

### `routers/sync.py`
- **Responsibility:** User-scoped sync endpoints (`POST /scans`, `POST /diagnoses`, `POST /escalations`), signed upload URL generation (`POST /scans/{id}/upload-url`), and reference data retrieval (`GET /reference-data`).
- **Layer:** Router / persistence & sync
- **Key objects:**
  - `ScanSyncItem`, `DiagnosisSyncItem`, `EscalationSyncItem`, `SyncResponse`, `UploadUrlResponse`

### `routers/diagnosis.py`
- **Responsibility:** `POST /interpret-diagnosis` endpoint; Gemini prompt formulation; multi-language output generation; Supabase audit logging to `llm_interpretation`.
- **Layer:** Router / AI integration

### `routers/auth.py`
- **Responsibility:** `/auth/request-otp` and `/auth/verify-otp` endpoints; Pydantic schemas; SlowAPI limiter instance; Supabase client factory.
- **Layer:** Router / presentation & auth

### `dependencies/jwt_auth.py`
- **Responsibility:** FastAPI dependencies for JWT verification (`get_current_user_id` strictly enforcing `HS256`, `get_optional_user_id` for guest workflows).
- **Layer:** Dependency / security

---

## 4. Backend Architecture in Practice

| Layer | Status | Notes |
|-------|--------|-------|
| FastAPI application layer | **Implemented** | `main.py` |
| API routers | **Implemented** | `/auth`, `/interpret-diagnosis`, and sync routers (`/scans`, `/diagnoses`, `/escalations`, `/reference-data`) |
| FastAPI dependency injection | **Implemented** | `get_current_user_id` and `get_optional_user_id` in `dependencies/jwt_auth.py` |
| SlowAPI rate limiting | **Implemented** | On `/auth/request-otp` and `/interpret-diagnosis` |
| Database persistence layer | **Implemented** | Service-role client direct query/upsert in routers |
| Storage signed URLs | **Implemented** | In `routers/sync.py` for `scan-images` bucket |
| Supabase Auth integration | **Implemented** | Via auth router; Auth Admin API |
| Gemini integration | **Implemented** | `google-generativeai` with JSON mode in `routers/diagnosis.py` |
| RLS security policies | **Implemented** | Maintained in `sql/schema_and_rls.sql` |
| JWT verification | **Implemented** | HS256 pinned; per-route dependency |
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
| JWT algorithm | `HS256`, explicitly pinned; token header `alg` is ignored |
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
| `OtpRequestBody`, `OtpVerifyBody` | `routers/auth.py` | API schema | Auth endpoints |
| `DiagnosisInterpretationRequest`, `DiagnosisInterpretationResponse` | `routers/diagnosis.py` | API schema | Treatment guidance |
| `ScanSyncItem`, `DiagnosisSyncItem`, `EscalationSyncItem` | `routers/sync.py` | API schema | Sync endpoints |
| `SyncResponse`, `UploadUrlResponse` | `routers/sync.py` | API schema | Sync & Upload URL endpoints |

---

## 10. External Services & Integrations

| Service | Purpose | File | Status | Notes |
|---------|---------|------|--------|-------|
| Supabase Auth (Admin API) | Send/verify OTP | `routers/auth.py` | **Implemented** | Service-role key |
| Supabase Postgres | Sync & audit persistence | `routers/sync.py`, `routers/diagnosis.py` | **Implemented** | Idempotent upserts |
| Supabase Storage | Direct image uploads | `routers/sync.py` | **Implemented** | Signed upload URLs |
| Gemini API | Treatment interpretation | `routers/diagnosis.py` | **Implemented** | `gemini-1.5-flash` with JSON mode |

---

## 11. Configuration & Environment

| Variable | Purpose | Read in | Default |
|----------|---------|---------|---------|
| `SUPABASE_URL` | Supabase project URL | `config.py` | `""` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key | `config.py` | `""` |
| `SUPABASE_JWT_SECRET` | Secret to verify Supabase-issued JWTs | `config.py` | `""` |
| `GEMINI_API_KEY` | Google Gemini API key | `config.py` | `""` |
| `PHONE_AUTH_ENABLED` | Enable phone OTP (`"true"` exact lowercase only) | `config.py` | `False` |

---

## 12. Testing

### Structure
```
tests/
├── conftest.py         # autouse env-var & limiter reset fixture
├── test_health.py      # 1 test (GET /health)
├── test_auth.py        # 13 tests (OTP, phone flag, rate limit, JWT validation)
├── test_diagnosis.py   # 8 tests (Gemini generation, JWT user scoping, validation, 500 error handling)
└── test_sync.py        # 12 tests (Auth enforcement, user scoping, idempotent upserts, upload URL, reference data)
```

**Total active passing tests:** 34 passed.

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
4. Preserve accepted architectural decisions: JWT pinned to HS256; user_id stamped server-side on sync; phone-auth feature flag; signed upload URLs for storage.
5. When adding new endpoints, use `get_current_user_id` for protected routes.
6. **Never expose secrets, API keys, or env var values in documentation, logs, or responses.**
7. Update `CODEBASE_MAP.md` and `DECISIONS.md` when a future implementation materially changes the documented backend structure.
