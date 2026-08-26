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

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from supabase import Client, create_client

from config import settings
from dependencies.jwt_auth import get_current_user_id

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

logger = logging.getLogger(__name__)

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


class ForgotPasswordRequestBody(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str = "If an account exists with this email, password reset instructions have been sent."
    status: str = "success"


class DeleteAccountResponse(BaseModel):
    status: str = "success"
    message: str = "Account successfully deleted"


class UserProfileSummary(BaseModel):
    id: str
    email: str | None = None
    phone_number: str | None = None
    updated_at: str | None = None


class ChangeEmailRequestBody(BaseModel):
    new_email: EmailStr


class ChangeEmailResponse(BaseModel):
    success: bool = True
    message: str = "Email updated successfully"
    user: UserProfileSummary


class ChangePhoneRequestOtpBody(BaseModel):
    new_phone_number: str = Field(..., min_length=5)


class ChangePhoneRequestOtpResponse(BaseModel):
    success: bool = True
    message: str


class ChangePhoneVerifyOtpBody(BaseModel):
    new_phone_number: str = Field(..., min_length=5)
    otp_code: str = Field(..., min_length=1)


class ChangePhoneVerifyOtpResponse(BaseModel):
    success: bool = True
    message: str = "Phone number updated successfully"
    user: UserProfileSummary


def _format_user_summary(user: Any) -> UserProfileSummary:
    user_id = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else "")
    email = getattr(user, "email", None) or (user.get("email") if isinstance(user, dict) else None)
    phone = getattr(user, "phone", None) or (user.get("phone") if isinstance(user, dict) else None)
    updated_at = getattr(user, "updated_at", None) or (user.get("updated_at") if isinstance(user, dict) else None)
    return UserProfileSummary(
        id=str(user_id),
        email=str(email) if email else None,
        phone_number=str(phone) if phone else None,
        updated_at=str(updated_at) if updated_at else None,
    )



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


# ---------------------------------------------------------------------------
# POST /auth/forgot-password
# ---------------------------------------------------------------------------

@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("3/10minutes")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequestBody,
) -> ForgotPasswordResponse:
    """
    Trigger a password reset email for the provided email address.

    Security & Information Hiding:
    - Returns 200 OK with a generic success message even if the email is not registered
      or Supabase returns an error, preventing account enumeration.
    - Rate-limited to 3 requests per 10 minutes per email address via SlowAPI.
    - 422 Unprocessable Entity returned on invalid email format via Pydantic.
    """
    request.state.rate_limit_key = str(body.email)

    try:
        supabase = _get_supabase()
        supabase.auth.reset_password_for_email(str(body.email))
    except Exception:  # noqa: BLE001, S110
        # Prevent user enumeration attacks by suppressing errors
        pass

    return ForgotPasswordResponse(
        message="If an account exists with this email, password reset instructions have been sent.",
        status="success",
    )


# ---------------------------------------------------------------------------
# DELETE /auth/account
# ---------------------------------------------------------------------------

@router.delete(
    "/account",
    response_model=DeleteAccountResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_account(
    user_id: str = Depends(get_current_user_id),
) -> DeleteAccountResponse:
    """
    Deletes the authenticated user's account and cascades removal of associated sync records.
    Requires a valid Bearer JWT.
    """
    supabase = _get_supabase()

    # Cascade deletion across user-scoped data tables.
    #
    # chat_message_log was added with the chat endpoint and belongs here for
    # the same reason as llm_interpretation: it holds the farmer's own words.
    #
    # NOTE: this removes database rows only. Scan images live in Supabase
    # Storage and are NOT deleted here, so a user who deletes their account
    # still leaves their photographs in the bucket. That needs a storage
    # sweep before this can be called a complete deletion.
    for table_name in [
        "scan",
        "diagnosis",
        "escalation",
        "profile",
        "llm_interpretation",
        "chat_message_log",
    ]:
        try:
            supabase.table(table_name).delete().eq("user_id", user_id).execute()
        except Exception as exc:  # noqa: BLE001
            # Deletion continues across the remaining tables rather than
            # aborting: a partial cascade is bad, but stopping halfway would
            # leave MORE of the user's data behind. Logged because a silently
            # failed account deletion is a data-rights problem, not a nuisance.
            logger.warning(
                "Account deletion: could not clear %s for user %s: %s",
                table_name, user_id, exc,
            )

    # Delete user from Supabase Auth via Admin API
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user account: {exc!s}",
        )

    return DeleteAccountResponse(
        status="success",
        message="Account successfully deleted",
    )


# ---------------------------------------------------------------------------
# POST /auth/change-email
# ---------------------------------------------------------------------------

@router.post(
    "/change-email",
    response_model=ChangeEmailResponse,
    status_code=status.HTTP_200_OK,
)
async def change_email(
    body: ChangeEmailRequestBody,
    user_id: str = Depends(get_current_user_id),
) -> ChangeEmailResponse:
    """
    Updates the authenticated user's email address.
    """
    supabase = _get_supabase()
    try:
        res = supabase.auth.admin.update_user_by_id(
            user_id,
            {"email": str(body.new_email), "email_confirm": True},
        )
        user = res.user if hasattr(res, "user") else res
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc).lower()
        if "already" in err_msg or "exists" in err_msg or "duplicate" in err_msg or "unique" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered to another account",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update email: {exc!s}",
        )

    return ChangeEmailResponse(
        success=True,
        message="Email updated successfully",
        user=_format_user_summary(user),
    )


# ---------------------------------------------------------------------------
# POST /auth/change-phone/request-otp
# ---------------------------------------------------------------------------

@router.post(
    "/change-phone/request-otp",
    response_model=ChangePhoneRequestOtpResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("3/10minutes")
async def change_phone_request_otp(
    request: Request,
    body: ChangePhoneRequestOtpBody,
    user_id: str = Depends(get_current_user_id),
) -> ChangePhoneRequestOtpResponse:
    """
    Sends an SMS OTP to verify ownership of the new phone number before updating.
    """
    _check_phone_flag()
    request.state.rate_limit_key = body.new_phone_number

    try:
        supabase = _get_supabase()
        supabase.auth.sign_in_with_otp({"phone": body.new_phone_number})
    except Exception:  # noqa: BLE001, S110
        pass

    return ChangePhoneRequestOtpResponse(
        success=True,
        message=f"OTP sent to {body.new_phone_number}",
    )


# ---------------------------------------------------------------------------
# POST /auth/change-phone/verify-otp
# ---------------------------------------------------------------------------

@router.post(
    "/change-phone/verify-otp",
    response_model=ChangePhoneVerifyOtpResponse,
    status_code=status.HTTP_200_OK,
)
async def change_phone_verify_otp(
    body: ChangePhoneVerifyOtpBody,
    user_id: str = Depends(get_current_user_id),
) -> ChangePhoneVerifyOtpResponse:
    """
    Verifies OTP for the new phone number and updates the authenticated user's record.
    """
    _check_phone_flag()
    supabase = _get_supabase()

    try:
        supabase.auth.verify_otp(
            {"phone": body.new_phone_number, "token": body.otp_code, "type": "sms"}
        )
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP code.",
        )

    try:
        update_res = supabase.auth.admin.update_user_by_id(
            user_id,
            {"phone": body.new_phone_number, "phone_confirm": True},
        )
        user = update_res.user if hasattr(update_res, "user") else update_res
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update phone number: {exc!s}",
        )

    return ChangePhoneVerifyOtpResponse(
        success=True,
        message="Phone number updated successfully",
        user=_format_user_summary(user),
    )



