# CODEBASE_MAP.md — CropCare Backend

> **Purpose:** Gives a fresh AI coding agent an accurate, compact understanding of the **current** CropCare backend.
> **Source-of-truth hierarchy:** Actual source code → `CropCare_System_Architecture.md` → `CropCare_Build_Checklist.md`.
> **Last updated:** 2026-08-24

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
| JWT auth dependency | **Implemented** | `dependencies/jwt_auth.py` (`get_current_user_id`, `get_optional_user_id`) |
| Rate limiting | **Implemented** | SlowAPI on `/auth/request-otp` (3/10min) and `/interpret-diagnosis` (20/min) |
| CORS middleware | **Not present** | Not configured in `main.py` |
| `/scans`, `/diagnoses`, `/escalations` sync routers | **Not present** | No files; no routes |
| `POST /scans/{id}/upload-url` | **Not present** | Signed URL generation not implemented |
| `GET /reference-data` | **Not present** | No route |
| Supabase Postgres access | **Partially implemented** | Service-role client used for Auth Admin and `llm_interpretation` audit table |
| Database tables / migrations | **Not present in repo** | No DDL, no ORM, no migration files |
| Repository/service/use-case layers | **Not present** | Flat structure only |
| ML / on-device inference | **Not present in backend** | Stays on Flutter client |
| Gemini integration | **Implemented** | `google-generativeai` with JSON mode in `routers/diagnosis.py` |
| Image upload / storage | **Not present** | No route, no Supabase Storage interaction |
| Settings/profile endpoints | **Not present** | — |
| CI pipeline | **Implemented** | GitHub Actions: `pytest` + `ruff check` on push/PR to `main` |

**Immediate development priorities (from build checklist, current phase):**
- Auth is partially done; remaining: apply `get_current_user_id` to sync routes, write RLS policies
- Next after diagnosis: sync engine endpoints (`POST /scans`, `POST /diagnoses`, `POST /escalations`, `POST /scans/{id}/upload-url`, `GET /reference-data`)

---

## 2. Repository Structure

```
cropcare-backend/
├── main.py                     # FastAPI app entry point (wires auth & diagnosis routers)
├── config.py                   # Settings singleton (env var reader)
├── requirements.txt            # Python dependencies (includes supabase, pyjwt, google-generativeai)
├── pytest.ini                  # Test runner config
├── DECISIONS.md                # Architectural and technical decisions log
├── .gitignore                  # Ignores .env, .venv, __pycache__
├── routers/
│   ├── __init__.py
│   ├── auth.py                 # /auth router, limiter, Pydantic schemas
│   └── diagnosis.py            # /interpret-diagnosis endpoint (Gemini integration)
├── dependencies/
│   ├── __init__.py
│   └── jwt_auth.py             # get_current_user_id & get_optional_user_id FastAPI dependencies
├── tests/
│   ├── conftest.py             # autouse env-var & limiter reset fixture
│   ├── test_auth.py            # Auth endpoint and JWT tests
│   ├── test_diagnosis.py       # Diagnosis interpretation & Gemini tests
│   └── test_health.py         # Health endpoint test
└── .github/
    └── workflows/
        └── ci.yml              # GitHub Actions: pytest + ruff
```

**Not present (architecture expects these):** `services/`, `repositories/`, `models/`, `schemas/`, `migrations/`, `alembic/`

---

## 3. Important Files

### `DECISIONS.md`
- **Responsibility:** Persistent record of all major architectural and technical decisions (TD-001 through TD-009).
- **Layer:** Documentation & architectural governance

### `main.py`
- **Responsibility:** Application factory; wires SlowAPI middleware + exception handler; includes `auth_router` and `diagnosis_router`; defines `/health`.
- **Layer:** Application entry point
- **Key objects:** `app` (FastAPI instance)
- **Dependencies:** `routers.auth.limiter`, `routers.auth.router`, `routers.diagnosis.router`

### `config.py`
- **Responsibility:** Reads env vars once at startup into a singleton `settings` object.
- **Layer:** Configuration
- **Key class:** `_Settings`
- **Attributes read:**
  - `supabase_url` ← `SUPABASE_URL`
  - `supabase_service_role_key` ← `SUPABASE_SERVICE_ROLE_KEY`
  - `supabase_jwt_secret` ← `SUPABASE_JWT_SECRET`
  - `gemini_api_key` ← `GEMINI_API_KEY`
  - `phone_auth_enabled` ← `PHONE_AUTH_ENABLED` (default `False`)
- **`__repr__` intentionally omits secrets.**
- **Consumers:** `routers/auth.py`, `routers/diagnosis.py`, `dependencies/jwt_auth.py`

