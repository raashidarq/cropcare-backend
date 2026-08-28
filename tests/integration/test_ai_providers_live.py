"""
Opt-in integration tests that make a REAL call to a REAL AI provider.

Every test in this file is marked `integration` and is skipped by default -
see tests/conftest.py's pytest_collection_modifyitems. Run deliberately with:

    RUN_AI_INTEGRATION_TESTS=true pytest tests/integration/test_ai_providers_live.py -v

Each test skips itself (rather than failing) if the provider it needs isn't
configured in the current environment, so this file is safe to run without
every provider set up - it just verifies whichever ones are.
"""

from __future__ import annotations

import os

import pytest

from config import settings
from dependencies.ai.gemini_provider import GeminiProvider
from dependencies.ai.nvidia_provider import NvidiaProvider

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set in this environment",
)
def test_gemini_answers_a_real_prompt():
    settings.gemini_api_key = os.environ["GEMINI_API_KEY"]
    settings.gemini_api_key_fallback = os.environ.get("GEMINI_API_KEY_FALLBACK", "")
    answer = GeminiProvider().generate(
        "Reply with exactly the word: pong", json_mode=False
    )
    assert answer.strip()


@pytest.mark.skipif(
    not os.environ.get("NVIDIA_API_KEY"),
    reason="NVIDIA_API_KEY not set in this environment",
)
def test_nvidia_answers_a_real_prompt():
    settings.nvidia_api_key = os.environ["NVIDIA_API_KEY"]
    settings.nvidia_model = os.environ.get("NVIDIA_MODEL", "")
    answer = NvidiaProvider().generate(
        "Reply with exactly the word: pong", json_mode=False
    )
    assert answer.strip()
