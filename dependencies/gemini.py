"""
Shared Gemini access.

Exists because both routers hardcoded `gemini-1.5-flash`, and that model stopped
being served on the v1beta endpoint that Google AI Studio keys use. The
symptom in production was:

    404 models/gemini-1.5-flash is not found for API version v1beta

which broke treatment guidance and chat simultaneously, with no way to fix it
without a code change and redeploy.

Two things prevent a repeat:

* The model name is configuration (`GEMINI_MODEL`), so it can be changed on
  Render without touching code.
* A failed model is retried against a list of candidates. Google retires model
  names on its own schedule; an app for farmers should not go dark because an
  alias was renamed. `gemini-flash-latest` leads the list precisely because it
  is an alias that tracks whatever the current fast model is.
"""

from __future__ import annotations

import logging

import google.generativeai as genai

from config import settings

logger = logging.getLogger(__name__)

# Ordered. The first is an alias that follows Google's current fast model; the
# rest are concrete fallbacks in case the alias is unavailable on a given key.
_FALLBACK_MODELS = [
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite",
]


def candidate_models() -> list[str]:
    """Model names to try, configured first."""
    configured = (settings.gemini_model or "").strip()
    if not configured:
        return list(_FALLBACK_MODELS)
    return [configured] + [m for m in _FALLBACK_MODELS if m != configured]


def _is_missing_model(exc: Exception) -> bool:
    """True when the failure is 'this model does not exist', not a real fault.

    Only that case is worth retrying against another name. A quota error or a
    bad key will fail identically on every candidate, and hammering four
    models to discover that wastes the farmer's time.
    """
    text = str(exc).lower()
    return "not found" in text or "404" in text or "is not supported" in text


def generate(prompt: str, *, json_mode: bool = False) -> str:
    """Runs `prompt` and returns the text, trying each candidate model.

    Raises the LAST error if every candidate fails, so the caller reports
    something real rather than a synthesised message.
    """
    if not settings.gemini_api_key:
        raise RuntimeError("Gemini API key is not configured on the server.")

    genai.configure(api_key=settings.gemini_api_key)

    generation_config = (
        {"response_mime_type": "application/json"} if json_mode else None
    )

    last_error: Exception | None = None
    for name in candidate_models():
        try:
            model = genai.GenerativeModel(
                model_name=name,
                generation_config=generation_config,
            )
            response = model.generate_content(prompt)
            return response.text or ""
        except Exception as exc:
            last_error = exc
            if not _is_missing_model(exc):
                raise
            logger.warning("Gemini model %s unavailable, trying next: %s", name, exc)

    raise last_error if last_error else RuntimeError("No Gemini model available.")
