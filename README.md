# cropcare-backend

FastAPI service behind [CropCare](https://github.com/raashidarq/cropcare): auth,
sync (scan/diagnosis/escalation upload, restore, delete), Gemini-powered
treatment guidance and scoped chat, and feedback collection.

**Live and already deployed** — `https://cropcare-backend-xy88.onrender.com`.
`GET /health` returns `{"status": "ok"}`. The CropCare app is pre-configured
to use this URL; you do not need to run this service to test the app.

Free-tier Render: the first request after idle can take 20–30s to wake the
service. Subsequent requests are fast.

---

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

### Environment variables

| Variable | Required | Notes |
|---|---|---|
| `SUPABASE_URL` | yes | |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | server-side key, never the anon key |
| `SUPABASE_JWT_SECRET` | yes | verifies tokens issued by Supabase Auth |
| `GEMINI_API_KEY` | yes | a Google AI Studio key |
| `GEMINI_MODEL` | no | overrides the model name without a redeploy — see `dependencies/gemini.py` |
| `GEMINI_API_KEY_FALLBACK` | no | a second key (e.g. from a separate Google account) tried when the primary is out of free-tier quota |
| `PHONE_AUTH_ENABLED` | no | default `false`. Every SMS provider bills per message; left off deliberately as a cost control |

No `.env.example` is committed — set these in your shell or in Render's
dashboard (**Environment** tab), not in a file that could be committed.

### Supabase tables this code expects

No migration files are checked into this repo — the schema was created
directly in the Supabase SQL editor, which is a real gap for anyone standing
up a fresh project from scratch. The tables the code reads and writes:
`scan`, `diagnosis`, `escalation`, `profile`, `llm_interpretation`,
`chat_message_log`. Each row is scoped by `user_id`.

### Run

```bash
uvicorn main:app --reload
```

## Testing

```bash
python -m pytest -q      # 125 passing at time of writing
ruff check .              # must stay clean — this is what CI runs
```

## API surface

| Method | Path | |
|---|---|---|
| `POST` | `/auth/register` | email + password signup |
| `POST` | `/auth/login` | email + password sign-in |
| `POST` | `/auth/request-otp` / `/auth/verify-otp` | phone (gated off by default) |
| `POST` | `/auth/forgot-password` | |
| `DELETE` | `/auth/account` | cascades every user-scoped table |
| `POST` | `/auth/change-email`, `/auth/change-phone/*` | |
| `POST` | `/scans`, `/diagnoses`, `/escalations` | idempotent sync upserts |
| `GET` | `/scans` | restore — paged, diagnosis inlined, images as signed URLs |
| `DELETE` | `/scans/{id}` | removes the row and its stored image |
| `POST` | `/scans/{id}/upload-url` | signed Supabase Storage upload URL |
| `GET` | `/reference-data` | crops, diseases, treatment guidelines, model versions |
| `POST` | `/interpret-diagnosis` | Gemini-written treatment steps |
| `POST` | `/chat-about-diagnosis` | scoped follow-up chat about one diagnosis |
| `POST` | `/feedback` | |
| `GET` | `/health` | |

## Gemini usage, cost, and why there's a fallback list

- **Free tier**: Google AI Studio keys get roughly 20 requests/day **per
  model, per project** — confirmed against a live rate-limit dashboard, not
  assumed. This matters more now that the app auto-fetches AI guidance on
  every diagnosis — see the architecture diagram in the app repo for how the
  on-device guideline stays on screen if that call fails or is rate-limited
  even after every fallback below is exhausted.
- **`dependencies/gemini.py`** centralises every call and retries along two
  independent axes:
  - **Model name.** `gemini-1.5-flash` was hardcoded until it was retired
    from the v1beta endpoint AI Studio keys use, taking treatment guidance
    and chat down simultaneously. `GEMINI_MODEL` lets the model be swapped on
    Render without a code change; a "model not found" failure also falls
    through a candidate list on its own, so a future rename degrades
    gracefully instead of going dark again.
  - **API key.** Since the daily cap is per model, a 429 on one candidate
    says nothing about the others on the same key — they're retried before
    the fallback key is touched at all. A timeout gets the same treatment,
    after a live check found every request timing out on the same first
    model on both keys while a rate-limit dashboard showed the other three
    candidates completely unused — a timeout most likely means one specific
    model is slow or overloaded, not that the whole key is bad. Only an auth
    failure (401/403, a bad or revoked key) moves straight to
    `GEMINI_API_KEY_FALLBACK`, since that's the one failure that's genuinely
    about the key, not any particular model.
  A failure that matches none of the above (a malformed prompt, a genuine
  network error) is raised immediately rather than retried across every key
  and model combination, which would only turn one real failure into several
  times the latency for the same result.
- **Every call is bounded by a 15s timeout** (`request_options={"timeout":
  ...}`). Found via a live check against the deployed service: without a
  timeout, a stalled connection to Google's API hung `/interpret-diagnosis`
  and `/chat-about-diagnosis` past three minutes with zero response — worse
  than a fast, clean failure the on-device fallback can absorb.

## Reading the codebase

- `CODEBASE_MAP.md`, `DECISIONS.md` — architecture and the reasoning behind
  non-obvious choices, shared conventions with the app repo.
- `CropCare_System_Architecture.md`, `CropCare_Build_Checklist.md` — the
  original design and build-out plan this service was built against.
