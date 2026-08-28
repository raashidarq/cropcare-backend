"""
NVIDIA NIM as an AIProvider.

NVIDIA's hosted NIM catalog (https://build.nvidia.com) exposes an
OpenAI-compatible `POST /v1/chat/completions` endpoint, so this needs
nothing beyond the `httpx` client already in requirements.txt - no new SDK,
no new dependency, matching this project's "small and maintainable" MVP
constraint.

Model choice: `meta/llama-3.1-8b-instruct` by default (see README for the
fuller reasoning) - a general instruction-following model, not a reasoning/
"thinking" variant (which would add latency and token cost neither
treatment guidance nor a short chat reply needs) and not a vision/multimodal
one (the ML model already did the image diagnosis; this layer only ever
sees text). Configurable via NVIDIA_MODEL without a code change, same
pattern as GEMINI_MODEL.
"""

from __future__ import annotations

import json
import logging

import httpx

from config import settings

from .base import AIProvider
from .errors import (
    AIConfigurationError,
    AIProviderUnavailable,
    AIQuotaExceeded,
    AIRequestFailed,
)

logger = logging.getLogger(__name__)

_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"

# Matches dependencies/gemini.py's own per-call budget. A stalled connection
# should fail fast enough for the on-device guidance to still feel like the
# right answer, not a consolation prize after a long wait.
_REQUEST_TIMEOUT_SECONDS = 15.0

_DEFAULT_MODEL = "meta/llama-3.1-8b-instruct"

# Treatment guidance and chat replies are both capped at a handful of short
# sentences by the prompt itself (see routers/diagnosis.py, routers/chat.py)
# - this is a hard ceiling against a model that ignores that instruction and
# rambles, not the expected length.
_MAX_OUTPUT_TOKENS = 700


class NvidiaProvider(AIProvider):
    name = "nvidia"

    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        api_key = (settings.nvidia_api_key or "").strip()
        if not api_key:
            raise AIConfigurationError("NVIDIA_API_KEY is not configured on the server.")

        model = (settings.nvidia_model or "").strip() or _DEFAULT_MODEL

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "stream": False,
        }
        # response_format is intentionally NOT sent even when json_mode is
        # set: NIM's support for it varies by model and isn't worth coupling
        # this provider to. Every json_mode prompt already states the exact
        # schema in its own text (see routers/diagnosis.py's _build_prompt),
        # which is what actually gets the JSON back, response_format or not.

        try:
            response = httpx.post(
                _ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException as exc:
            raise AIProviderUnavailable(f"NVIDIA NIM request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AIProviderUnavailable(f"NVIDIA NIM request failed: {exc}") from exc

        if response.status_code == 429:
            raise AIQuotaExceeded(f"NVIDIA NIM rate limit exceeded: {_safe_body(response)}")
        if response.status_code in (401, 403):
            raise AIProviderUnavailable(
                f"NVIDIA NIM rejected the API key ({response.status_code}): {_safe_body(response)}"
            )
        if response.status_code == 404:
            # Almost always an unknown/retired model name, not a dead
            # endpoint - worth falling back on, the same way a missing
            # Gemini model name is, rather than treated as a hard bug.
            raise AIConfigurationError(
                f"NVIDIA NIM model {model!r} was not found (404): {_safe_body(response)}"
            )
        if response.status_code >= 500:
            raise AIProviderUnavailable(
                f"NVIDIA NIM server error ({response.status_code}): {_safe_body(response)}"
            )
        if response.status_code >= 400:
            # A genuine bad request (malformed payload, invalid parameter) -
            # a different provider would not fix this, so it is not worth
            # hiding behind a fallback.
            raise AIRequestFailed(
                f"NVIDIA NIM rejected the request ({response.status_code}): {_safe_body(response)}"
            )

        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise AIRequestFailed(
                f"NVIDIA NIM returned a response this app could not parse: {exc}"
            ) from exc

        return _strip_code_fence(text or "")


def _safe_body(response: httpx.Response) -> str:
    """A short, log-safe excerpt of an error body - never the API key
    (which lives only in the request header, never echoed back), truncated
    so one bad response can't flood the logs."""
    try:
        return response.text[:300]
    except Exception:  # noqa: BLE001
        return "<unreadable response body>"


def _strip_code_fence(text: str) -> str:
    """Small open models frequently wrap a JSON answer in a ```json ... ```
    fence even when told to reply with JSON only - Gemini's structured
    output mode never does this, so dependencies/gemini.py has no
    equivalent of this cleanup. Only strips a fence that wraps the WHOLE
    response, so it can never eat a code block that's actually part of a
    prose answer.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    return "\n".join(lines[1:-1]).strip()
