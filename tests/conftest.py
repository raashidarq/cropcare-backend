"""
Pytest configuration.

Sets SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_JWT_SECRET to safe dummy
values so that importing config never crashes during the test suite.
Individual tests override these via monkeypatch or unittest.mock.patch as needed.
"""

import os

import pytest

import config
from routers.auth import limiter


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch):
    """Ensure env vars required by config.py are always present and limiter is reset."""
    supabase_url = os.environ.get("SUPABASE_URL", "https://test.supabase.co")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
    jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "testsecret")
    gemini_key = os.environ.get("GEMINI_API_KEY", "test-gemini-key")

    monkeypatch.setenv("SUPABASE_URL", supabase_url)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", service_role_key)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("GEMINI_API_KEY", gemini_key)
    monkeypatch.delenv("PHONE_AUTH_ENABLED", raising=False)

    config.settings.supabase_url = supabase_url
    config.settings.supabase_service_role_key = service_role_key
    config.settings.supabase_jwt_secret = jwt_secret
    config.settings.gemini_api_key = gemini_key
    config.settings.phone_auth_enabled = False

    try:
        limiter.reset()
    except Exception:  # noqa: BLE001, S110
        pass

