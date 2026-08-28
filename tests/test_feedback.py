"""
Feedback endpoint tests (POST /feedback).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt
from fastapi.testclient import TestClient


def _make_valid_jwt(secret: str = "testsecret", user_id: str = "user-uuid-1234") -> str:
    payload = {
        "sub": user_id,
        "aud": "authenticated",  # every real Supabase token carries this
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class TestFeedbackEndpoint:
    def test_submit_feedback_guest_success(self):
        """Guest user without Authorization header can submit feedback."""
        with patch("config.settings") as mock_settings:
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            mock_table = MagicMock()
            mock_table.insert.return_value = mock_table
            mock_table.execute.return_value = MagicMock(data=[{"id": "fb-1"}])

            mock_supabase = MagicMock()
            mock_supabase.table.return_value = mock_table

            with patch("routers.feedback.create_client", return_value=mock_supabase):
                from main import app
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/feedback",
                    json={
                        "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
                        "category": "suggestion",
                        "message": "The diagnosis for Paddy blast was helpful. Please add advice on bio-pesticides.",
                        "timestamp": "2026-08-25T10:15:30.000Z",
                    },
                )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["message"] == "Feedback received"

        mock_supabase.table.assert_called_once_with("feedback")
        mock_table.insert.assert_called_once_with({
            "category": "suggestion",
            "message": "The diagnosis for Paddy blast was helpful. Please add advice on bio-pesticides.",
            "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
            "created_at": "2026-08-25T10:15:30.000Z",
        })

    def test_submit_feedback_authenticated_user(self):
        """Authenticated user's JWT user_id is automatically stamped."""
        valid_jwt = _make_valid_jwt(secret="testsecret", user_id="auth-user-999")

        with patch("config.settings") as mock_settings:
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            mock_table = MagicMock()
            mock_table.insert.return_value = mock_table
            mock_table.execute.return_value = MagicMock(data=[{"id": "fb-2"}])

            mock_supabase = MagicMock()
            mock_supabase.table.return_value = mock_table

            with patch("routers.feedback.create_client", return_value=mock_supabase):
                from main import app
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/feedback",
                    headers={"Authorization": f"Bearer {valid_jwt}"},
                    json={
                        "category": "bug",
                        "message": "Camera screen freezes on low light.",
                    },
                )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        mock_table.insert.assert_called_once_with({
            "category": "bug",
            "message": "Camera screen freezes on low light.",
            "user_id": "auth-user-999",
        })

    def test_submit_feedback_empty_message_returns_422(self):
        """Missing or empty message fails Pydantic validation with 422."""
        with patch("config.settings") as mock_settings:
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            from main import app
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/feedback",
                json={"category": "general", "message": ""},
            )

        assert response.status_code == 422

    def test_submit_feedback_db_error_still_returns_200(self):
        """Best-effort logging: DB insert error does not fail the client response."""
        with patch("config.settings") as mock_settings:
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            mock_table = MagicMock()
            mock_table.insert.side_effect = RuntimeError("Supabase connection error")

            mock_supabase = MagicMock()
            mock_supabase.table.return_value = mock_table

            with patch("routers.feedback.create_client", return_value=mock_supabase):
                from main import app
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/feedback",
                    json={"message": "Great app overall!"},
                )

        assert response.status_code == 200
        assert response.json()["status"] == "success"
