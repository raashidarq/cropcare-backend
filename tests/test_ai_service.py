"""
Tests for dependencies/ai/service.py - the provider-independent routing
layer that lets CropCare survive one provider's quota running out without
going dark, which is the entire reason this abstraction exists (see that
module's own docstring, and dependencies/ai/errors.py for what each error
type means for routing).

Uses small fake AIProvider implementations rather than real Gemini/NVIDIA
providers - this file is testing ROUTING, not any one provider's HTTP/SDK
details (those live in test_gemini_provider.py / test_nvidia_provider.py).
Nothing here makes a network call.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dependencies.ai import service
from dependencies.ai.base import AIProvider
from dependencies.ai.errors import (
    AIConfigurationError,
    AIProviderUnavailable,
    AIQuotaExceeded,
    AIRequestFailed,
)


class _Fake(AIProvider):
    """A provider that either returns a fixed answer or raises a fixed
    error, and records whether it was actually called - so a test can
    assert a provider was NEVER touched, not just that the end result
    matched."""

    def __init__(self, name: str, *, answer: str | None = None, error: Exception | None = None):
        self.name = name
        self._answer = answer
        self._error = error
        self.calls: list[tuple[str, bool]] = []

    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        self.calls.append((prompt, json_mode))
        if self._error is not None:
            raise self._error
        return self._answer or ""


def _settings(primary="gemini", fallback="nvidia"):
    class _S:
        ai_provider = primary
        ai_fallback_provider = fallback

    return _S()


def _patched(primary_provider, fallback_provider, *, primary_name="gemini", fallback_name="nvidia"):
    return patch.object(
        service,
        "_PROVIDERS",
        {primary_name: lambda: primary_provider, fallback_name: lambda: fallback_provider},
    )


class TestPrimarySucceeds:
    def test_returns_the_primarys_answer_without_touching_the_fallback(self):
        primary = _Fake("gemini", answer="treat the leaf with copper fungicide")
        fallback = _Fake("nvidia", answer="should never be seen")

        with patch.object(service, "settings", _settings()), _patched(primary, fallback):
            result = service.generate("prompt")

        assert result == "treat the leaf with copper fungicide"
        assert fallback.calls == []

    def test_json_mode_is_forwarded_to_the_provider(self):
        primary = _Fake("gemini", answer="{}")
        with patch.object(service, "settings", _settings()), _patched(primary, _Fake("nvidia")):
            service.generate("prompt", json_mode=True)
        assert primary.calls == [("prompt", True)]


class TestFallback:
    def test_quota_error_falls_through_to_the_fallback_provider(self):
        primary = _Fake("gemini", error=AIQuotaExceeded("429 quota exceeded"))
        fallback = _Fake("nvidia", answer="answered by nvidia")

        with patch.object(service, "settings", _settings()), _patched(primary, fallback):
            result = service.generate("prompt")

        assert result == "answered by nvidia"

    def test_provider_unavailable_falls_through_to_the_fallback_provider(self):
        primary = _Fake("gemini", error=AIProviderUnavailable("504 timeout"))
        fallback = _Fake("nvidia", answer="answered by nvidia")

        with patch.object(service, "settings", _settings()), _patched(primary, fallback):
            assert service.generate("prompt") == "answered by nvidia"

    def test_misconfigured_primary_falls_through_to_the_fallback_provider(self):
        primary = _Fake("gemini", error=AIConfigurationError("no key configured"))
        fallback = _Fake("nvidia", answer="answered by nvidia")

        with patch.object(service, "settings", _settings()), _patched(primary, fallback):
            assert service.generate("prompt") == "answered by nvidia"

    def test_request_failed_is_never_retried_against_the_fallback(self):
        # The one case that must NOT fall back: a bug in the request itself
        # would fail identically on a different provider, so silently
        # retrying it would just hide the bug behind extra latency.
        primary = _Fake("gemini", error=AIRequestFailed("malformed prompt"))
        fallback = _Fake("nvidia", answer="should never be reached")

        with patch.object(service, "settings", _settings()), _patched(primary, fallback), \
                pytest.raises(AIRequestFailed):
            service.generate("prompt")

        assert fallback.calls == []

    def test_both_providers_failing_surfaces_both_reasons(self):
        primary = _Fake("gemini", error=AIQuotaExceeded("gemini is out of quota"))
        fallback = _Fake("nvidia", error=AIConfigurationError("nvidia has no api key"))

        with patch.object(service, "settings", _settings()), _patched(primary, fallback), \
                pytest.raises(AIProviderUnavailable) as excinfo:
            service.generate("prompt")

        # Whoever reads this error should see BOTH root causes, not just
        # whichever provider happened to be asked last - that is the
        # difference between a one-glance fix and a second round of
        # debugging.
        assert "gemini is out of quota" in str(excinfo.value)
        assert "nvidia has no api key" in str(excinfo.value)

    def test_no_fallback_configured_raises_the_primarys_own_error(self):
        primary = _Fake("gemini", error=AIProviderUnavailable("504 timeout"))
        fallback = _Fake("nvidia", answer="should never be reached")

        with patch.object(service, "settings", _settings(fallback="")), _patched(primary, fallback), \
                pytest.raises(AIProviderUnavailable, match="504 timeout"):
            service.generate("prompt")

        assert fallback.calls == []

    def test_fallback_same_as_primary_is_not_retried_against_itself(self):
        # Retrying an exhausted provider against itself teaches nothing and
        # only slows the failure down - same reasoning as
        # dependencies/gemini.py's candidate_keys() dropping a fallback key
        # identical to the primary. Only one fake registered on purpose: if
        # the fallback path were (wrongly) taken, _build("gemini") would
        # return this SAME instance a second time, which the call-count
        # assertion below would still catch.
        primary = _Fake("gemini", error=AIProviderUnavailable("504 timeout"))

        with patch.object(service, "settings", _settings(primary="gemini", fallback="gemini")), \
                patch.object(service, "_PROVIDERS", {"gemini": lambda: primary}), \
                pytest.raises(AIProviderUnavailable, match="504 timeout"):
            service.generate("prompt")

        assert len(primary.calls) == 1


class TestProviderSelection:
    def test_unknown_primary_provider_is_a_configuration_error(self):
        with patch.object(service, "settings", _settings(primary="not-a-real-provider")), \
                pytest.raises(AIConfigurationError, match="not-a-real-provider"):
            service.generate("prompt")

    def test_unknown_fallback_provider_surfaces_once_the_primary_fails(self):
        # Wrapped the same way two real providers both failing would be -
        # an unknown fallback name is just another way the fallback attempt
        # itself fails, not a different code path.
        primary = _Fake("gemini", error=AIProviderUnavailable("504 timeout"))
        with patch.object(service, "settings", _settings(fallback="not-a-real-provider")), \
                patch.object(service, "_PROVIDERS", {"gemini": lambda: primary}), \
                pytest.raises(AIProviderUnavailable, match="not-a-real-provider"):
            service.generate("prompt")
