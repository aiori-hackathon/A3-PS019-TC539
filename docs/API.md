# FieldLedger API

The full specification is [`api-contract.md`](api-contract.md) (API v1, machine-checked by `tests/web/test_contract.py`).
A running server also publishes its OpenAPI schema at `http://127.0.0.1:8765/api/openapi.json`.

## Additions to the contract (additive, still API v1)

| Addition | Where |
|---|---|
| `POST /api/workspace/bundled` loads the bundled synthetic data and metadata into an empty workspace | contract section 5, endpoint table |
| `latest_scan_id` in `GET /api/workspace` so a reloaded page finds its results | contract section 5 |
| `by_class_metadata` in `GET /api/scans/{id}/summary` | contract section 6 |
| `suppressed_rules` in a finding detail (rules that matched but were suppressed by an exclusion, from the detector's own `explain_column`) | implementation, not in the contract |
| `exclude_patterns` on each rule in `GET /api/config` | implementation, not in the contract |

## Calling the API from PowerShell

State-changing requests must send `X-FieldLedger-Client: ui`.

```powershell
$h = @{ "X-FieldLedger-Client" = "ui" }
$b = "http://127.0.0.1:8765"
Invoke-RestMethod "$b/api/health"
Invoke-RestMethod -Method Post -Headers $h "$b/api/workspace/bundled" | Out-Null
$scan = Invoke-RestMethod -Method Post -Headers $h -ContentType "application/json" -Body '{"formats":["json","csv","md"]}' "$b/api/scans"
Start-Sleep 2
Invoke-RestMethod "$b/api/scans/$($scan.scan_id)"
Invoke-RestMethod "$b/api/scans/$($scan.scan_id)/summary"
Invoke-RestMethod "$b/api/scans/$($scan.scan_id)/findings?class=CONTACT_INFORMATION&sort=column&page_size=10"
Invoke-WebRequest "$b/api/scans/$($scan.scan_id)/export/csv" -OutFile inventory.csv
```

## Endpoint summary

See the table in `api-contract.md` section 4 (24 endpoints). Errors always use
`{"error": {"code": "...", "message": "..."}}` with the codes in section 2.
