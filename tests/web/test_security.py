"""Security behaviour of the web layer (docs/api-contract.md, sections 2, 5, 10 and 12)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fieldledger.web import contract
from fieldledger.web.apierrors import ApiError
from fieldledger.web.csvsafe import neutralise_cell, neutralise_csv_text
from fieldledger.web.paths import validate_relative_path
from fieldledger.web.__main__ import main as web_main
from webtestlib import BASE_URL, CUSTOMERS_CSV, HEADERS, ROOT, make_app, upload


# ------------------------------------------------------------------ guard middleware
def test_unknown_host_header_is_forbidden(tmp_path):
    with TestClient(make_app(tmp_path), base_url="http://evil.example") as c:
        r = c.get("/api/health")
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_loopback_hosts_are_allowed(tmp_path, host):
    with TestClient(make_app(tmp_path), base_url=f"http://{host}") as c:
        assert c.get("/api/health").status_code == 200


def test_state_changing_requests_need_the_client_header(client):
    for method, url in (("post", "/api/scans"), ("delete", "/api/workspace"), ("post", "/api/workspace/bundled")):
        r = getattr(client, method)(url)
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    r = client.post("/api/workspace/bundled", headers={"X-FieldLedger-Client": "other"})
    assert r.status_code == 403
    assert client.get("/api/workspace").status_code == 200  # GET needs no header


def test_cross_origin_requests_are_forbidden(client):
    bad = client.post("/api/scans", headers={**HEADERS, "Origin": "http://evil.example"}, json={})
    assert bad.status_code == 403
    same = client.get("/api/workspace", headers={"Origin": BASE_URL})
    assert same.status_code == 200 and "access-control-allow-origin" not in same.headers
    ok = client.delete("/api/workspace", headers={**HEADERS, "Origin": BASE_URL})
    assert ok.status_code != 403
    null = client.delete("/api/workspace", headers={**HEADERS, "Origin": "null"})
    assert null.status_code == 403


def test_security_headers_and_no_cache_on_api(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"


def test_unexpected_errors_are_generic_and_logged_without_message(tmp_path, caplog, monkeypatch):
    app = make_app(tmp_path)
    with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as c:
        def boom():
            raise RuntimeError("/secret/path/with/value-ABC123")
        monkeypatch.setattr(app.state.manager, "state", boom)
        with caplog.at_level(logging.INFO, logger="fieldledger.web"):
            r = c.get("/api/workspace")
        assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
        assert "secret" not in r.text and "value-ABC123" not in caplog.text


def test_logs_contain_no_file_names_or_columns(tmp_path, caplog):
    with TestClient(make_app(tmp_path), base_url=BASE_URL) as c, caplog.at_level(logging.DEBUG):
        c.post("/api/workspace/files", headers=HEADERS,
               files=[("files", ("secretcanary_file.csv", CUSTOMERS_CSV))],
               data={"relative_paths": ["secretcanary_dir/secretcanary_file.csv"]})
        from webtestlib import run_scan
        run_scan(c)
        c.delete("/api/workspace/files", headers=HEADERS, params={"source_key": "secretcanary_dir/secretcanary_file.csv"})
    # Only the server's own loggers matter; the HTTP test client logs its own request lines.
    server_log = "\n".join(r.getMessage() for r in caplog.records
                           if r.name.startswith("fieldledger"))
    assert "scan started" in server_log  # the check is looking at real server output
    for needle in ("secretcanary", "email_address", "full_name", "customer_id"):
        assert needle not in server_log


def test_uvicorn_access_log_is_disabled(monkeypatch):
    import uvicorn
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    assert web_main(["--root", str(ROOT), "--port", "8799"]) == 0
    assert seen["access_log"] is False and seen["host"] == "127.0.0.1"


def test_body_limit_rejects_oversized_uploads_early(tmp_path):
    app = make_app(tmp_path, limits=contract.WorkspaceLimits(max_workspace_bytes=10_000))
    with TestClient(app, base_url=BASE_URL) as c:
        big = b"a,b\n" + b"1,2\n" * 3_000_000  # ~12 MB, far above limit + overhead
        r = c.post("/api/workspace/files", headers=HEADERS,
                   files=[("files", ("big.csv", big))], data={"relative_paths": ["big.csv"]})
        assert r.status_code == 413 and r.json()["error"]["code"] == "workspace_too_large"
        meta = c.post("/api/workspace/metadata", headers=HEADERS,
                      files=[("file", ("m.yaml", b"x" * (contract.WorkspaceLimits().max_metadata_bytes + 3_000_000)))])
        assert meta.status_code == 413


# ------------------------------------------------------------------ path validation
@pytest.mark.parametrize("value", ["a.csv", "dir/sub/a.CSV", "data/app.sqlite3", "ünï/cødé.csv", "a b/c d.csv"])
def test_valid_relative_paths(value):
    assert validate_relative_path(value) == value


@pytest.mark.parametrize("value", [
    "", "/a.csv", "../a.csv", "a/../b.csv", "./a.csv", "a/./b.csv", "a//b.csv", "a\\b.csv", "C:a.csv",
    "c:/a.csv", "a\x00.csv", "a\n.csv", "a\t.csv", 'a".csv', "a<b.csv", "a|b.csv", "a*.csv", "x" * 300 + ".csv",
    "/".join(["d"] * 600) + "/a.csv", "COM1.csv", "aux.tar.csv", "LPT9/a.csv", "trail./a.csv", "trail /a.csv", 5, None,
])
def test_invalid_relative_paths(value):
    with pytest.raises(ApiError) as exc:
        validate_relative_path(value)
    assert exc.value.code in {"invalid_name", "unsupported_type"}


# ------------------------------------------------------------------ CSV formula injection
@pytest.mark.parametrize("cell", ["=1+1", "+1", "-1", "@SUM(A1)", "\uff1dx", "\uff0bx", " =x", "\u200b=x", "\t=x", "\rx", "\nx", "\ufeff@x"])
def test_formula_cells_are_neutralised(cell):
    assert neutralise_cell(cell).startswith("'")


@pytest.mark.parametrize("cell", ["", "name", "a=b", "x+y", "1", "'=x", "email address"])
def test_plain_cells_are_unchanged(cell):
    assert neutralise_cell(cell) == cell


def test_unchanged_csv_is_byte_identical_and_changed_csv_is_quoted():
    plain = "a,b\n1,\"x,y\"\n"
    assert neutralise_csv_text(plain) == plain
    out = neutralise_csv_text("a,b\n=cmd,\"-x\ny\"\n")
    assert out == "a,b\n'=cmd,\"'-x\ny\"\n"


# ------------------------------------------------------------------ workspace lifecycle
def test_idle_expiry_removes_the_directory(tmp_path):
    app = make_app(tmp_path, limits=contract.WorkspaceLimits(idle_timeout_seconds=60))
    manager = app.state.manager
    now = [1000.0]
    manager._clock = lambda: now[0]
    manager._last = now[0]
    with TestClient(app, base_url=BASE_URL) as c:
        upload(c, {"a.csv": CUSTOMERS_CSV})
        assert list(tmp_path.glob("fieldledger-ws-*"))
        now[0] += 30
        assert c.get("/api/workspace").json()["exists"] is True  # activity refreshes the timer
        now[0] += 61
        assert c.get("/api/workspace").json() == {"exists": False}
        assert not list(tmp_path.glob("fieldledger-ws-*"))
        assert c.post("/api/scans", headers=HEADERS, json={}).json()["error"]["code"] == "no_workspace"


def test_shutdown_removes_the_workspace(tmp_path):
    with TestClient(make_app(tmp_path), base_url=BASE_URL) as c:
        upload(c, {"a.csv": CUSTOMERS_CSV})
        assert list(tmp_path.glob("fieldledger-ws-*"))
    assert not list(tmp_path.glob("fieldledger-ws-*"))


def test_stale_directories_are_removed_at_start(tmp_path):
    import os, time
    stale = tmp_path / "fieldledger-ws-old"
    (stale / "data").mkdir(parents=True)
    old = time.time() - 7200
    os.utime(stale, (old, old))
    fresh = tmp_path / "fieldledger-ws-fresh"
    fresh.mkdir()
    other = tmp_path / "unrelated-dir"
    other.mkdir()
    with TestClient(make_app(tmp_path), base_url=BASE_URL):
        pass
    assert not stale.exists() and fresh.exists() and other.exists()


def test_workspace_files_never_leave_the_workspace_directory(client, tmp_path):
    upload(client, {"a/b/c.csv": CUSTOMERS_CSV})
    root = next(tmp_path.glob("fieldledger-ws-*"))
    files = [p for p in root.rglob("*") if p.is_file()]
    assert files and all(root in p.resolve().parents for p in files)
    assert not any(p.is_symlink() for p in root.rglob("*"))
    assert not list(ROOT.glob("*.csv")) and not (ROOT / "output").exists()


# ------------------------------------------------------------------ static files and CLI
def test_static_serving_and_traversal(tmp_path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>FL</title>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    with TestClient(make_app(tmp_path, static_dir=static), base_url=BASE_URL) as c:
        assert "FL" in c.get("/").text and "FL" in c.get("/findings").text  # SPA fallback
        assert c.get("/assets/app.js").text == "console.log(1)"
        for attempt in ("/../secret.txt", "/..%2fsecret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt"):
            assert "TOP-SECRET" not in c.get(attempt).text
        assert c.get("/api/nothing").json()["error"]["code"] == "not_found"


def test_server_refuses_non_loopback_without_flag(capsys):
    assert web_main(["--host", "0.0.0.0", "--root", str(ROOT)]) == 2
    assert "--allow-network" in capsys.readouterr().err


def test_server_rejects_non_positive_limits(capsys):
    assert web_main(["--max-files", "0", "--root", str(ROOT)]) == 1
    assert "positive integer" in capsys.readouterr().err
