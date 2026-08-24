# CropCare — Build Checklist

- **Automated testing and a CI pipeline are in scope**, woven into each day rather than bolted on at the end — now for two codebases (Flutter app + FastAPI service).
  - NOTE: While testing was initially bolted on at the end. NOW, at present it is more secondary over completing implementation of the main workflow and its alloted auxiliary features and important edge cases.
- **A FastAPI backend now sits between the app and both Supabase and Gemini.**

- **"Hosted" = a live FastAPI service on Render + a live Supabase project + an installable app build** distributed via a shareable link (Firebase App Distribution or similar) — not a web app.

## Lock the model + dataset (before Day 1's clock really starts)

- [ ] Once locked: confirm the `.tflite` file, and document the exact class-index → disease-name mapping (this populates your `disease` table)

**Actual output:**
I've locked in a pre-trained ML model for crop-disease classification thats on https://huggingface.co/Daksh159/plant-disease-mobilenetv2

I've run a python script to get me to the stage of ONNX file and data. The plan was to convert it to a tflite file that I can then put to on-device ML inference. I haven't yet gotten to that stage yet.

**Tested output:** you can state, in one sentence, "model X takes a `[size]` image and outputs one of these `[N]` classes, and dataset Y has confirmed real examples for each of them."

## I am unable to confidently say the above tested output.

## Environment, project skeletons (app + backend), both databases, CI pipelines (DONE)

**Tasks — Flutter app**

- [ ] Install Flutter SDK, Android Studio (or VS Code + extensions), set up an emulator or connect a real phone
- [ ] Run `flutter doctor` until all checks pass
- [ ] `flutter create cropcare` — confirm it runs
- [ ] Set up folders matching the layered architecture: `lib/presentation`, `lib/application`, `lib/domain`, `lib/data/repositories`, `lib/data/local`, `lib/data/remote`, `lib/platform`
- [ ] Add packages: `flutter_bloc`, `drift` (or `sqflite`), `dio` (or `http`) for calling FastAPI, `image_picker`, `permission_handler`, `flutter_secure_storage`
- [ ] Set up local SQLite schema (Drift/sqflite) — start with `app_state`, `crop`, `disease`, `scan`, `diagnosis`; add the rest as later days need them
- [ ] Git init, first commit, push

**Tasks — FastAPI backend**

