# FieldLedger web API contract (v1)

Status: **draft for review** (UI/API task, branch `ui/frontend`). Nothing in this document is
implemented yet; it is the specification the service layer, the frontend types and the tests are
written against. `src/fieldledger/web/contract.py` holds the same names as constants, and
`tests/web/test_contract.py` fails if this document, those constants or the real backend drift
apart. Blocks between `<!-- contract:NAME:start -->` and `<!-- contract:NAME:end -->` markers are
machine-checked; edit them together with `contract.py`.

## 1. Scope and principles

The web layer is a **thin adapter** over the existing public Python functions
(`discover_sources`, `profile_source`, `detect_candidates`, `load_metadata`, `link_metadata`,
`write_reports`, `load_labels`, `evaluate`). It does not reimplement detection, does not edit
`detector.py`, `rules.py`, `evaluation.py` or `report.py`, and leaves the CLI and its output
unchanged.

- Findings are **technical candidates** from column-name rule matching. Every response that carries
  findings includes the backend's `EVIDENCE_NOTICE`. Nothing here is a legal determination.
- Rules are **provisional**; the five class IDs are the confirmed taxonomy and are never renamed.
- **No raw data leaves the server.** No endpoint returns uploaded file bytes, cell values, or a
  preview. Responses carry structure (names, types, counts) and findings only.
- **Nothing is invented.** Progress is `sources_done` of `sources_total`. There is no percentage.
  Accuracy figures exist only after a real evaluation run. Review states exist only because a
  person set them. Explanations exist only where the parity rule in section 9 holds.
- Server logs contain counts, state transitions and error codes. They never contain file names,
  `source_key` values, column names, paths or declaration text.

## 2. Conventions

- Base path `/api`. JSON, UTF-8. Timestamps are ISO-8601 UTC with seconds.
- The server binds to `127.0.0.1` by default. A non-loopback `--host` requires the explicit
  `--allow-network` flag and prints a warning. There is no authentication; this is a local tool.
- Defence in depth (additional to safe file handling, not a substitute for it):
  - Requests whose `Host` header is not loopback or the configured host get `403 forbidden`.
  - Every `POST`, `PUT` and `DELETE` must send `X-FieldLedger-Client: ui` and, if an `Origin`
    header is present, it must match the server origin. CORS is not enabled. In development the
    Vite proxy makes the browser see a single origin.
- Errors always use `{"error": {"code": "<code>", "message": "<fixed or FieldLedgerError text>"}}`.
  Messages never contain paths, cell values or header text. Codes and HTTP status:

<!-- contract:error-codes:start -->
| Code | HTTP | Meaning |
|---|---|---|
| `bad_request` | 400 | Malformed or invalid parameters or body |
| `forbidden` | 403 | Host or Origin check failed, or the client header is missing |
| `unsupported_type` | 415 | File extension is not one of the supported suffixes |
| `invalid_name` | 400 | Unsafe or unusable file name or relative path |
| `duplicate_name` | 409 | A file with the same name already exists (compared case-insensitively) |
| `file_too_large` | 413 | A single file exceeds its size limit |
| `workspace_too_large` | 413 | The workspace byte limit would be exceeded |
| `too_many_files` | 413 | The file-count limit would be exceeded |
| `malformed_input` | 422 | An uploaded file is unusable for a reason found at upload time |
| `invalid_metadata` | 422 | The metadata YAML failed `load_metadata` validation |
| `no_workspace` | 404 | There is no active workspace (never created, cleared, or expired) |
| `not_found` | 404 | Unknown scan, evaluation, finding or file |
| `scan_in_progress` | 409 | A scan is already queued or running |
| `scan_not_ready` | 409 | Results were requested before the scan succeeded |
| `format_not_generated` | 409 | That export format was not requested for this scan |
| `benchmark_unavailable` | 503 | The bundled synthetic benchmark files were not found |
| `internal_error` | 500 | Unexpected failure. The body is generic; details go to the server log without data |
<!-- contract:error-codes:end -->

