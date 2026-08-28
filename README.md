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
| `AI_PROVIDER` | no | default `gemini` — which provider handles treatment guidance and chat, see `dependencies/ai/service.py` |
| `AI_FALLBACK_PROVIDER` | no | default `nvidia` — tried if the primary can't serve a request (misconfigured, out of quota, unreachable). Inert until that provider's own key is set, same as `GEMINI_API_KEY_FALLBACK` always worked |
| `GEMINI_API_KEY` | yes if `gemini` is in use | a Google AI Studio key |
| `GEMINI_MODEL` | no | overrides the model name without a redeploy — see `dependencies/gemini.py` |
| `GEMINI_API_KEY_FALLBACK` | no | a second Gemini key (e.g. from a separate Google account) tried when the primary is out of free-tier quota — this is a fallback WITHIN the Gemini provider, independent of `AI_FALLBACK_PROVIDER` |
| `NVIDIA_API_KEY` | yes if `nvidia` is in use | from [build.nvidia.com](https://build.nvidia.com) — free tier, no credit card |
| `NVIDIA_MODEL` | no | default `meta/llama-3.1-8b-instruct` — see `dependencies/ai/nvidia_provider.py` |
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
python -m pytest -q      # 198 passing, 2 skipped at time of writing
ruff check .              # must stay clean — this is what CI runs
```

`pytest` never makes a real call to Gemini or NVIDIA — every provider test
mocks the SDK/HTTP layer directly, and `tests/conftest.py` force-clears
`NVIDIA_API_KEY` on every test regardless of what happens to be set in your
own shell, so an unmocked test can't accidentally reach the network even by
mistake. The 2 skipped tests are real, opt-in calls to whichever provider is
configured in your environment:

```bash
RUN_AI_INTEGRATION_TESTS=true pytest tests/integration/test_ai_providers_live.py -v
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
| `POST` | `/interpret-diagnosis` | AI-written treatment steps |
| `POST` | `/chat-about-diagnosis` | scoped follow-up chat about one diagnosis |
| `POST` | `/feedback` | |
| `GET` | `/health` | |

## AI provider abstraction, and why there's a fallback

Nothing in `routers/diagnosis.py` or `routers/chat.py` imports Gemini or
NVIDIA directly — both call `dependencies/ai/service.py`, which routes to
whichever provider `AI_PROVIDER` names and falls back to
`AI_FALLBACK_PROVIDER` if the primary can't serve the request:

```
Treatment / Chat router
          |
  dependencies.ai.service.generate()
          |
    AIProvider (interface)
          |
   +------+------+
   |             |
GeminiProvider  NvidiaProvider
```

This exists because a single provider's free-tier quota turned out to be a
real, live blocker: automated testing and normal development traffic drain
the same daily allowance a farmer's actual usage draws from, and once it's
gone, waiting for a midnight reset is the only recovery — not viable mid-demo.
A second, independent provider with its own quota is a much sturdier answer
than trying to make one provider's limit stretch further.

- **NVIDIA provider — model choice.** `meta/llama-3.1-8b-instruct` by
  default: a general instruction-following model, not a "thinking"/reasoning
  variant (unnecessary latency and token cost for a few short sentences of
  guidance) and not a vision/multimodal one (the ML model already did the
  image diagnosis — this layer only ever sees text). NVIDIA's own Nemotron
  family was also considered; Llama 3.1 8B was picked for broader, better-
  documented general instruction-following at a comparable size. Configurable
  via `NVIDIA_MODEL` without a code change, same pattern as `GEMINI_MODEL`.
  **Known limitation, stated plainly:** neither this model nor most other
  free/fast NVIDIA NIM options have officially documented Sinhala or Tamil
  support the way Gemini does — expect noticeably better fluency in those
  languages from Gemini than from the NVIDIA fallback. This is an accepted
  trade-off for quota resilience at zero cost, not an oversight; the
  on-device seeded guidance is the real safety net for language quality
  regardless of which AI provider answers.
- **Error classification (`dependencies/ai/errors.py`)** decides what's worth
  a fallback: `AIQuotaExceeded` and `AIProviderUnavailable` (timeouts, 5xx,
  auth rejection, misconfiguration) trigger the fallback provider.
  `AIRequestFailed` — a malformed prompt or a response neither provider could
  have handled — is raised immediately instead, since a different provider
  would fail on the exact same bad input, and silently retrying it would turn
  a real, fixable bug into a confusing one two layers further downstream. If
  the fallback ALSO fails, the error message embeds both providers' original
  failure reasons, not just whichever was asked last.
- **`dependencies/gemini.py`** (wrapped by `GeminiProvider`) still handles
  everything specific to Gemini and centralises every call, retrying along
  two independent axes of its own:
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
