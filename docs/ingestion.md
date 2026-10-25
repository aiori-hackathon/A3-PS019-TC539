# FieldLedger ingestion layer (Milestone 2)

Profiles CSV files and SQLite databases into **structural metadata only**: source type,
file/table name, column names, broad types, row counts, and null / non-null counts.
No cell values are returned, stored, or logged. No personal-data classification happens
here, and the class taxonomy remains provisional and untouched.

## API

```python
from fieldledger.ingest_csv import profile_csv
from fieldledger.ingest_sqlite import profile_sqlite
from fieldledger.limits import IngestionLimits

profile = profile_csv("customers.csv", allowed_base="data/input")
profile = profile_sqlite("app.db", allowed_base="data/input", limits=IngestionLimits(max_tables=50))
print(profile.to_dict())   # JSON-serialisable, stable key order, schema_version = 1
```

`path` is resolved with `safety.resolve_within(allowed_base, path)`; anything outside the
base (including `..` traversal, absolute paths, and symlinks that resolve outside) raises
`PathAccessError`.

## Default limits (`IngestionLimits`)

| Limit | Default | Applies to |
|---|---|---|
| `max_file_bytes` | 50 MiB | CSV (stat check and while reading) |
| `max_rows` | 100,000 | CSV data rows (header excluded) |
| `max_columns` | 1,000 | CSV and each SQLite table |
| `max_line_chars` | 262,144 | CSV physical line, terminator included |
| `max_db_bytes` | 256 MiB | SQLite file |
| `max_tables` | 1,000 | SQLite user tables |
| `sqlite_timeout_seconds` | 30.0 | whole SQLite call; lock wait capped at 5 s |

## Errors (all under `IngestionError`, which extends `FieldLedgerError`)

| Situation | Exception |
|---|---|
| Path outside allowed base | `PathAccessError` (existing) |
| Wrong extension | `UnsupportedFileTypeError` |
| Missing, directory, unreadable, UNC path, unopenable DB | `InputAccessError` |
| Over any size/row/column/table limit | `InputTooLargeError` |
| Empty CSV, bad header, bad row, bad encoding, bad quoting, NUL, long line | `MalformedCsvError` |
| Not a SQLite file, corrupt DB, unusable table/column name | `InvalidDatabaseError` |
| SQLite time budget exceeded or database locked | `DatabaseTimeoutError` |
| Unknown `encoding=` option | `ConfigError` (existing) |

Messages contain line numbers, column positions and counts only. They never contain cell
values, header text, or absolute paths.

## CSV rules

- Encoding: UTF-8 with or without BOM by default. Any other encoding must be requested
  explicitly (`encoding="cp1252"` or `"latin-1"`); there is no automatic guessing.
- Delimiter is always a comma; quoting is strict.
- First non-blank row is the header; blank lines are skipped and not counted.
- Blank headers and duplicate headers (compared after trimming, case-insensitive) are
  rejected, never renamed. Header names are returned trimmed.
- Any row whose field count differs from the header fails the whole file (first offending
  line reported). There is no lenient mode.
- Header-only file: valid, 0 rows, all columns typed `empty`.
- A cell is null if empty or whitespace-only. Text such as `NULL` or `N/A` is a value.

## Type inference limits

`inferred_type` describes **format only**. It is not a personal-data class and does not
imply sensitivity.

- CSV (`type_basis = observed_values`): a column gets a type only if every non-empty cell
  fits. Recognised: integer, float (plain decimal / exponent), boolean (`true false yes no`,
  any case), ISO date `YYYY-MM-DD`, ISO datetime. Otherwise `string`. Leading-zero numbers
  (`007`), thousands separators, `NaN`/`Infinity`, locale dates and `0`/`1` flags are not
  recognised as such (`0`/`1` are integers). A column with no non-empty cell is `empty`.
- SQLite (`type_basis = declared_schema`): derived from the declared column type using
  SQLite's affinity rules plus name hints (`BOOL*`, `DATE*`, `DATETIME`/`TIMESTAMP`).
  No values are read for typing. SQLite is dynamically typed, so stored values may differ
  from the declared type. Undeclared columns are `unknown`; `NUMERIC`/`DECIMAL` are `numeric`.

## SQLite behaviour and trade-offs

- Opened with a URI built from `Path.as_uri()` + `?mode=ro`, then `query_only=ON`,
  `trusted_schema=OFF`, `cell_size_check=ON`, extension loading disabled. No API accepts
  caller SQL. Identifiers used in SQL are validated and quoted (`quote_identifier`);
  table names for metadata are bound parameters.
- Included: ordinary user tables, including generated columns. Excluded: `sqlite_*`
  internal tables, views, virtual tables (their shadow tables are ordinary tables and
  are included).
- A 0-byte file is a valid empty database (no tables), not an error.
- Row and null counts require one full scan per table; cost is bounded by `max_db_bytes`
  and the time budget, not by `max_rows`.
- Read-only WAL-mode databases may fail to open (`InputAccessError`) if their `-wal`/`-shm`
  files cannot be created or are missing; profile a copy in that case.
- Network (UNC) paths are refused. Table and column names containing control characters
  or longer than 256 characters cause `InvalidDatabaseError` for the whole database.
- Requires SQLite 3.26+ (`table_xinfo`); Python 3.10+ builds on current OSes meet this.
- Time budget is checked every 1,000 SQLite VM steps; a single very long C-level step can
  overshoot slightly.
- Not a legal or compliance determination. Technical evidence for human review only.
