# CropCare — System Architecture

## 1. Architecture Style

**Offline-first, layered client architecture, talking to exactly one network endpoint: a FastAPI gateway that mediates Supabase and Gemini.**

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLIENT (Flutter)                        │
│                                                                   │
│  Presentation Layer (UI / Widgets)                                │
│         ↓ events            ↑ state                              │
│  Application Layer (BLoC / Cubit — per feature)                   │
│         ↓ calls                                                  │
│  Domain Layer (use cases, entities, pure Dart — no framework dep) │
│         ↓ calls                                                  │
│  Repository Layer (single source of truth abstraction)            │
│      ↓                    ↓                    ↓                 │
│  Local Data Source   ML Inference Engine   Platform Services      │
│  (SQLite via Drift)  (TFLite / ONNX)       (Camera, Gallery, TTS, │
│      ↓                                       Notifications)       │
│  Sync Engine (outbox pattern, background worker)                  │
└───────────────────────────┬───────────────────────────────────────┘
                             │  HTTPS — the app's ONLY network calls,
                             │  all to one base URL (Bearer JWT after login)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  BACKEND GATEWAY (FastAPI, on Render)            │
│                                                                   │
│  /auth/*            JWT verification (all routes except /auth/*) │
│  /scans, /diagnoses, /escalations   Rate limiting (per user/IP)  │
│  /interpret-diagnosis               Holds: Supabase service-role │
│  /reference-data                    key + Gemini API key         │
└──────────────┬──────────────────────────────────┬────────────────┘
               │                                   │
               ▼                                   ▼
┌───────────────────────────────┐      ┌───────────────────────────┐
│         Supabase               │      │       Gemini API           │
│  Auth (Phone/OTP) · Postgres    │      │  (treatment interpretation) │
│  Storage (images)               │      └───────────────────────────┘
│  RLS enabled (defense-in-depth) │
└───────────────────────────────┘

                    External, outside CropCare's control:
                    WhatsApp (farmer-initiated share, native OS intent —
                    does not go through FastAPI)
```

**The app never holds a Supabase key or a Gemini key.** After login it holds only a short-lived Supabase JWT (relayed to it by FastAPI), which it sends to FastAPI as a Bearer token on every subsequent call. FastAPI is the only thing that ever calls Supabase or Gemini directly.

---

## 2. Client Layers in Detail

### 2.1 Presentation Layer
- Screens/widgets only. No business logic, no direct DB/API calls.
- Reads state from BLoC/Cubit, dispatches events.
- Fully localized (no hardcoded strings — all via localization keys resolved from `app_state.language_code`).

### 2.2 Application Layer (State Management)
- One BLoC/Cubit per feature: `OnboardingCubit`, `AuthCubit`, `ScanCubit`, `DiagnosisCubit`, `HistoryCubit`, `SettingsCubit`, `SyncStatusCubit`.
- Talks only to the Domain layer (use cases) — never touches SQLite or the network directly.
- Owns loading/error/success UI states per feature, independent of data source.

### 2.3 Domain Layer
- Use cases as plain functions/classes: `CaptureScanUseCase`, `ValidateImageUseCase`, `RunDiagnosisUseCase`, `EscalateToExpertUseCase`, `SyncPendingOperationsUseCase`.
- Defines entities (`Scan`, `Diagnosis`, `Crop`, `Escalation`) independent of storage format.
- No knowledge of SQLite, FastAPI, or Flutter — this layer is what makes the app testable and keeps offline/online logic swappable.

### 2.4 Repository Layer
- One repository per aggregate: `ScanRepository`, `DiagnosisRepository`, `AuthRepository`, `ReferenceDataRepository`, `EscalationRepository`.
- **Always reads/writes local SQLite first.** Never blocks on network.
- Responsible for enqueuing a `sync_operation` row whenever it mutates syncable data.
- Is the *only* layer allowed to know both "local" and "remote (via FastAPI)" exist.

### 2.5 Data Sources

| Data source | Technology | Responsibility |
|---|---|---|
| Local DB | SQLite (Drift/sqflite) | Source of truth on-device |
| ML Inference | TFLite / ONNX Runtime | On-device crop/disease classification |
| Remote API | HTTP client (`dio`/`http`) → FastAPI | Auth, CRUD, signed upload URLs, Gemini interpretation — **never calls Supabase or Gemini SDKs directly** |
| Platform services | Flutter plugins | Camera, gallery, permissions, TTS, local notifications, connectivity |

### 2.6 Sync Engine
- Background worker (`WorkManager` / `BackgroundFetch`) + in-app trigger on connectivity change and app resume.
- Reads `sync_operation` outbox table, processes `PENDING` rows in `created_at` order.
- Each operation becomes one authenticated HTTP call to the matching FastAPI endpoint, idempotent on `(user_id, local_entity_id)` — safe to retry.
- Exponential backoff on failure; surfaces `FAILED` state via `SyncStatusCubit` → local notification.

---

## 3. Backend Gateway (FastAPI)

```
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI (Render, single service)            │
│                                                                  │
│  Middleware:                                                    │
│    - JWT verification (Supabase JWT secret) on all routes        │
│      except /auth/request-otp and /auth/verify-otp               │
│    - Rate limiting (slowapi): per-phone on /auth/*,               │
│      per-user on /interpret-diagnosis and /scans                  │
│    - Request validation (Pydantic models mirroring entities)      │
│                                                                  │
│  Routers:                                                        │
│    /auth        → proxies Supabase Auth Admin API                │
│    /scans        → upsert, scoped to JWT's user_id                │
│    /diagnoses     → upsert, scoped to JWT's user_id                │
│    /escalations    → upsert, scoped to JWT's user_id                │
│    /interpret-diagnosis → calls Gemini, logs to llm_interpretation   │
│    /reference-data → read-only pull of crop/disease/guideline/model  │
│                                                                  │
│  Clients held server-side only:                                  │
│    - supabase-py (service-role key)                                │
│    - google-generativeai (Gemini key)                               │
└──────────────────────────────────────────────────────────────┘
```

**Why FastAPI instead of calling Supabase/Gemini straight from the app (the earlier design):** a client-held key — even Supabase's public anon key plus RLS — still lets a buggy or compromised client hit Supabase or Gemini directly, with no single place to throttle it. Centralizing every write and every Gemini call behind one gateway means rate limiting, JWT verification, and request validation happen in exactly one place, and neither key ever ships inside the app binary.

**Authorization boundary:** FastAPI calls Postgres using the Supabase **service-role key**, which bypasses Row-Level Security. That means FastAPI's own query-scoping (verify JWT → extract `user_id` → filter every query by it) is the *real* authorization boundary now, not RLS. RLS is still left enabled on every user-scoped table as defense-in-depth — it costs nothing and catches a bug in FastAPI's own scoping logic — but it's a second line, not the primary one.

**Row-Level Security (RLS):** every table with a `user_id` column restricts `SELECT/INSERT/UPDATE` to `auth.uid() = user_id`. Reference tables (`crop`, `disease`, `treatment_guideline`, `model_version`) are public-read, admin-write only. Left enabled per the note above.

**Storage uploads bypass FastAPI's own bandwidth:** for image uploads, FastAPI issues a short-lived signed Supabase Storage URL (scoped to that user's path) rather than proxying the image bytes itself — a Render free-tier instance has limited bandwidth/CPU time, and there's no benefit to routing large binary payloads through it.

---

## 4. On-Device ML Architecture

```
Captured Image (JPEG)
      │
      ▼
Preprocessing (resize, normalize, orientation fix)
      │
      ▼
Image Quality Check (lightweight heuristic/model — blur, exposure, plant presence)
      │
      ├─ fails ──▶ return rejection reason, stop
      │
      ▼
Crop-conditioned Disease Classifier (TFLite, quantized)
      │
      ▼
Raw output: {disease_id, confidence, severity, alternatives[]}
      │
      ▼
Confidence Gate (≥80% → CONFIDENT, else LOW_CONFIDENCE)
      │
      ▼
Structured Diagnosis object (no treatment text yet)
      │
      ▼
Treatment Guidance Resolution (online: FastAPI → Gemini; offline: local treatment_guideline)
      │
      ▼
Presented to farmer, localized
```

Model files are bundled with the app or delivered via asset update (not per-inference network calls) — inference itself never requires connectivity or touches FastAPI. Only the treatment-guidance step reaches the network.

---

## 5. End-to-End Component Diagram

```
┌──────────┐   ┌────────────┐   ┌────────────┐
│  Camera/  │   │   Image    │   │  On-device │
│  Gallery  │──▶│ Validation │──▶│    ML      │
└──────────┘   └────────────┘   └──────┬─────┘
                                        │
                                        ▼
                                ┌───────────────┐
                                │  SQLite (scan, │
                                │  diagnosis)    │
                                └───────┬───────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                                        ▼
          ┌──────────────────┐                     ┌──────────────────┐
          │  Sync Outbox      │                     │  WhatsApp Share   │
          │  (sync_operation) │                     │  (if escalated)   │
          └────────┬─────────┘                     └──────────────────┘
                   │ (when online, Bearer JWT)
                   ▼
          ┌──────────────────┐
          │  FastAPI (Render)  │──────▶ Gemini API (treatment interpretation)
          └────────┬─────────┘
                   │ (service-role key)
                   ▼
          ┌──────────────────┐
          │ Supabase Postgres │
          │  + Storage         │
          │  + Auth            │
          └──────────────────┘
```

---

## 6. Detailed Sequence Flows

### 6.1 App Launch

```
User opens app
   │
   ▼
Read app_state (SQLite)
   │
   ├─ onboarding_completed = 0 ──▶ Onboarding → Language Select ──▶ Home
   │
   └─ onboarding_completed = 1 ──▶ Home directly
   │
   ▼
(parallel, non-blocking) Connectivity check
   │
   ├─ online ──▶ trigger Sync Engine + Reference Data pull (via FastAPI)
   └─ offline ──▶ Home renders from local data only
```

### 6.2 Scan → Diagnosis (offline-capable, critical path)

```
Home ──▶ Select Crop ──▶ Capture/Upload Image
   │
   ▼
ScanCubit.captureImage()
   │
   ▼
CaptureScanUseCase ──▶ ScanRepository.createScan()
   │
   ▼
SQLite: INSERT scan (status=CREATED)
   │
   ▼
ValidateImageUseCase ──▶ SQLite: INSERT image_validation
   │
   ├─ rejected ──▶ UI shows specific reason ──▶ retake
   │
   ▼
RunDiagnosisUseCase ──▶ ML Inference Engine (on-device, no network)
   │
   ▼
SQLite: INSERT diagnosis, UPDATE scan.status
   │
   ▼
TreatmentResolutionUseCase:
   online  ──▶ FastAPI POST /interpret-diagnosis (Bearer JWT) ──▶ Gemini
   offline ──▶ local treatment_guideline lookup
   │
   ▼
ScanRepository enqueues sync_operation (scan, diagnosis)
   │
   ▼
DiagnosisCubit emits result state ──▶ UI renders localized diagnosis + treatment + TTS button
   │
   ├─ confidence ≥ 80% ──▶ end flow (Done / New Scan)
   └─ confidence < 80% ──▶ 6.3 Escalation
```

### 6.3 Expert Escalation

```
DiagnosisCubit (LOW_CONFIDENCE) ──▶ EscalationCubit
   │
   ▼
EscalateToExpertUseCase builds package from scan+diagnosis
   │
   ▼
UI: farmer enters/selects WhatsApp number
   │
   ▼
EscalationRepository: INSERT escalation (SQLite) + enqueue sync_operation
   │
   ▼
Platform share intent ──▶ WhatsApp app (external, outside FastAPI/CropCare backend entirely)
   │
   ▼
scan.status = 'SHARED'
```

### 6.4 Authentication (fully mediated by FastAPI)

```
Guest taps "Sign in" (Settings or prompted on sync attempt)
   │
   ▼
AuthCubit.sendOtp(phone) ──▶ AuthRepository ──▶ FastAPI POST /auth/request-otp
   │
   ▼
FastAPI: rate-limit check (per phone) ──▶ Supabase Auth Admin API sends OTP
   │
   ▼
Farmer enters OTP ──▶ AuthCubit.verifyOtp() ──▶ FastAPI POST /auth/verify-otp
   │
   ▼
FastAPI verifies with Supabase, relays back access_token + refresh_token
   │
   ▼
AuthRepository writes session to SQLite (local_user)
   │
   ▼
AuthRepository: for all existing guest scans ──▶ attach user_id, enqueue sync_operation
   │
   ▼
Sync Engine picks up queued operations on next trigger
```

The app never calls Supabase Auth directly — every step above goes through FastAPI, which is what makes OTP rate-limiting per phone number actually enforceable.

### 6.5 Sync Engine Cycle

```
Trigger: connectivity restored | app resumed | periodic | manual
   │
   ▼
SyncEngine.run()
   │
   ▼
SELECT * FROM sync_operation WHERE status='PENDING' ORDER BY created_at
   │
   ▼
For each op:
   UPDATE status='SYNCING'
      │
      ▼
   If entity has image ──▶ FastAPI POST /scans/{id}/upload-url (Bearer JWT)
      │                      returns signed Supabase Storage URL
      │                      app uploads image bytes DIRECTLY to Storage (not via FastAPI)
      │
      ▼
   Call matching FastAPI endpoint (Bearer JWT), idempotency key: user_id + local_entity_id
      │
      ▼
   FastAPI verifies JWT, scopes write to user_id, upserts into Postgres (service-role key)
      │
      ├─ success ──▶ status='SYNCED', write remote id back to SQLite row
      └─ failure ──▶ retry_count++, backoff, status='PENDING' or 'FAILED'
   │
   ▼
UPDATE app_state.last_sync_at
   │
   ▼
SyncStatusCubit emits summary ──▶ Settings shows "Last synced: ..." / pending count
```

### 6.6 Reference Data Update (remote → local, via FastAPI)

```
On app start (online) or periodic background check
   │
   ▼
ReferenceDataRepository.pullUpdates(since=last_sync_at)
   │
   ▼
FastAPI GET /reference-data?since=... (Bearer JWT)
   │
   ▼
FastAPI queries Supabase: crop/disease/treatment_guideline/model_version WHERE updated_at > since
   │
   ▼
SQLite: UPSERT into local reference tables
   │
   ▼
If model_version changed ──▶ flag for model asset update (handled via app/asset update channel, not runtime download in MVP)
```

### 6.7 Gemini Treatment Interpretation (via FastAPI)

```
TreatmentResolutionUseCase (online)
   │
   ▼
App: FastAPI POST /interpret-diagnosis (Bearer JWT)
   payload: {crop, disease_id, confidence, severity, language_code}
   │
   ▼
FastAPI: per-user rate-limit check
   │
   ├─ over limit ──▶ 429 response ──▶ app falls back to local treatment_guideline
   │
   ▼
FastAPI calls Gemini API (server-held key)
   │
   ├─ success ──▶ FastAPI writes public.llm_interpretation, returns parsed fields
   │                app caches locally, diagnosis.treatment_source='LLM'
   │
   └─ failure/timeout ──▶ FastAPI returns error ──▶ app falls back to local treatment_guideline
```

---

## 7. Technology Stack

| Layer | Technology |
|---|---|
| Client framework | Flutter |
| State management | BLoC / Cubit |
| Local database | SQLite (Drift or sqflite) |
| On-device ML | TensorFlow Lite (or ONNX Runtime), quantized model |
| Backend gateway | FastAPI (Python), hosted on Render |
| Backend → Supabase | `supabase-py`, service-role key |
| Backend → Gemini | `google-generativeai` SDK |
| Backend auth | PyJWT (verifying Supabase-issued JWTs) |
| Backend rate limiting | `slowapi` (per-user / per-phone) |
| Backend validation | Pydantic models |
| Data store | Supabase (Postgres, Auth, Storage) |
| Background sync | WorkManager (Android) / BGTaskScheduler (iOS) via Flutter plugin |
| Push notifications | Firebase Cloud Messaging (if/when added) |
| TTS | Platform TTS (flutter_tts) with Sinhala/Tamil/English voice support |
| Escalation transport | Native share intent → WhatsApp |

---

## 8. Security & Privacy Architecture

- **The app holds no Supabase key and no Gemini key, ever.** Only a short-lived Supabase JWT after login, sent to FastAPI as a Bearer token.
- **FastAPI holds both secrets server-side** (Supabase service-role key, Gemini API key), configured as Render environment variables, never committed to source control.
- **JWT verification on every authenticated route** — FastAPI rejects any request with a missing/invalid/expired token before touching Supabase or Gemini.
- **Rate limiting is the actual spam/abuse defense** requested for this design: per-phone-number limits on OTP requests, per-user limits on Gemini calls and scan submissions.
- **RLS stays enabled on every user-scoped table** as defense-in-depth, even though FastAPI's service-role key bypasses it in normal operation — see §3 for why this is now a secondary safeguard rather than the primary one.
- **Guest data stays device-local** until the farmer authenticates; nothing is uploaded pre-auth.
- **Escalation packages are minimized** — only crop, prediction, confidence, image, and farmer-entered notes are included; phone number/location are excluded by default.
- **Images in Storage are private**, uploaded and served via short-lived signed URLs, never public buckets.
- **Session tokens** stored in secure local storage (Keychain/Keystore) on-device, not plain SQLite.
- **Local DB is on-device only** — no cross-app access; sensitive fields (phone number) are not required for core diagnosis use.

---

## 9. Reliability & Scalability Notes

- **Idempotent sync** (unique constraint on `user_id + local_entity_id`, enforced by FastAPI's upsert logic) means dropped connections, app kills, or duplicate retries never create duplicate server rows.
- **Outbox pattern** decouples UI responsiveness from network reliability — the farmer never waits on a network call during the diagnosis flow.
- **Reference data is pull-based and versioned**, so treatment guidance changes roll out without breaking historical diagnoses (each diagnosis pins its `treatment_guideline_id`/`llm_interpretation_id` and `model_version_id`).
- **FastAPI is now a real stateful component to operate**, unlike the earlier Supabase-only design — it needs its own uptime monitoring, and its rate-limit counters (if kept in-memory) only work correctly on a single instance. Render's free tier runs one instance, which is fine for MVP; scaling to multiple instances later would need the rate-limit state moved to something shared (e.g. Redis) instead of in-process memory.
- **Render's free tier spins down on inactivity**, causing a cold-start delay (tens of seconds) on the first request after idle. Worth a loading state on first launch after a period of no use; not a functional risk, just a UX one to plan for.

---

## 10. Environments

| Environment | Purpose |
|---|---|
| Local/dev | Local Supabase project + FastAPI run locally (`uvicorn`); test model builds |
| Staging | Shared Supabase project, FastAPI on a separate Render service, pre-release builds |
| Production | Live Supabase project, live FastAPI Render service, signed app builds, RLS fully enabled |

FastAPI's environment variables (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `GEMINI_API_KEY`) are configured per-environment in Render's dashboard — never in the Flutter app, which only ever needs one config value: the FastAPI base URL for that environment.

Model versions and treatment guidelines are promoted independently of app releases where possible, using the `is_active` / `is_current` flags — allowing content/model updates without requiring an app store release.
