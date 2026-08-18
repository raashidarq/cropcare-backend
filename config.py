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
        # Phone auth is DISABLED by default — deliberate cost-control gate.
        # Costs real money per SMS; enable only when explicitly configured.
        self.phone_auth_enabled: bool = (
            os.environ.get("PHONE_AUTH_ENABLED", "").strip().lower() == "true"
        )

    def __repr__(self) -> str:  # pragma: no cover
        # Never print secrets in logs/repr
        return (
            f"<Settings supabase_url={self.supabase_url!r} "
            f"phone_auth_enabled={self.phone_auth_enabled}>"
        )


settings = _Settings()
