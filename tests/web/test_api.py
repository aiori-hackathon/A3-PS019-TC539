"""End-to-end API tests against the real engine (docs/api-contract.md)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from fieldledger.classes import load_class_registry
from fieldledger.detector import detect_candidates
from fieldledger.discovery import discover_sources
from fieldledger.evaluation import evaluate, load_labels
from fieldledger.findings import source_key_for
from fieldledger.profiler import profile_source
from fieldledger.rules import load_rule_set
from fieldledger.web import contract

from webtestlib import CUSTOMERS_CSV, HEADERS, ROOT, make_app, run_scan, upload, wait_scan


def _poll_eval(client, eid):
    import time
    for _ in range(300):
        e = client.get(f"/api/evaluations/{eid}").json()
        if e["state"] in ("succeeded", "failed"):
            return e
        time.sleep(0.05)
    raise AssertionError("evaluation did not finish")


# ------------------------------------------------------------------ basics
def test_health_and_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    cfg = client.get("/api/config").json()
    assert [c["id"] for c in cfg["classes"]] == list(contract.CLASS_IDS)
    assert cfg["taxonomy_status"] == "confirmed"
    assert cfg["benchmark_available"] is True
    assert cfg["limits"]["max_files"] == 100
    assert cfg["supported_suffixes"] == list(contract.SUPPORTED_SUFFIXES)
    assert all(r["patterns"] for r in cfg["rules"])


def test_no_workspace_state_and_errors(client):
    assert client.get("/api/workspace").json() == {"exists": False}
    r = client.post("/api/scans", headers=HEADERS, json={})
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_workspace"
    r = client.get("/api/scans/nope/summary")
    assert r.status_code == 404 and r.json()["error"]["code"] in {"no_workspace", "not_found"}
    assert client.get("/api/does-not-exist").json()["error"]["code"] == "not_found"


# ------------------------------------------------------------------ upload
def test_upload_scan_roundtrip_and_no_raw_values(client):
    r = upload(client, {"crm/customers.csv": CUSTOMERS_CSV})
    assert r.status_code == 200 and r.json()["results"][0]["accepted"] is True
    state = client.get("/api/workspace").json()
    assert state["files"] == [{"source_key": "crm/customers.csv", "type": "csv",
                               "size_bytes": len(CUSTOMERS_CSV)}]
    sid, status = run_scan(client)
    assert status["state"] == "succeeded" and status["sources_done"] == status["sources_total"] == 1
    summary = client.get(f"/api/scans/{sid}/summary").json()
    assert summary["total_findings"] > 0 and summary["metadata"] is None

    blobs = [json.dumps(summary), json.dumps(client.get(f"/api/scans/{sid}/sources").json()),
             json.dumps(client.get(f"/api/scans/{sid}/findings").json()),
             json.dumps(client.get(f"/api/scans/{sid}/metadata").json()),
             json.dumps(client.get("/api/workspace").json())]
    for item in client.get(f"/api/scans/{sid}/findings").json()["items"]:
        blobs.append(json.dumps(client.get(f"/api/scans/{sid}/findings/{item['id']}").json()))
    for fmt in ("json", "csv", "md"):
        blobs.append(client.get(f"/api/scans/{sid}/export/{fmt}").text)
    joined = "\n".join(blobs)
    for canary in ("canary-value-ONE", "canary-value-TWO", "one@example.invalid", "5550000001"):
        assert canary not in joined


@pytest.mark.parametrize("name,code", [
    ("../evil.csv", "invalid_name"), ("a/../evil.csv", "invalid_name"),
    ("/abs.csv", "invalid_name"), ("C:/x.csv", "invalid_name"), ("a\\b.csv", "invalid_name"),
    ("CON.csv", "invalid_name"), ("dir/nul.csv", "invalid_name"), ("dir /x.csv", "invalid_name"),
    ("a:b.csv", "invalid_name"), ("q?.csv", "invalid_name"), ("dot./x.csv", "invalid_name"),
    ("notes.txt", "unsupported_type"), ("run.exe", "unsupported_type"), ("a//b.csv", "invalid_name"),
])
def test_unsafe_names_are_rejected_and_nothing_is_stored(client, name, code):
    r = upload(client, {name: CUSTOMERS_CSV})
    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["accepted"] is False and result["code"] == code
    assert client.get("/api/workspace").json()["files"] == []


def test_duplicate_names_are_case_insensitive_and_partial_acceptance(client):
    assert upload(client, {"a.csv": CUSTOMERS_CSV}).json()["results"][0]["accepted"]
    r = upload(client, {"A.CSV": CUSTOMERS_CSV, "b.csv": CUSTOMERS_CSV}).json()["results"]
    assert r[0]["code"] == "duplicate_name" and r[1]["accepted"] is True
    assert upload(client, {"a.csv/inner.csv": CUSTOMERS_CSV}).json()["results"][0]["code"] == "invalid_name"


def test_unusable_files_are_rejected_at_upload(client):
    r = upload(client, {"empty.csv": b"", "fake.db": b"this is not sqlite", "bin.csv": b"a,b\n\x00\x01\n"})
    assert [x["code"] for x in r.json()["results"]] == ["malformed_input"] * 3


def test_mismatched_relative_paths_is_bad_request(client):
    r = client.post("/api/workspace/files", headers=HEADERS,
                    files=[("files", ("a.csv", CUSTOMERS_CSV, "text/csv"))],
                    data={"relative_paths": ["a.csv", "b.csv"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_size_and_count_limits(tmp_path):
    from fastapi.testclient import TestClient
    from fieldledger.limits import IngestionLimits

    app = make_app(tmp_path, ingestion=IngestionLimits(max_file_bytes=100),
                   limits=contract.WorkspaceLimits(max_files=2, max_files_per_request=3))
    from webtestlib import BASE_URL
    with TestClient(app, base_url=BASE_URL) as c:
        r = upload(c, {"big.csv": CUSTOMERS_CSV}).json()["results"][0]
        assert r["code"] == "file_too_large"
        small = b"a,b\n1,2\n"
        res = upload(c, {"x.csv": small, "y.csv": small}).json()["results"]
        assert all(x["accepted"] for x in res)
        assert upload(c, {"z.csv": small}).json()["results"][0]["code"] == "too_many_files"
        r = upload(c, {"p.csv": small, "q.csv": small, "r.csv": small, "s.csv": small})
        assert r.status_code == 413 and r.json()["error"]["code"] == "too_many_files"
        assert not list(tmp_path.glob("fieldledger-ws-*/incoming/*"))  # no partial files left


def test_workspace_byte_limit(tmp_path):
    from fastapi.testclient import TestClient
    from webtestlib import BASE_URL
    app = make_app(tmp_path, limits=contract.WorkspaceLimits(max_workspace_bytes=1500))
    with TestClient(app, base_url=BASE_URL) as c:
        blob = b"h1,h2\n" + b"1,2\n" * 300
        first = upload(c, {"a.csv": blob}).json()["results"][0]
        second = upload(c, {"b.csv": blob}).json()["results"][0]
        assert first["accepted"] is True
        assert second["code"] == "workspace_too_large"


def test_delete_file_and_clear_workspace_remove_from_disk(client, tmp_path):
    upload(client, {"d/a.csv": CUSTOMERS_CSV, "b.csv": CUSTOMERS_CSV})
    assert client.delete("/api/workspace/files", headers=HEADERS, params={"source_key": "d/a.csv"}).status_code == 200
    assert client.delete("/api/workspace/files", headers=HEADERS, params={"source_key": "d/a.csv"}).status_code == 404
    root = next(tmp_path.glob("fieldledger-ws-*"))
    assert not (root / "data" / "d").exists()
    assert client.delete("/api/workspace", headers=HEADERS).json() == {"cleared": True}
    assert not list(tmp_path.glob("fieldledger-ws-*"))
    assert client.get("/api/workspace").json() == {"exists": False}


# ------------------------------------------------------------------ metadata
def test_metadata_upload_validation(client):
    good = (b"metadata_origin: user_provided\nentries:\n  - source_key: crm/customers.csv\n"
            b"    table: customers\n    column: email_address\n    purpose: Support\n")
    r = client.post("/api/workspace/metadata", headers=HEADERS, files=[("file", ("m.yaml", good))])
    assert r.status_code == 200 and r.json()["entries"] == 1 and r.json()["origin"] == "user_provided"
    bad = client.post("/api/workspace/metadata", headers=HEADERS,
                      files=[("file", ("m.yaml", b"metadata_origin: nope\nentries: []\n"))])
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_metadata"
    wrong = client.post("/api/workspace/metadata", headers=HEADERS, files=[("file", ("m.txt", good))])
    assert wrong.status_code == 415
    assert client.get("/api/workspace").json()["metadata"] == {"entries": 1, "origin": "user_provided"}
    assert client.delete("/api/workspace/metadata", headers=HEADERS).status_code == 200


def test_declarations_require_metadata(client):
    upload(client, {"a.csv": CUSTOMERS_CSV})
    r = client.post("/api/scans", headers=HEADERS, json={"include_declarations": True})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


# ------------------------------------------------------------------ scans
def test_failed_source_is_recorded_not_dropped(client):
    upload(client, {"ok.csv": CUSTOMERS_CSV, "bad.csv": b"a,b\n1,2,3\n"})
    sid, status = run_scan(client)
    assert status["state"] == "succeeded"
    assert status["sources_total"] == 2 and status["sources_done"] == 2
    assert [e["code"] for e in status["source_errors"]] == ["malformed_csv"]
    assert status["source_errors"][0]["source_key"] == "bad.csv"
    summary = client.get(f"/api/scans/{sid}/summary").json()
    assert summary["sources_scanned"] == 1 and summary["sources_failed"] == 1


def test_scan_stale_after_workspace_change_and_old_ids_are_gone(client):
    upload(client, {"a.csv": CUSTOMERS_CSV})
    sid, _ = run_scan(client)
    upload(client, {"b.csv": CUSTOMERS_CSV})
    assert client.get(f"/api/scans/{sid}").json()["stale"] is True
    sid2, _ = run_scan(client)
    assert client.get(f"/api/scans/{sid}").status_code == 404
    assert client.get(f"/api/scans/{sid2}").json()["stale"] is False


def test_bad_scan_requests(client):
    upload(client, {"a.csv": CUSTOMERS_CSV})
    assert client.post("/api/scans", headers=HEADERS, json={"formats": []}).status_code == 400
    assert client.post("/api/scans", headers=HEADERS, json={"formats": ["pdf"]}).status_code == 400
    assert client.post("/api/scans", headers=HEADERS, json={"unknown": 1}).status_code == 400


def test_format_not_generated(client):
    upload(client, {"a.csv": CUSTOMERS_CSV})
    sid, _ = run_scan(client, formats=["json"])
    assert client.get(f"/api/scans/{sid}/export/json").status_code == 200
    r = client.get(f"/api/scans/{sid}/export/csv")
    assert r.status_code == 409 and r.json()["error"]["code"] == "format_not_generated"
    assert client.get(f"/api/scans/{sid}/export/pdf").status_code == 400


def test_cancel_on_finished_scan_is_harmless(client):
    upload(client, {"a.csv": CUSTOMERS_CSV})
    sid, _ = run_scan(client)
    r = client.post(f"/api/scans/{sid}/cancel", headers=HEADERS)
    assert r.status_code == 200 and r.json()["state"] == "succeeded"


# ------------------------------------------------------------------ findings on the bundled data
def test_scan_matches_direct_engine_results(loaded):
    client, sid = loaded
    registry = load_class_registry(ROOT / "config" / "classes.yaml")
    ruleset = load_rule_set(ROOT / "config" / "rules.yaml", registry)
    base = ROOT / "data" / "synthetic"
    expected = set()
    for p in discover_sources(base):
        key = source_key_for(p, base)
        for f in detect_candidates(profile_source(p, base), ruleset, source_key=key):
            expected.add(f.identity)
    got = set()
    page = 1
    while True:
        data = client.get(f"/api/scans/{sid}/findings", params={"page": page, "page_size": 200}).json()
        got |= {(i["source_key"], i["table"], i["column"], i["class_id"]) for i in data["items"]}
        if page * 200 >= data["total"]:
            break
        page += 1
    assert got == expected and len(got) > 100


def test_findings_filter_sort_paginate(loaded):
    client, sid = loaded
    allf = client.get(f"/api/scans/{sid}/findings", params={"page_size": 200}).json()
    total = allf["total"]
    by_class = client.get(f"/api/scans/{sid}/findings", params={"class": "CONTACT_INFORMATION", "page_size": 200}).json()
    assert 0 < by_class["total"] < total and {i["class_id"] for i in by_class["items"]} == {"CONTACT_INFORMATION"}
    both = client.get(f"/api/scans/{sid}/findings", params=[("class", "CONTACT_INFORMATION"), ("class", "LOCATION_ADDRESS"), ("page_size", 200)]).json()
    assert both["total"] > by_class["total"]
    p1 = client.get(f"/api/scans/{sid}/findings", params={"page_size": 10, "page": 1}).json()
    p2 = client.get(f"/api/scans/{sid}/findings", params={"page_size": 10, "page": 2}).json()
    assert p1["total"] == p2["total"] == total and not {i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]}
    asc = client.get(f"/api/scans/{sid}/findings", params={"sort": "column", "page_size": 200}).json()["items"]
    desc = client.get(f"/api/scans/{sid}/findings", params={"sort": "column", "order": "desc", "page_size": 200}).json()["items"]
    cols = [i["column"].casefold() for i in asc]
    assert cols == sorted(cols) and [i["column"].casefold() for i in desc] == sorted(cols, reverse=True)
    q = client.get(f"/api/scans/{sid}/findings", params={"q": "EMAIL", "page_size": 200}).json()
    assert q["total"] > 0 and all("email" in (i["source_key"] + i["table"] + i["column"]).lower() for i in q["items"])
    amb = client.get(f"/api/scans/{sid}/findings", params={"ambiguous": "true", "page_size": 200}).json()
    assert amb["total"] > 0 and all(i["ambiguous"] and i["other_class_ids"] for i in amb["items"])
    gap = client.get(f"/api/scans/{sid}/findings", params={"metadata_status": "missing_purpose", "page_size": 200}).json()
    assert gap["total"] > 0 and {i["metadata_status"] for i in gap["items"]} == {"missing_purpose"}


@pytest.mark.parametrize("params", [
    {"class": "NOPE"}, {"sort": "banana"}, {"order": "up"}, {"page": "0"}, {"page": "x"},
    {"page_size": "201"}, {"ambiguous": "maybe"}, {"metadata_status": "x"}, {"review_status": "x"},
    {"surprise": "1"},
])
def test_bad_finding_params(loaded, params):
    client, sid = loaded
    r = client.get(f"/api/scans/{sid}/findings", params=params)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_summary_counts_agree_with_list(loaded):
    client, sid = loaded
    s = client.get(f"/api/scans/{sid}/summary").json()
    total = client.get(f"/api/scans/{sid}/findings").json()["total"]
    assert s["total_findings"] == total == sum(s["by_class"].values()) == sum(s["by_source"].values())
    assert s["metadata"]["flagged_columns"] == sum(s["metadata"]["by_status"].values())
    assert sum(s["review_counts"].values()) == total


def test_finding_detail_explanations_agree_with_detector(loaded):
    client, sid = loaded
    items = client.get(f"/api/scans/{sid}/findings", params={"page_size": 200}).json()["items"]
    assert items
    for item in items:
        d = client.get(f"/api/scans/{sid}/findings/{item['id']}").json()
        assert d["explanation"]["kind"] == "matched_pattern", item
        assert {m["rule_id"] for m in d["explanation"]["matches"]} == set(d["rule_ids"])
        assert [r["id"] for r in d["rules"]] == d["rule_ids"]
        assert "declaration" not in d and d["column_profile"] is not None
    assert client.get(f"/api/scans/{sid}/findings/0000000000000000").status_code == 404


def test_declarations_are_only_present_when_requested(client):
    client.post("/api/workspace/bundled", headers=HEADERS)
    sid, _ = run_scan(client, include_declarations=True)
    item = client.get(f"/api/scans/{sid}/findings", params={"metadata_status": "complete"}).json()["items"][0]
    d = client.get(f"/api/scans/{sid}/findings/{item['id']}").json()
    assert d["declaration"]["purpose"] and d["declaration"]["retention"]
    assert client.get(f"/api/scans/{sid}/metadata").json()["declarations_included"] is True


def test_metadata_endpoint_shapes(loaded):
    client, sid = loaded
    m = client.get(f"/api/scans/{sid}/metadata").json()
    assert m["available"] is True and m["declarations_included"] is False
    assert set(m["summary"]["by_status"]) == set(contract.METADATA_STATUSES)
    assert len(m["linked_fields"]) == m["summary"]["flagged_columns"]
    assert all("purpose" not in row for row in m["linked_fields"])


def test_sources_endpoint_counts(loaded):
    client, sid = loaded
    data = client.get(f"/api/scans/{sid}/sources").json()["sources"]
    assert {s["source_key"] for s in data} >= {"app.db", "crm/customers.csv", "crm/archive/customers.csv"}
    total = client.get(f"/api/scans/{sid}/summary").json()["total_findings"]
    assert sum(s["finding_count"] for s in data) == total
    for s in data:
        assert s["finding_count"] == sum(c["finding_count"] for t in s["tables"] for c in t["columns"])


def test_delimiter_warning(client):
    upload(client, {"semi.csv": b"customer_id;email_address\n1;a\n"})
    sid, _ = run_scan(client)
    src = client.get(f"/api/scans/{sid}/sources").json()["sources"][0]
    assert src["warnings"] == ["possible_delimiter_mismatch"]


# ------------------------------------------------------------------ reviews
def test_review_annotations_are_separate_from_detection(loaded):
    client, sid = loaded
    before = client.get(f"/api/scans/{sid}/export/json").text
    items = client.get(f"/api/scans/{sid}/findings", params={"page_size": 5}).json()["items"]
    fid = items[0]["id"]
    r = client.put(f"/api/scans/{sid}/findings/{fid}/review", headers=HEADERS, json={"status": "not_personal_data"})
    assert r.json()["review_status"] == "not_personal_data"
    assert client.put(f"/api/scans/{sid}/findings/{fid}/review", headers=HEADERS, json={"status": "bogus"}).status_code == 400
    assert client.put(f"/api/scans/{sid}/findings/ffffffffffffffff/review", headers=HEADERS, json={"status": "confirmed"}).status_code == 404
    total = client.get(f"/api/scans/{sid}/findings").json()["total"]
    assert client.get(f"/api/scans/{sid}/summary").json()["total_findings"] == total  # still counted
    flt = client.get(f"/api/scans/{sid}/findings", params={"review_status": "not_personal_data"}).json()
    assert flt["total"] == 1 and flt["items"][0]["id"] == fid
    assert json.loads(client.get(f"/api/scans/{sid}/export/json").text).keys() == json.loads(before).keys()
    assert "not_personal_data" not in client.get(f"/api/scans/{sid}/export/json").text
    assert "not_personal_data" not in client.get(f"/api/scans/{sid}/export/csv").text
    exp = client.get("/api/workspace/reviews/export")
    assert exp.headers["content-disposition"].endswith('fieldledger_review_annotations.json"')
    doc = exp.json()
    assert doc["count"] == 1 and doc["annotations"][0]["current"] is True and "not detection output" in doc["notice"]
    sid2, _ = run_scan(client)  # survives re-scan
    assert client.get(f"/api/scans/{sid2}/findings", params={"review_status": "not_personal_data"}).json()["total"] == 1
    client.put(f"/api/scans/{sid2}/findings/{fid}/review", headers=HEADERS, json={"status": "unreviewed"})
    assert client.get("/api/workspace").json()["review_count"] == 0
    client.put(f"/api/scans/{sid2}/findings/{fid}/review", headers=HEADERS, json={"status": "confirmed"})
    assert client.delete("/api/workspace/reviews", headers=HEADERS).json() == {"removed": 1}


# ------------------------------------------------------------------ exports
def test_csv_export_is_formula_safe_and_json_is_authoritative(client):
    evil = b"=evil_id,+phone_number,@email_addr,-dob,full_name\n1,2,3,4,5\n"
    upload(client, {"=evil.csv": evil})
    sid, status = run_scan(client)
    assert status["state"] == "succeeded"
    text = client.get(f"/api/scans/{sid}/export/csv").text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows and all(not r["source_key"].startswith("=") for r in rows)
    assert any(r["column"].startswith("'+phone") for r in rows) and any(r["column"].startswith("'@email") for r in rows)
    raw = json.loads(client.get(f"/api/scans/{sid}/export/json").text)
    assert any(f["column"] == "+phone_number" for f in raw["findings"])  # JSON is unmodified


def test_csv_export_identical_to_cli_when_nothing_to_neutralise(loaded, tmp_path):
    client, sid = loaded
    from fieldledger.cli import main as cli_main
    out = tmp_path / "cli"
    code = cli_main(["scan", "--input", str(ROOT / "data/synthetic"), "--metadata",
                     str(ROOT / "data/synthetic/metadata/purpose_retention.yaml"), "--out", str(out),
                     "--classes", str(ROOT / "config/classes.yaml"), "--rules", str(ROOT / "config/rules.yaml"),
                     "--formats", "csv"])
    assert code == 0
    assert client.get(f"/api/scans/{sid}/export/csv").content == (out / "fieldledger_inventory.csv").read_bytes()


# ------------------------------------------------------------------ evaluation
def test_evaluation_matches_direct_engine_call(client):
    eid = client.post("/api/evaluations", headers=HEADERS, json={"dataset": "bundled_synthetic"}).json()["evaluation_id"]
    e = _poll_eval(client, eid)
    assert e["state"] == "succeeded", e
    res = e["result"]
    registry = load_class_registry(ROOT / "config" / "classes.yaml")
    ruleset = load_rule_set(ROOT / "config" / "rules.yaml", registry)
    base = ROOT / "data" / "synthetic"
    profiles, findings = {}, []
    for p in discover_sources(base):
        key = source_key_for(p, base)
        profiles[key] = profile_source(p, base)
        findings += detect_candidates(profiles[key], ruleset, source_key=key)
    direct = evaluate(findings, load_labels(base / "ground_truth/labels.yaml", registry), profiles,
                      registry=registry, ruleset=ruleset).to_dict()
    assert res["classes"] == direct["classes"] and res["scope"] == "synthetic_benchmark"
    assert res["micro"]["derived"] is True
    assert res["micro"]["tp"] == sum(c["true_positives"] for c in direct["classes"])
    assert "synthetic" in res["scope_notice"].lower()
    assert client.post("/api/evaluations", headers=HEADERS, json={"dataset": "uploaded"}).status_code == 400
    assert client.get("/api/evaluations/unknown").status_code == 404


def test_benchmark_unavailable(tmp_path):
    from fastapi.testclient import TestClient
    import shutil
    from webtestlib import BASE_URL
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "config", root / "config")
    from fieldledger.web.app import AppConfig, create_app
    app = create_app(AppConfig(root=root, temp_base=tmp_path, static_dir=tmp_path / "none"))
    with TestClient(app, base_url=BASE_URL) as c:
        assert c.get("/api/config").json()["benchmark_available"] is False
        r = c.post("/api/evaluations", headers=HEADERS, json={})
        assert r.status_code == 503 and r.json()["error"]["code"] == "benchmark_unavailable"
        assert c.post("/api/workspace/bundled", headers=HEADERS).status_code == 503


def test_bundled_requires_empty_workspace(client):
    upload(client, {"a.csv": CUSTOMERS_CSV})
    assert client.post("/api/workspace/bundled", headers=HEADERS).status_code == 400


def test_additive_fields_latest_scan_id_and_class_metadata(loaded):
    client, sid = loaded
    assert client.get("/api/workspace").json()["latest_scan_id"] == sid
    s = client.get(f"/api/scans/{sid}/summary").json()
    assert set(s["by_class_metadata"]) == set(contract.CLASS_IDS)
    assert sum(sum(v.values()) for v in s["by_class_metadata"].values()) == s["total_findings"]
    for cid, n in s["by_class"].items():
        assert sum(s["by_class_metadata"][cid].values()) == n
