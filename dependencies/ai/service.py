"""
The one entry point routers call: dependencies.ai.service.generate(...).

Routes to the configured primary provider, falling back to the configured
secondary provider on anything that looks like "this provider could not
serve the request" (misconfigured, out of quota, unreachable). A genuine
request-level failure - a bug, not a provider problem - is raised
immediately: a different provider would fail on the exact same bad input,
and silently retrying it would turn a real, fixable bug into a confusing
one two layers further downstream.

    Treatment / Chat router
              |
      dependencies.ai.service.generate()
              |
        AIProvider (interface)
              |
       +------+------+
       |             |
   GeminiProvider  NvidiaProvider

Neither router imports a provider module directly - see routers/diagnosis.py
and routers/chat.py, which only ever import this module.
"""

from __future__ import annotations

import logging

from config import settings

from .base import AIProvider
from .errors import (
    AIConfigurationError,
    AIError,
    AIProviderUnavailable,
    AIRequestFailed,
)
from .gemini_provider import GeminiProvider
from .nvidia_provider import NvidiaProvider

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[AIProvider]] = {
    "gemini": GeminiProvider,
    "nvidia": NvidiaProvider,
}


def _build(name: str) -> AIProvider:
    key = (name or "").strip().lower()
    cls = _PROVIDERS.get(key)
    if cls is None:
        raise AIConfigurationError(
            f"Unknown AI provider {name!r}. Configured providers: "
            f"{', '.join(sorted(_PROVIDERS))}."
        )
    return cls()


def generate(prompt: str, *, json_mode: bool = False) -> str:
    """Runs `prompt` against the configured primary provider, falling back
    to the configured secondary provider if the primary could not serve it.

    Raises AIRequestFailed immediately, without attempting a fallback, when
    the primary's failure looks like a bug rather than a provider problem.
    Raises the combined failure of BOTH providers if the fallback also
    fails, so whoever reads the error sees the actual root cause (e.g. "no
    Gemini key configured") rather than just whichever provider happened to
    be asked last.
    """
    primary_name = (settings.ai_provider or "gemini").strip().lower()
    fallback_name = (settings.ai_fallback_provider or "").strip().lower()

    primary = _build(primary_name)

    try:
        return primary.generate(prompt, json_mode=json_mode)
    except AIRequestFailed:
        raise  # a real bug - a different provider would not fix this
    except AIError as primary_exc:
        if not fallback_name or fallback_name == primary_name:
            raise

        logger.warning(
            "AI provider %r unavailable (%s), trying fallback %r",
            primary_name, primary_exc, fallback_name,
        )

        try:
            fallback = _build(fallback_name)
            return fallback.generate(prompt, json_mode=json_mode)
        except AIError as fallback_exc:
            raise AIProviderUnavailable(
                f"primary provider {primary_name!r} failed ({primary_exc}); "
                f"fallback provider {fallback_name!r} also failed ({fallback_exc})"
            ) from fallback_exc
