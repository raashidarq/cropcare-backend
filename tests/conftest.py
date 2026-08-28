"""
Pytest configuration.

Sets SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_JWT_SECRET to safe dummy
values so that importing config never crashes during the test suite.
Individual tests override these via monkeypatch or unittest.mock.patch as needed.

Also the one thing standing between "pytest" and accidentally spending real
AI provider quota: NVIDIA_API_KEY is force-cleared on every test, regardless
of what happens to be set in the developer's own shell - see
_clear_nvidia_key below for why that specific guarantee matters enough to
be its own fixture rather than folded into _safe_env.
"""

import os

import pytest

import config as app_config
from routers.auth import limiter


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: makes a REAL call to an external AI provider and "
        "consumes real quota. Skipped by default - set "
        "RUN_AI_INTEGRATION_TESTS=true to run it.",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_AI_INTEGRATION_TESTS", "").strip().lower() == "true":
        return
    skip = pytest.mark.skip(
        reason="set RUN_AI_INTEGRATION_TESTS=true to run - makes a real "
        "call and consumes real provider quota"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


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

    app_config.settings.supabase_url = supabase_url
    app_config.settings.supabase_service_role_key = service_role_key
    app_config.settings.supabase_jwt_secret = jwt_secret
    app_config.settings.gemini_api_key = gemini_key
    app_config.settings.phone_auth_enabled = False

    try:
        limiter.reset()
    except Exception:  # noqa: BLE001, S110
        pass


@pytest.fixture(autouse=True)
def _clear_nvidia_key(monkeypatch):
    """Guarantees NvidiaProvider is unconfigured (and therefore makes no
    HTTP call - see its own check for this) in every non-integration test,
    even a test that forgets to mock httpx and even on a machine where the
    developer's own shell happens to export a real NVIDIA_API_KEY. Separate
    from _safe_env because the property this protects (never spend real
    provider quota from a plain `pytest` run) is a hard requirement, not a
    convenience default - it deserves to be impossible to accidentally
    delete by editing an unrelated env var default above.
    """
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    app_config.settings.nvidia_api_key = ""

