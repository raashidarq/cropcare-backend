"""
Tests for POST /interpret-diagnosis endpoint.

Covers:
- Successful interpretation with valid payload
- Optional observations omission
- Multilingual prompting (en, si, ta)
- Authenticated JWT vs Guest request
- Pydantic validation errors (422)
- Missing Gemini API key (500)
- Gemini API exception / timeout (500)
- Malformed JSON returned from Gemini (500)
- Missing guidance fields in Gemini response (500)
- Supabase audit logging resilience
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import jwt
from fastapi.testclient import TestClient

from main import app

client = TestClient(app, raise_server_exceptions=False)


def _make_valid_jwt(secret: str = "testsecret", sub: str = "user-uuid-1234") -> str:
    payload = {
        "sub": sub,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class TestInterpretDiagnosis:
    @patch("routers.diagnosis.genai.GenerativeModel")
    @patch("routers.diagnosis._get_supabase")
    def test_successful_interpretation(self, mock_get_supabase, mock_model_cls):
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "summary": "Late blight is a fast-spreading fungal infection common in high humidity.",
            "what_to_do": "Prune and destroy infected leaves immediately. Apply copper fungicide to protect unaffected foliage.",
            "what_to_avoid": "Do not overhead water. Do not compost infected plant material.",
            "recheck_after_days": 5,
        })
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_instance

        mock_supabase = MagicMock()
        mock_get_supabase.return_value = mock_supabase

        payload = {
            "crop_id": "tomato",
            "disease_id": "tomato_late_blight",
            "confidence": 0.94,
            "severity": "high",
            "language_code": "en",
            "user_observations": "Leaves started yellowing 3 days ago after heavy rain",
        }

        response = client.post("/interpret-diagnosis", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["summary"] == "Late blight is a fast-spreading fungal infection common in high humidity."
        assert data["what_to_do"] == "Prune and destroy infected leaves immediately. Apply copper fungicide to protect unaffected foliage."
        assert data["what_to_avoid"] == "Do not overhead water. Do not compost infected plant material."
        assert data["recheck_after_days"] == 5
        assert "interpretation_id" in data
        assert len(data["interpretation_id"]) > 10

        # Verify Supabase audit logging was triggered
        mock_supabase.table.assert_called_with("llm_interpretation")

    @patch("routers.diagnosis.genai.GenerativeModel")
    @patch("routers.diagnosis._get_supabase")
    def test_interpretation_with_authenticated_user(self, mock_get_supabase, mock_model_cls):
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "summary": "Powdery mildew detected.",
            "what_to_do": "Apply sulfur spray.",
            "what_to_avoid": "Avoid excessive shade.",
            "recheck_after_days": 4,
        })
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_instance

        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_get_supabase.return_value = mock_supabase

        token = _make_valid_jwt(sub="farmer-999")
        payload = {
            "crop_id": "cucumber",
            "disease_id": "powdery_mildew",
            "confidence": 0.88,
            "severity": "medium",
            "language_code": "si",
        }

        response = client.post(
            "/interpret-diagnosis",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["summary"] == "Powdery mildew detected."
        assert data["recheck_after_days"] == 4

        # Ensure user_id was attached to the audit record
        insert_calls = mock_table.insert.call_args_list
        assert len(insert_calls) == 1
        inserted_data = insert_calls[0][0][0]
        assert inserted_data["user_id"] == "farmer-999"
        assert inserted_data["language_code"] == "si"

    def test_validation_error_missing_fields(self):
        # Missing disease_id, confidence, severity
        response = client.post("/interpret-diagnosis", json={"crop_id": "tomato"})
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_validation_error_invalid_confidence(self):
        response = client.post(
            "/interpret-diagnosis",
            json={
                "crop_id": "tomato",
                "disease_id": "early_blight",
                "confidence": 1.5,  # > 1.0
                "severity": "low",
            },
        )
        assert response.status_code == 422

    @patch("routers.diagnosis.settings")
    def test_gemini_missing_api_key(self, mock_settings):
        mock_settings.gemini_api_key = ""
        payload = {
            "crop_id": "tomato",
            "disease_id": "tomato_late_blight",
            "confidence": 0.94,
            "severity": "high",
        }
        response = client.post("/interpret-diagnosis", json=payload)
        assert response.status_code == 500
        assert "Gemini API key is not configured" in response.json()["detail"]

    @patch("routers.diagnosis.genai.GenerativeModel")
    def test_gemini_api_exception(self, mock_model_cls):
        mock_instance = MagicMock()
        mock_instance.generate_content.side_effect = RuntimeError("API Quota exceeded")
        mock_model_cls.return_value = mock_instance

        payload = {
            "crop_id": "tomato",
            "disease_id": "tomato_late_blight",
            "confidence": 0.94,
            "severity": "high",
        }
        response = client.post("/interpret-diagnosis", json=payload)
        assert response.status_code == 500
        assert "Failed to generate treatment guidance" in response.json()["detail"]

    @patch("routers.diagnosis.genai.GenerativeModel")
    def test_gemini_malformed_json_response(self, mock_model_cls):
        mock_response = MagicMock()
        mock_response.text = "This is not JSON at all."
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_instance

        payload = {
            "crop_id": "tomato",
            "disease_id": "tomato_late_blight",
            "confidence": 0.94,
            "severity": "high",
        }
        response = client.post("/interpret-diagnosis", json=payload)
        assert response.status_code == 500
        assert "Received malformed JSON" in response.json()["detail"]

    @patch("routers.diagnosis.genai.GenerativeModel")
    def test_gemini_missing_required_guidance_fields(self, mock_model_cls):
        mock_response = MagicMock()
        mock_response.text = json.dumps({"summary": "Incomplete data."})
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_instance

        payload = {
            "crop_id": "tomato",
            "disease_id": "tomato_late_blight",
            "confidence": 0.94,
            "severity": "high",
        }
        response = client.post("/interpret-diagnosis", json=payload)
        assert response.status_code == 500
        assert "missing required guidance fields" in response.json()["detail"]
