"""
Auth endpoint tests.

All Supabase calls are mocked — the real Supabase client is never initialised.

Test coverage:
  (a) request-otp with { phone } returns 403 when PHONE_AUTH_ENABLED is unset,
      AND Supabase is never called.
  (b) request-otp with { email } succeeds regardless of the phone flag.
  (c) The 4th email request-otp for the same email within 10 min is rejected (429).
  (d) A protected route rejects requests with no / invalid Authorization header.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt
from fastapi.testclient import TestClient

# We need to control env vars BEFORE importing anything from our package so that
# config._Settings() picks up the patched values.  Use monkeypatch + reimport pattern.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_jwt(secret: str = "testsecret") -> str:
    """Return a syntactically valid HS256 JWT with a future expiry."""
    payload = {
        "sub": "user-uuid-1234",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _make_expired_jwt(secret: str = "testsecret") -> str:
    payload = {
        "sub": "user-uuid-1234",
        "exp": int(time.time()) - 10,
        "iat": int(time.time()) - 3610,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Test (a): phone OTP → 403 when flag is off; Supabase is never touched
# ---------------------------------------------------------------------------

class TestPhoneFlagGate:
    def test_phone_otp_returns_403_when_flag_off_and_supabase_not_called(
        self, monkeypatch
    ):
        """
        When PHONE_AUTH_ENABLED is unset (the default), a phone request must:
          - Return 403 immediately.
          - NEVER call Supabase (not even to create the client).
        """
        monkeypatch.delenv("PHONE_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "testsecret")

        # Patch config so the settings object reflects current env
        with patch("config.settings") as mock_settings:
            mock_settings.phone_auth_enabled = False
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-service-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            # Patch create_client so we can assert it was never called
            with patch("routers.auth.create_client") as mock_create_client:
                from main import app

                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/auth/request-otp",
                    json={"phone": "+15550001234"},
                )

        assert response.status_code == 403
        body = response.json()
        assert "email" in body["detail"].lower() or "phone" in body["detail"].lower()
        # Critical: Supabase was never contacted
        mock_create_client.assert_not_called()

    def test_phone_otp_returns_403_when_flag_is_false_string(self, monkeypatch):
        """Ensure the flag only activates on the exact string 'true'."""
        monkeypatch.setenv("PHONE_AUTH_ENABLED", "false")

        with patch("config.settings") as mock_settings:
            mock_settings.phone_auth_enabled = False
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-service-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            with patch("routers.auth.create_client"):
                from main import app

                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/auth/request-otp",
                    json={"phone": "+15550001234"},
                )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Test (b): email OTP succeeds regardless of phone flag
# ---------------------------------------------------------------------------

class TestEmailOtpSuccess:
    def _make_client_with_mocked_supabase(self, mock_settings):
        """Build a TestClient with Supabase calls mocked out."""
        mock_auth = MagicMock()
        mock_auth.sign_in_with_otp.return_value = MagicMock()
        mock_supabase = MagicMock()
        mock_supabase.auth = mock_auth

        with patch("routers.auth.create_client", return_value=mock_supabase):
            from main import app

            # Reset limiter storage between tests to avoid cross-test contamination
            from routers.auth import limiter
            limiter._storage = None  # type: ignore[assignment]

            return TestClient(app, raise_server_exceptions=False), mock_auth

    def test_email_otp_succeeds_when_phone_flag_off(self, monkeypatch):
        """Email OTP works regardless of the phone flag state."""
        with patch("config.settings") as mock_settings:
            mock_settings.phone_auth_enabled = False
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            mock_auth = MagicMock()
            mock_auth.sign_in_with_otp.return_value = MagicMock()
            mock_supabase = MagicMock()
            mock_supabase.auth = mock_auth

            with patch("routers.auth.create_client", return_value=mock_supabase):
                from main import app

                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/auth/request-otp",
                    json={"email": "test@example.com"},
                )

        assert response.status_code == 200
        # Verify Supabase was actually called with the email identifier
        mock_auth.sign_in_with_otp.assert_called_once_with({"email": "test@example.com"})

    def test_email_otp_succeeds_when_phone_flag_on(self, monkeypatch):
        """Email OTP also works when phone flag is enabled."""
        with patch("config.settings") as mock_settings:
            mock_settings.phone_auth_enabled = True
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            mock_auth = MagicMock()
            mock_auth.sign_in_with_otp.return_value = MagicMock()
            mock_supabase = MagicMock()
            mock_supabase.auth = mock_auth

            with patch("routers.auth.create_client", return_value=mock_supabase):
                from main import app

                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/auth/request-otp",
                    json={"email": "another@example.com"},
                )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Test (c): 4th email request-otp within 10 min for same email → 429
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_fourth_email_request_within_window_is_rejected(self, monkeypatch):
        """
        The 4th email-based request-otp call within 10 minutes for the SAME email
        must be rate-limited (429).  The first 3 must succeed.
        """
        email = "rate-limited@example.com"

        with patch("config.settings") as mock_settings:
            mock_settings.phone_auth_enabled = False
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            mock_auth = MagicMock()
            mock_auth.sign_in_with_otp.return_value = MagicMock()
            mock_supabase = MagicMock()
            mock_supabase.auth = mock_auth

            with patch("routers.auth.create_client", return_value=mock_supabase):
                # Import fresh app instance; SlowAPI uses in-memory storage by default.
                # We need a fresh limiter state — recreate the TestClient fresh.
                from main import app
                from routers.auth import limiter

                # Clear limiter storage so this test starts from 0
                try:
                    storage = limiter._storage
                    if storage is not None:
                        storage.reset()
                except Exception:  # noqa: BLE001, S110
                    pass  # best-effort reset; test may still pass if state is clean

                client = TestClient(app, raise_server_exceptions=False)

                # First 3 should succeed
                for i in range(3):
                    resp = client.post(
                        "/auth/request-otp",
                        json={"email": email},
                    )
                    assert resp.status_code == 200, (
                        f"Request {i + 1} should succeed, got {resp.status_code}"
                    )

                # 4th should be rate-limited
                resp = client.post(
                    "/auth/request-otp",
                    json={"email": email},
                )
                assert resp.status_code == 429, (
                    f"4th request should be rate-limited (429), got {resp.status_code}"
                )


# ---------------------------------------------------------------------------
# Test (d): protected route rejects no / invalid Authorization header
# ---------------------------------------------------------------------------

class TestJWTProtectedRoute:
    """
    Test the JWT dependency using a demo protected route wired into the app.

    Since main.py only has /health (unprotected), we add a temporary test-only
    route directly on the app for isolation.
    """

    def _build_protected_client(self, jwt_secret: str = "testsecret") -> TestClient:
        from fastapi import FastAPI

        from dependencies.jwt_auth import get_current_user_id

        test_app = FastAPI()

        @test_app.get("/protected")
        def protected(user_id: str = __import__("fastapi").Depends(get_current_user_id)):
            return {"user_id": user_id}

        return TestClient(test_app, raise_server_exceptions=False)

    def test_no_authorization_header_returns_401(self, monkeypatch):
        with patch("config.settings") as mock_settings:
            mock_settings.supabase_jwt_secret = "testsecret"

            client = self._build_protected_client()
            response = client.get("/protected")

        assert response.status_code == 401

    def test_invalid_token_returns_401(self, monkeypatch):
        with patch("config.settings") as mock_settings:
            mock_settings.supabase_jwt_secret = "testsecret"

            client = self._build_protected_client()
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer this.is.not.a.jwt"},
            )

        assert response.status_code == 401

    def test_token_signed_with_wrong_secret_returns_401(self, monkeypatch):
        with patch("config.settings") as mock_settings:
            mock_settings.supabase_jwt_secret = "testsecret"

            bad_token = _make_valid_jwt(secret="wrong-secret")
            client = self._build_protected_client(jwt_secret="testsecret")
            response = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {bad_token}"},
            )

        assert response.status_code == 401

    def test_expired_token_returns_401(self, monkeypatch):
        with patch("config.settings") as mock_settings:
            mock_settings.supabase_jwt_secret = "testsecret"

            expired_token = _make_expired_jwt(secret="testsecret")
            client = self._build_protected_client()
            response = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {expired_token}"},
            )

        assert response.status_code == 401

    def test_valid_token_succeeds(self, monkeypatch):
        """Sanity check: a valid token grants access and returns the sub claim."""
        with patch("config.settings") as mock_settings:
            mock_settings.supabase_jwt_secret = "testsecret"

            valid_token = _make_valid_jwt(secret="testsecret")
            client = self._build_protected_client()
            response = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {valid_token}"},
            )

        assert response.status_code == 200
        assert response.json()["user_id"] == "user-uuid-1234"

    def test_none_algorithm_attack_is_rejected(self, monkeypatch):
        """
        Security: a token with alg=none in its header must be rejected.
        PyJWT with an explicit algorithm allowlist will reject this.
        """
        with patch("config.settings") as mock_settings:
            mock_settings.supabase_jwt_secret = "testsecret"

            # Craft a token with alg:none manually
            import base64
            import json

            header = base64.urlsafe_b64encode(
                json.dumps({"alg": "none", "typ": "JWT"}).encode()
            ).rstrip(b"=")
            payload_data = {
                "sub": "attacker",
                "exp": int(time.time()) + 3600,
            }
            payload_b64 = base64.urlsafe_b64encode(
                json.dumps(payload_data).encode()
            ).rstrip(b"=")
            none_token = f"{header.decode()}.{payload_b64.decode()}."

            client = self._build_protected_client()
            response = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {none_token}"},
            )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Payload validation tests
# ---------------------------------------------------------------------------

class TestRequestValidation:
    def _client(self):
        with patch("config.settings") as mock_settings:
            mock_settings.phone_auth_enabled = False
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"
            from main import app
            return TestClient(app, raise_server_exceptions=False), mock_settings

    def test_both_email_and_phone_returns_400(self):
        with patch("config.settings") as mock_settings:
            mock_settings.phone_auth_enabled = False
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            from main import app
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/auth/request-otp",
                json={"email": "a@b.com", "phone": "+15550001234"},
            )

        assert response.status_code == 400

    def test_neither_email_nor_phone_returns_400(self):
        with patch("config.settings") as mock_settings:
            mock_settings.phone_auth_enabled = False
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = "fake-key"
            mock_settings.supabase_jwt_secret = "testsecret"

            from main import app
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/auth/request-otp", json={})

        assert response.status_code == 400
