"""
Pytest configuration.

Sets SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_JWT_SECRET to safe dummy
values so that importing config never crashes during the test suite.
Individual tests override these via monkeypatch or unittest.mock.patch as needed.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch):
    """Ensure env vars required by config.py are always present."""
    monkeypatch.setenv("SUPABASE_URL", os.environ.get("SUPABASE_URL", "https://test.supabase.co"))
    monkeypatch.setenv(
        "SUPABASE_SERVICE_ROLE_KEY",
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key"),
    )
    monkeypatch.setenv(
        "SUPABASE_JWT_SECRET",
        os.environ.get("SUPABASE_JWT_SECRET", "testsecret"),
    )
    monkeypatch.delenv("PHONE_AUTH_ENABLED", raising=False)
