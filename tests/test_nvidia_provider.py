"""
Tests for dependencies/ai/nvidia_provider.py.

Every test here mocks httpx.post directly - none of them touch the network,
and the very first test (no API key configured) exists specifically to
prove that an unconfigured NvidiaProvider never even tries to, which is the
property tests/conftest.py's _clear_nvidia_key fixture depends on to keep
`pytest` from being able to spend real NVIDIA quota by accident.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from dependencies.ai.errors import (
    AIConfigurationError,
    AIProviderUnavailable,
    AIQuotaExceeded,
    AIRequestFailed,
)
from dependencies.ai.nvidia_provider import NvidiaProvider


def _response(status_code=200, json_body=None, text=""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text or ""
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no body")
    return resp


def _chat_completion(content: str):
    return {"choices": [{"message": {"content": content}}]}


class TestNoApiKeyConfigured:
    def test_raises_without_ever_calling_httpx(self):
        with patch("dependencies.ai.nvidia_provider.settings") as mock_settings, \
                patch("dependencies.ai.nvidia_provider.httpx.post") as mock_post:
            mock_settings.nvidia_api_key = ""
            mock_settings.nvidia_model = ""
            with pytest.raises(AIConfigurationError):
                NvidiaProvider().generate("prompt")
        mock_post.assert_not_called()


class TestSuccess:
    def _settings(self, key="test-nvidia-key", model=""):
        s = MagicMock()
        s.nvidia_api_key = key
        s.nvidia_model = model
        return s

    def test_returns_the_message_content(self):
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(200, _chat_completion("spray copper fungicide"))):
            assert NvidiaProvider().generate("prompt") == "spray copper fungicide"

    def test_sends_the_configured_model_and_bearer_key(self):
        mock_post = MagicMock(return_value=_response(200, _chat_completion("ok")))
        with patch("dependencies.ai.nvidia_provider.settings",
                    self._settings(key="secret-123", model="meta/llama-3.1-8b-instruct")), \
                patch("dependencies.ai.nvidia_provider.httpx.post", mock_post):
            NvidiaProvider().generate("prompt")

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["model"] == "meta/llama-3.1-8b-instruct"
        assert kwargs["headers"]["Authorization"] == "Bearer secret-123"

    def test_empty_model_setting_falls_back_to_the_default(self):
        mock_post = MagicMock(return_value=_response(200, _chat_completion("ok")))
        with patch("dependencies.ai.nvidia_provider.settings", self._settings(model="")), \
                patch("dependencies.ai.nvidia_provider.httpx.post", mock_post):
            NvidiaProvider().generate("prompt")
        assert mock_post.call_args.kwargs["json"]["model"]  # non-empty

    def test_a_json_code_fence_wrapping_the_whole_reply_is_stripped(self):
        fenced = '```json\n{"summary": "ok"}\n```'
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(200, _chat_completion(fenced))):
            assert NvidiaProvider().generate("prompt", json_mode=True) == '{"summary": "ok"}'

    def test_a_code_fence_inside_a_prose_answer_is_left_alone(self):
        # Only a fence wrapping the ENTIRE response is stripped - one that's
        # part of the actual answer (e.g. quoting a config snippet back to
        # the farmer) must never be eaten.
        prose = 'Try this:\n```\nnpm install\n```\nthen restart.'
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(200, _chat_completion(prose))):
            assert NvidiaProvider().generate("prompt") == prose


class TestErrorClassification:
    def _settings(self):
        s = MagicMock()
        s.nvidia_api_key = "test-key"
        s.nvidia_model = ""
        return s

    def test_429_is_a_quota_error(self):
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(429, text="rate limited")), \
                pytest.raises(AIQuotaExceeded):
            NvidiaProvider().generate("prompt")

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_rejection_is_provider_unavailable(self, status):
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(status, text="unauthorized")), \
                pytest.raises(AIProviderUnavailable):
            NvidiaProvider().generate("prompt")

    def test_404_unknown_model_is_a_configuration_error(self):
        # Worth falling back on, the same way a missing Gemini model name
        # is - a misconfigured NVIDIA_MODEL, not a bug in the prompt.
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(404, text="model not found")), \
                pytest.raises(AIConfigurationError):
            NvidiaProvider().generate("prompt")

    def test_server_error_is_provider_unavailable(self):
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(503, text="service unavailable")), \
                pytest.raises(AIProviderUnavailable):
            NvidiaProvider().generate("prompt")

    def test_bad_request_is_a_request_failure_not_provider_unavailable(self):
        # A malformed request is a bug in how this app built the call - a
        # different provider would not fix it, so it must not be classified
        # as something worth falling back on.
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(400, text="invalid request")), \
                pytest.raises(AIRequestFailed):
            NvidiaProvider().generate("prompt")

    def test_timeout_is_provider_unavailable(self):
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      side_effect=httpx.TimeoutException("timed out")), \
                pytest.raises(AIProviderUnavailable):
            NvidiaProvider().generate("prompt")

    def test_connection_failure_is_provider_unavailable(self):
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      side_effect=httpx.ConnectError("connection refused")), \
                pytest.raises(AIProviderUnavailable):
            NvidiaProvider().generate("prompt")

    def test_unparseable_success_body_is_a_request_failure(self):
        with patch("dependencies.ai.nvidia_provider.settings", self._settings()), \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(200, json_body={"unexpected": "shape"})), \
                pytest.raises(AIRequestFailed):
            NvidiaProvider().generate("prompt")


class TestApiKeyNeverLeaks:
    def test_the_key_never_appears_in_a_raised_error_message(self):
        with patch("dependencies.ai.nvidia_provider.settings") as mock_settings, \
                patch("dependencies.ai.nvidia_provider.httpx.post",
                      return_value=_response(500, text="internal error, no secrets here")):
            mock_settings.nvidia_api_key = "sk-super-secret-value"
            mock_settings.nvidia_model = ""
            with pytest.raises(AIProviderUnavailable) as excinfo:
                NvidiaProvider().generate("prompt")
        assert "sk-super-secret-value" not in str(excinfo.value)
