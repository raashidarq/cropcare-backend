"""JWT authentication dependency for FastAPI routes.

Design decisions:
- Algorithm is EXPLICITLY pinned to HS256 — the token's own 'alg' header is NEVER trusted.
  This prevents algorithm confusion attacks (e.g., RS256/none attacks).
- Signature IS verified (not just payload decoded) using PyJWT with the full secret.
- Any failure (missing header, bad signature, expired, missing 'sub') raises 401 with a
  single generic message — never leak *why* validation failed to the caller.
- SUPABASE_JWT_SECRET is never logged, printed, or included in any response body.
"""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

_bearer_scheme = HTTPBearer(auto_error=False)

_GENERIC_401 = "Authentication required."

# Pinned — we never read the token's 'alg' header.
_JWT_ALGORITHM = "HS256"


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),  # noqa: B008
) -> str:
    """
    FastAPI dependency that validates the Bearer JWT and returns the user ID (sub).

    Raises:
        HTTPException(401): on ANY validation failure — signature mismatch, expiry,
                            missing/malformed header, missing sub claim, etc.
    """
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_401)

    token = credentials.credentials

    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=[_JWT_ALGORITHM],  # explicit allowlist — ignores token's own header
            options={"require": ["exp", "sub"]},  # both claims must be present
        )
    except jwt.ExpiredSignatureError:
        # Expired — same generic response; don't tell caller it was expiry vs bad sig.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_401)
    except jwt.PyJWTError:
        # Covers: invalid signature, wrong algorithm, malformed token, missing claims, etc.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_401)

    user_id: str | None = payload.get("sub")
    if not user_id:
        # Should be caught by 'require' above, but be explicit as a belt-and-suspenders guard.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_401)

    return user_id
