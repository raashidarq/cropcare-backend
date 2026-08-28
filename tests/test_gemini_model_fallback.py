"""
Tests for dependencies/gemini.py.

Three real production incidents motivate this module, and this file is split
along the same line:

* A hardcoded model name (`gemini-1.5-flash`) was retired by Google, and both
  routers went dark simultaneously with no fix short of a redeploy. That is
  TestModelFallback below.
* Google AI Studio's free tier is a low daily cap PER MODEL PER KEY, not per
  key. A live rate-limit dashboard proved this: one model sat at 23/20
  requests for the day while three other candidates on the SAME key sat at
  0/20, untouched. A same-key model swap fixes a quota error far more often
  than the code used to assume, and only once every candidate model on a key
  is exhausted does it move to a second key. That is TestKeyFallback below.
* A live check against the deployed service found /interpret-diagnosis and
  /chat-about-diagnosis hanging past three minutes with no response at all -
  generate_content() had no timeout, so a stalled connection to Google's API
  hung the whole request indefinitely. That is TestTimeout below. A second
  live check then found EVERY request timing out on the SAME first model on
  both keys while a rate-limit dashboard showed three other candidate models
  completely unused - a timeout that jumped straight to the next key, like
  auth errors do, never gave those idle models a chance. TestKeyFallback
  covers that too.

The two retry axes are independent on purpose: a missing-model error, a
quota error, or a timeout stays on the same key and tries the next model -
none of the three say anything about the other candidates on that key. Only
an auth error (a bad or revoked key) abandons the remaining models on that
key and moves to the next key, because that really is a property of the key
itself, not of any one model name.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dependencies import gemini


def _model(text="ok"):
    resp = MagicMock()
    resp.text = text
    inst = MagicMock()
    inst.generate_content.return_value = resp
    return inst


def _settings(primary="k1", fallback=""):
    """A settings mock with BOTH gemini key fields explicitly set.

    Patching `dependencies.gemini.settings` replaces the whole object with a
    MagicMock; any attribute not explicitly set here (like
    gemini_api_key_fallback) auto-vivifies as a truthy MagicMock rather than
    an empty string, which candidate_keys() would then treat as a second
    real key. Every test sets both fields for exactly that reason.
    """
    s = MagicMock()
    s.gemini_api_key = primary
    s.gemini_api_key_fallback = fallback
    s.gemini_model = ""
    return s


class TestCandidateModels:
    def test_configured_model_is_tried_first(self):
        with patch("dependencies.gemini.settings", _settings()) as s:
            s.gemini_model = "gemini-custom"
            assert gemini.candidate_models()[0] == "gemini-custom"

    def test_fallbacks_follow_the_configured_one(self):
        with patch("dependencies.gemini.settings", _settings()) as s:
            s.gemini_model = "gemini-custom"
            names = gemini.candidate_models()
        assert len(names) > 1

    def test_no_duplicates_when_configured_matches_a_fallback(self):
        with patch("dependencies.gemini.settings", _settings()) as s:
            s.gemini_model = "gemini-flash-latest"
            names = gemini.candidate_models()
        assert len(names) == len(set(names))

    def test_unset_config_falls_back_to_the_list(self):
        with patch("dependencies.gemini.settings", _settings()):
            names = gemini.candidate_models()
        assert names[0] == "gemini-flash-latest"


class TestCandidateKeys:
    def test_only_the_primary_when_no_fallback_is_configured(self):
        with patch("dependencies.gemini.settings", _settings(primary="k1", fallback="")):
            assert gemini.candidate_keys() == ["k1"]

    def test_both_when_a_fallback_is_configured(self):
        with patch("dependencies.gemini.settings", _settings(primary="k1", fallback="k2")):
            assert gemini.candidate_keys() == ["k1", "k2"]

    def test_a_fallback_identical_to_the_primary_is_dropped(self):
        # Retrying the same exhausted key against itself teaches nothing and
        # only slows the failure down.
        with patch("dependencies.gemini.settings", _settings(primary="k1", fallback="k1")):
            assert gemini.candidate_keys() == ["k1"]

    def test_whitespace_only_fallback_counts_as_unset(self):
        with patch("dependencies.gemini.settings", _settings(primary="k1", fallback="   ")):
            assert gemini.candidate_keys() == ["k1"]

    def test_no_keys_at_all_is_an_empty_list(self):
        with patch("dependencies.gemini.settings", _settings(primary="", fallback="")):
            assert gemini.candidate_keys() == []


class TestGenerate:
    def test_returns_text_from_the_first_working_model(self):
        with patch("dependencies.gemini.settings", _settings()), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.return_value = _model("hello")
            assert gemini.generate("prompt") == "hello"

    def test_a_missing_model_falls_through_to_the_next_model_same_key(self):
        calls = []

        def make(model_name, generation_config=None):
            calls.append(model_name)
            if len(calls) == 1:
                raise RuntimeError(
                    "404 models/gemini-flash-latest is not found for API version v1beta"
                )
            return _model("recovered")

        with patch("dependencies.gemini.settings", _settings()), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.side_effect = make
            assert gemini.generate("p") == "recovered"

        assert len(calls) == 2

    def test_an_unrecognised_failure_is_not_retried_at_all(self):
        calls = []

        def make(model_name, generation_config=None):
            calls.append(model_name)
            raise RuntimeError("500 internal server error")

        with patch("dependencies.gemini.settings", _settings()), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.side_effect = make
            with pytest.raises(RuntimeError, match="500"):
                gemini.generate("p")

        # Not a missing-model or quota/auth signature, so it is treated as a
        # real failure and surfaced immediately rather than burning through
        # every remaining model.
        assert len(calls) == 1

    def test_the_last_error_surfaces_when_every_model_is_missing(self):
        with patch("dependencies.gemini.settings", _settings()), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.side_effect = RuntimeError("404 not found")
            with pytest.raises(RuntimeError, match="404"):
                gemini.generate("p")

    def test_json_mode_asks_for_json(self):
        with patch("dependencies.gemini.settings", _settings()), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.return_value = _model("{}")
            gemini.generate("p", json_mode=True)
            _, kwargs = g.GenerativeModel.call_args
            assert kwargs["generation_config"] == {
                "response_mime_type": "application/json"
            }

    def test_plain_mode_sends_no_generation_config(self):
        with patch("dependencies.gemini.settings", _settings()), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.return_value = _model("text")
            gemini.generate("p")
            _, kwargs = g.GenerativeModel.call_args
            assert kwargs["generation_config"] is None

    def test_no_keys_configured_fails_before_any_call(self):
        with patch("dependencies.gemini.settings", _settings(primary="", fallback="")), \
                patch("dependencies.gemini.genai") as g:
            with pytest.raises(RuntimeError, match="not configured"):
                gemini.generate("p")
            g.GenerativeModel.assert_not_called()

    def test_every_call_is_bounded_by_a_timeout(self):
        # The bug this guards: generate_content() had no timeout at all, so a
        # stalled connection to Google's API hung the whole request
        # indefinitely - confirmed live, past three minutes with no response.
        with patch("dependencies.gemini.settings", _settings()), \
                patch("dependencies.gemini.genai") as g:
            instance = _model("hello")
            g.GenerativeModel.return_value = instance
            gemini.generate("p")
            _, kwargs = instance.generate_content.call_args
            assert "request_options" in kwargs
            assert kwargs["request_options"]["timeout"] == gemini._REQUEST_TIMEOUT_SECONDS
            # Farmers give up long before this; so does a demo audience.
            assert 0 < gemini._REQUEST_TIMEOUT_SECONDS <= 20


class TestKeyFallback:
    """The behaviour this whole change exists for: the primary key's free
    tier runs out mid-demo, and the app keeps answering instead of going
    dark."""

    def test_a_quota_error_tries_every_model_on_the_same_key_before_switching(self):
        configured_keys = []

        def track_configure(api_key):
            configured_keys.append(api_key)

        calls = []

        def make(model_name, generation_config=None):
            calls.append((configured_keys[-1], model_name))
            if configured_keys[-1] == "primary-key":
                raise RuntimeError("429 Resource has been exhausted (quota)")
            return _model("answered on the fallback key")

        with patch("dependencies.gemini.settings",
                   _settings(primary="primary-key", fallback="fallback-key")), \
                patch("dependencies.gemini.genai") as g:
            g.configure.side_effect = track_configure
            g.GenerativeModel.side_effect = make
            result = gemini.generate("p")

        assert result == "answered on the fallback key"
        # Every candidate model gets one attempt on the exhausted key before
        # the fallback key is touched at all - quota is tracked per model, so
        # a 429 on one candidate says nothing about whether the others still
        # have budget left.
        primary_attempts = [c for c in calls if c[0] == "primary-key"]
        assert len(primary_attempts) == len(gemini.candidate_models())

    def test_an_auth_error_also_switches_keys(self):
        attempt = {"n": 0}

        def make(model_name, generation_config=None):
            attempt["n"] += 1
            if attempt["n"] == 1:
                raise RuntimeError("401 API_KEY_INVALID: API key not valid")
            return _model("ok")

        with patch("dependencies.gemini.settings",
                   _settings(primary="bad-key", fallback="good-key")), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.side_effect = make
            assert gemini.generate("p") == "ok"

    def test_missing_model_does_not_burn_through_keys(self):
        # A model that does not exist, does not exist on either key - this
        # should exhaust the model list on key one before ever touching key
        # two, not alternate between them.
        configured_keys = []

        def track_configure(api_key):
            configured_keys.append(api_key)

        calls = []

        def make(model_name, generation_config=None):
            calls.append(configured_keys[-1])
            raise RuntimeError("404 not found")

        with patch("dependencies.gemini.settings",
                   _settings(primary="k1", fallback="k2")), \
                patch("dependencies.gemini.genai") as g:
            g.configure.side_effect = track_configure
            g.GenerativeModel.side_effect = make
            with pytest.raises(RuntimeError, match="404"):
                gemini.generate("p")

        # All of key one's models exhausted (4 candidates), then all of key
        # two's.
        assert calls.count("k1") == len(gemini.candidate_models())
        assert calls.count("k2") == len(gemini.candidate_models())

    def test_both_keys_exhausted_surfaces_the_last_error(self):
        with patch("dependencies.gemini.settings",
                   _settings(primary="k1", fallback="k2")), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.side_effect = RuntimeError("429 quota exceeded")
            with pytest.raises(RuntimeError, match="429"):
                gemini.generate("p")

    def test_with_no_fallback_key_a_quota_error_still_tries_every_model(self):
        calls = []

        def make(model_name, generation_config=None):
            calls.append(model_name)
            raise RuntimeError("429 quota exceeded")

        with patch("dependencies.gemini.settings", _settings(primary="k1", fallback="")), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.side_effect = make
            with pytest.raises(RuntimeError, match="429"):
                gemini.generate("p")

        # No second key configured doesn't change quota behaviour - every
        # candidate model on the one available key still gets tried, since
        # each may have its own untouched daily budget.
        assert len(calls) == len(gemini.candidate_models())


class TestMissingModelDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "404 models/gemini-1.5-flash is not found for API version v1beta",
            "Model NOT FOUND",
            "is not supported for generateContent",
        ],
    )
    def test_recognises_a_retired_model(self, message):
        assert gemini._is_missing_model(RuntimeError(message)) is True

    @pytest.mark.parametrize(
        "message",
        ["429 quota exceeded", "401 invalid api key", "500 internal error"],
    )
    def test_does_not_mistake_other_failures_for_a_retired_model(self, message):
        assert gemini._is_missing_model(RuntimeError(message)) is False


class TestQuotaDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "429 Resource has been exhausted (quota)",
            "RESOURCE_EXHAUSTED",
            "rate limit exceeded, try again later",
        ],
    )
    def test_recognises_a_per_model_quota_failure(self, message):
        assert gemini._is_quota_error(RuntimeError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "404 models/gemini-1.5-flash is not found",
            "500 internal server error",
            "connection reset by peer",
            "401 Unauthorized",
            "403 PERMISSION_DENIED",
        ],
    )
    def test_does_not_mistake_other_failures_for_a_quota_problem(self, message):
        assert gemini._is_quota_error(RuntimeError(message)) is False


class TestAuthDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "401 Unauthorized",
            "403 PERMISSION_DENIED",
            "API_KEY_INVALID: API key not valid",
        ],
    )
    def test_recognises_a_key_level_failure(self, message):
        assert gemini._is_auth_error(RuntimeError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "404 models/gemini-1.5-flash is not found",
            "500 internal server error",
            "connection reset by peer",
            "429 quota exceeded",
        ],
    )
    def test_does_not_mistake_other_failures_for_a_key_problem(self, message):
        assert gemini._is_auth_error(RuntimeError(message)) is False


class TestTimeoutDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "504 Deadline Exceeded",
            "DeadlineExceeded: 20.0s timeout",
            "Request timed out",
            "503 The service is currently unavailable",
        ],
    )
    def test_recognises_a_stalled_call(self, message):
        assert gemini._is_timeout(RuntimeError(message)) is True

    @pytest.mark.parametrize(
        "message",
        ["429 quota exceeded", "404 not found", "401 invalid api key"],
    )
    def test_does_not_mistake_other_failures_for_a_timeout(self, message):
        assert gemini._is_timeout(RuntimeError(message)) is False

    def test_a_timeout_tries_the_next_model_on_the_same_key_before_switching(self):
        # The bug this guards against: gemini-flash-latest (tried first)
        # times out, and a dashboard shows the OTHER candidate models on the
        # same key sitting completely unused - so the retry has to reach them
        # before giving up on the key, not jump to the fallback key on the
        # very first timeout.
        configured_keys = []

        def track_configure(api_key):
            configured_keys.append(api_key)

        calls = []

        def make(model_name, generation_config=None):
            calls.append((configured_keys[-1], model_name))
            if model_name == gemini.candidate_models()[0]:
                raise RuntimeError("504 Deadline Exceeded")
            return _model("answered by a less-loaded model on the same key")

        with patch("dependencies.gemini.settings",
                   _settings(primary="primary-key", fallback="fallback-key")), \
                patch("dependencies.gemini.genai") as g:
            g.configure.side_effect = track_configure
            g.GenerativeModel.side_effect = make
            result = gemini.generate("p")

        assert result == "answered by a less-loaded model on the same key"
        # Recovered on the SAME key's second candidate - the fallback key was
        # never even configured against.
        assert all(c[0] == "primary-key" for c in calls)
        assert len(calls) == 2

    def test_a_timeout_on_every_model_eventually_falls_through_to_the_next_key(self):
        attempt = {"n": 0}

        def make(model_name, generation_config=None):
            attempt["n"] += 1
            # Every model on the first key times out; the fallback key's
            # first model recovers.
            if attempt["n"] <= len(gemini.candidate_models()):
                raise RuntimeError("504 Deadline Exceeded")
            return _model("recovered on the fallback key")

        with patch("dependencies.gemini.settings",
                   _settings(primary="slow-key", fallback="good-key")), \
                patch("dependencies.gemini.genai") as g:
            g.GenerativeModel.side_effect = make
            assert gemini.generate("p") == "recovered on the fallback key"
        # Every candidate model got tried on the slow key before the fallback
        # key was touched at all.
        assert attempt["n"] == len(gemini.candidate_models()) + 1
