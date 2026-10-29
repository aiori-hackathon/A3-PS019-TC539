# FieldLedger â€” AIORI-3 | PS-019

**Team:** Cipher
**Team Code:** A3-PS019-TC539
**Problem Statement:** PS-019
**Team Lead:** Yashvardhan Vashishtha
**Institute:** National Forensic Sciences University, Guwahati

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

## Team Introduction Video

[Watch Team Cipher's AIORI-3 introduction video](https://youtu.be/VfAXWC7Eesw)
