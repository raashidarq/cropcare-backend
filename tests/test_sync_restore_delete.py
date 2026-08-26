"""
Tests for GET /sync/scans (restore) and DELETE /sync/scans/{id}.

The security-critical property here is user scoping. Both endpoints take an id
or return rows keyed on the caller's identity, and the id comes from the
client - so every query must be filtered by user_id, not just the ones where
it seems obviously necessary.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt
from fastapi.testclient import TestClient

from main import app

client = TestClient(app, raise_server_exceptions=False)


def _jwt(secret: str = "testsecret", sub: str = "user-1") -> str:
    return jwt.encode(
        {"sub": sub, "exp": int(time.time()) + 3600, "iat": int(time.time())},
        secret,
        algorithm="HS256",
    )


def _auth():
    return {"Authorization": f"Bearer {_jwt()}"}


class _FakeQuery:
    """Records the filters applied, so tests can assert on user scoping."""

    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters = {}
        store.setdefault("calls", []).append(self)

    def select(self, *a, **k):
        return self

    def delete(self):
        self.store.setdefault("deleted", []).append(self)
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self.filters[col] = vals
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        rows = self.store.get(self.table, [])
        # Apply the eq filters the endpoint set, so ownership actually bites.
        for col, val in self.filters.items():
            if isinstance(val, list):
                rows = [r for r in rows if r.get(col) in val]
            else:
                rows = [r for r in rows if r.get(col) == val]
        res = MagicMock()
        res.data = rows
        res.count = len(rows)
        return res


def _fake_supabase(store):
    sb = MagicMock()
    sb.table.side_effect = lambda name: _FakeQuery(store, name)

    storage = MagicMock()
    storage.create_signed_url.return_value = {"signedURL": "https://signed/x.jpg"}
    storage.remove.return_value = True
    sb.storage.from_.return_value = storage
    store["storage"] = storage
    return sb


def _run(store):
    return patch("routers.sync._get_supabase", return_value=_fake_supabase(store))


def _auth_patch():
    m = patch("dependencies.jwt_auth.settings")
    s = m.start()
    s.supabase_jwt_secret = "testsecret"
    return m


class TestRestore:
    def test_returns_only_the_callers_scans(self):
        store = {
            "scan": [
                {"id": "s1", "user_id": "user-1", "image_url": "user-1/s1.jpg",
                 "crop_id": "tomato", "status": "DIAGNOSED",
                 "captured_at": "2026-08-01"},
                {"id": "s2", "user_id": "someone-else",
                 "image_url": "x/s2.jpg", "crop_id": "chili",
                 "status": "DIAGNOSED", "captured_at": "2026-08-02"},
            ],
            "diagnosis": [],
        }
        m = _auth_patch()
        try:
            with _run(store):
                r = client.get("/scans", headers=_auth())
        finally:
            m.stop()

        assert r.status_code == 200
        data = r.json()
        assert [s["id"] for s in data["scans"]] == ["s1"]

    def test_diagnosis_is_inlined(self):
        store = {
            "scan": [
                {"id": "s1", "user_id": "user-1", "image_url": None,
                 "crop_id": "tomato", "status": "DIAGNOSED",
                 "captured_at": "2026-08-01"},
            ],
            "diagnosis": [
                {"scan_id": "s1", "disease_id": "tomato_late_blight",
                 "confidence": 0.88, "severity": "high",
                 "result_state": "CONFIDENT", "diagnosed_at": "2026-08-01"},
            ],
        }
        m = _auth_patch()
        try:
            with _run(store):
                r = client.get("/scans", headers=_auth())
        finally:
            m.stop()

        scan = r.json()["scans"][0]
        # Inlined so a phone that loses signal between two requests does not
        # end up with photos it cannot show a result for.
        assert scan["disease_id"] == "tomato_late_blight"
        assert scan["confidence"] == 0.88

    def test_image_urls_are_signed(self):
        store = {
            "scan": [
                {"id": "s1", "user_id": "user-1", "image_url": "user-1/s1.jpg",
                 "crop_id": "tomato", "status": "DIAGNOSED",
                 "captured_at": "2026-08-01"},
            ],
            "diagnosis": [],
        }
        m = _auth_patch()
        try:
            with _run(store):
                r = client.get("/scans", headers=_auth())
        finally:
            m.stop()

        # Storage is not public, so the stored path alone is useless.
        assert r.json()["scans"][0]["image_url"] == "https://signed/x.jpg"
        store["storage"].create_signed_url.assert_called()

    def test_a_scan_with_no_readable_image_still_comes_back(self):
        store = {
            "scan": [
                {"id": "s1", "user_id": "user-1", "image_url": "user-1/s1.jpg",
                 "crop_id": "tomato", "status": "DIAGNOSED",
                 "captured_at": "2026-08-01"},
            ],
            "diagnosis": [],
        }
        m = _auth_patch()
        try:
            with _run(store):
                store["storage"].create_signed_url.side_effect = RuntimeError("gone")
                r = client.get("/scans", headers=_auth())
        finally:
            m.stop()

        # The scan and its result are still the farmer's, image or not.
        assert r.status_code == 200
        assert r.json()["scans"][0]["image_url"] is None

    def test_requires_authentication(self):
        assert client.get("/scans").status_code == 401

    def test_limit_is_bounded(self):
        m = _auth_patch()
        try:
            r = client.get("/scans?limit=99999", headers=_auth())
        finally:
            m.stop()
        # A season of daily scanning is thousands of rows on a budget phone.
        assert r.status_code == 422


class TestDelete:
    def _store(self):
        return {
            "scan": [
                {"id": "s1", "user_id": "user-1", "image_url": "user-1/s1.jpg"},
                {"id": "s2", "user_id": "someone-else", "image_url": "x/s2.jpg"},
            ],
            "diagnosis": [{"scan_id": "s1", "user_id": "user-1"}],
            "escalation": [],
        }

    def test_deletes_the_scan_and_its_image(self):
        store = self._store()
        m = _auth_patch()
        try:
            with _run(store):
                r = client.delete("/scans/s1", headers=_auth())
        finally:
            m.stop()

        assert r.status_code == 200
        assert r.json()["image_deleted"] is True
        # Account deletion leaves photographs in the bucket; this must not
        # repeat that.
        store["storage"].remove.assert_called_once_with(["user-1/s1.jpg"])

    def test_cannot_delete_someone_elses_scan(self):
        store = self._store()
        m = _auth_patch()
        try:
            with _run(store):
                r = client.delete("/scans/s2", headers=_auth())
        finally:
            m.stop()

        # The id comes from the client. Without the user_id filter this would
        # delete anyone's scan given its id.
        assert r.status_code == 404
        store["storage"].remove.assert_not_called()

    def test_unknown_id_is_404_not_a_silent_success(self):
        store = self._store()
        m = _auth_patch()
        try:
            with _run(store):
                r = client.delete("/scans/nope", headers=_auth())
        finally:
            m.stop()
        assert r.status_code == 404

    def test_every_delete_is_user_scoped(self):
        store = self._store()
        m = _auth_patch()
        try:
            with _run(store):
                client.delete("/scans/s1", headers=_auth())
        finally:
            m.stop()

        deletes = store.get("deleted", [])
        assert deletes, "expected delete statements"
        for q in deletes:
            assert q.filters.get("user_id") == "user-1", (
                f"unscoped delete on {q.table}: {q.filters}"
            )

    def test_a_failed_image_removal_is_reported_not_hidden(self):
        store = self._store()
        m = _auth_patch()
        try:
            with _run(store):
                store["storage"].remove.side_effect = RuntimeError("storage down")
                r = client.delete("/scans/s1", headers=_auth())
        finally:
            m.stop()

        # The row is gone but the photograph is not, and the caller should be
        # able to know that rather than being told everything worked.
        assert r.status_code == 200
        assert r.json()["image_deleted"] is False

    def test_requires_authentication(self):
        assert client.delete("/scans/s1").status_code == 401
