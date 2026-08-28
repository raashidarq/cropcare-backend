"""
Tests for POST /auth/register and POST /auth/login.

These endpoints did not exist. The app has always called them, so account
creation and sign-in both returned 404 in production — nobody could make an
account at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app, raise_server_exceptions=False)


def _supabase_with_session(user_id="u-1", email="farmer@example.com",
                           with_session=True):
    sb = MagicMock()
    session = MagicMock()
    session.access_token = "access-123"
    session.refresh_token = "refresh-123"
    session.expires_in = 3600

    user = MagicMock()
    user.id = user_id
    user.email = email
    user.phone = None

    result = MagicMock()
    result.session = session if with_session else None
    result.user = user

    sb.auth.sign_up.return_value = result
    sb.auth.sign_in_with_password.return_value = result
    return sb


def _creds(**over):
    body = {"email": "farmer@example.com", "password": "correcthorse"}
    body.update(over)
    return body


class TestRegister:
    def test_creates_an_account_and_returns_a_session(self):
        with patch("routers.auth._get_supabase",
                   return_value=_supabase_with_session()):
            r = client.post("/auth/register", json=_creds())

        assert r.status_code == 200
        d = r.json()
        assert d["access_token"] == "access-123"
        assert d["refresh_token"] == "refresh-123"
        # The app builds its LocalUser from these. Returning tokens alone left
        # it with an empty user id.
        assert d["user_id"] == "u-1"
        assert d["email"] == "farmer@example.com"

    def test_email_confirmation_required_is_not_reported_as_success(self):
        # Supabase returns a user with no session when the project requires
        # confirmation. Saying "signed in" would leave the farmer looking at a
        # signed-out app that thinks otherwise.
        with patch("routers.auth._get_supabase",
                   return_value=_supabase_with_session(with_session=False)):
            r = client.post("/auth/register", json=_creds())

        assert r.status_code == 401
        assert "confirm your email" in r.json()["detail"].lower()

    def test_an_existing_account_says_so(self):
        sb = MagicMock()
        sb.auth.sign_up.side_effect = RuntimeError("User already registered")
        with patch("routers.auth._get_supabase", return_value=sb):
            r = client.post("/auth/register", json=_creds())

        # Worth distinguishing: it is a usable instruction, and nothing an
        # attacker could not learn by trying to sign in.
        assert r.status_code == 409
        assert "already exists" in r.json()["detail"]

    def test_other_failures_do_not_leak_supabase_internals(self):
        sb = MagicMock()
        sb.auth.sign_up.side_effect = RuntimeError(
            "postgres connection refused at 10.0.0.4:5432"
        )
        with patch("routers.auth._get_supabase", return_value=sb):
            r = client.post("/auth/register", json=_creds())

        assert r.status_code == 400
        assert "10.0.0.4" not in r.json()["detail"]

    def test_a_malformed_email_is_rejected(self):
        r = client.post("/auth/register", json=_creds(email="not-an-email"))
        assert r.status_code == 422

    def test_a_short_password_is_rejected_before_supabase(self):
        with patch("routers.auth._get_supabase") as sb:
            r = client.post("/auth/register", json=_creds(password="short"))
        assert r.status_code == 422
        sb.assert_not_called()


class TestMissingSupabaseConfig:
    """A live production incident: SUPABASE_SERVICE_ROLE_KEY was unset on the
    host, and _get_supabase() had no guard before create_client() - the raw
    supabase-py error ("supabase_key is required") reached the endpoint's
    broad `except Exception`, which had no way to tell "Supabase isn't
    configured" apart from "this login attempt failed", so it got reported
    as a generic 400/401 with no indication the server was misconfigured.

    These tests exercise the real _get_supabase() (not mocked away, unlike
    the rest of this file) so the guard inside it is actually under test."""

    def test_register_reports_missing_credentials_as_a_clear_500(self):
        with patch("routers.auth.settings") as mock_settings:
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = ""
            r = client.post("/auth/register", json=_creds())

        assert r.status_code == 500
        assert "not configured" in r.json()["detail"].lower()

    def test_login_reports_missing_credentials_as_a_clear_500(self):
        with patch("routers.auth.settings") as mock_settings:
            mock_settings.supabase_url = "https://fake.supabase.co"
            mock_settings.supabase_service_role_key = ""
            r = client.post("/auth/login", json=_creds())

        assert r.status_code == 500
        assert "not configured" in r.json()["detail"].lower()

    def test_missing_credentials_are_never_reported_as_bad_login(self):
        # The bug this guards against specifically: a config problem must
        # never look like "wrong password" to the farmer, or they will keep
        # retrying a password that was never the issue.
        with patch("routers.auth.settings") as mock_settings:
            mock_settings.supabase_url = ""
            mock_settings.supabase_service_role_key = ""
            r = client.post("/auth/login", json=_creds())

        assert r.status_code != 401


class TestLogin:
    def test_signs_in_and_returns_a_session(self):
        with patch("routers.auth._get_supabase",
                   return_value=_supabase_with_session()):
            r = client.post("/auth/login", json=_creds())

        assert r.status_code == 200
        assert r.json()["access_token"] == "access-123"
        assert r.json()["user_id"] == "u-1"

    def test_a_wrong_password_and_an_unknown_account_look_identical(self):
        sb_wrong = MagicMock()
        sb_wrong.auth.sign_in_with_password.side_effect = RuntimeError(
            "Invalid login credentials"
        )
        sb_missing = MagicMock()
        sb_missing.auth.sign_in_with_password.side_effect = RuntimeError(
            "User not found"
        )

        with patch("routers.auth._get_supabase", return_value=sb_wrong):
            a = client.post("/auth/login", json=_creds())
        with patch("routers.auth._get_supabase", return_value=sb_missing):
            b = client.post("/auth/login", json=_creds(email="nobody@example.com"))

        # Distinguishing them tells an attacker which emails are registered.
        assert a.status_code == b.status_code == 401
        assert a.json()["detail"] == b.json()["detail"]

    def test_the_error_does_not_leak_supabase_internals(self):
        sb = MagicMock()
        sb.auth.sign_in_with_password.side_effect = RuntimeError(
            "JWT secret mismatch for project abcdef"
        )
        with patch("routers.auth._get_supabase", return_value=sb):
            r = client.post("/auth/login", json=_creds())

        assert "abcdef" not in r.json()["detail"]

    def test_a_session_less_response_is_a_401(self):
        with patch("routers.auth._get_supabase",
                   return_value=_supabase_with_session(with_session=False)):
            r = client.post("/auth/login", json=_creds())
        assert r.status_code == 401


class TestRoutesExist:
    def test_the_paths_the_app_actually_calls_are_served(self):
        # The regression that started this: the app called /auth/register and
        # /auth/login, and both returned 404.
        for path in ("/auth/register", "/auth/login"):
            r = client.post(path, json={})
            assert r.status_code != 404, f"{path} is missing"