### `routers/auth.py`
- **Responsibility:** `/auth/request-otp` and `/auth/verify-otp` endpoints; Pydantic schemas; SlowAPI limiter instance; Supabase client factory.
- **Layer:** Router / presentation
- **Key objects:**
  - `limiter` — `Limiter(key_func=_identifier_key)` — exported to `main.py` and `routers/diagnosis.py`
  - `router` — `APIRouter(prefix="/auth", tags=["auth"])`
  - `OtpRequestBody`, `OtpVerifyBody`
- **Key functions:** `_identifier_key`, `_get_supabase`, `_validate_identifier`, `_check_phone_flag`, `request_otp`, `verify_otp`

### `routers/diagnosis.py`
- **Responsibility:** `POST /interpret-diagnosis` endpoint; Gemini prompt formulation; multi-language output generation; Supabase audit logging to `llm_interpretation`.
- **Layer:** Router / presentation & AI integration
- **Key objects:**
  - `router` — `APIRouter(tags=["diagnosis"])`
  - `DiagnosisInterpretationRequest` — Pydantic request schema (`crop_id`, `disease_id`, `confidence`, `severity`, `language_code`, `user_observations`)
  - `DiagnosisInterpretationResponse` — Pydantic response schema (`summary`, `what_to_do`, `what_to_avoid`, `recheck_after_days`, `interpretation_id`)
- **Key functions:**
  - `_build_prompt(body)` — formats structured instructions in target language (`en`, `si`, `ta`)
  - `_log_to_supabase(data)` — best-effort insert into Supabase `llm_interpretation` table
  - `interpret_diagnosis(...)` — handler with `20/minute` rate limit and optional JWT user scoping

### `dependencies/jwt_auth.py`
- **Responsibility:** FastAPI dependencies for JWT verification.
- **Layer:** Dependency / cross-cutting
- **Key functions:**
  - `get_current_user_id(credentials)` — strict token validation (HS256 pinned); returns `user_id` or 401.
  - `get_optional_user_id(credentials)` — validates Bearer JWT if provided; returns `None` if omitted; 401 if invalid.

### `tests/conftest.py`
- **Responsibility:** `autouse` fixture; sets safe dummy env vars (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `GEMINI_API_KEY`); resets SlowAPI limiter storage before each test.

### `tests/test_diagnosis.py`
- **Responsibility:** Test suite for `POST /interpret-diagnosis`.
- **Covers:** Valid guidance generation, authenticated JWT scoping, validation errors (422), missing API key (500), API quota/network failure (500), malformed JSON (500), missing guidance fields (500).

### `tests/test_auth.py`
- **Responsibility:** Auth and JWT dependency tests. All Supabase calls mocked.

### `tests/test_health.py`
- **Responsibility:** Single test: `GET /health` → 200 + `{"status": "ok"}`.

### `.github/workflows/ci.yml`
- **Responsibility:** CI pipeline. Runs `python -m pytest` and `ruff check .` on push/PR to `main`. Python 3.12.

---

## 4. Backend Architecture in Practice

| Layer | Status | Notes |
|-------|--------|-------|
| FastAPI application layer | **Implemented** | `main.py` |
| API routers | **Partially implemented** | `/auth` and `/interpret-diagnosis` exist |
| FastAPI dependency injection | **Implemented** | `get_current_user_id` and `get_optional_user_id` in `dependencies/jwt_auth.py` |
| SlowAPI rate limiting | **Implemented** | On `/auth/request-otp` and `/interpret-diagnosis` |
| Application/service layer | **Not present** | No `services/` package; flat architecture |
| Domain/business logic layer | **Not present** | Logic contained in routers |
| Repository layer | **Not present** | No repository classes |
| Database/data-access layer | **Partially implemented** | Best-effort Supabase client write to `llm_interpretation` |
| Supabase Auth integration | **Implemented** | Via auth router; Auth Admin API |
| Gemini integration | **Implemented** | `google-generativeai` with JSON mode in `routers/diagnosis.py` |
| JWT verification | **Implemented** | HS256 pinned; per-route dependency |
| CORS middleware | **Not present** | Not configured |
| Configuration | **Implemented** | `config.py` singleton |
| Pydantic validation | **Implemented** | In `routers/auth.py` and `routers/diagnosis.py` |
| Error handling | **Implemented** | Explicit HTTPExceptions; standard `{ "detail": "..." }` responses |

---

## 5. API Surface

| Method | Path | Purpose | Request Body | Response Body | Auth | Status |
|--------|------|---------|-------------|--------------|------|--------|
| `GET` | `/health` | Liveness check | — | `{"status":"ok"}` | None | Implemented |
| `POST` | `/auth/request-otp` | Send OTP | `{email?,phone?}` | `{"message":"..."}` | None | Implemented |
| `POST` | `/auth/verify-otp` | Verify OTP, return tokens | `{email?,phone?,code}` | `{access_token,refresh_token,expires_at}` | None | Implemented |
| `POST` | `/interpret-diagnosis` | AI Treatment guidance | `{crop_id,disease_id,confidence,severity,language_code,user_observations?}` | `{summary,what_to_do,what_to_avoid,recheck_after_days,interpretation_id}` | Optional Bearer JWT | Implemented |

