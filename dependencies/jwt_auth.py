"""JWT authentication dependency for FastAPI routes.

Design decisions:
- The algorithm allowlist is EXPLICIT in every jwt.decode() call — the token's own 'alg'
  header never by itself decides what gets trusted. This prevents algorithm confusion
  attacks (e.g. RS256/none attacks); it does not mean only one algorithm is ever accepted.
- Signature IS verified (not just payload decoded) using PyJWT with real key material.
- Any failure (missing header, bad signature, expired, missing 'sub') raises 401 with a
  single generic message — never leak *why* validation failed to the caller.
- SUPABASE_JWT_SECRET is never logged, printed, or included in any response body.

Two verification paths, tried in order - this project's real tokens were confirmed (by
decoding a live one, not assumed) to use the first one:

1. Supabase's asymmetric JWT signing keys (ES256). Supabase publishes the project's
   public key at SUPABASE_URL/auth/v1/.well-known/jwks.json specifically for this -
   verifying against it needs no shared secret at all. This was a live, total outage
   before it was added: every request from every farmer failed with "session expired"
   forever, because every real token is ES256-signed and the code only ever tried to
   verify it as HS256 against a shared secret - a combination that cannot succeed
   regardless of how correct that secret is or how fresh the token is.
2. The legacy shared HS256 secret (SUPABASE_JWT_SECRET), tried only if the JWKS lookup
   itself fails (no matching key, endpoint unreachable) - kept as a fallback for a
   project that hasn't migrated to signing keys, or during a migration window.
"""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from config import settings

_bearer_scheme = HTTPBearer(auto_error=False)

_GENERIC_401 = "Authentication required."

# What this project's tokens are actually signed with, confirmed by decoding a live
# token's header - not the algorithm this code originally assumed.
_JWKS_ALGORITHM = "ES256"
# The legacy fallback path's algorithm.
_LEGACY_ALGORITHM = "HS256"

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
        # Supabase's own edge cache holds this ~10 minutes; a 5-minute local
        # cache errs toward fresher without re-fetching on every request.
        # timeout is tight on purpose - this runs on every authenticated
        # request, and a slow JWKS fetch should fail fast into the legacy
        # fallback rather than hang the farmer's request.
        _jwks_client = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300, timeout=5)
    return _jwks_client


# Every Supabase Auth token for a signed-in user carries this exact
# audience claim - confirmed by decoding a live token's payload, not
# assumed. PyJWT enforces the 'aud' claim once a token carries one UNLESS
# told what to expect, so leaving `audience` unset here doesn't skip this
# check - it fails it, on every token, unconditionally. That was the
# SECOND bug hiding behind the first: fixing only the ES256 signing
# algorithm got signature verification to actually pass for the first
# time, which is what made this one visible at all - the old HS256-only
# code never got far enough to reach it.
_EXPECTED_AUDIENCE = "authenticated"


def _decode(token: str) -> dict:
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
    except jwt.PyJWKClientError:
        # No matching key for this token (wrong kid, endpoint unreachable,
        # or this project genuinely isn't using signing keys) - fall
        # through to the legacy secret rather than fail outright.
        pass
    else:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[_JWKS_ALGORITHM],
            audience=_EXPECTED_AUDIENCE,
            options={"require": ["exp", "sub"]},
        )

    return jwt.decode(
        token,
        settings.supabase_jwt_secret,
        algorithms=[_LEGACY_ALGORITHM],
        audience=_EXPECTED_AUDIENCE,
        options={"require": ["exp", "sub"]},
    )


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
        payload = _decode(token)
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


def get_optional_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),  # noqa: B008
) -> str | None:
    """
    FastAPI dependency that validates the Bearer JWT if present and returns the user ID (sub).
    Returns None if no Authorization header was provided.

    Raises:
        HTTPException(401): if a token was provided but is invalid or expired.
    """
    if credentials is None:
        return None
    return get_current_user_id(credentials)

