"""
Diagnosis interpretation router: POST /interpret-diagnosis

Handles AI-powered agricultural treatment guidance using Google Gemini,
with structured JSON generation, multi-language localization, optional
user scoping, rate limiting, and Supabase audit logging.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import google.generativeai as genai
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from slowapi.util import get_remote_address
from supabase import Client, create_client

from config import settings
from dependencies.jwt_auth import get_optional_user_id
from routers.auth import limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["diagnosis"])

# ---------------------------------------------------------------------------
# Supported language display names for LLM prompting
# ---------------------------------------------------------------------------
_LANGUAGE_MAP = {
    "en": "English",
    "si": "Sinhala",
    "ta": "Tamil",
}


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------
class DiagnosisInterpretationRequest(BaseModel):
    crop_id: str = Field(..., min_length=1, description="Identifier of the crop, e.g. 'tomato'")
    disease_id: str = Field(..., min_length=1, description="Identifier of the diagnosed disease")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence score between 0.0 and 1.0")
    severity: str = Field(..., min_length=1, description="Assessed severity, e.g. 'low', 'medium', 'high'")
    language_code: str = Field(default="en", min_length=2, max_length=10, description="Target language code ('en', 'si', 'ta')")
    user_observations: str | None = Field(default=None, description="Optional extra observations from the farmer")


class DiagnosisInterpretationResponse(BaseModel):
    summary: str
    what_to_do: str
    what_to_avoid: str
    recheck_after_days: int
    interpretation_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _build_prompt(body: DiagnosisInterpretationRequest) -> str:
    lang_name = _LANGUAGE_MAP.get(body.language_code.lower(), body.language_code)
    user_notes = body.user_observations if body.user_observations else "None provided"

    return f"""You are an expert agronomist providing actionable, clear treatment guidance to a farmer.

Crop: {body.crop_id}
Diagnosed Disease / Issue: {body.disease_id}
Detection Confidence: {body.confidence:.2f}
Severity Level: {body.severity}
Farmer Observations: {user_notes}
Target Language: {lang_name} (code: {body.language_code})

Respond ONLY with a valid JSON object strictly matching this schema:
{{
  "summary": "<1-2 concise sentences explaining what the disease is and how it affects the crop in {lang_name}>",
  "what_to_do": "<Clear, actionable, step-by-step treatment guidance including safe organic or chemical treatments and cultural practices in {lang_name}>",
  "what_to_avoid": "<Crucial mistakes to avoid, such as improper watering, unapproved pesticide mixtures, or spreading infected foliage in {lang_name}>",
  "recheck_after_days": <Integer representing recommended days before checking the crop again, typically 3 to 14>
}}
"""


def _log_to_supabase(data: dict[str, Any]) -> None:
    """Best-effort audit log to Supabase llm_interpretation table."""
    try:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            return
        supabase = _get_supabase()
        supabase.table("llm_interpretation").insert(data).execute()
    except Exception as exc:  # noqa: BLE001
        # Non-blocking: failure to write audit log must not fail the farmer's diagnosis
        logger.warning("Failed to record llm_interpretation audit log: %s", exc)


# ---------------------------------------------------------------------------
# POST /interpret-diagnosis
# ---------------------------------------------------------------------------
@router.post(
    "/interpret-diagnosis",
    response_model=DiagnosisInterpretationResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("20/minute")
async def interpret_diagnosis(
    request: Request,
    body: DiagnosisInterpretationRequest,
    user_id: str | None = Depends(get_optional_user_id),
) -> DiagnosisInterpretationResponse:
    """
    Generate treatment guidance for a diagnosed crop disease using Gemini.

    - Supports multi-language translation (English, Sinhala, Tamil).
    - Rate limited to 20 requests / minute per user or IP.
    - Records interpretation in Supabase audit table.
    """
    # Rate limit key: user_id if authenticated, else client IP
    request.state.rate_limit_key = user_id or get_remote_address(request)

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gemini API key is not configured on the server.",
        )

    prompt = _build_prompt(body)

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config={"response_mime_type": "application/json"},
        )
        response = model.generate_content(prompt)
        raw_text = response.text or "{}"
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Received malformed JSON from Gemini model.",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate treatment guidance: {exc!s}",
        )

    # Validate required fields from LLM response
    summary = parsed.get("summary")
    what_to_do = parsed.get("what_to_do")
    what_to_avoid = parsed.get("what_to_avoid")
    recheck_after_days = parsed.get("recheck_after_days")

    if not summary or not what_to_do or not what_to_avoid or recheck_after_days is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gemini response was missing required guidance fields.",
        )

    try:
        recheck_days_int = int(recheck_after_days)
    except (ValueError, TypeError):
        recheck_days_int = 5

    interpretation_id = str(uuid.uuid4())

    # Log to Supabase llm_interpretation table for auditing
    audit_record = {
        "id": interpretation_id,
        "user_id": user_id,
        "crop_id": body.crop_id,
        "disease_id": body.disease_id,
        "confidence": body.confidence,
        "severity": body.severity,
        "language_code": body.language_code,
        "user_observations": body.user_observations,
        "summary": summary,
        "what_to_do": what_to_do,
        "what_to_avoid": what_to_avoid,
        "recheck_after_days": recheck_days_int,
    }
    _log_to_supabase(audit_record)

    return DiagnosisInterpretationResponse(
        summary=summary,
        what_to_do=what_to_do,
        what_to_avoid=what_to_avoid,
        recheck_after_days=recheck_days_int,
        interpretation_id=interpretation_id,
    )
