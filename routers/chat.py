"""
Follow-up chat router: POST /chat-about-diagnosis

A farmer who has just had a leaf diagnosed can ask questions about *that
specific scan* in their own words — "can I still eat the fruit?", "how long
before it spreads?", "I already sprayed last week, what now?".

This is deliberately NOT a general chatbot. Every request carries the
diagnosis it is scoped to, and the prompt refuses questions outside it. Two
reasons: an open-ended assistant invites questions this app has no business
answering (medical, financial, legal), and a scoped one can be grounded in
real context and stay short.

Design notes:
- The model's confidence is part of the prompt. A low-confidence diagnosis
  must produce visibly more hedged answers — the classifier is a closed-set
  softmax with no rejection option, so it can be confidently wrong, and the
  chat must not launder that into fluent prose.
- The client sends its own conversation history. There is no server-side
  session: the app is offline-first and owns the transcript in its local
  database, which is the only copy that survives a dropped connection.
- No image is accepted. Image upload has its own signed-URL path in the sync
  API and is metered-data sensitive.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import google.generativeai as genai
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from slowapi.util import get_remote_address
from supabase import Client, create_client

from config import settings
from dependencies.jwt_auth import get_optional_user_id
from routers.auth import limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_LANGUAGE_MAP = {
    "en": "English",
    "si": "Sinhala",
    "ta": "Tamil",
}

# Keeps one exchange bounded. A farmer on a metered connection should not be
# able to accidentally send a novel, and Gemini's context is not free.
_MAX_HISTORY_MESSAGES = 20
_MAX_QUESTION_CHARS = 1000


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------
class ChatTurn(BaseModel):
    role: Literal["USER", "ASSISTANT"]
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    crop_id: str = Field(..., min_length=1)
    disease_id: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    severity: str | None = Field(default=None)
    result_state: str | None = Field(
        default=None,
        description="confident | lowConfidence | unsupported | analysisFailed",
    )
    language_code: str = Field(default="en", min_length=2, max_length=10)
    question: str = Field(..., min_length=1, max_length=_MAX_QUESTION_CHARS)
    user_observations: str | None = Field(default=None, max_length=2000)
    treatment_summary: str | None = Field(
        default=None,
        max_length=4000,
        description="Guidance already shown to the farmer, so answers stay consistent with it.",
    )
    history: list[ChatTurn] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    message_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _build_prompt(body: ChatRequest) -> str:
    lang_name = _LANGUAGE_MAP.get(body.language_code.lower(), body.language_code)
    observations = body.user_observations or "None provided"
    treatment = body.treatment_summary or "None shown yet"
    severity = body.severity or "unknown"

    # The hedging instruction is stronger when the classifier was unsure. This
    # is the single most important part of the prompt: the app's whole posture
    # is that the model can be confidently wrong (see TD-014 in the app repo).
    if body.result_state == "lowConfidence" or body.confidence < 0.80:
        certainty = (
            "IMPORTANT: this diagnosis is UNCERTAIN. Say so plainly in your first "
            "sentence. Encourage the farmer to check the alternative possibilities "
            "in the app or to consult a human agronomist before doing anything "
            "expensive or irreversible."
        )
    else:
        certainty = (
            "The diagnosis is reasonably confident, but it is still an automated "
            "guess from a single photograph, not an expert inspection. Do not "
            "present it as certain."
        )

    history_block = "\n".join(
        f"{'Farmer' if turn.role == 'USER' else 'You'}: {turn.content}"
        for turn in body.history[-_MAX_HISTORY_MESSAGES:]
    ) or "No previous messages."

    return f"""You are helping a smallholder farmer in Sri Lanka understand ONE specific crop diagnosis made by an app. You are not a general assistant.

THE DIAGNOSIS THIS CONVERSATION IS ABOUT
Crop: {body.crop_id}
Diagnosed problem: {body.disease_id}
Model confidence: {body.confidence:.2f}
Severity: {severity}
Farmer's own observations: {observations}
Guidance the app has already shown them: {treatment}

{certainty}

RULES
- Answer ONLY questions about this diagnosis, this crop, or caring for this plant. If asked about anything else — other crops, medical or human-health questions, money, legal matters, or general knowledge — say briefly that you can only help with this scan, and stop.
- Reply in {lang_name} (code: {body.language_code}). Use simple, everyday words. Assume the farmer may read slowly.
- Be short: at most 4 sentences unless they asked for steps, in which case use a short list.
- Prefer practical, low-cost actions available to a smallholder. Name chemicals only with a safety note.
- Never invent a fact about this specific plant that you were not given. If you do not know, say so and suggest asking an agronomist.
- Do not repeat the whole treatment guidance back to them; they can already see it.

CONVERSATION SO FAR
{history_block}

FARMER'S QUESTION
{body.question}

Your reply:"""


def _log_to_supabase(data: dict[str, Any]) -> None:
    """Best-effort audit log. Never fails the farmer's request."""
    try:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            return
        supabase = _get_supabase()
        supabase.table("chat_message_log").insert(data).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to record chat_message_log audit row: %s", exc)


# ---------------------------------------------------------------------------
# POST /chat-about-diagnosis
# ---------------------------------------------------------------------------
@router.post(
    "/chat-about-diagnosis",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/minute")
async def chat_about_diagnosis(
    request: Request,
    body: ChatRequest,
    user_id: str | None = Depends(get_optional_user_id),
) -> ChatResponse:
    """
    Answer one follow-up question about an existing diagnosis.

    - Scoped to the diagnosis in the request body; refuses unrelated questions.
    - Hedges harder when the classifier was unsure.
    - Rate limited to 30 requests / minute per user or IP.
    """
    request.state.rate_limit_key = user_id or get_remote_address(request)

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gemini API key is not configured on the server.",
        )

    prompt = _build_prompt(body)

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(model_name="gemini-1.5-flash")
        response = model.generate_content(prompt)
        answer = (response.text or "").strip()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate an answer: {exc!s}",
        )

    if not answer:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The model returned an empty answer.",
        )

    import uuid

    message_id = str(uuid.uuid4())

    _log_to_supabase(
        {
            "id": message_id,
            "user_id": user_id,
            "crop_id": body.crop_id,
            "disease_id": body.disease_id,
            "confidence": body.confidence,
            "language_code": body.language_code,
            "question": body.question,
            "answer": answer,
        }
    )

    return ChatResponse(answer=answer, message_id=message_id)
