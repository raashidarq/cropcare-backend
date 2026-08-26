"""
Tests for dependencies/gemini.py.

This module exists because of a real production outage: both routers hardcoded
`gemini-1.5-flash`, that name stopped being served on the v1beta endpoint Google
AI Studio keys use, and treatment guidance and chat both went dark at once with
no fix short of a code change and redeploy.
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


class TestCandidateModels:
    def test_configured_model_is_tried_first(self):
        with patch("dependencies.gemini.settings") as s:
            s.gemini_model = "gemini-custom"
            assert gemini.candidate_models()[0] == "gemini-custom"

    def test_fallbacks_follow_the_configured_one(self):
        with patch("dependencies.gemini.settings") as s:
            s.gemini_model = "gemini-custom"
            names = gemini.candidate_models()
        # A single configured name that Google retires would otherwise take the
        # whole app down again.
        assert len(names) > 1

    def test_no_duplicates_when_configured_matches_a_fallback(self):
        with patch("dependencies.gemini.settings") as s:
            s.gemini_model = "gemini-flash-latest"
            names = gemini.candidate_models()
        assert len(names) == len(set(names))

    def test_unset_config_falls_back_to_the_list(self):
        with patch("dependencies.gemini.settings") as s:
            s.gemini_model = ""
            names = gemini.candidate_models()
        # The alias that tracks Google's current fast model leads, precisely so
        # a rename does not need a deploy.
        assert names[0] == "gemini-flash-latest"


class TestGenerate:
    def test_returns_text_from_the_first_working_model(self):
        with patch("dependencies.gemini.settings") as s, \
                patch("dependencies.gemini.genai") as g:
            s.gemini_api_key = "k"
            s.gemini_model = ""
            g.GenerativeModel.return_value = _model("hello")
            assert gemini.generate("prompt") == "hello"

    def test_a_missing_model_falls_through_to_the_next(self):
        calls = []

        def make(model_name, generation_config=None):
            calls.append(model_name)
            if len(calls) == 1:
                raise RuntimeError(
                    "404 models/gemini-flash-latest is not found for API version v1beta"
                )
            return _model("recovered")

        with patch("dependencies.gemini.settings") as s, \
                patch("dependencies.gemini.genai") as g:
            s.gemini_api_key = "k"
            s.gemini_model = ""
            g.GenerativeModel.side_effect = make
            assert gemini.generate("p") == "recovered"

        assert len(calls) == 2

    def test_a_real_failure_is_not_retried_against_every_model(self):
        calls = []

        def make(model_name, generation_config=None):
            calls.append(model_name)
            raise RuntimeError("429 quota exceeded")

        with patch("dependencies.gemini.settings") as s, \
                patch("dependencies.gemini.genai") as g:
            s.gemini_api_key = "k"
            s.gemini_model = ""
            g.GenerativeModel.side_effect = make
            with pytest.raises(RuntimeError, match="quota"):
                gemini.generate("p")

        # A quota error fails identically on every candidate; hammering four
        # models to discover that wastes the farmer's time.
        assert len(calls) == 1

    def test_the_last_error_surfaces_when_every_model_is_missing(self):
        with patch("dependencies.gemini.settings") as s, \
                patch("dependencies.gemini.genai") as g:
            s.gemini_api_key = "k"
            s.gemini_model = ""
            g.GenerativeModel.side_effect = RuntimeError("404 not found")
            with pytest.raises(RuntimeError, match="404"):
                gemini.generate("p")

    def test_json_mode_asks_for_json(self):
        with patch("dependencies.gemini.settings") as s, \
                patch("dependencies.gemini.genai") as g:
            s.gemini_api_key = "k"
            s.gemini_model = ""
            g.GenerativeModel.return_value = _model("{}")
            gemini.generate("p", json_mode=True)
            _, kwargs = g.GenerativeModel.call_args
            assert kwargs["generation_config"] == {
                "response_mime_type": "application/json"
            }

    def test_plain_mode_sends_no_generation_config(self):
        with patch("dependencies.gemini.settings") as s, \
                patch("dependencies.gemini.genai") as g:
            s.gemini_api_key = "k"
            s.gemini_model = ""
            g.GenerativeModel.return_value = _model("text")
            gemini.generate("p")
            _, kwargs = g.GenerativeModel.call_args
            assert kwargs["generation_config"] is None

    def test_a_missing_api_key_fails_before_any_call(self):
        with patch("dependencies.gemini.settings") as s, \
                patch("dependencies.gemini.genai") as g:
            s.gemini_api_key = ""
            with pytest.raises(RuntimeError, match="API key"):
                gemini.generate("p")
            g.GenerativeModel.assert_not_called()


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