### `/interpret-diagnosis` detail
- Router: `routers/diagnosis.py`
- Rate limit: 20/minute keyed on `user_id` (if logged in) or client IP
- Auth: `Depends(get_optional_user_id)` — works for guests and authenticated farmers
- Gemini: `gemini-1.5-flash` with `response_mime_type="application/json"`
- Supabase: records audit log into `llm_interpretation` table (non-blocking)
- Errors: 422 (validation error), 429 (rate limited), 500 (missing API key, Gemini failure, malformed JSON)

---

## 6. Implemented Request / Runtime Flows

### Treatment guidance flow
```
POST /interpret-diagnosis  {crop_id, disease_id, confidence, severity, language_code, user_observations}
  → Optional Bearer JWT validated via get_optional_user_id (extracts user_id or None)
  → DiagnosisInterpretationRequest validation (422 if invalid)
  → request.state.rate_limit_key = user_id or client_ip
  → SlowAPI rate-limit check (429 if >20/min)
  → Prompt built with target language localization (en, si, ta)
  → Google Gemini (gemini-1.5-flash) called with JSON mode
  → Response parsed into summary, what_to_do, what_to_avoid, recheck_after_days
  → UUID generated for interpretation_id
  → Best-effort Supabase insert to llm_interpretation table
  → return DiagnosisInterpretationResponse
```

### OTP request flow
```
POST /auth/request-otp  {email or phone}
  → OtpRequestBody Pydantic validation (422 on type errors)
  → _validate_identifier() — exactly-one check (400 on violation)
  → if phone: _check_phone_flag() (403 if flag off) — stops here
  → request.state.rate_limit_key = id_value
  → SlowAPI rate-limit check (429 if exceeded)
  → _get_supabase() — create_client(supabase_url, service_role_key)
  → supabase.auth.sign_in_with_otp({identifier})
  → any exception silently swallowed
  → return {"message": "If that identifier is registered, an OTP has been sent."}
```

### OTP verify flow
```
POST /auth/verify-otp  {email or phone, code}
  → OtpVerifyBody Pydantic validation
  → _validate_identifier() (400 on violation)
  → if phone: _check_phone_flag() (403)
  → _get_supabase() → supabase.auth.verify_otp({identifier, token:code, type})
  → any exception → raise HTTPException 401
  → response.session is None → raise HTTPException 401
  → return {access_token, refresh_token, expires_at}
```

### Health flow
```
GET /health → {"status": "ok"}
```

---

## 7. Authentication & Authorization

| Aspect | Current implementation |
|--------|----------------------|
| Auth method | Supabase OTP (email always active; phone gated) |
| Email OTP | **Implemented** — `supabase.auth.sign_in_with_otp` via service-role key |
| Phone OTP | **Code exists** — gated by `PHONE_AUTH_ENABLED=true`; off by default (cost control) |
| JWT verification | **Implemented** as `get_current_user_id` and `get_optional_user_id` |
| JWT algorithm | HS256, pinned; token's own `alg` header never trusted |
| JWT claims required | `exp`, `sub` |
| JWT secret source | `settings.supabase_jwt_secret` ← `SUPABASE_JWT_SECRET` |
| Optional auth usage | Applied to `POST /interpret-diagnosis` |

---

## 8. Database & Persistence

| Aspect | Status |
|--------|--------|
| Database | Supabase Postgres |
| Supabase client | `supabase` package installed; client created lazily via service-role key |
| Tables accessed | `llm_interpretation` (audit insert on diagnosis) |
| Repositories | Direct client usage in routers |

---

## 9. Data Models & Schemas

| Model | File | Layer | Fields | Used by |
|-------|------|-------|--------|---------|
| `OtpRequestBody` | `routers/auth.py` | API schema | `email: EmailStr \| None`, `phone: str \| None` | `request_otp` endpoint |
| `OtpVerifyBody` | `routers/auth.py` | API schema | `email: EmailStr \| None`, `phone: str \| None`, `code: str` | `verify_otp` endpoint |
| `DiagnosisInterpretationRequest` | `routers/diagnosis.py` | API schema | `crop_id`, `disease_id`, `confidence`, `severity`, `language_code`, `user_observations` | `interpret_diagnosis` endpoint |
| `DiagnosisInterpretationResponse` | `routers/diagnosis.py` | API schema | `summary`, `what_to_do`, `what_to_avoid`, `recheck_after_days`, `interpretation_id` | `interpret_diagnosis` endpoint |

