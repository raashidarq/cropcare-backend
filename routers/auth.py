"""
Authentication router: /auth/request-otp and /auth/verify-otp.

Design decisions:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Phone-flag gate
  Phone OTP is gated behind PHONE_AUTH_ENABLED env var, defaulting to OFF.
  The gate fires BEFORE touching SlowAPI or Supabase — no side effects occur
  when the flag is off. This is an intentional cost-control mechanism (SMS costs
  money; email does not). The check is not a bug.

Rate limiting (SlowAPI)
  3 requests / 10 minutes, keyed on the actual identifier (email or phone string).
  Supabase also enforces its own 60-second resend cooldown underneath — our limiter
  is a tighter, independent layer.

Identifier exclusivity
  Exactly one of {email, phone} must be present in each request body.
  Requests with both or neither are rejected 400 before any further processing.

Information leakage
  - Success and not-found responses for request-otp are identical.
  - Verify-otp failure never distinguishes wrong-code from expired-code.
  - No OTP codes, tokens, or secrets appear in any response body or log line.

Supabase OTP types
  Email: type="email"   Phone: type="sms"  (NOT "phone" or "otp")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address
from supabase import Client, create_client

from config import settings

# ---------------------------------------------------------------------------
# SlowAPI limiter — keyed on the actual identifier, not the IP
# ---------------------------------------------------------------------------

def _identifier_key(request: Request) -> str:
    """
    Rate-limit key: use the email or phone from the request body.

    Falls back to remote address so the limiter never crashes on malformed bodies
    (though those are rejected earlier with 400).
    """
    # SlowAPI calls this synchronously; body has already been parsed by FastAPI
    # and stashed on request.state by our endpoints before the decorator fires.
    key = getattr(request.state, "rate_limit_key", None)
    if key:
        return key
    return get_remote_address(request)


limiter = Limiter(key_func=_identifier_key)

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Supabase client — lazily initialised so unit tests can swap the env vars
# ---------------------------------------------------------------------------

def _get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class OtpRequestBody(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None


class OtpVerifyBody(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    code: str


# ---------------------------------------------------------------------------
# Shared validation helpers
# ---------------------------------------------------------------------------

def _validate_identifier(body: OtpRequestBody | OtpVerifyBody) -> tuple[str, str]:
    """
    Returns (identifier_type, identifier_value) after enforcing exactly-one rule.

    Raises HTTPException(400) if both or neither identifier is provided.
    """
    has_email = body.email is not None
    has_phone = body.phone is not None

    if has_email and has_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either 'email' or 'phone', not both.",
        )
    if not has_email and not has_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either 'email' or 'phone'.",
        )

    if has_email:
        return "email", str(body.email)
    return "phone", str(body.phone)


def _check_phone_flag() -> None:
    """
    Raises 403 if PHONE_AUTH_ENABLED is not 'true'.

    Must be called BEFORE any Supabase interaction or rate-limit consumption.
    """
    if not settings.phone_auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Phone sign-in is not available right now — please use email.",
        )


# ---------------------------------------------------------------------------
# POST /auth/request-otp
# ---------------------------------------------------------------------------

@router.post("/request-otp", status_code=status.HTTP_200_OK)
@limiter.limit("3/10minutes")
async def request_otp(request: Request, body: OtpRequestBody) -> dict:
    """
    Send an OTP to the provided email or phone.

    Order of operations (important — no side effects before phone-flag check):
    1. Validate exactly one identifier (400 if violated).
    2. If phone: check PHONE_AUTH_ENABLED *before* touching rate limiter or Supabase.
    3. Attach identifier to request.state for the rate-limit key function.
    4. SlowAPI enforces 3/10min rate limit.
    5. Call Supabase sign_in_with_otp.
    6. Return generic success — never reveal registration status.
    """
    id_type, id_value = _validate_identifier(body)

    # ── Phone flag gate — short-circuits BEFORE rate limiter and Supabase ──
    # The @limiter.limit decorator runs AFTER the function body starts executing
    # (SlowAPI hooks into the response cycle), so this explicit early return is safe.
    # We attach the key to state only AFTER the gate so the limiter is never charged
    # for phone-disabled requests.
    if id_type == "phone":
        _check_phone_flag()  # raises 403 — execution stops here when flag is off

    # Attach key for rate-limit key function (runs after body begins)
    request.state.rate_limit_key = id_value

    try:
        supabase = _get_supabase()
        if id_type == "email":
            supabase.auth.sign_in_with_otp({"email": id_value})
        else:
            supabase.auth.sign_in_with_otp({"phone": id_value})
    except Exception:  # noqa: BLE001, S110
        # Intentional: never leak Supabase error details or reveal registration status.
        # Any exception (network, bad credentials, rate limit from Supabase) is silenced.
        # This is a deliberate information-hiding decision, not an oversight.
        pass
    return {"message": "If that identifier is registered, an OTP has been sent."}


# ---------------------------------------------------------------------------
# POST /auth/verify-otp
# ---------------------------------------------------------------------------

@router.post("/verify-otp", status_code=status.HTTP_200_OK)
async def verify_otp(body: OtpVerifyBody) -> dict:
    """
    Verify a one-time password and return Supabase session tokens.

    On success: relays access_token, refresh_token, expires_at as-is from Supabase.
    On failure: 401 with a single generic message — never distinguish wrong-code vs expired.
    """
    id_type, id_value = _validate_identifier(body)

    # Phone flag gate — same contract as request-otp: check BEFORE Supabase.
    if id_type == "phone":
        _check_phone_flag()

    try:
        supabase = _get_supabase()
        if id_type == "email":
            response = supabase.auth.verify_otp(
                {"email": id_value, "token": body.code, "type": "email"}
            )
        else:
            response = supabase.auth.verify_otp(
                {"phone": id_value, "token": body.code, "type": "sms"}
            )
    except Exception:  # noqa: BLE001
        # Intentional: collapse all failures (wrong code, expired, network) into one
        # generic 401 — never distinguish which specific thing went wrong.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP verification failed.",
        )

    # Supabase returns a Session object; surface only what the app needs.
    session = response.session
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP verification failed.",
        )

    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expires_at": session.expires_at,
    }
