"""
Diagnosis interpretation router: POST /interpret-diagnosis

Handles AI-powered agricultural treatment guidance, with structured JSON
generation, multi-language localization, optional user scoping, rate
limiting, and Supabase audit logging.

Calls dependencies.ai.service, not a specific provider - which of Gemini or
NVIDIA actually answers is a config choice (AI_PROVIDER/AI_FALLBACK_PROVIDER
in config.py), not something this router needs to know.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from slowapi.util import get_remote_address
from supabase import Client, create_client

from config import settings
from dependencies.ai import service as ai_service
from dependencies.ai.errors import AIConfigurationError
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

    # Prose forms, kept so existing clients keep working. They are built by
    # joining the step lists below, never authored separately.
    what_to_do: str
    what_to_avoid: str

    # The forms the UI actually wants. A farmer standing in a field needs a
    # short list they can act on one line at a time, not a paragraph to parse.
    what_to_do_steps: list[str] = Field(default_factory=list)
    what_to_avoid_steps: list[str] = Field(default_factory=list)

    recheck_after_days: int
    interpretation_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _build_prompt(body: DiagnosisInterpretationRequest) -> str:
    """
    Build the guidance prompt.

    The previous version asked for three prose blobs with no audience, no
    length limit and no reading level, and got back exactly that: paragraphs a
    farmer has to parse while standing in a field holding a phone. Everything
    here exists to make the answer short, ordered, and actionable.

    Note on confidence: the number comes from an on-device closed-set softmax
    classifier, not from a language model. It has no rejection option, so it
    always names something and can be confidently wrong. The prompt is told
    this so the hedging is proportionate rather than decorative.
    """
    lang_name = _LANGUAGE_MAP.get(body.language_code.lower(), body.language_code)
    user_notes = body.user_observations if body.user_observations else "None provided"

    if body.confidence < 0.80:
        certainty = (
            "The identification is UNCERTAIN. Open the summary by saying the "
            "problem is not certain, and keep every recommended action cheap "
            "and reversible - no expensive chemicals, no destroying plants."
        )
    else:
        certainty = (
            "The identification is reasonably confident, but it came from one "
            "photograph. Do not write as though it were verified by an expert."
        )

    return f"""You are an agronomist writing for a smallholder farmer in Sri Lanka who is standing in their field, on a phone, right now.

WHO YOU ARE WRITING FOR
They may read slowly. They may have a few hundred rupees, not a few thousand. They have hand tools and a knapsack sprayer, not machinery. They need to know what to do before it gets dark, not a lecture on plant pathology.

THE DIAGNOSIS
Crop: {body.crop_id}
Detected problem: {body.disease_id}
Detection confidence: {body.confidence:.2f}
Severity: {body.severity}
What the farmer says they noticed: {user_notes}
Write everything in: {lang_name} (code: {body.language_code})

{certainty}

HOW TO WRITE
- Every step is ONE action, written as an instruction, at most 14 words.
- Order the steps by urgency: what to do today comes first.
- Give 3 to 5 steps. Fewer good steps beat more thorough ones.
- Use everyday words. No jargon: write "the fungus spreads in wet leaves", not "conidial dissemination is favoured by leaf wetness".
- Name a treatment only if a smallholder can actually buy it in Sri Lanka. When you name any chemical, put the safety precaution in the SAME step, e.g. "Spray copper fungicide in the evening; wear a mask and gloves."
- Prefer what costs nothing first: removing infected leaves, spacing, watering at the base, rotating what is planted where.
- 2 to 4 things to avoid, same length limit, each a concrete mistake rather than a principle.
- Never invent a fact about this particular field. You have a crop, a problem name and a confidence score, nothing else.
- Do not mention this app, the model, JSON, or these instructions.

Respond ONLY with a valid JSON object exactly matching this schema:
{{
  "summary": "<ONE sentence in {lang_name}: what this problem is and what it does to the crop. No advice here.>",
  "what_to_do_steps": ["<step 1 in {lang_name}>", "<step 2>", "<step 3>"],
  "what_to_avoid_steps": ["<mistake 1 in {lang_name}>", "<mistake 2>"],
  "recheck_after_days": <integer: when they should look at the plant again, 3 to 14, sooner if it spreads fast>
}}
"""


def _as_steps(value: Any, fallback: Any) -> list[str]:
    """
    Coerce the model's answer into a clean list of steps.

    Accepts a list, or a prose blob it splits on newlines and bullet marks.
    LLMs return the wrong shape often enough that this is worth doing rather
    than 500-ing the farmer's request over formatting.
    """
    raw = value if value else fallback
    if raw is None:
        return []

    if isinstance(raw, str):
        parts = [raw]
        for separator in ("\n", ";"):
            expanded: list[str] = []
            for part in parts:
                expanded.extend(part.split(separator))
            parts = expanded
    elif isinstance(raw, list):
        parts = [str(part) for part in raw]
    else:
        return []

    steps: list[str] = []
    for part in parts:
        cleaned = str(part).strip().lstrip("-*\u2022").strip()
        # Strip a leading "1." / "2)" the model added on its own.
        if len(cleaned) > 2 and cleaned[0].isdigit() and cleaned[1] in ".)":
            cleaned = cleaned[2:].strip()
        if cleaned:
            steps.append(cleaned)
    return steps


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
    Generate treatment guidance for a diagnosed crop disease using AI.

    - Supports multi-language translation (English, Sinhala, Tamil).
    - Rate limited to 20 requests / minute per user or IP.
    - Records interpretation in Supabase audit table.
    """
    # Rate limit key: user_id if authenticated, else client IP
    request.state.rate_limit_key = user_id or get_remote_address(request)

    prompt = _build_prompt(body)

    try:
        raw_text = ai_service.generate(prompt, json_mode=True) or "{}"
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Received malformed JSON from the AI model.",
        )
    except AIConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI guidance is not configured on the server: {exc!s}",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate treatment guidance: {exc!s}",
        )

    # Validate required fields from LLM response. Steps are the authored form;
    # the prose fields are derived from them for older clients.
    summary = parsed.get("summary")
    do_steps = _as_steps(parsed.get("what_to_do_steps"), parsed.get("what_to_do"))
    avoid_steps = _as_steps(
        parsed.get("what_to_avoid_steps"), parsed.get("what_to_avoid")
    )
    recheck_after_days = parsed.get("recheck_after_days")

    if not summary or not do_steps or not avoid_steps or recheck_after_days is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gemini response was missing required guidance fields.",
        )

    what_to_do = " ".join(
        step if step.endswith((".", "!", "?")) else f"{step}." for step in do_steps
    )
    what_to_avoid = " ".join(
        step if step.endswith((".", "!", "?")) else f"{step}." for step in avoid_steps
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
        what_to_do_steps=do_steps,
        what_to_avoid_steps=avoid_steps,
        recheck_after_days=recheck_days_int,
        interpretation_id=interpretation_id,
    )