- **Finding ID**: the first 16 hex characters of SHA-256 over
  `source_key NUL table NUL column NUL class_id` (UTF-8). It is deterministic, so it survives
  re-scans and is the key for review annotations. The service must fail the scan if two findings
  in one scan ever share an ID. `contract.finding_id` is the reference implementation.
- One list row is one **finding** (one column and one class). An ambiguous column appears once per
  matched class. This matches the backend's 149-finding report and the CSV inventory.

## 3. Limits and configuration

Ingestion limits are the backend's own `IngestionLimits` defaults. They are enforced by the real
profilers at scan time. The web layer enforces the file and workspace limits below *while
streaming the upload* and aborts and deletes partial files when a limit is hit. The browser checks
the same numbers (from `GET /api/config`) for fast feedback; the server is the only authority.

<!-- contract:ingestion-limits:start -->
- `max_file_bytes` = 52428800 (50 MiB, CSV)
- `max_rows` = 100000
- `max_columns` = 1000
- `max_line_chars` = 262144
- `max_db_bytes` = 268435456 (256 MiB, SQLite)
- `max_tables` = 1000
- `sqlite_timeout_seconds` = 30.0
<!-- contract:ingestion-limits:end -->

<!-- contract:workspace-limits:start -->
- `max_files` = 100
- `max_workspace_bytes` = 536870912 (512 MiB)
- `max_files_per_request` = 50
- `max_metadata_bytes` = 1000000 (the strict YAML loader's `MAX_CONFIG_BYTES`)
- `idle_timeout_seconds` = 3600
- `scan_timeout_seconds` = 900
<!-- contract:workspace-limits:end -->

The workspace limits are configurable at startup through flags or environment variables
(`--max-files` / `FIELDLEDGER_MAX_FILES`, `--max-workspace-mb` / `FIELDLEDGER_MAX_WORKSPACE_MB`,
`--idle-timeout-seconds` / `FIELDLEDGER_IDLE_TIMEOUT_SECONDS`, `--scan-timeout-seconds` /
`FIELDLEDGER_SCAN_TIMEOUT_SECONDS`). Values must be positive integers. The idle timeout can be
lowered but **never raised above 3600 seconds**. `scan_timeout_seconds` is checked between sources;
a single source is bounded by the per-source limits above, including the 30 s SQLite budget.

Supported suffixes are the backend's `SUPPORTED_SUFFIXES`:

<!-- contract:suffixes:start -->
- `.csv`
- `.db`
- `.db3`
- `.sqlite`
- `.sqlite3`
<!-- contract:suffixes:end -->

Report formats are the backend's three:

<!-- contract:report-formats:start -->
- `json`
- `csv`
- `md`
<!-- contract:report-formats:end -->

Class IDs are the five official IDs, taken from `config/classes.yaml` and never altered:

<!-- contract:class-ids:start -->
- `PERSONAL_IDENTITY`
- `CONTACT_INFORMATION`
- `GOVERNMENT_IDENTIFIERS`
- `FINANCIAL_INFORMATION`
- `LOCATION_ADDRESS`
<!-- contract:class-ids:end -->

The server reads `config/classes.yaml` and `config/rules.yaml` from `./config` relative to the
working directory (as the CLI does) and fails at startup if they are missing or invalid. The
`*.provisional.yaml` files are not selectable from the UI, because offering them would replace
the confirmed taxonomy. The taxonomy and rules are displayed read-only.

## 4. Endpoint index

<!-- contract:endpoints:start -->
| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness and API version |
| `GET` | `/api/config` | Taxonomy, rules, limits, notices, benchmark availability |
| `GET` | `/api/workspace` | Workspace state (names, sizes, counts; never contents) |
| `POST` | `/api/workspace/files` | Upload data files (multipart) |
| `DELETE` | `/api/workspace/files` | Remove one file (`?source_key=`) |
| `POST` | `/api/workspace/metadata` | Upload the purpose/retention metadata YAML |
| `DELETE` | `/api/workspace/metadata` | Remove the metadata file |
| `DELETE` | `/api/workspace` | Clear the whole workspace |
| `POST` | `/api/scans` | Start a scan (202, background job) |
| `GET` | `/api/scans/{scan_id}` | Scan status |
| `POST` | `/api/scans/{scan_id}/cancel` | Request cooperative cancellation |
| `GET` | `/api/scans/{scan_id}/summary` | Aggregate counts |
| `GET` | `/api/scans/{scan_id}/sources` | Source, table and column profiles |
| `GET` | `/api/scans/{scan_id}/findings` | Filtered, sorted, paginated findings |
| `GET` | `/api/scans/{scan_id}/findings/{finding_id}` | One finding with full detail |
| `PUT` | `/api/scans/{scan_id}/findings/{finding_id}/review` | Set a local review annotation |
| `GET` | `/api/scans/{scan_id}/metadata` | Purpose/retention gap report |
| `GET` | `/api/scans/{scan_id}/export/{format}` | Download a generated report |
| `GET` | `/api/workspace/reviews/export` | Download local review annotations |
| `DELETE` | `/api/workspace/reviews` | Reset all local review annotations |
| `POST` | `/api/evaluations` | Run the synthetic benchmark (202) |
| `GET` | `/api/evaluations/{evaluation_id}` | Benchmark state and result |
<!-- contract:endpoints:end -->

File removal uses a query parameter rather than a path segment because `source_key` contains
slashes, and servers and proxies decode encoded slashes inconsistently.

## 5. Config and workspace

**`GET /api/config`** returns `taxonomy_status`, `rules_status`, `classes[{id,label,description}]`,
`rules[{id,class_id,match,patterns[]}]` (each pattern is its normalised tokens joined by a space),
`limits{...}` (both limit blocks above), `supported_suffixes[]`, `report_formats[]`,
`metadata_origins[]` (`team_authored_synthetic`, `organizer_provided`, `user_provided`),
`notices{evidence,heuristic,gap}`, `csv_notes[]` (UTF-8 only, comma delimiter only) and
`benchmark_available` (boolean).

**`GET /api/workspace`** returns `{"exists": false}` or
`{exists, revision, files[{source_key,type,size_bytes}], metadata: null | {entries, origin},
review_count, expires_at, idle_timeout_seconds}`. `revision` increases on every change to files or
metadata. Reading this endpoint counts as activity and resets the idle timer.

**`POST /api/workspace/files`**: multipart with repeated `files` parts and an equally long, ordered
list of `relative_paths` form fields. Result: `{results[{name, accepted, code?, message?}],
revision}`. Rejected files are never stored; accepted ones are kept even if others in the same
request fail. A folder drop keeps its structure through `relative_paths`.

Name and path rules, applied to every `relative_paths` value (a stricter superset of the
backend's `validate_source_key`): non-empty, at most 1024 characters, forward slashes only, no
leading slash, no drive letter, no `.` or `..` or empty segment, no control characters, none of
`< > : " | ? *`, no segment ending in a dot or space, no Windows reserved device name in any
segment (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, with or without an extension),
and a supported suffix. Names are compared case-insensitively for duplicates. The target path is
then resolved with the backend's `resolve_within`, and anything escaping the workspace root is
rejected. Uploaded files are written by the server under server-chosen names; they are never
symlinks and are never executed.

**`DELETE /api/workspace/files?source_key=...`** removes one file. **`POST
/api/workspace/metadata`** takes one multipart YAML file, validated by the real `load_metadata`
(1,000,000-byte cap, strict YAML, a required `metadata_origin`, entries keyed by `source_key`,
`table`, `column`). It returns `{entries, origin}` or `invalid_metadata` with the backend's
safe message. Metadata `source_key` values must equal the uploaded relative paths exactly, and
entries that match nothing are reported by the metadata endpoint, not hidden.

**`DELETE /api/workspace`** removes the workspace, scans, exports, review annotations and the
metadata file. See section 10 for what "removed" does and does not mean.

## 6. Scans

**`POST /api/scans`** with `{"include_declarations": false, "formats": ["json","csv","md"]}`.
`formats` must be a non-empty subset of the report formats. `include_declarations: true` requires
metadata to be present (otherwise `bad_request`) and exposes purpose and retention text in
responses and exports; the UI shows a privacy warning first. Returns `202 {"scan_id"}`, or
`scan_in_progress`, or `no_workspace`. Only the latest scan per workspace is kept; earlier IDs
return `not_found`.

The service composes the public functions itself rather than calling the CLI's private
`_run_scan`. The reason is that `_run_scan` drops sources that fail profiling with only a log
warning (verified: a directory with two CSVs, one malformed, reports `sources_scanned: 1` and
exits 0). The web service records every failure.

**`GET /api/scans/{id}`** returns:

```json
{
  "scan_id": "...",
  "state": "running",
  "stage": "profiling",
  "sources_total": 6,
  "sources_done": 2,
  "source_errors": [{"source_key": "bad.csv", "code": "malformed_csv", "message": "..."}],
  "workspace_revision": 4,
  "stale": false,
  "started_at": "...",
  "finished_at": null,
  "error": null
}
```

<!-- contract:scan-states:start -->
- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`
<!-- contract:scan-states:end -->

<!-- contract:scan-stages:start -->
- `discovering`
- `profiling`
- `detecting`
- `linking_metadata`
- `writing_reports`
<!-- contract:scan-stages:end -->

`sources_total` is known once discovery finishes (before that it is `null`), and `sources_done`
counts sources that have been profiled or recorded as failed. Those are real counts; the UI shows
"2 of 6 sources" and an indeterminate indicator, never a percentage. `stale` becomes `true` when
the workspace `revision` has moved past the one the scan used; the UI then offers a re-scan.
`failed` is for scan-level failures only. A scan in which some sources failed still `succeeded`,
with those sources listed in `source_errors`, and the UI shows them prominently.

Each source error maps a backend exception class to a stable code:

<!-- contract:source-error-codes:start -->
- `unsupported_type`
- `input_access`
- `input_too_large`
- `malformed_csv`
- `invalid_database`
- `database_timeout`
- `path_access`
- `ingest_failed`
<!-- contract:source-error-codes:end -->

**`POST /api/scans/{id}/cancel`** sets a cancel flag and returns the current status. Cancellation
is cooperative and takes effect **between sources**; a source already being profiled completes or
hits its own limit first. The UI says so.

Results endpoints return `scan_not_ready` until `state` is `succeeded`.

**`GET /api/scans/{id}/summary`**: `{generated_at, sources_scanned, sources_failed,
total_findings, by_class{class_id: n}, by_source{source_key: n}, ambiguous_columns, metadata: null
| {flagged_columns, ambiguous_columns, by_status{}, metadata_entries, orphan_entries,
unmatched_entries}, review_counts{}, taxonomy_status, rules_status, notices}`. All numbers come
from the backend objects (findings, `GapReport.summary`); none are computed from guesses.

**`GET /api/scans/{id}/sources`**: `{sources[{source_key, source_type, type_basis, finding_count,
warnings[], tables[{name, row_count, columns[{name, position, inferred_type, declared_type,
non_null_count, null_count, finding_count}]}]}]}`, built from `SourceProfile.to_dict()`. These are
structure and counts only; no values.

`warnings` currently has one code, `possible_delimiter_mismatch`. A CSV with a single column whose
name contains `;`, `|` or a tab is flagged because the CSV profiler accepts only commas and does
not error on other delimiters (verified: `a;b` is profiled as one column named `a;b`).

<!-- contract:source-warning-codes:start -->
- `possible_delimiter_mismatch`
<!-- contract:source-warning-codes:end -->

## 7. Findings

**`GET /api/scans/{id}/findings`** query parameters:

<!-- contract:finding-sort-keys:start -->
- `source`
- `table`
- `column`
- `class`
- `position`
<!-- contract:finding-sort-keys:end -->

`q` (case-insensitive substring over source, table and column), `class` (repeatable),
`source` (repeatable, exact `source_key`), `metadata_status` (repeatable), `review_status`
(repeatable), `ambiguous` (`true` or `false`), `sort` (default `source`), `order` (`asc` or
`desc`, default `asc`), `page` (from 1), `page_size` (default 50, maximum 200). Sorting is
deterministic: ties are broken by source, table, position, then class. The response is
`{items[], total, page, page_size}` and `total` is the *filtered* count, so the table, the
pagination and the filter chips always agree. Unknown parameter values are `bad_request`.

A **list item** (this example is the real `pan_card_number` finding from the synthetic data; its
ambiguity comes from the token `card`, which matches a financial rule, alongside `RULE_GI_PAN`):

```json
{
  "id": "31f2b291402f76ec",
  "source_key": "app.db",
  "table": "kyc_records",
  "column": "pan_card_number",
  "position": 23,
  "class_id": "FINANCIAL_INFORMATION",
  "class_label": "Financial information",
  "rule_ids": ["RULE_FI_CARD"],
  "evidence_kind": "column_name_match",
  "ambiguous": true,
  "other_class_ids": ["GOVERNMENT_IDENTIFIERS"],
  "metadata_status": "complete",
  "has_purpose": true,
  "has_retention": true,
  "review_status": "unreviewed"
}
```

`metadata_status`, `has_purpose` and `has_retention` are `null` when no metadata was supplied.

<!-- contract:metadata-statuses:start -->
- `complete`
- `missing_purpose`
- `missing_retention`
- `missing_both`
- `no_entry`
<!-- contract:metadata-statuses:end -->

**Detail** (`.../findings/{finding_id}`) adds `rules[{id,class_id,match,patterns[]}]` (from the
loaded rule set), `column_tokens[]` (from `tokenize_name`), `explanation`, `column_profile
{inferred_type, declared_type, non_null_count, null_count, table_row_count}`, `siblings[{id,
class_id}]`, `declaration{purpose, retention}` (present only if `include_declarations` was true),
and `notice` (the evidence notice). The `ambiguous`, `other_class_ids` and `siblings` fields come
from the backend's own grouping of findings by column.

## 8. Metadata coverage

**`GET /api/scans/{id}/metadata`** returns `{"available": false}` when no metadata was supplied.
Otherwise it returns the backend gap report: `metadata_origin`, `summary`, `linked_fields[]`,
`orphan_entries[]`, `unmatched_entries[]`, `declarations_included`, and the gap notice. A missing
declaration is a review gap in the supplied metadata, not evidence of a violation, and a complete
one is not evidence of compliance. The UI repeats that. `orphan_entries` are metadata entries for
columns that no rule flagged (24 in the synthetic set, including columns the rules miss such as
`ph_no`); the UI labels them "declared in metadata but not flagged by any rule" and does not call
them errors.

## 9. Explanations (matched patterns)

The detector records only `rule_ids`. The API adds `explanation` as follows, without changing the
detector:

- `{"kind": "matched_pattern", "matches": [{"rule_id": "...", "pattern": "..."}]}` when, for
  **every** rule ID on the finding, the API can reproduce at least one matching pattern using the
  public `tokenize_name` and `Rule.patterns` with the detector's semantics (`token`: the pattern's
  tokens occur as a contiguous run in the column's tokens; `exact`: the token tuples are equal).
- `{"kind": "rule_patterns_only"}` otherwise. The UI then shows the rule and its configured
  patterns and says only that the rule matched. It never names a pattern as the cause.

<!-- contract:explanation-kinds:start -->
- `matched_pattern`
- `rule_patterns_only`
<!-- contract:explanation-kinds:end -->

A parity test runs this logic over every finding in the synthetic dataset and the labelled
fixtures and requires that the explanation agrees with the detector's output. If matching
semantics ever change in the detector, that test fails and the API falls back to
`rule_patterns_only` until it is updated. The long-term fix is for the backend owner to record
the matched patterns on `Finding` (see section 13).

## 10. Workspace lifecycle, storage and deletion

- The workspace is created implicitly by the first upload, in a new randomly named directory under
  the system temporary directory, with owner-only permissions where the operating system supports
  them. There is one workspace per server process.
- It holds uploaded files, the metadata file, generated reports, and `reviews.json`. It lives
  only there. Nothing is written to the repository, the `output/` directory or the browser's
  storage.
- Any request to `/api/workspace*` or `/api/scans*` refreshes the idle timer. After
  `idle_timeout_seconds` (at most 3600) a background sweep removes the directory. Cleanup also
  runs on normal server shutdown (FastAPI lifespan and `atexit`) and after any failed upload or
  failed scan for the files involved. A crash or power loss can leave a directory behind, and the
  server removes stale directories with its own prefix at the next start.
- Profiling a SQLite database can create `-wal` and `-shm` side files beside it, even though the
  main file is left byte-identical (verified for a WAL-mode database). Therefore removal deletes
  the **whole directory tree**, and the workspace byte count is measured over the whole tree.
- "Clear workspace" removes the files from the file system. The UI says "removed from this
  workspace". It does not claim secure deletion: the operating system, file system, backups and
  SSD wear-levelling can leave recoverable copies, and the server cannot guarantee otherwise.
- Uploaded bytes are never returned by any endpoint and are never logged.

SQLite access is by the backend's `profile_sqlite`: URI `mode=ro`, `PRAGMA query_only=ON`,
`trusted_schema=OFF`, extension loading disabled, a magic-header check, and a progress handler
enforcing the time budget. This was verified in the development clone: profiling the synthetic
`app.db` left its SHA-256 and modification time unchanged. A hostile file is further bounded by
the size, table and column limits, and by a single scan worker.

## 11. Review annotations (local, separate from detection)

<!-- contract:review-statuses:start -->
- `unreviewed`
- `confirmed`
- `not_personal_data`
- `needs_follow_up`
<!-- contract:review-statuses:end -->

**`PUT /api/scans/{id}/findings/{finding_id}/review`** with `{"status": "..."}`. The finding must
exist in that scan. Setting `unreviewed` removes the stored annotation.

- Annotations are **separate from detection results**. A finding marked `not_personal_data`
  remains in the findings list, in every count and in every official export, as a detection
  result with a separate review status. Filtering by review status is something the person does;
  nothing is hidden by default.
- They are stored only in `reviews.json` inside the temporary workspace, keyed by finding ID with
  the four identity fields. They survive a re-scan while the workspace exists. They are deleted
  with the workspace, on idle expiry and on shutdown.
- They are never written into `fieldledger_report.json`, the inventory CSV or the Markdown
  summary. They have their own download, **`GET /api/workspace/reviews/export`**, named
  `fieldledger_review_annotations.json`. It is labelled as local review annotations and states
  that they are not detection output. Each entry carries `current: true|false`, meaning whether a
  finding with that identity exists in the latest scan.
- **`DELETE /api/workspace/reviews`** resets all annotations. The UI asks for confirmation and
  shows the count first, and shows where they are stored (the temporary workspace).

## 12. Exports

`GET /api/scans/{id}/export/{format}` serves the files that the real `write_reports` produced
for this scan, as downloads (`Content-Disposition: attachment`, content types `application/json`,
`text/csv`, `text/markdown`). The UI offers download only. It never renders an export as HTML.

**CSV formula-injection mitigation (web export only).** The core CLI's CSV is unchanged. The
inventory CSV contains `source_key`, `table` and `column` values taken from uploaded file names,
CSV stems and headers. This was reproduced with the CLI: a file named `=evil.csv` with columns
`=email`, `+phone_number`, `@email_addr` and `-dob`, and a SQLite column named ` =email`, all
reach the CSV verbatim. The web export therefore post-processes the real CSV output: it parses
it, and for every cell whose first *significant* character is a spreadsheet formula trigger it
prepends a single quote (`'`). "Significant" skips leading characters that are whitespace
(Unicode `isspace`), control characters (category Cc) or invisible format characters (category
Cf, such as zero-width spaces, the BOM and bidirectional marks). Triggers are `=`, `+`, `-`, `@`
and their full-width forms (`＝ ＋ － ＠`). A cell that begins with any control character,
including tab, carriage return and line feed, is also prefixed. When no cell needs changing, the
output is byte-identical to the CLI's CSV (a test asserts this).

Limitations, stated in the UI next to the export action:

1. The quote becomes part of the cell text in many tools, so a few values differ from the
   original names. `fieldledger_report.json` is the authoritative, unmodified record.
2. It targets cells that begin with formula-trigger characters. It cannot guarantee the behaviour
   of every spreadsheet application and version.
3. It protects only this CSV. Other consumers of the data must apply their own handling.
4. The Markdown summary is not escaped (it is generated by the backend, and the UI does not
   render it as HTML). Treat it as plain text.
5. Preprocessing a CSV that has been through other tools is out of scope.

Proposed upstream fix, to coordinate with the backend owner: a shared `neutralise_cell` helper
and an opt-in `csv_safe` option on `write_reports` with a `--csv-safe` CLI flag, default off so
CLI output stays compatible. The web layer would then call it instead of post-processing.

## 13. Evaluation

**`POST /api/evaluations`** with `{"dataset": "bundled_synthetic"}` runs the real `evaluate`
against the repository's `data/synthetic`, `data/synthetic/ground_truth/labels.yaml` and
`config/`. It does not touch the workspace and never scores uploaded data. Returns
`202 {"evaluation_id"}` or `benchmark_unavailable` (for example when installed without the
repository's data). **`GET /api/evaluations/{id}`** returns `{state, result}`. The result is the
backend's evaluation JSON unchanged (`scope_notice`, `label_origin`, per-class counts, precision
and recall, ambiguous columns) plus `scope: "synthetic_benchmark"` and `micro{tp, fp, fn,
predicted, labelled_positive, precision, recall, derived: true}`. The micro figures are sums over
the per-class counts and are marked as derived, because the backend does not emit them. Expected
values from the checked-in synthetic set: TP 114, FP 35, FN 25, 149 predicted, 139 labelled
positive. They are shown only after a run, always with the synthetic-benchmark warning, and are
not described as real-world accuracy.

The backend reports counts only. It does not say which columns were missed or wrongly flagged,
so the UI does not list them.

## 14. Running on Windows and connecting the UI (planned, not yet run)

```powershell
# one-time, from the repository root
.\.venv\Scripts\python.exe -m pip install -e ".[web]"

# backend (binds to 127.0.0.1:8765; run from the repository root so ./config is found)
.\.venv\Scripts\python.exe -m fieldledger.web --port 8765

# frontend development (Vite serves the UI on 127.0.0.1:5173 and proxies /api to the backend)
cd frontend
npm install
npm run dev

# single-process, production-style run: build once, then the API serves the UI
npm run build          # writes to ..\src\fieldledger\web\static
cd ..
.\.venv\Scripts\python.exe -m fieldledger.web --port 8765    # open http://127.0.0.1:8765
```

The core CLI needs only `pip install -e ".[dev]"`. The `web` extra is optional, and
`fieldledger.web.contract` imports without it.

## 15. Backend observations for the detection-engine owner

These were found while preparing this contract. None is fixed by the UI task, and the web layer
works around each one.

1. `report._write_csv` writes `source_key`, table and column verbatim, so formula-leading names
   reach the CSV (section 12). Proposed: an opt-in `csv_safe` option.
2. `cli._cmd_evaluate` does not check for a missing `--input`; it raises `AttributeError`
   (verified: a traceback instead of the `error:` line `scan` prints). A missing `--metadata`
   file for `scan` prints a second, confusing error before exiting 1.
3. `cli._run_scan` skips sources that fail profiling with a log warning only, so
   `sources_scanned` undercounts and the report carries no failure list (section 6).
4. `Finding` records `rule_ids` but not the matched patterns (section 9).
5. The evaluation JSON has per-class counts only: no micro averages, no identities of missed or
   wrongly flagged columns (section 13).
6. The CSV profiler has no delimiter detection, so a semicolon-delimited file is read as one
   column without an error (section 6).
7. `profile_sqlite` can leave `-wal` and `-shm` files beside a WAL-mode database (section 10).

## 16. Change policy

`API_VERSION` is `1`. Additive changes (new optional fields, new endpoints) keep the version.
Renaming or removing a field, changing a status or a code, or changing semantics increases it and
needs the contract, `contract.py`, the tests and the frontend types updated in the same commit.
