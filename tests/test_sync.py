"""
Tests for Sync and Reference Data endpoints:
- POST /scans
- POST /diagnoses
- POST /escalations
- POST /scans/{id}/upload-url
- GET /reference-data
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app, raise_server_exceptions=False)


def _make_valid_jwt(secret: str = "testsecret", sub: str = "user-uuid-1234") -> str:
    payload = {
        "sub": sub,
        "aud": "authenticated",  # every real Supabase token carries this
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _make_expired_jwt(secret: str = "testsecret") -> str:
    payload = {
        "sub": "user-uuid-1234",
        "aud": "authenticated",
        "exp": int(time.time()) - 10,
        "iat": int(time.time()) - 3610,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ==============================================================================
# 1. Authentication Enforcement (401 on missing/invalid JWT)
# ==============================================================================
class TestSyncAuthenticationEnforcement:
    @pytest.mark.parametrize(
        ("method", "endpoint", "payload"),
        [
            ("post", "/scans", {"local_scan_id": "loc-1", "crop_id": "tomato"}),
            ("post", "/diagnoses", {"local_diagnosis_id": "diag-1", "confidence": 0.9}),
            ("post", "/escalations", {"local_escalation_id": "esc-1"}),
            ("post", "/scans/scan-123/upload-url", None),
            ("get", "/reference-data", None),
        ],
    )
    def test_unauthenticated_request_returns_401(self, method, endpoint, payload):
        if method == "post":
            response = client.post(endpoint, json=payload or {})
        else:
            response = client.get(endpoint)
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required."

    def test_expired_jwt_returns_401(self):
        token = _make_expired_jwt()
        response = client.post(
            "/scans",
            json={"local_scan_id": "loc-1", "crop_id": "tomato"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401


# ==============================================================================
# 2. Security Scoping (Server overwrites user_id with JWT claim)
# ==============================================================================
class TestSyncSecurityScoping:
    @patch("routers.sync._get_supabase")
    def test_scan_sync_overwrites_spoofed_user_id(self, mock_get_supabase):
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_table.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "remote-scan-1"}])
        mock_supabase.table.return_value = mock_table
        mock_get_supabase.return_value = mock_supabase

        token = _make_valid_jwt(sub="actual-farmer-id")
        payload = {
            "user_id": "attacker-spoofed-id",  # Attempt to spoof someone else's user_id
            "local_scan_id": "loc-scan-99",
            "crop_id": "tomato",
            "status": "CREATED",
        }

        response = client.post(
            "/scans",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "synced"
        assert response.json()["local_entity_id"] == "loc-scan-99"

        # Verify that the record passed to Supabase upsert has the JWT's sub
        upsert_calls = mock_table.upsert.call_args_list
        assert len(upsert_calls) == 1
        record = upsert_calls[0][0][0]
        assert record["user_id"] == "actual-farmer-id"
        assert record["local_scan_id"] == "loc-scan-99"

    @patch("routers.sync._get_supabase")
    def test_scan_sync_accepts_unknown_and_derived_crop_id(self, mock_get_supabase):
        """
        Verifies scan sync handles crop_id: 'unknown' (pre-inference or unsupported)
        and derived crop_id strings (e.g. 'tomato').
        """
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_table.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "scan-uuid-1"}])
        mock_supabase.table.return_value = mock_table
        mock_get_supabase.return_value = mock_supabase

        token = _make_valid_jwt(sub="farmer-uuid")

        # Case 1: Pre-inference or unsupported fallback ("unknown")
        payload_unknown = {
            "id": "scan-uuid-1",
            "local_scan_id": "loc-uuid-1",
            "crop_id": "unknown",
            "image_url": "farmer-uuid/scan-uuid-1.jpg",
            "status": "COMPLETED",
        }
        res1 = client.post("/scans", json=payload_unknown, headers={"Authorization": f"Bearer {token}"})
        assert res1.status_code == 200
        record1 = mock_table.upsert.call_args_list[-1][0][0]
        assert record1["crop_id"] == "unknown"
        assert record1["status"] == "COMPLETED"

        # Case 2: Derived crop_id (e.g. "tomato")
        payload_derived = {
            "id": "scan-uuid-2",
            "local_scan_id": "loc-uuid-2",
            "crop_id": "tomato",
            "image_url": "farmer-uuid/scan-uuid-2.jpg",
            "status": "COMPLETED",
        }
        res2 = client.post("/scans", json=payload_derived, headers={"Authorization": f"Bearer {token}"})
        assert res2.status_code == 200
        record2 = mock_table.upsert.call_args_list[-1][0][0]
        assert record2["crop_id"] == "tomato"

    @patch("routers.sync._get_supabase")
    def test_diagnosis_sync_scopes_to_jwt_user(self, mock_get_supabase):
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_table.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "remote-diag-1"}])
        mock_supabase.table.return_value = mock_table
        mock_get_supabase.return_value = mock_supabase

        token = _make_valid_jwt(sub="actual-farmer-id")
        payload = {
            "local_diagnosis_id": "loc-diag-1",
            "confidence": 0.95,
            "disease_id": "tomato_late_blight",
            "severity": "high",
        }

        response = client.post(
            "/diagnoses",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        upsert_calls = mock_table.upsert.call_args_list
        assert len(upsert_calls) == 1
        record = upsert_calls[0][0][0]
        assert record["user_id"] == "actual-farmer-id"
        assert record["confidence"] == 0.95

    @patch("routers.sync._get_supabase")
    def test_escalation_sync_scopes_to_jwt_user(self, mock_get_supabase):
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_table.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "remote-esc-1"}])
        mock_supabase.table.return_value = mock_table
        mock_get_supabase.return_value = mock_supabase

        token = _make_valid_jwt(sub="actual-farmer-id")
        payload = {
            "local_escalation_id": "loc-esc-1",
            "channel": "WHATSAPP",
            "notes": "Urgent consultation needed",
        }

        response = client.post(
            "/escalations",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        upsert_calls = mock_table.upsert.call_args_list
        assert len(upsert_calls) == 1
        record = upsert_calls[0][0][0]
        assert record["user_id"] == "actual-farmer-id"
        assert record["notes"] == "Urgent consultation needed"


# ==============================================================================
# 3. Storage Signed Upload URL
# ==============================================================================
class TestStorageUploadUrl:
    @patch("routers.sync._get_supabase")
    def test_generate_scan_upload_url(self, mock_get_supabase):
        mock_supabase = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.create_signed_upload_url.return_value = {
            "signedUrl": "https://storage.supabase.co/upload/sign/scan-images/user-123/scan-555.jpg?token=abc",
            "path": "user-123/scan-555.jpg",
            "token": "abc",
        }
        mock_supabase.storage.from_.return_value = mock_bucket
        mock_get_supabase.return_value = mock_supabase

        token = _make_valid_jwt(sub="user-123")
        response = client.post(
            "/scans/scan-555/upload-url",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["path"] == "user-123/scan-555.jpg"
        assert "upload_url" in data
        assert "scan-555.jpg" in data["upload_url"]

        # Verify storage folder was scoped to the user_id
        mock_bucket.create_signed_upload_url.assert_called_with("user-123/scan-555.jpg")


# ==============================================================================
# 4. Reference Data Retrieval & Date Filtering
# ==============================================================================
class TestReferenceData:
    @patch("routers.sync._get_supabase")
    def test_get_reference_data(self, mock_get_supabase):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[{"id": "tomato", "name_en": "Tomato"}])
        mock_supabase.table.return_value = mock_query
        mock_get_supabase.return_value = mock_supabase

        token = _make_valid_jwt(sub="user-123")
        response = client.get(
            "/reference-data",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "crops" in data
        assert "diseases" in data
        assert "treatment_guidelines" in data
        assert "model_versions" in data

    @patch("routers.sync._get_supabase")
    def test_get_reference_data_with_since_filter(self, mock_get_supabase):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.gt.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[])
        mock_supabase.table.return_value = mock_query
        mock_get_supabase.return_value = mock_supabase

        token = _make_valid_jwt(sub="user-123")
        response = client.get(
            "/reference-data?since=2026-08-20T00:00:00Z",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        # Verify gt was called with 'updated_at' and the timestamp
        assert mock_query.gt.called
        gt_calls = mock_query.gt.call_args_list
        assert any(call[0] == ("updated_at", "2026-08-20T00:00:00Z") for call in gt_calls)
