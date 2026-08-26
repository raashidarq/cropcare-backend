"""
Tests for the step-shaped guidance added to POST /interpret-diagnosis.

The endpoint used to ask the model for three prose blobs, so the app rendered
paragraphs a farmer had to parse standing in a field. Guidance is now authored
as short ordered steps, with the prose fields derived from them so older
clients keep working.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from main import app
from routers.diagnosis import DiagnosisInterpretationRequest, _as_steps, _build_prompt

client = TestClient(app, raise_server_exceptions=False)


def _payload(**overrides):
    body = {
        "crop_id": "tomato",
        "disease_id": "tomato_late_blight",
        "confidence": 0.91,
        "severity": "high",
        "language_code": "en",
    }
    body.update(overrides)
    return body


def _mock_gemini(mock_model_cls, payload):
    mock_response = MagicMock()
    mock_response.text = json.dumps(payload) if isinstance(payload, dict) else payload
    mock_instance = MagicMock()
    mock_instance.generate_content.return_value = mock_response
    mock_model_cls.return_value = mock_instance
    return mock_instance


_GOOD = {
    "summary": "Late blight is a fast fungus that rots leaves and fruit.",
    "what_to_do_steps": [
        "Remove and burn infected leaves today.",
        "Spray copper fungicide in the evening; wear a mask and gloves.",
        "Water at the base, never over the leaves.",
    ],
    "what_to_avoid_steps": [
        "Do not compost infected plants.",
        "Do not spray in the midday sun.",
    ],
    "recheck_after_days": 5,
}


class TestStepShapedGuidance:
    @patch("routers.diagnosis.genai.GenerativeModel")
    def test_steps_are_returned_as_a_list(self, mock_model_cls):
        _mock_gemini(mock_model_cls, _GOOD)

        with patch("routers.diagnosis.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-key"
            mock_settings.supabase_url = ""
            mock_settings.supabase_service_role_key = ""
            response = client.post("/interpret-diagnosis", json=_payload())

        assert response.status_code == 200
        data = response.json()
        assert data["what_to_do_steps"] == _GOOD["what_to_do_steps"]
        assert data["what_to_avoid_steps"] == _GOOD["what_to_avoid_steps"]

    @patch("routers.diagnosis.genai.GenerativeModel")
    def test_prose_fields_are_derived_for_older_clients(self, mock_model_cls):
        _mock_gemini(mock_model_cls, _GOOD)

        with patch("routers.diagnosis.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-key"
            mock_settings.supabase_url = ""
            mock_settings.supabase_service_role_key = ""
            response = client.post("/interpret-diagnosis", json=_payload())

        data = response.json()
        # A client that predates the step lists still gets usable prose.
        assert "Remove and burn infected leaves today." in data["what_to_do"]
        assert "Do not compost infected plants." in data["what_to_avoid"]

    @patch("routers.diagnosis.genai.GenerativeModel")
    def test_a_model_that_ignores_the_schema_still_works(self, mock_model_cls):
        # LLMs return the wrong shape often enough that this must not 500.
        _mock_gemini(
            mock_model_cls,
            {
                "summary": "A fungus.",
                "what_to_do": "1. Remove leaves\n2. Spray copper\n- Water at base",
                "what_to_avoid": "Do not compost; do not spray at noon",
                "recheck_after_days": 5,
            },
        )

        with patch("routers.diagnosis.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-key"
            mock_settings.supabase_url = ""
            mock_settings.supabase_service_role_key = ""
            response = client.post("/interpret-diagnosis", json=_payload())

        assert response.status_code == 200
        data = response.json()
        assert data["what_to_do_steps"] == [
            "Remove leaves",
            "Spray copper",
            "Water at base",
        ]
        assert data["what_to_avoid_steps"] == [
            "Do not compost",
            "do not spray at noon",
        ]


class TestPromptQuality:
    def _prompt(self, **overrides):
        return _build_prompt(
            DiagnosisInterpretationRequest(**_payload(**overrides))
        )

    def test_prompt_states_who_it_is_writing_for(self):
        prompt = self._prompt()
        # The old prompt said only "a farmer", which is why answers read like
        # an extension bulletin.
        assert "Sri Lanka" in prompt
        assert "read slowly" in prompt

    def test_prompt_caps_step_length_and_count(self):
        prompt = self._prompt()
        assert "14 words" in prompt
        assert "3 to 5 steps" in prompt

    def test_prompt_orders_by_urgency_and_cost(self):
        prompt = self._prompt()
        assert "urgency" in prompt
        assert "costs nothing first" in prompt

    def test_prompt_requires_safety_beside_any_chemical(self):
        prompt = self._prompt()
        assert "SAME step" in prompt

    def test_low_confidence_keeps_advice_cheap_and_reversible(self):
        prompt = self._prompt(confidence=0.41)
        # The classifier has no rejection option, so an uncertain result must
        # not send someone out to buy chemicals.
        assert "UNCERTAIN" in prompt
        assert "reversible" in prompt

    def test_confident_result_still_refuses_to_sound_verified(self):
        prompt = self._prompt(confidence=0.95)
        assert "one photograph" in prompt

    def test_prompt_forbids_leaking_its_own_instructions(self):
        prompt = self._prompt()
        assert "Do not mention this app" in prompt


class TestStepCoercion:
    def test_list_passes_through_cleanly(self):
        assert _as_steps(["  Do this  ", "Do that"], None) == ["Do this", "Do that"]

    def test_numbered_prefixes_are_stripped(self):
        assert _as_steps(["1. First", "2) Second"], None) == ["First", "Second"]

    def test_bullets_are_stripped(self):
        assert _as_steps(["- First", "* Second"], None) == ["First", "Second"]

    def test_empty_entries_are_dropped(self):
        assert _as_steps(["Real step", "", "   "], None) == ["Real step"]

    def test_falls_back_to_prose_when_steps_are_absent(self):
        assert _as_steps(None, "One thing; another thing") == [
            "One thing",
            "another thing",
        ]

    def test_nothing_at_all_yields_an_empty_list(self):
        assert _as_steps(None, None) == []
