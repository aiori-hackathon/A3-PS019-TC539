# FieldLedger web UI: delivery report

Everything below was checked by running it, except where marked **not verified**.

## Completed (verified on Linux, Python 3.12.3, Node 22)

- **Backend** (`src/fieldledger/web/`): FastAPI service calling the existing public engine functions (`profile_source`, `detect_candidates`, `explain_column`, `load_metadata`, `link_metadata`, `write_reports`, `load_labels`, `evaluate`). No detection logic was re-implemented, in Python or JavaScript. 25 endpoints (the 24 in the contract plus `POST /api/workspace/bundled`).
- **Frontend** (`frontend/`): React + TypeScript + Vite, eight pages: Overview, Upload, Sources, Scan, Findings (search, class/source/metadata/review filters, sort, pagination, detail drawer), Metadata coverage, Evaluation, Help. Light theme, navy text, teal accent, local fonts, responsive (no overflow at 390 px).
- **Real data paths only**: upload (files, folders, metadata YAML), scanning with progress and cancel, per-source error reporting, review annotations (saved server-side, exportable, kept separate from reports), metadata gap analysis, evaluation, JSON/CSV/Markdown exports. No mock data anywhere; empty states appear until a real run.
- **Security**: loopback-only by default (non-loopback needs `--allow-network`), Host allow-list, required client header and Origin check on state-changing requests, upload name/type/size/signature validation, path-traversal protection (Windows reserved names included), random owner-only temp workspace with idle expiry and cleanup on shutdown, generic error bodies, no file/column names in logs (uvicorn access log disabled), CSV formula neutralisation, CSP and security headers.
- **Tests**: 1035 Python (911 baseline + 124 new), 30 frontend, typecheck, production build, plus a scripted headless-browser run through all pages and the file-picker upload. Details in `TEST_RESULTS.md`.
- **Preserved**: no edit to `detector.py`, `rules.py`, `config/`, taxonomy, datasets, ground truth, or any existing test. Modified tracked files: `docs/api-contract.md`, `src/fieldledger/web/contract.py` (one additive endpoint, kept consistent by the contract test) and `README.md` (appended section).

Evaluation on the bundled synthetic benchmark, computed live: TP 138, FP 4, FN 1 (precision 97.2%, recall 99.3%). The older 114/35/25 figures are stale and not used. These are results on the team's own synthetic data, not general accuracy.

## Not verified

- **Windows 10/11, Python 3.13.5, Node.js 24**: none of the runs were on the target environment. `start_fieldledger.bat` and `run_tests_windows.bat` were written but never executed. The earlier Windows baseline (908 passed, 3 skipped) was reported to me, not reproduced.
- **Commit `7654f1f`**: the ZIP contained no `.git`. The patch is generated against a baseline commit I made from the uploaded tree, so I could not confirm that tree equals `7654f1f`. `git apply --check` was run only against that reconstructed baseline.
- **`npm run dev`** (Vite dev server and its Origin-rewriting proxy): not run. Only the production build served by FastAPI was tested.
- **Drag-and-drop of folders** (`webkitGetAsEntry`) and the "Choose folder" picker: code written, not exercised in a browser. File picker upload was.
- **Large inputs**: no test near the 50 MiB / 256 MiB limits; the limit logic is unit-tested with small limits.
- **Accessibility**: keyboard and focus styles are present, but no screen-reader audit; the detail drawer has no focus trap.
- Other browsers: only headless Chromium.

## Known limitations

- One workspace and one latest scan per server process; starting a scan discards the previous scan's results.
- Starlette spools an accepted multipart body to temporary storage before the handler streams it into the workspace; oversize bodies are rejected early (Content-Length and while streaming), but accepted ones are briefly written twice.
- Scan cancel and the time limit are checked between sources, not inside one large file.
- The evaluation scores only the bundled dataset (as the contract specifies); uploaded files are never scored. The evaluator returns counts, so missed/false columns are not listed.
- No authentication by design; the app is for local use.
- Markdown export is not escaped.
- Deployment (hosted, Round 2) is not part of this work.
