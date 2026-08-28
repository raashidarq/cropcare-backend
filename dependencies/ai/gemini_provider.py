"""
Gemini as an AIProvider.

Deliberately a thin wrapper, not a reimplementation: dependencies/gemini.py
already retries across model names AND API keys (see that module's own
docstring for the incidents that shaped it), and none of that logic changes
here. This file's only job is translating whatever dependencies/gemini.py
raises into the shared error types dependencies/ai/service.py knows how to
route on - reusing its own classifier functions rather than re-deriving
"was this a quota error" from scratch, so the two stay in sync by
construction instead of by remembering to update both.
"""

from __future__ import annotations

from dependencies import gemini as _gemini

from .base import AIProvider
from .errors import (
    AIConfigurationError,
    AIError,
    AIProviderUnavailable,
    AIQuotaExceeded,
    AIRequestFailed,
)


class GeminiProvider(AIProvider):
    name = "gemini"

    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        try:
            return _gemini.generate(prompt, json_mode=json_mode)
        except Exception as exc:
            # Reclassified below, never re-raised bare - BLE001 doesn't
            # apply, this is the one place that HAS to catch everything.
            raise _classify(exc) from exc


def _classify(exc: Exception) -> AIError:
    text = str(exc)

    # dependencies.gemini.generate() raises a plain RuntimeError with this
    # exact wording when candidate_keys() is empty - no key configured at
    # all, not a call that was ever attempted.
    if "not configured" in text.lower() or "no gemini key available" in text.lower():
        return AIConfigurationError(text)

    if _gemini._is_quota_error(exc):
        return AIQuotaExceeded(text)

    if _gemini._is_auth_error(exc) or _gemini._is_timeout(exc):
        return AIProviderUnavailable(text)

    # Every candidate model missing (all retired/renamed) exhausts the
    # provider just as thoroughly as a bad key does - still "this provider
    # couldn't serve the request", not a bug in the request itself.
    if _gemini._is_missing_model(exc):
        return AIProviderUnavailable(text)

    return AIRequestFailed(text)
