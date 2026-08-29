"""
Central settings module.

All environment variables are read once at startup.
Secrets are intentionally excluded from __repr__ to avoid accidental leakage in logs.
"""

import os


class _Settings:
    """
    Holds application-wide configuration derived from environment variables.

    Attributes are accessed as singletons via the module-level `settings` object.
    """

    def __init__(self) -> None:
        self.supabase_url: str = os.environ.get("SUPABASE_URL", "")
        self.supabase_service_role_key: str = os.environ.get(
            "SUPABASE_SERVICE_ROLE_KEY", ""
        )
        self.supabase_jwt_secret: str = os.environ.get("SUPABASE_JWT_SECRET", "")
        self.gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
        # Optional second key, e.g. from a separate Google account's AI
        # Studio project. Google AI Studio's free tier is a low daily request
        # cap; once the primary key is exhausted, every further call to it
        # fails until the quota resets. Config, not code, because it can be
        # rotated or removed on Render without a deploy.
        self.gemini_api_key_fallback: str = os.environ.get(
            "GEMINI_API_KEY_FALLBACK", ""
        )
        # Configurable so a retired model name can be changed on the host
        # without a code change. Empty falls back to the candidate list in
        # dependencies/gemini.py. See that module for why this exists.
        self.gemini_model: str = os.environ.get("GEMINI_MODEL", "")

        # Which AI provider handles treatment guidance and chat, and which
        # one to fall back to if it can't serve a request (misconfigured,
        # out of quota, unreachable). See dependencies/ai/service.py.
        #
        # Defaults preserve today's behaviour with zero Render config
        # changes (gemini primary, as it always was) while making NVIDIA
        # live the moment NVIDIA_API_KEY is set - no redeploy needed, same
        # pattern as GEMINI_API_KEY_FALLBACK. An unset fallback key just
        # means the fallback provider reports itself unconfigured, same as
        # today's single-provider behaviour when GEMINI_API_KEY_FALLBACK is
        # unset.
        self.ai_provider: str = os.environ.get("AI_PROVIDER", "gemini")
        self.ai_fallback_provider: str = os.environ.get("AI_FALLBACK_PROVIDER", "nvidia")
        self.nvidia_api_key: str = os.environ.get("NVIDIA_API_KEY", "")
        # See dependencies/ai/nvidia_provider.py for why this specific model.
        self.nvidia_model: str = os.environ.get("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")
        # Phone auth: costs real money per SMS, so this is a deliberate
        # cost-control gate, not an oversight either way it's set.
        #
        # Default flipped to ON here (2026-08-29) for a Twilio trial-number
        # demo — Render has no PHONE_AUTH_ENABLED var set, and the point was
        # to enable it without a dashboard step. Render's own env var still
        # wins if set (PHONE_AUTH_ENABLED=false there turns it back off
        # without another deploy). If this is still "true" after the demo,
        # that's a stale default, not a decision — flip it back to "false".
        self.phone_auth_enabled: bool = (
            os.environ.get("PHONE_AUTH_ENABLED", "true").strip().lower() == "true"
        )

    def __repr__(self) -> str:  # pragma: no cover
        # Never print secrets in logs/repr
        return (
            f"<Settings supabase_url={self.supabase_url!r} "
            f"phone_auth_enabled={self.phone_auth_enabled}>"
        )


settings = _Settings()
