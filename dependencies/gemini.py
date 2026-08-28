"""
Shared Gemini access.

Exists because both routers hardcoded `gemini-1.5-flash`, and that model stopped
being served on the v1beta endpoint that Google AI Studio keys use. The
symptom in production was:

    404 models/gemini-1.5-flash is not found for API version v1beta

which broke treatment guidance and chat simultaneously, with no way to fix it
without a code change and redeploy.

Three things prevent a repeat of that, and of the second real failure mode -
running out of free-tier quota mid-demo:

* The model name is configuration (`GEMINI_MODEL`), so it can be changed on
  Render without touching code.
* A failed model is retried against a list of candidates. Google retires model
  names on its own schedule; an app for farmers should not go dark because an
  alias was renamed. `gemini-flash-latest` leads the list precisely because it
  is an alias that tracks whatever the current fast model is.
* A quota or authentication failure is retried against a second API key
  (`GEMINI_API_KEY_FALLBACK`), if one is configured. AI Studio's free tier is
  a low daily cap per key; once it is spent, every further call to that key
  fails until the quota resets, which a same-key model swap cannot fix.

These are deliberately two SEPARATE retry axes, not one combined list, because
they mean different things: "this model name doesn't exist" says nothing about
the other models on the same key, but "this key is out of quota" says nothing
about the model name - it will fail identically on every model until either
the quota resets or a different key is used.
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


def candidate_keys() -> list[str]:
    """API keys to try, primary first. Empty/unset ones are dropped, and a
    fallback identical to the primary is dropped too - retrying the same
    exhausted key against itself teaches nothing and only slows the failure
    down."""
    keys = [settings.gemini_api_key, settings.gemini_api_key_fallback]
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        k = (k or "").strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _is_missing_model(exc: Exception) -> bool:
    """True when the failure is 'this model does not exist', not a real fault.

    Worth retrying against another MODEL NAME on the same key. Does not
    indicate anything about whether the key itself is good.
    """
    text = str(exc).lower()
    return "not found" in text or "404" in text or "is not supported" in text


# Wall-clock budget for a single generate_content() call.
#
# This existed as NO timeout at all until a live check on the deployed
# service found /interpret-diagnosis and /chat-about-diagnosis both hanging
# past three minutes with zero response - not slow, stuck. The
# google-generativeai SDK does not bound a call by default; a stalled
# connection to Google's API, or a slow network path from the host, hangs
# the whole request indefinitely. Farmers give up long before three minutes;
# so does a demo audience.
_REQUEST_TIMEOUT_SECONDS = 20


def _is_quota_or_auth_error(exc: Exception) -> bool:
    """True when the failure is about the KEY, not the model: exhausted quota,
    a bad key, or a permission problem. Worth retrying against a different
    key. Not worth retrying against a different model on the SAME key - a
    quota cap and a bad key both fail identically regardless of model name.
    """
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "429",
            "quota",
            "rate limit",
            "resource_exhausted",
            "401",
            "403",
            "permission_denied",
            "api_key_invalid",
            "invalid api key",
            "api key not valid",
        )
    )


def _is_timeout(exc: Exception) -> bool:
    """True when the call ran out of its wall-clock budget rather than
    getting a real answer from Google. Worth retrying against a different
    key: a stalled connection is more often a property of the network path
    for that key/project than of one particular model name.
    """
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("deadline", "timeout", "timed out", "504", "unavailable")
    )


def generate(prompt: str, *, json_mode: bool = False) -> str:
    """Runs `prompt` and returns the text.

    Tries each configured key in order; for each key, tries each candidate
    model in order. A missing-model error moves to the next model on the SAME
    key. A quota/auth error abandons the remaining models on this key and
    moves straight to the next key - there is no reason to keep asking an
    exhausted key for a different model.

    Any other kind of failure (a bad prompt, a malformed response, a network
    error) is raised immediately rather than retried across every key and
    model combination, which would only turn one real failure into several
    times the latency for the same result.

    Raises the LAST error if every candidate is exhausted, so the caller
    reports something real rather than a synthesised message.
    """
    keys = candidate_keys()
    if not keys:
        raise RuntimeError("Gemini API key is not configured on the server.")

    generation_config = (
        {"response_mime_type": "application/json"} if json_mode else None
    )

    last_error: Exception | None = None
    for key_index, api_key in enumerate(keys):
        genai.configure(api_key=api_key)
        is_last_key = key_index == len(keys) - 1

        for name in candidate_models():
            try:
                model = genai.GenerativeModel(
                    model_name=name,
                    generation_config=generation_config,
                )
                response = model.generate_content(
                    prompt,
                    request_options={"timeout": _REQUEST_TIMEOUT_SECONDS},
                )
                return response.text or ""
            except Exception as exc:
                last_error = exc

                if _is_missing_model(exc):
                    logger.warning(
                        "Gemini model %s unavailable on key %d, trying next model: %s",
                        name, key_index, exc,
                    )
                    continue

                if _is_quota_or_auth_error(exc) or _is_timeout(exc):
                    logger.warning(
                        "Gemini key %d rejected or timed out (%s), %s",
                        key_index, exc,
                        "no fallback key left" if is_last_key else "trying fallback key",
                    )
                    break  # stop trying models on this key; move to next key

                # Not a recognised transient condition - fail fast rather than
                # burning through every remaining key and model for nothing.
                raise

    raise last_error if last_error else RuntimeError("No Gemini key available.")
