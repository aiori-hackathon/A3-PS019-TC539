# Test results (actual runs)

**Environment of these runs: Linux (Ubuntu 24), Python 3.12.3, Node.js 22.22.2, headless Chromium.**
They were **not** run on Windows 10/11, Python 3.13.5 or Node.js 24. See `DELIVERY_REPORT.md`.

## Python (pytest)

| Run | Result |
|---|---|
| Baseline, uploaded tree before any change | 911 passed |
| Whole suite after the UI work (`pytest -q -rs`) | **1035 passed** |
| of which `tests/web` (contract + new API and security tests) | 237 passed |

The Windows baseline reported with the upload was 908 passed, 3 skipped; the 3 skips did not occur on Linux (all 911 ran).
No existing test, fixture, `detector.py`, `rules.py`, `config/rules.yaml`, taxonomy, dataset or ground-truth file was modified.

New tests: `tests/web/test_api.py` (end-to-end against the real engine and bundled data), `tests/web/test_security.py`.

## Frontend

| Check | Result |
|---|---|
| `npm run typecheck` (`tsc --noEmit`, strict) | no errors |
| `npm test` (vitest) | 3 files, **30 passed** |
| `npm run build` | succeeds; JS 334 kB (102 kB gzip), fonts bundled locally |

## Browser end-to-end (headless Chromium driving the real server)

Scripted run: reset workspace, click *Load bundled synthetic data and scan*, wait for the scan, visit all eight pages, filter Findings by class (27 rows), open a finding, set a review (server confirmed `review_count: 1`), close the drawer with Escape, run the evaluation, open Help, then a 390 px viewport.
Result: no console errors or warnings, no failed requests, no Content-Security-Policy violations, no horizontal overflow at 390 px.
The first run of this script found a real defect (a small font inlined as a `data:` URI was blocked by the CSP); it was fixed in the build configuration and the run repeated clean. Screenshots: `docs/screenshots/`.
This script is not part of the repository's automated tests.

## Evaluation on the bundled synthetic benchmark (computed live, current rules)

| Class | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| PERSONAL_IDENTITY | 29 | 1 | 0 | 96.7% | 100.0% |
| CONTACT_INFORMATION | 27 | 0 | 0 | 100.0% | 100.0% |
| GOVERNMENT_IDENTIFIERS | 25 | 0 | 1 | 100.0% | 96.2% |
| FINANCIAL_INFORMATION | 26 | 2 | 0 | 92.9% | 100.0% |
| LOCATION_ADDRESS | 31 | 1 | 0 | 96.9% | 100.0% |

Micro (derived by summing the counts): TP 138, FP 4, FN 1, 142 predicted, 139 labelled positive, precision 97.2%, recall 99.3%.
These describe the team's own synthetic dataset only. The figures in the older contract text (TP 114 / FP 35 / FN 25) are superseded by the detection improvements and are no longer stated anywhere.
