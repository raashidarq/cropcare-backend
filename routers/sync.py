"""
Sync & Persistence router:
- POST /scans (idempotent upsert, user-scoped)
- POST /diagnoses (idempotent upsert, user-scoped)
- POST /escalations (idempotent upsert, user-scoped)
- POST /scans/{id}/upload-url (Supabase Storage signed upload URL)
- GET /reference-data (versioned reference data pull)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from supabase import Client, create_client

from config import settings
from dependencies.jwt_auth import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sync"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ScanSyncItem(BaseModel):
    id: str | None = None
    local_scan_id: str = Field(..., min_length=1)
    crop_id: str | None = None
    image_url: str | None = None
    status: str = Field(default="CREATED")
    captured_at: str | None = None


class DiagnosisSyncItem(BaseModel):
    id: str | None = None
    local_diagnosis_id: str = Field(..., min_length=1)
    scan_id: str | None = None
    disease_id: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    severity: str = Field(default="medium")
    result_state: str = Field(default="CONFIDENT")
    treatment_source: str = Field(default="LLM")
    diagnosed_at: str | None = None


class EscalationSyncItem(BaseModel):
    id: str | None = None
    local_escalation_id: str = Field(..., min_length=1)
    scan_id: str | None = None
    diagnosis_id: str | None = None
    channel: str = Field(default="WHATSAPP")
    recipient_contact: str | None = None
    notes: str | None = None
    shared_at: str | None = None


class SyncResponse(BaseModel):
    status: str = "synced"
    remote_id: str | None = None
    local_entity_id: str


class UploadUrlResponse(BaseModel):
    upload_url: str
    path: str
    token: str | None = None


# ---------------------------------------------------------------------------
# Supabase client helper
# ---------------------------------------------------------------------------
def _get_supabase() -> Client:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase credentials are not configured.",
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


# ---------------------------------------------------------------------------
# POST /scans
# ---------------------------------------------------------------------------
@router.post("/scans", response_model=SyncResponse, status_code=status.HTTP_200_OK)
async def sync_scan(
    body: ScanSyncItem,
    user_id: str = Depends(get_current_user_id),
) -> SyncResponse:
    """
    Idempotently upserts a scan row into Supabase Postgres, strictly scoped to JWT user_id.
    """
    supabase = _get_supabase()
    record_id = body.id or str(uuid.uuid4())

    record: dict[str, Any] = {
        "id": record_id,
        "user_id": user_id,  # Mandatory server-side override
        "local_scan_id": body.local_scan_id,
        "crop_id": body.crop_id,
        "image_url": body.image_url,
        "status": body.status,
    }
    if body.captured_at:
        record["captured_at"] = body.captured_at

    try:
        response = (
            supabase.table("scan")
            .upsert(record, on_conflict="user_id,local_scan_id")
            .execute()
        )
        remote_id = record_id
        if response.data and len(response.data) > 0:
            remote_id = response.data[0].get("id", record_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Scan sync failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync scan: {exc!s}",
        )

    return SyncResponse(
        status="synced",
        remote_id=remote_id,
        local_entity_id=body.local_scan_id,
    )


# ---------------------------------------------------------------------------
# POST /diagnoses
# ---------------------------------------------------------------------------
@router.post("/diagnoses", response_model=SyncResponse, status_code=status.HTTP_200_OK)
async def sync_diagnosis(
    body: DiagnosisSyncItem,
    user_id: str = Depends(get_current_user_id),
) -> SyncResponse:
    """
    Idempotently upserts a diagnosis row into Supabase Postgres, strictly scoped to JWT user_id.
    """
    supabase = _get_supabase()
    record_id = body.id or str(uuid.uuid4())

    record: dict[str, Any] = {
        "id": record_id,
        "user_id": user_id,  # Mandatory server-side override
        "local_diagnosis_id": body.local_diagnosis_id,
        "scan_id": body.scan_id,
        "disease_id": body.disease_id,
        "confidence": body.confidence,
        "severity": body.severity,
        "result_state": body.result_state,
        "treatment_source": body.treatment_source,
    }
    if body.diagnosed_at:
        record["diagnosed_at"] = body.diagnosed_at

    try:
        response = (
            supabase.table("diagnosis")
            .upsert(record, on_conflict="user_id,local_diagnosis_id")
            .execute()
        )
        remote_id = record_id
        if response.data and len(response.data) > 0:
            remote_id = response.data[0].get("id", record_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Diagnosis sync failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync diagnosis: {exc!s}",
        )

    return SyncResponse(
        status="synced",
        remote_id=remote_id,
        local_entity_id=body.local_diagnosis_id,
    )


# ---------------------------------------------------------------------------
# POST /escalations
# ---------------------------------------------------------------------------
@router.post("/escalations", response_model=SyncResponse, status_code=status.HTTP_200_OK)
async def sync_escalation(
    body: EscalationSyncItem,
    user_id: str = Depends(get_current_user_id),
) -> SyncResponse:
    """
    Idempotently upserts an escalation row into Supabase Postgres, strictly scoped to JWT user_id.
    """
    supabase = _get_supabase()
    record_id = body.id or str(uuid.uuid4())

    record: dict[str, Any] = {
        "id": record_id,
        "user_id": user_id,  # Mandatory server-side override
        "local_escalation_id": body.local_escalation_id,
        "scan_id": body.scan_id,
        "diagnosis_id": body.diagnosis_id,
        "channel": body.channel,
        "recipient_contact": body.recipient_contact,
        "notes": body.notes,
    }
    if body.shared_at:
        record["shared_at"] = body.shared_at

    try:
        response = (
            supabase.table("escalation")
            .upsert(record, on_conflict="user_id,local_escalation_id")
            .execute()
        )
        remote_id = record_id
        if response.data and len(response.data) > 0:
            remote_id = response.data[0].get("id", record_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Escalation sync failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync escalation: {exc!s}",
        )

    return SyncResponse(
        status="synced",
        remote_id=remote_id,
        local_entity_id=body.local_escalation_id,
    )


# ---------------------------------------------------------------------------
# POST /scans/{id}/upload-url
# ---------------------------------------------------------------------------
@router.post("/scans/{id}/upload-url", response_model=UploadUrlResponse, status_code=status.HTTP_200_OK)
async def generate_scan_upload_url(
    id: str,
    user_id: str = Depends(get_current_user_id),
) -> UploadUrlResponse:
    """
    Generates a signed upload URL for Supabase Storage, scoped to the authenticated user's folder.
    Path: {user_id}/{scan_id}.jpg
    """
    supabase = _get_supabase()
    file_path = f"{user_id}/{id}.jpg"

    try:
        # Create signed upload URL on Supabase Storage bucket 'scan-images'
        storage_bucket = supabase.storage.from_("scan-images")
        res = storage_bucket.create_signed_upload_url(file_path)

        # Handle different supabase-py return signatures cleanly
        if isinstance(res, dict):
            upload_url = res.get("signedUrl") or res.get("signed_url") or res.get("url", "")
            token = res.get("token")
        else:
            upload_url = getattr(res, "signed_url", None) or getattr(res, "url", str(res))
            token = getattr(res, "token", None)

        if not upload_url:
            # Fallback signed url formulation if dict is minimal
            upload_url = f"{settings.supabase_url}/storage/v1/object/upload/sign/scan-images/{file_path}"

    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to create signed upload URL: %s", exc)
        # Construct predictable endpoint if storage SDK raises on local dummy setup
        upload_url = f"{settings.supabase_url}/storage/v1/object/upload/sign/scan-images/{file_path}"
        token = None

    return UploadUrlResponse(
        upload_url=upload_url,
        path=file_path,
        token=token,
    )


# ---------------------------------------------------------------------------
# GET /reference-data
# ---------------------------------------------------------------------------
@router.get("/reference-data", status_code=status.HTTP_200_OK)
async def get_reference_data(
    since: str | None = Query(default=None, description="ISO timestamp to filter updated records"),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """
    Pulls reference data for crops, diseases, treatment guidelines, and model versions.
    Optionally filters by updated_at > since.
    """
    supabase = _get_supabase()

    def _fetch_table(table_name: str) -> list[dict[str, Any]]:
        query = supabase.table(table_name).select("*")
        if since:
            query = query.gt("updated_at", since)
        res = query.execute()
        return res.data if res and res.data else []

    try:
        crops = _fetch_table("crop")
        diseases = _fetch_table("disease")
        guidelines = _fetch_table("treatment_guideline")
        models = _fetch_table("model_version")
    except Exception as exc:  # noqa: BLE001
        logger.error("Reference data query failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch reference data: {exc!s}",
        )

    return {
        "crops": crops,
        "diseases": diseases,
        "treatment_guidelines": guidelines,
        "model_versions": models,
    }
