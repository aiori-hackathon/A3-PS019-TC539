"""Helpers shared by the web API tests (imported as ``webtestlib``)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fieldledger.limits import IngestionLimits  # noqa: E402
from fieldledger.web import contract  # noqa: E402
from fieldledger.web.app import AppConfig, create_app  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HEADERS = {"X-FieldLedger-Client": "ui"}
BASE_URL = "http://127.0.0.1:8765"

CUSTOMERS_CSV = (
    "customer_id,full_name,email_address,mobile_number,city,notes\n"
    "1,Test Person One,one@example.invalid,5550000001,Testville,canary-value-ONE\n"
    "2,Test Person Two,two@example.invalid,5550000002,Testburg,canary-value-TWO\n"
).encode()


def make_app(tmp_path: Path, **overrides):
    limits = overrides.pop("limits", contract.WorkspaceLimits())
    ingestion = overrides.pop("ingestion", IngestionLimits())
    cfg = AppConfig(root=ROOT, limits=limits, ingestion=ingestion, temp_base=tmp_path,
                    static_dir=overrides.pop("static_dir", tmp_path / "no-static"), **overrides)
    return create_app(cfg)


def upload(client, files: dict[str, bytes], **kw):
    parts = [("files", (name.rsplit("/", 1)[-1], data, "application/octet-stream"))
             for name, data in files.items()]
    return client.post("/api/workspace/files", headers=HEADERS, files=parts,
                       data={"relative_paths": list(files)}, **kw)


def wait_scan(client, scan_id: str, timeout: float = 30.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        status = client.get(f"/api/scans/{scan_id}").json()
        if status["state"] not in ("queued", "running"):
            return status
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def run_scan(client, **body):
    payload = {"include_declarations": False, "formats": ["json", "csv", "md"], **body}
    r = client.post("/api/scans", headers=HEADERS, json=payload)
    assert r.status_code == 202, r.text
    sid = r.json()["scan_id"]
    return sid, wait_scan(client, sid)