---

## 10. ML / Diagnosis Integration Points

- **ML inference:** Stays on-device on the Flutter client.
- **Diagnosis guidance:** `POST /interpret-diagnosis` generates expert agronomist guidance via Gemini 1.5 Flash.
- **Languages supported:** English (`en`), Sinhala (`si`), Tamil (`ta`), and fallback to any ISO code.
- **Supabase audit:** Writes to `llm_interpretation` table with `user_id`, inputs, guidance, and `interpretation_id`.

---

## 11. External Services & Integrations

| Service | Purpose | File | Status | Notes |
|---------|---------|------|--------|-------|
| Supabase Auth (Admin API) | Send/verify OTP | `routers/auth.py` | **Implemented** | Service-role key |
| Supabase Postgres | Audit log (`llm_interpretation`) | `routers/diagnosis.py` | **Implemented** | Service-role key |
| Gemini API | Treatment interpretation | `routers/diagnosis.py` | **Implemented** | `gemini-1.5-flash` with JSON mode |

---

## 12. Middleware, Dependencies & Cross-Cutting Concerns

| Concern | Implementation | File |
|---------|---------------|------|
| Rate limiting | SlowAPI `Limiter`, `SlowAPIMiddleware`, `RateLimitExceeded` handler | `main.py`, `routers/auth.py` |
| JWT auth | `get_current_user_id`, `get_optional_user_id` | `dependencies/jwt_auth.py` |
| Request validation | Pydantic `BaseModel` on request bodies | `routers/auth.py`, `routers/diagnosis.py` |
| Environment/config | `_Settings` singleton | `config.py` |

---

## 13. Configuration & Environment

| Variable | Purpose | Read in | Default |
|----------|---------|---------|---------|
| `SUPABASE_URL` | Supabase project URL | `config.py` | `""` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key | `config.py` | `""` |
| `SUPABASE_JWT_SECRET` | Secret to verify Supabase-issued JWTs | `config.py` | `""` |
| `GEMINI_API_KEY` | Google Gemini API key | `config.py` | `""` |
| `PHONE_AUTH_ENABLED` | Enable phone OTP (`"true"` exact lowercase only) | `config.py` | `False` |

---

## 14. Testing

### Structure
```
tests/
├── conftest.py         # autouse env-var & limiter reset fixture
├── test_health.py      # 1 test (GET /health)
├── test_auth.py        # 13 tests (OTP, phone flag, rate limit, JWT validation)
└── test_diagnosis.py   # 8 tests (Gemini generation, JWT user scoping, validation, 500 error handling)
```

**Total active passing tests:** 22 passed.

---

## 15. Existing Backend Conventions

| Convention | Pattern in use |
|---|---|
| Naming | `snake_case` throughout; private helpers prefixed with `_` |
| File organisation | One router per feature in `routers/`; dependencies in `dependencies/` |
| Router registration | `APIRouter(tags=[...])` in router file; imported and included in `main.py` |
| Dependency injection | FastAPI `Depends()` pattern |
| Rate limiting | `@limiter.limit(...)` decorator; `request.state.rate_limit_key` set in handler |
| Pydantic schemas | Explicit field constraints (`min_length`, `ge`, `le`) |
| Error handling | `HTTPException` with explicit `status_code` and `detail` |
| Configuration | All config via `config.settings` singleton |
| JWT algorithm | Pinned to `HS256`; token's `alg` header never trusted |

---

## 16. Known Gaps / Discrepancies

| # | Discrepancy |
|---|-------------|
| 1 | Architecture: `/scans`, `/diagnoses`, `/escalations`, `/reference-data` routers → Current: **not yet implemented (next phase)** |
| 2 | Architecture: `POST /scans/{id}/upload-url` for signed Storage URLs → Current: **not yet implemented** |
| 3 | Architecture: CORS not explicitly described → Current: CORS not configured in `main.py` |
| 4 | `verify_otp` has no rate limiting (per design) |

---

## 17. AI Implementation Rules

1. **Treat the actual backend source code as the only source of truth for what currently exists.**
2. Treat `CropCare_System_Architecture.md` as the intended architectural direction.
3. Treat `CropCare_Build_Checklist.md` as the planned implementation sequence.
4. Preserve accepted architectural decisions: JWT pinned to HS256; information-hiding in auth errors; phone-auth feature flag; per-identifier rate-limit key.
5. The established pattern for a new protected endpoint: new router file → `get_current_user_id` dependency → include in `main.py`.
6. When uncertain about existing behavior, read the relevant source file before making a change.
7. **Never expose secrets, API keys, or env var values in documentation, logs, or responses.**
8. Update `CODEBASE_MAP.md` when a future implementation materially changes the documented backend structure.
