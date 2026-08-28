"""
Tests for dependencies/ai/gemini_provider.py.

Only tests the TRANSLATION from whatever dependencies/gemini.py raises into
the shared AIError types - dependencies/gemini.py's own retry-across-models-
and-keys behaviour is already covered by test_gemini_model_fallback.py and
is not re-tested here. Mocks dependencies.gemini.generate() directly, so
nothing here touches the real google.generativeai SDK or the network.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dependencies.ai.errors import (
    AIConfigurationError,
    AIProviderUnavailable,
    AIQuotaExceeded,
    AIRequestFailed,
)
from dependencies.ai.gemini_provider import GeminiProvider


def _raise(exc: Exception):
    def _inner(prompt, *, json_mode=False):
        raise exc
    return _inner


class TestGeminiProviderSuccess:
    def test_returns_the_text_from_dependencies_gemini(self):
        with patch("dependencies.ai.gemini_provider._gemini.generate", return_value="hello farmer"):
            assert GeminiProvider().generate("prompt") == "hello farmer"

    def test_json_mode_is_forwarded(self):
        with patch("dependencies.ai.gemini_provider._gemini.generate", return_value="{}") as mock_gen:
            GeminiProvider().generate("prompt", json_mode=True)
        mock_gen.assert_called_once_with("prompt", json_mode=True)


class TestGeminiProviderErrorClassification:
    def test_no_key_configured_is_a_configuration_error(self):
        exc = RuntimeError("Gemini API key is not configured on the server.")
        with patch("dependencies.ai.gemini_provider._gemini.generate", side_effect=exc), \
                pytest.raises(AIConfigurationError):
            GeminiProvider().generate("prompt")

    def test_quota_exceeded_is_a_quota_error(self):
        exc = RuntimeError("429 Resource has been exhausted (quota)")
        with patch("dependencies.ai.gemini_provider._gemini.generate", side_effect=exc), \
                pytest.raises(AIQuotaExceeded):
            GeminiProvider().generate("prompt")

    def test_auth_failure_is_provider_unavailable(self):
        exc = RuntimeError("401 API_KEY_INVALID: API key not valid")
        with patch("dependencies.ai.gemini_provider._gemini.generate", side_effect=exc), \
                pytest.raises(AIProviderUnavailable):
            GeminiProvider().generate("prompt")

    def test_timeout_is_provider_unavailable(self):
        exc = RuntimeError("504 Deadline Exceeded")
        with patch("dependencies.ai.gemini_provider._gemini.generate", side_effect=exc), \
                pytest.raises(AIProviderUnavailable):
            GeminiProvider().generate("prompt")

    def test_every_candidate_model_missing_is_provider_unavailable(self):
        # Every model name retired is just as much "this provider could not
        # serve the request" as a bad key is - not a bug in the request.
        exc = RuntimeError("404 models/gemini-2.5-flash is not found")
        with patch("dependencies.ai.gemini_provider._gemini.generate", side_effect=exc), \
                pytest.raises(AIProviderUnavailable):
            GeminiProvider().generate("prompt")

    def test_an_unrecognised_failure_is_a_request_failure_not_retried(self):
        exc = RuntimeError("400 The prompt was blocked for safety reasons")
        with patch("dependencies.ai.gemini_provider._gemini.generate", side_effect=exc), \
                pytest.raises(AIRequestFailed):
            GeminiProvider().generate("prompt")
