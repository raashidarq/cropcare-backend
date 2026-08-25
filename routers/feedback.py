"""
Feedback router: POST /feedback
Receives in-app user feedback, bug reports, and suggestions.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from supabase import Client, create_client

from config import settings
from dependencies.jwt_auth import get_optional_user_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])


def _get_supabase() -> Client:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase credentials are not configured.",
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


class FeedbackRequestBody(BaseModel):
    user_id: str | None = None
    category: str = Field(default="general", description="Feedback category: general, bug, suggestion, etc.")
    message: str = Field(..., min_length=1, description="Feedback content")
    timestamp: str | None = Field(default=None, description="ISO8601 timestamp")


class FeedbackResponse(BaseModel):
    status: str = "success"
    message: str = "Feedback received"


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_200_OK,
)
async def submit_feedback(
    body: FeedbackRequestBody,
    user_id_from_token: str | None = Depends(get_optional_user_id),
) -> FeedbackResponse:
    """
    Submits user feedback, suggestions, or bug reports.
    Supports both authenticated and guest feedback.
    """
    effective_user_id = user_id_from_token or body.user_id

    record: dict[str, Any] = {
        "category": body.category,
        "message": body.message,
    }
    if effective_user_id:
        record["user_id"] = effective_user_id
    if body.timestamp:
        record["created_at"] = body.timestamp

    try:
        supabase = _get_supabase()
        supabase.table("feedback").insert(record).execute()
    except Exception as exc:  # noqa: BLE001
        # Best-effort audit / feedback logging - log error if db table issues arise
        logger.warning("Feedback insert to Supabase encountered issue: %s", exc)

    return FeedbackResponse(
        status="success",
        message="Feedback received",
    )
