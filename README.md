# FieldLedger AIORI-3 | PS-019

**Team:** Cipher
**Team Code:** A3-PS019-TC539
**Problem Statement:** PS-019
**Team Lead:** Yashvardhan Vashishtha
**Institute:** National Forensic Sciences University, Guwahati

## Live Deployment

FieldLedger is deployed and available online!

Live Website: https://a3-ps019-tc539.onrender.com/

## Overview

FieldLedger is a prototype for identifying candidate personal-data fields in structured datasets. It examines CSV files and SQLite databases, applies configurable detection rules, and produces an inventory of potential personal-data fields for human review.

The prototype also links detected fields to declared purpose and retention metadata and highlights missing or unmatched metadata entries.

## Key Features

- **CSV and SQLite discovery:** Inspect supported structured data sources.
- **Configurable classification:** Apply rules to identify candidate personal-data fields across five configured classes.
- **Metadata review:** Link fields to declared purpose and retention information and flag gaps for review.
- **Multiple report formats:** Generate JSON, CSV, and Markdown outputs.
- **Evaluation:** Calculate per-class precision and recall against a labelled dataset.
- **Human-in-the-loop design:** Present technical findings for review rather than making legal or compliance decisions.

## Technology

- Python
- CSV and SQLite
- YAML configuration
- pytest

## Setup

Run the following commands from the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

If the virtual environment has already been configured, you can use it directly.

## Run a Scan

```powershell
.\.venv\Scripts\python.exe -m fieldledger scan `
    --input .\data\synthetic `
    --metadata ".\data\synthetic\metadata\purpose_retention.yaml" `
    --out .\output\milestone6 `
    --classes .\config\classes.yaml `
    --rules .\config\rules.yaml `
    --formats json,csv,md
```

The generated reports are written to `output\milestone6`.

## Run Evaluation

```powershell
.\.venv\Scripts\python.exe -m fieldledger evaluate `
    --input .\data\synthetic `
    --labels .\data\synthetic\ground_truth\labels.yaml `
    --out .\output\milestone6-evaluation `
    --classes .\config\classes.yaml `
    --rules .\config\rules.yaml
```

Evaluation results are written to `output\milestone6-evaluation`.

**Evaluation note:** The current labelled dataset is team-authored synthetic data. Its metrics describe performance on that dataset only and should not be interpreted as evidence of real-world accuracy.

## Run Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q -rs
```

Previously verified result: **628 passed, 3 skipped**. Rerun the tests to confirm the result for your current checkout.

## Limitations and Responsible Use

- Detection rules are provisional and rely on technical evidence such as column names.
- Findings are candidates for review, not definitive proof that a field contains personal data.
- Metadata gaps indicate information that may need review; they do not independently establish a legal violation.
- The prototype does not determine lawful basis, establish compliance, or infer every data flow.
- Human review is required before acting on findings.

## Demo Video

[Watch the FieldLedger project demonstration](https://youtu.be/_CIeXKjhdGY)

## Team Introduction Video

[Watch Team Cipher's AIORI-3 introduction video](https://youtu.be/VfAXWC7Eesw)

## Web UI (FastAPI + React)

A local, offline web interface over the same detection engine. The Python engine is the single source of truth; the UI never re-implements detection.

**Pages:** Overview, Upload, Sources, Scan, Findings, Metadata coverage, Evaluation, Help.

### Quick start on Windows 10/11 (Python 3.13, Node.js 24)

Double-click `start_fieldledger.bat`, or run from PowerShell in the project root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]"
.\.venv\Scripts\python.exe -m fieldledger.web --port 8765
```

Then open http://127.0.0.1:8765. The built UI is already included in `src\fieldledger\web\static`. To rebuild it (needs Node.js 24):

```powershell
cd frontend
npm ci
npm run build
cd ..
```

Frontend development with hot reload (backend on 8765, UI on 5173):

```powershell
.\.venv\Scripts\python.exe -m fieldledger.web --port 8765
cd frontend; npm run dev        # open http://127.0.0.1:5173
```

Demo on the project's own data: open **Overview** and choose *Load bundled synthetic data and scan*.

### Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[web-dev]"
.\.venv\Scripts\python.exe -m pytest -q -rs
cd frontend; npm run typecheck; npm test
```

Or run `run_tests_windows.bat`. See `docs\TEST_RESULTS.md` for the recorded results and `docs\DELIVERY_REPORT.md` for what is verified and what is not.

### Security defaults

The server binds to `127.0.0.1` only (a non-loopback `--host` needs `--allow-network` and prints a warning; there is no authentication). Uploads are validated by name, type, size and content signature and stored in a random temporary directory that is removed on idle expiry, shutdown or clearing. State-changing requests need a custom header and a matching Origin. CSV exports neutralise spreadsheet formula prefixes. Server logs never contain file names, column names or values. Details: `docs\api-contract.md`.
