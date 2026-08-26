"""
Sync & Persistence router:
- POST /scans (idempotent upsert, user-scoped)
- POST /diagnoses (idempotent upsert, user-scoped)
- POST /escalations (idempotent upsert, user-scoped)
- POST /scans/{id}/upload-url (Supabase Storage signed upload URL)
- GET /scans (restore: pull the user's own scans back down)
- DELETE /scans/{id} (remove one scan, its children, and its stored image)
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


class RestoredScan(BaseModel):
    """One scan as it comes back down, with its diagnosis inlined.

    Inlined rather than returned as a separate collection the client has to
    join: restore runs on a phone that may lose signal between two requests,
    and a scan without its diagnosis is a row the app cannot show.
    """

    id: str
    local_scan_id: str | None = None
    crop_id: str | None = None
    image_url: str | None = None
    status: str | None = None
    captured_at: str | None = None

    disease_id: str | None = None
    confidence: float | None = None
    severity: str | None = None
    result_state: str | None = None
    diagnosed_at: str | None = None


class RestoreScansResponse(BaseModel):
    scans: list[RestoredScan]
    total: int
    has_more: bool


class DeleteScanResponse(BaseModel):
    status: str = "deleted"
    scan_id: str
    image_deleted: bool


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
# GET /scans  -  restore
# ---------------------------------------------------------------------------
@router.get("/scans", response_model=RestoreScansResponse, status_code=status.HTTP_200_OK)
async def restore_scans(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
) -> RestoreScansResponse:
    """
    Returns the authenticated user's scans, newest first, so a device that has
    deleted its local copies can get them back.

    Until this existed, sync was one-way: the app said "Backed up" next to a
    "Delete local scans" button and could not honour it. A farmer who freed up
    storage lost their history permanently.

    Paged, because a season of daily scanning is thousands of rows and this
    runs on a budget phone over a rural connection.

    Image URLs are signed and short-lived. Storage is not public, so a stored
    path is useless to the client on its own.
    """
    supabase = _get_supabase()

    try:
        result = (
            supabase.table("scan")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .order("captured_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        rows = result.data or []
        total = result.count if result.count is not None else len(rows)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to read scans for restore: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read your scans. Please try again.",
        )

    # One query for every diagnosis, not one per scan: a hundred round trips
    # from a phone on a rural connection is not a restore, it is a hang.
    diagnoses_by_scan: dict[str, dict[str, Any]] = {}
    scan_ids = [r["id"] for r in rows if r.get("id")]
    if scan_ids:
        try:
            diag = (
                supabase.table("diagnosis")
                .select("*")
                .in_("scan_id", scan_ids)
                .execute()
            )
            for d in diag.data or []:
                # Keep the first per scan; a scan should only have one.
                diagnoses_by_scan.setdefault(d.get("scan_id"), d)
        except Exception as exc:  # noqa: BLE001
            # Degrade to scans without diagnoses rather than failing the whole
            # restore - a photo with no result is still the farmer's photo.
            logger.warning("Could not attach diagnoses to restore: %s", exc)

    storage = supabase.storage.from_("scan-images")
    scans: list[RestoredScan] = []
    for r in rows:
        d = diagnoses_by_scan.get(r.get("id"), {})

        image_url = None
        stored_path = r.get("image_url")
        if stored_path:
            try:
                signed = storage.create_signed_url(stored_path, 3600)
                if isinstance(signed, dict):
                    image_url = signed.get("signedURL") or signed.get("signedUrl")
                else:
                    image_url = getattr(signed, "signed_url", None)
            except Exception:  # noqa: BLE001
                # A missing or unreadable image must not sink the row. The
                # scan and its result are still worth restoring.
                image_url = None

        scans.append(
            RestoredScan(
                id=r.get("id"),
                local_scan_id=r.get("local_scan_id"),
                crop_id=r.get("crop_id"),
                image_url=image_url,
                status=r.get("status"),
                captured_at=r.get("captured_at"),
                disease_id=d.get("disease_id"),
                confidence=d.get("confidence"),
                severity=d.get("severity"),
                result_state=d.get("result_state"),
                diagnosed_at=d.get("diagnosed_at"),
            )
        )

    return RestoreScansResponse(
        scans=scans,
        total=total,
        has_more=(offset + len(rows)) < total,
    )


# ---------------------------------------------------------------------------
# DELETE /scans/{id}
# ---------------------------------------------------------------------------
@router.delete("/scans/{id}", response_model=DeleteScanResponse, status_code=status.HTTP_200_OK)
async def delete_scan(
    id: str,
    user_id: str = Depends(get_current_user_id),
) -> DeleteScanResponse:
    """
    Removes one scan from the cloud: its children, its row, and its image.

    Scoped to the authenticated user on every statement. The id comes from the
    client, so without the user_id filter this would delete anyone's scan given
    its id.

    The image is deleted too. Account deletion currently drops database rows
    and leaves photographs in the bucket, which is not really deletion; this
    endpoint does not repeat that.
    """
    supabase = _get_supabase()

    # Confirm ownership before deleting anything, so a wrong id returns 404
    # rather than silently succeeding.
    try:
        owned = (
            supabase.table("scan")
            .select("id, image_url")
            .eq("id", id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Ownership check failed for scan %s: %s", id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete that scan. Please try again.",
        )

    if not owned.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found.",
        )

    stored_path = owned.data[0].get("image_url")

    # Children first. Foreign keys may or may not cascade depending on how the
    # schema was applied, so this does not rely on them.
    for table_name in ("escalation", "diagnosis"):
        try:
            supabase.table(table_name).delete().eq("scan_id", id).eq(
                "user_id", user_id
            ).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not delete %s rows for scan %s: %s", table_name, id, exc)

    try:
        supabase.table("scan").delete().eq("id", id).eq("user_id", user_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to delete scan %s: %s", id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete that scan. Please try again.",
        )

    image_deleted = False
    if stored_path:
        try:
            supabase.storage.from_("scan-images").remove([stored_path])
            image_deleted = True
        except Exception as exc:  # noqa: BLE001
            # Reported honestly rather than swallowed: the row is gone but the
            # photograph is not, and the caller should be able to know that.
            logger.warning("Deleted scan %s but not its image: %s", id, exc)

    return DeleteScanResponse(scan_id=id, image_deleted=image_deleted)


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
