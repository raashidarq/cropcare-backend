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

- **Free tier**: Google AI Studio keys get roughly 20 requests/day per model
  on the free tier. This matters more now that the app auto-fetches AI
  guidance on every diagnosis — see the architecture diagram in the app repo
  for how the on-device guideline stays on screen if that call fails or is
  rate-limited.
- **`dependencies/gemini.py`** centralises every call and retries against a
  candidate model list rather than one hardcoded name. This exists because
  production broke once already: `gemini-1.5-flash` was hardcoded and Google
  retired it from the v1beta endpoint AI Studio keys use, taking both
  treatment guidance and chat down simultaneously. `GEMINI_MODEL` lets the
  model be swapped on Render without a code change; the fallback list means a
  future rename degrades gracefully instead of going dark again.
- Only a "model not found" failure triggers a fallback attempt — a quota or
  auth error fails fast rather than retrying four times against the same
  wall.

## Reading the codebase

- `CODEBASE_MAP.md`, `DECISIONS.md` — architecture and the reasoning behind
  non-obvious choices, shared conventions with the app repo.
- `CropCare_System_Architecture.md`, `CropCare_Build_Checklist.md` — the
  original design and build-out plan this service was built against.