- [ ] Create a separate folder/repo for the backend, e.g. `cropcare-backend/`
- [ ] `pip install fastapi uvicorn supabase python-jose slowapi google-generativeai pydantic` (`python-jose` or `pyjwt` for JWT verification)
- [ ] Build a minimal `main.py` with one `GET /health` route, run it locally with `uvicorn main:app --reload`, confirm it responds
- [ ] Create a free Render account, connect the backend repo, deploy the `/health`-only version — confirm you get a live public URL
- [ ] Store `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (not the anon key), `SUPABASE_JWT_SECRET`, `GEMINI_API_KEY` as Render environment variables — never commit these to git
- [ ] Git init, first commit, push (separate repo/history from the Flutter app)

**Tasks — Databases**

- [ ] Create the Supabase project; note the project URL, anon key (for reference only — the app won't use it), service-role key, and JWT secret (Render env vars only)
- [ ] In Supabase SQL editor, run the full remote schema DDL — `profile`, `crop`, `disease`, `treatment_guideline`, `model_version`, `scan`, `diagnosis`, `llm_interpretation`, `escalation`, `sync_log`, `push_notification`
- [ ] Enable Row-Level Security on user-scoped tables (actual policies come Day 4 — for now just turn RLS on)

**Tasks — CI**

- [ ] In the Flutter repo: `.github/workflows/ci.yml` running `flutter analyze` and `flutter test` on every push
- [ ] In the backend repo: `.github/workflows/ci.yml` running `pytest` (even with zero tests, a passing empty suite is fine) and a lint step (`ruff` or `flake8`)
- [ ] Confirm both workflows show green on GitHub

**Actual Ouput:** ALL TASKS HAVE BEEN COMPLETED.

---

## Reference data, onboarding, language selection, first tests

**Goal:** App shows onboarding once, lets the farmer pick a language, and remembers both — offline — with first automated tests running in CI.

**Tasks**

- [ ] Seed `crop` and `disease` tables (local) with your real supported crops/diseases — using the Day 0 class mapping

**Completed work:**

- [ ] Build `AppStateRepository` — read/write the singleton `app_state` row
- [ ] Build 2–3 onboarding screens + skip button
- [ ] Build `OnboardingCubit` — tracks whether onboarding is done, calls repository to persist
- [ ] Build language selection screen (English / Sinhala / Tamil) — writes `app_state.language_code`
- [ ] On app launch: check `app_state.onboarding_completed` → route accordingly
- [ ] Kill and reopen the app — confirm onboarding does not show again
- [ ] Write 2–3 unit tests for `AppStateRepository`; write 1 widget test for the onboarding skip button
- [ ] Confirm `flutter test` passes locally and in CI

---

## Guest scan creation shell, settings skeleton, more tests (DONE)

**Goal:** A guest user can select a crop and capture a photo (image stays local — no upload flow yet, that's Day 8), settings screen exists.

**Tasks**

- [ ] Create a guest `local_user` row automatically on first launch (`is_guest = 1`)
- [ ] Build crop selection screen — reads from local `crop` table. (Actual: Fake crops have been put in)
- [ ] Build camera capture screen — request permission, handle denial with a clear explanation, capture a photo, save the local file path
- [ ] Build "retake" control
- [ ] On capture: `INSERT scan (status='CREATED')` with the local image path and selected crop
- [ ] Build a placeholder Result screen showing the raw scan row
- [ ] Build Settings screen shell: Language, Accessibility, Notifications, Offline Data sections
- [ ] Write a unit test confirming crop selection returns the correct seeded list
- [ ] Write a widget test mocking a denied camera permission
- [ ] Manually confirm permission denial once for real too

**Tested output by end of day:** Guest can select a crop, take a photo, scan row appears in SQLite. Permission denial covered by a test and confirmed manually. CI still green.

**Actual Ouput:** ALL TASKS HAVE BEEN COMPLETED.

---

## Authentication, now fully mediated by FastAPI (POSTPONED FOR AFTER MAIN FLOW)

**Goal:** A farmer can sign up/sign in via phone + OTP, with every call going through FastAPI — and guest scans attach to their account.

**Updated goal**: Implementing OTP is costly due to the needing a SMS provider in Twilio. Such functionality has been reserved for MVP demonstrations only and general usecase is to use email implementation via Supabase.

**Tasks — Backend** (PARTIALLY IMPLEMENTED)

- [ ] Enable phone auth provider in Supabase dashboard
- [ ] Build `POST /auth/request-otp`: accepts a phone number, rate-limits per phone (e.g. 3 requests / 10 min via `slowapi`), calls Supabase Auth Admin API to send the OTP
- [ ] Build `POST /auth/verify-otp`: accepts phone + code, calls Supabase Auth to verify, on success relays back `access_token` + `refresh_token`
- [ ] Build JWT-verification middleware/dependency: reads the `Authorization: Bearer` header, verifies the signature against `SUPABASE_JWT_SECRET`, extracts `user_id`, rejects invalid/expired tokens with 401 — apply it to every route except the two `/auth/*` ones
- [ ] Write the actual RLS policies now that `auth.uid()` is meaningful (kept as defense-in-depth per the architecture doc)
- [ ] Write a couple of `pytest` tests: request-otp respects the rate limit, verify-otp rejects a bad code, a protected route rejects a missing/invalid JWT

**Tasks — App** (NOT IMPLEMENTED YET)

- [ ] Build phone number entry screen with basic validation
- [ ] Call `POST /auth/request-otp` on your FastAPI base URL
- [ ] Build OTP entry screen, call `POST /auth/verify-otp`
- [ ] On success: store the relayed tokens securely (`flutter_secure_storage`), update `local_user` (`is_guest=0`, `remote_user_id` decoded from the JWT, tokens, expiry)
- [ ] Handle: wrong OTP, expired OTP, resend, no network, and a 429 from the rate limiter (show "too many attempts, try again shortly" rather than a generic error)
- [ ] Confirm guest mode still works fully if sign-in is skipped
- [ ] Write a unit test for client-side phone/OTP format validation (postponed till after core functionality is implemented)

**Tested output by end of day:** You can sign up with your own phone, receive a real OTP via FastAPI → Supabase, verify it, and see your `profile` row in Supabase. A request past the rate limit is rejected cleanly. Guest mode still works. Backend and app tests both pass in their respective CIs.

---

## On-device ML inference (the core feature, stays fully offline) (DONE)

**Goal:** A captured photo produces a real diagnosis, entirely offline, using the model locked on Day 0. Nothing here touches FastAPI.

**Learn today:** TFLite Flutter plugin basics, image preprocessing, reading a model's input/output tensor shapes.

**Tasks**

- [ ] Add `tflite_flutter` (or your chosen inference package)
- [ ] Place your `.tflite` model in `assets/models/`, register it in `pubspec.yaml`
- [ ] Standalone sanity check: load the model, run one known sample image, print the raw output
- [ ] Build preprocessing function: resize → normalize
- [ ] Build basic image quality checks (blur/too-dark/too-small heuristics)
- [ ] Wire: capture → validate → if usable, run inference → map raw output index to `disease_id` (Day 0 mapping) → confidence
- [ ] `INSERT diagnosis` with `result_state` from your confidence threshold, `model_version_id`
- [ ] Update `scan.status` to `DIAGNOSED`
- [ ] Time the inference on your test device
- [ ] Write a unit test for the preprocessing function's output shape (postponed till after core functionality is implemented)
- [ ] Write a unit test for the confidence-threshold logic (postponed till after core functionality is implemented)
- [ ] Test with a diseased leaf, a healthy leaf, a blurry photo, and a non-plant photo — confirm none crash the app (postponed till after core functionality is implemented)

**Tested output by end of day:** A real photo produces a real disease prediction with a confidence number, entirely offline. Preprocessing and threshold logic have passing tests. CI still green.

---

## Treatment guidance: FastAPI → Gemini, with local fallback, plus TTS (IN_PROGRESS)

**Goal:** The diagnosis screen shows real, localized treatment guidance — Gemini via FastAPI when online, local fallback offline — and both branches are tested.

**UPDATED GOAL**: Local feedback and TTS is postponed till after core functionality is implemented.

**Tasks — Backend** (NOT IMPLEMENTED YET)

- [ ] Build `POST /interpret-diagnosis`: accepts `{crop, disease_id, confidence, severity, language_code}`, rate-limited per user
- [ ] Call Gemini with a prompt asking explicitly for the four fields (summary, what to do, what to avoid, recheck days) in the target language
- [ ] Parse the response into those fields; on any failure/timeout, return a clear error status rather than a partial/malformed body
- [ ] Write to `public.llm_interpretation` (success or failure) for audit
- [ ] Write a `pytest` test mocking the Gemini call to fail, confirming the endpoint returns a clean error (not a 500 crash) — this is what lets the app's fallback logic trust the response shape (postponed till after core functionality is implemented)
- [ ] Write a `pytest` test mocking Gemini to succeed, confirming the response is parsed and logged correctly (postponed till after core functionality is implemented)

**Tasks — App** (NOT IMPLEMENTED YET. Seeding the treatment_guideline local is postponed till after main features are implemented. Right now, the LLM is the only source of treatment guidance. Fallback should be prepared but left unused till after main feature flow is done. i.e. treatment guidance is done and escalation and scan history list is also done. Also, TTS is also done.)

- [ ] Seed `treatment_guideline` (local) with real, reviewed text for every supported disease, in all 3 languages
- [ ] Build `TreatmentResolutionUseCase`: check connectivity → call FastAPI `POST /interpret-diagnosis` → success: `INSERT llm_interpretation`, `treatment_source='LLM'` → failure/offline/429: fall back to `treatment_guideline`, `treatment_source='LOCAL_FALLBACK'`
- [ ] Build the real Result screen: crop, disease, confidence, severity, treatment text, localized
- [ ] Add "Read aloud" via `flutter_tts`
- [ ] Handle TTS failure gracefully
- [ ] Write a unit test for `TreatmentResolutionUseCase` with the FastAPI call mocked to fail — confirm correct fallback. **One of the highest-value tests in the app.**
- [ ] Write the mirror test for the success case
- [ ] Manually confirm online vs. airplane-mode behavior once for real, same disease

**Tested output by end of day:** Online, a diagnosis shows Gemini-generated guidance (routed through FastAPI); offline, the same disease shows local fallback text — both readable and localized. Both branches covered by automated tests, on both sides of the connection. CI still green in both repos.

---

## Escalation, scan history, scan lifecycle polish (DONE)

**Goal:** Low-confidence results escalate via WhatsApp, every past scan is browsable, and escalation-routing logic is tested.

**Learn today:** `share_plus` or a WhatsApp URL-scheme share intent, basic list/filter UI, testing with a fake/in-memory repository.

**Tasks**

- [x] Build escalation trigger: `diagnosis.confidence < 80%` displays manual escalation advisory banner prompting WhatsApp consultation
- [x] Build the escalation card content (crop, prediction, confidence, image, farmer notes) — exclude phone number/location
- [x] Build WhatsApp share feature to share escalation card with attached leaf image file via `share_plus`
- [x] `INSERT escalation` locally, set `shared_at`, update `scan.status='SHARED'`
- [x] Build scan history list — embedded directly on `HomeScreen` below scan button, local `scan` joined with `diagnosis`, thumbnail/crop/diagnosis/status/date
- [x] Add filters: All, Low Confidence, Shared, Healthy
- [x] Add clean "Scan Again" exit from the diagnosis flow
- [x] Walk through every `scan.status` and confirm each one actually gets set somewhere
- [x] Write a unit test: low-confidence advisory banner rendering & escalation routing
- [x] Write a widget test: embedded history list renders scans from a seeded fake repository

**Tested output by end of day:** A low-confidence scan displays advisory, routes manually to escalation screen, generates formatted text with attached photo file, and opens WhatsApp share intent. History section on HomeScreen displays all past scans with dynamic filtering and tap-to-review. 27 unit & widget tests passing. CI green.

---

END OF MAIN WORKFLOW

---

## Sync engine: app ↔ FastAPI ↔ Supabase, image uploads via signed URLs

**Goal:** Everything created offline reaches Supabase correctly and exactly once, once connectivity returns — through FastAPI, with images uploaded directly to Storage.

**Learn today:** Generating a Supabase signed upload URL server-side, idempotent upserts (`ON CONFLICT DO UPDATE`), background task basics (or a simpler sync-on-resume + manual button if time is tight), testing against a fake instead of a live network call.

**Tasks — Backend**

- [ ] Build `POST /scans/{id}/upload-url`: authenticated, generates and returns a short-lived Supabase Storage signed URL scoped to that user's path
- [ ] Build `POST /scans`, `POST /diagnoses`, `POST /escalations`: authenticated, each upserts using `(user_id, local_entity_id)` as the idempotency key, scoped to the JWT's `user_id`
- [ ] Build `GET /reference-data?since=`: authenticated, returns changed reference rows
- [ ] Write a `pytest` test: calling `POST /scans` twice with the same `local_scan_id` results in one row, not two (the idempotency behavior itself, not just trusting Postgres)
- [ ] Write a `pytest` test: a request with someone else's `user_id` in the payload still gets scoped to the JWT's actual `user_id`, not the payload's

**Tasks — App**

- [ ] Build `sync_operation` outbox usage: every repository write that should sync also inserts a `sync_operation` row
- [ ] Build `SyncEngine.run()`: read `PENDING` operations in order, process each
- [ ] For scans with images: call `POST /scans/{id}/upload-url`, upload the image bytes directly to the returned Storage URL (not through FastAPI)
- [ ] Call the matching FastAPI endpoint (Bearer JWT) for each pending operation
- [ ] Handle failure: increment `retry_count`, cap retries, mark `FAILED` past the cap
- [ ] Trigger sync: on connectivity restored, on app resume, and via a manual "Sync now" button
- [ ] Confirm previously-guest scans get queued and synced correctly after Day 4's auth flow
- [ ] Write a unit test for the local idempotency-key logic (retrying a sync op doesn't produce two outbound calls)
- [ ] Manually test the full cycle once for real: airplane mode, create 2–3 scans including a low-confidence one, reconnect, confirm everything appears correctly and once in Supabase, with the image actually present in Storage

**Tested output by end of day:** Offline-created data reaches Supabase correctly and exactly once after reconnecting, via FastAPI, with images landing in Storage through a signed URL. Idempotency is covered by tests on both the backend and the app. CI green in both repos.

---

## Stabilize, full manual walkthrough, release build, CI green — both codebases

**Goal:** A feature-complete, tested, installable release build, and a stable, tested, live FastAPI service. **Not** demo prep, hosting polish, or distribution — that's the 5-day buffer's job.

**Learn today:** `flutter build apk --release`, basic crash-log reading, checking Render logs for backend errors.

**Tasks**

- [ ] Full walkthrough, fresh install, online: onboarding → language → guest scan → capture → diagnosis → treatment (via FastAPI/Gemini) → TTS → history
- [ ] Full walkthrough, offline from launch: guest scan → capture → diagnosis → local fallback treatment → escalation attempt (should clearly indicate it needs connectivity, not crash) → history
- [ ] Full walkthrough, authenticated: sign up → OTP (via FastAPI) → scan → sync → confirm directly in Supabase, including the uploaded image
- [ ] Test every documented rejection path once more: permission denied, blurry photo, wrong OTP, rate-limit hit, sync failure mid-way, Gemini failure (simulate by temporarily breaking the key or forcing a timeout) → confirm fallback still works
- [ ] Fix any crashes found — prioritize crashes over polish
- [ ] Check every screen in all 3 languages
- [ ] Confirm the full automated test suite passes locally and in CI, for **both** the Flutter app and the FastAPI backend
- [ ] Check Render's logs once for any unexpected errors under your test traffic
- [ ] `flutter build apk --release` — install the actual release build and re-run the fresh-install walkthrough once more
- [ ] Write a short README for each repo: what's implemented, known limitations, how to run it, required environment variables
- [ ] **Stop here.** Demo prep, hosting/distribution polish, and any remaining rough edges belong in the 5-day buffer.

**Tested output by end of day:** A release APK, installed fresh, survives the full walkthrough in all three languages, online and offline and authenticated, without crashing. The FastAPI service is live, rate-limited, and its test suite is green. Both CI pipelines are green.

## NOTE

I need at least 2 days to implement this after completing the main work to do this:
The final project submission consists of two main parts:

1. A maximum 20-minute project presentation and demonstration video.
2. Five Teamtailor questions containing essential written information, validation details, project links,
   and supporting evidence.
