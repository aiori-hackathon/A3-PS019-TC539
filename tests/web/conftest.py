"""Fixtures for the web API tests. Synthetic data only; no network, no real files."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from webtestlib import BASE_URL, HEADERS, make_app, run_scan  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    with TestClient(make_app(tmp_path), base_url=BASE_URL) as c:
        yield c


@pytest.fixture()
def loaded(client):
    """A workspace holding the bundled synthetic data and its metadata, and a finished scan."""
    r = client.post("/api/workspace/bundled", headers=HEADERS)
    assert r.status_code == 200, r.text
    sid, status = run_scan(client)
    assert status["state"] == "succeeded", status
    return client, sid
