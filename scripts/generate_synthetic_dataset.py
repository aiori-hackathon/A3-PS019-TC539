#!/usr/bin/env python3
"""Deterministic generator for the FieldLedger PS-019 synthetic dataset.

Produces, from ONE fixed seed, a fully synthetic dataset that contains no real personal data:

    <out>/crm/customers.csv, crm/archive/customers.csv, hr/employees.csv,
         finance/payments.csv, ops/orders.csv        UTF-8 CSV files with a header row
    <out>/app.db                                     SQLite database (5 tables)
    <out>/ground_truth/labels.yaml                   per-column ground truth (official classes)
    <out>/metadata/purpose_retention.yaml            declared purpose/retention, with deliberate gaps
    <out>/manifest.json                              seed, dataset size and group counts
    <out>/DATASET.md                                 methodology and formats

Labelling methodology (see DATASET.md): every column is drawn from a fixed pool of column
specifications and carries the label of the pool it came from. A column is labelled with an
official class only if it holds the value itself (for example an email address, a phone
number, an Aadhaar number). Columns that merely mention a keyword but hold something else
(email_opt_in, phone_model, ip_address, account_age_days, ...) are labelled
NOT_PERSONAL_DATA, as are look-alike columns whose values only resemble personal data.

Reproducibility: all randomness comes from random.Random.random() (the only method Python
guarantees to be stable across versions), seeded from SHA-256 of the seed and a stream name.
Files are written with explicit UTF-8, "\\n" line endings and sorted keys/rows where relevant.
Only the SQLite file's bytes can differ between SQLite versions, so compare it logically.

Synthetic values are invalid by construction where a rule is known: e-mail addresses use the
reserved ".invalid" TLD, phone numbers start with 0, Aadhaar-like and account/card-like
numbers start with 0, PAN-like values use an unassigned holder-type letter, PIN codes start
with 0, passport-like values have a 0 second character, IFSC-like values use a fictional
bank code, UPI handles use a fictional bank, coordinates fall in the open South Pacific.
For formats with no known invalid pattern (voter ID, driving licence) values use fictional
prefixes; a collision with a real identifier cannot be formally excluded, but no value is
derived from any real person or record.

Usage:
    python scripts/generate_synthetic_dataset.py --out data/synthetic [--seed N] [--overwrite]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Callable

DEFAULT_SEED = 20261027
ROWS_PER_TABLE = 24
NOT_PERSONAL = "NOT_PERSONAL_DATA"
CLASS_IDS = (
    "PERSONAL_IDENTITY",
    "CONTACT_INFORMATION",
    "GOVERNMENT_IDENTIFIERS",
    "FINANCIAL_INFORMATION",
    "LOCATION_ADDRESS",
)

# Groups (documented in DATASET.md)
POS_STANDARD = "positive_standard_name"
POS_OPAQUE = "positive_opaque_name"
NEG_PLAIN = "negative_plain"
NEG_VALUE_LOOKALIKE = "negative_value_lookalike"
NEG_NAME_LOOKALIKE = "negative_name_lookalike"

# ------------------------------------------------------------------ column pools
# (name, kind). The kind selects the value generator; it never decides the label.

PERSONAL_IDENTITY_STANDARD = [
    ("full_name", "person_name"), ("first_name", "first_name"), ("last_name", "last_name"),
    ("middle_name", "first_name"), ("surname", "last_name"), ("given_name", "first_name"),
    ("family_name", "last_name"), ("customer_name", "person_name"),
    ("employee_name", "person_name"), ("applicant_name", "person_name"),
    ("account_holder_name", "person_name"), ("nominee_name", "person_name"),
    ("date_of_birth", "dob"), ("dob", "dob"), ("birth_date", "dob"), ("birthdate", "dob"),
    ("birthday", "dob"), ("customer_dob", "dob"), ("gender", "gender"), ("sex", "gender"),
    ("customer_gender", "gender"), ("age", "age"), ("customer_age", "age"),
    ("applicant_age", "age"),
]
PERSONAL_IDENTITY_OPAQUE = [
    ("fname", "first_name"), ("cust_nm", "person_name"), ("d_o_b", "dob"),
    ("gndr", "gender"), ("yrs_old", "age"),
]
CONTACT_INFORMATION_STANDARD = [
    ("email", "email"), ("email_address", "email"), ("primary_email", "email"),
    ("work_email", "email"), ("personal_email", "email"), ("customer_email", "email"),
    ("contact_email", "email"), ("e_mail", "email"), ("phone", "phone"),
    ("phone_number", "phone"), ("mobile", "phone"), ("mobile_number", "phone"),
    ("mobile_no", "phone"), ("telephone", "phone"), ("home_phone", "phone"),
    ("work_phone", "phone"), ("contact_number", "phone"), ("contact_no", "phone"),
    ("customer_phone", "phone"), ("emergency_contact_number", "phone"),
    ("msisdn", "phone"), ("alternate_mobile", "phone"),
]
CONTACT_INFORMATION_OPAQUE = [
    ("ph_no", "phone"), ("em_id", "email"), ("cell_no", "phone"), ("mob", "phone"),
    ("contact_detail_1", "phone"),
]
GOVERNMENT_IDENTIFIERS_STANDARD = [
    ("aadhaar", "aadhaar"), ("aadhaar_number", "aadhaar"), ("aadhaar_no", "aadhaar"),
    ("aadhar_number", "aadhaar"), ("uidai_number", "aadhaar"), ("pan", "pan"),
    ("pan_number", "pan"), ("pan_no", "pan"), ("pan_card_number", "pan"),
    ("passport", "passport"), ("passport_number", "passport"), ("passport_no", "passport"),
    ("voter_id", "voter"), ("voter_id_number", "voter"), ("voter_card_no", "voter"),
    ("driving_licence_number", "dl"), ("driving_license_no", "dl"),
    ("driver_license_number", "dl"), ("driver_licence_no", "dl"),
    ("customer_aadhaar", "aadhaar"), ("employee_pan", "pan"),
]
GOVERNMENT_IDENTIFIERS_OPAQUE = [
    ("uid_no", "aadhaar"), ("dl_no", "dl"), ("epic_no", "voter"), ("ppt_no", "passport"),
    ("tax_ref", "pan"),
]
FINANCIAL_INFORMATION_STANDARD = [
    ("bank_account_number", "bank_account"), ("account_number", "bank_account"),
    ("account_no", "bank_account"), ("bank_account", "bank_account"),
    ("acct_no", "bank_account"), ("ifsc", "ifsc"), ("ifsc_code", "ifsc"),
    ("branch_ifsc", "ifsc"), ("card_number", "card"), ("card_no", "card"),
    ("credit_card_number", "card"), ("debit_card_number", "card"),
    ("credit_card", "card"), ("debit_card", "card"), ("upi_id", "upi"), ("upi", "upi"),
    ("upi_handle", "upi"), ("payer_upi_id", "upi"), ("payee_upi", "upi"), ("vpa", "upi"),
    ("salary_account_number", "bank_account"),
]
FINANCIAL_INFORMATION_OPAQUE = [
    ("acc_num", "bank_account"), ("cc_num", "card"), ("ifs_cd", "ifsc"),
    ("pay_handle", "upi"), ("bk_acct", "bank_account"),
]
LOCATION_ADDRESS_STANDARD = [
    ("address", "address"), ("home_address", "address"), ("postal_address", "address"),
    ("street_address", "address"), ("billing_address", "address"),
    ("shipping_address", "address"), ("mailing_address", "address"),
    ("residential_address", "address"), ("permanent_address", "address"),
    ("current_address", "address"), ("office_address", "address"),
    ("address_line1", "address"), ("address_line2", "address"), ("pincode", "pincode"),
    ("pin_code", "pincode"), ("postal_code", "pincode"), ("zip_code", "pincode"),
    ("zipcode", "pincode"), ("zip", "pincode"), ("latitude", "lat"), ("longitude", "lon"),
    ("lat", "lat"), ("lon", "lon"), ("lng", "lon"), ("gps_location", "geo_pair"),
    ("geolocation", "geo_pair"),
]
LOCATION_ADDRESS_OPAQUE = [
    ("addr_ln1", "address"), ("pin", "pincode"), ("loc_coords", "geo_pair"),
    ("geo_pt", "geo_pair"), ("res_addr", "address"),
]

POSITIVE_POOLS = {
    "PERSONAL_IDENTITY": (PERSONAL_IDENTITY_STANDARD, PERSONAL_IDENTITY_OPAQUE),
    "CONTACT_INFORMATION": (CONTACT_INFORMATION_STANDARD, CONTACT_INFORMATION_OPAQUE),
    "GOVERNMENT_IDENTIFIERS": (GOVERNMENT_IDENTIFIERS_STANDARD, GOVERNMENT_IDENTIFIERS_OPAQUE),
    "FINANCIAL_INFORMATION": (FINANCIAL_INFORMATION_STANDARD, FINANCIAL_INFORMATION_OPAQUE),
    "LOCATION_ADDRESS": (LOCATION_ADDRESS_STANDARD, LOCATION_ADDRESS_OPAQUE),
}

NEGATIVE_PLAIN = [
    ("order_id", "order_id"), ("order_date", "date"), ("order_total", "money"),
    ("quantity", "small_int"), ("unit_price", "money"), ("currency_code", "code"),
    ("discount_pct", "percent"), ("tax_amount", "money"), ("created_at", "datetime"),
    ("updated_at", "datetime"), ("status", "status"), ("category", "category"),
    ("sku", "code"), ("warehouse_code", "code"), ("shipment_status", "status"),
    ("delivery_days", "small_int"), ("rating", "small_int"), ("review_count", "small_int"),
    ("is_active", "bool"), ("notes", "text"), ("department", "category"),
    ("job_title", "category"), ("salary_band", "category"), ("hire_date", "date"),
    ("country_code", "code"), ("language", "category"), ("plan_type", "category"),
    ("loyalty_tier", "category"), ("signup_channel", "category"),
    ("last_login_at", "datetime"), ("session_count", "small_int"),
    ("page_views", "small_int"), ("device_type", "category"), ("browser", "category"),
    ("os_version", "version"), ("referrer", "category"), ("utm_source", "category"),
    ("campaign_id", "code"), ("invoice_total", "money"), ("payment_status", "status"),
    ("payment_method", "category"), ("refund_flag", "bool"), ("product_category", "category"),
    ("stock_level", "small_int"), ("reorder_level", "small_int"), ("supplier_code", "code"),
    ("branch_code", "code"), ("region_code", "code"), ("cost_center", "code"),
    ("project_code", "code"), ("ticket_priority", "status"), ("shift_code", "code"),
    ("grade", "category"), ("approval_status", "status"), ("retry_count", "small_int"),
    ("weight_kg", "money"), ("tax_code", "code"), ("is_deleted", "bool"),
    ("channel", "category"), ("batch_id", "order_id"), ("customer_segment", "category"),
    ("last_purchase_date", "date"), ("cart_value", "money"), ("return_reason", "category"),
    ("shipping_method", "category"), ("packaging_type", "category"),
    ("stock_location_code", "code"), ("audit_flag", "bool"), ("price_tier", "category"),
    ("review_text", "text"), ("support_queue", "category"), ("contract_type", "category"),
    ("leave_balance_days", "small_int"), ("performance_score", "small_int"),
]
NEGATIVE_VALUE_LOOKALIKE = [  # innocuous names; values only RESEMBLE personal-data formats
    ("order_ref", "phone"), ("parcel_ref", "phone"), ("tracking_number", "aadhaar"),
    ("invoice_code", "pan"), ("ticket_number", "pincode"), ("batch_serial", "card"),
    ("gift_card_ref", "card"), ("txn_ref", "bank_account"), ("branch_ref", "ifsc"),
    ("coupon_code", "upi"), ("route_code", "geo_pair"), ("asset_tag", "voter"),
    ("policy_ref", "passport"), ("permit_ref", "dl"), ("serial_no", "aadhaar"),
    ("lot_number", "phone"), ("member_ref", "phone"), ("doc_ref", "aadhaar"),
    ("remit_ref", "bank_account"), ("zone_code", "pincode"),
]
NEGATIVE_NAME_LOOKALIKE = [  # names contain a keyword but the column holds something else
    ("name", "product_title"), ("product_name", "product_title"), ("file_name", "filename"),
    ("company_name", "company"), ("city_name", "city"), ("email_opt_in", "bool"),
    ("email_template_id", "code"), ("email_bounce_count", "small_int"),
    ("phone_model", "device_model"), ("mobile_app_version", "version"),
    ("mobile_os", "category"), ("ip_address", "ip"), ("mac_address", "mac"),
    ("web_address", "url"), ("account_age_days", "small_int"),
    ("age_of_account", "small_int"), ("age_limit", "small_int"), ("card_type", "category"),
    ("card_expiry", "date"), ("passport_expiry", "date"), ("pan_india_flag", "bool"),
    ("voter_turnout_pct", "percent"), ("address_verified_flag", "bool"),
    ("address_type", "category"), ("zip_archive_name", "filename"), ("upi_enabled", "bool"),
    ("ifsc_verified_flag", "bool"), ("license_key", "code"),
    ("latitude_precision", "small_int"), ("gender_ratio", "percent"),
    ("phone_verified_flag", "bool"), ("email_domain_count", "small_int"),
    ("email_sent_at", "datetime"), ("phone_call_count", "small_int"), ("gps_enabled", "bool"),
    ("address_count", "small_int"), ("gender_pay_gap_pct", "percent"),
    ("age_bucket_size", "small_int"), ("zip_compression_ratio", "percent"),
]

# SQLite declared type per kind (default TEXT)
SQLITE_TYPES = {
    "age": "INTEGER", "small_int": "INTEGER", "money": "REAL", "percent": "REAL",
    "lat": "REAL", "lon": "REAL", "bool": "INTEGER",
}

SOURCES = [  # (source_key, table, kind)
    ("app.db", "members", "sqlite"),
    ("app.db", "kyc_records", "sqlite"),
    ("app.db", "branches", "sqlite"),
    ("app.db", "web_events", "sqlite"),
    ("app.db", "inventory", "sqlite"),
    ("crm/customers.csv", "customers", "csv"),
    ("crm/archive/customers.csv", "customers", "csv"),
    ("finance/payments.csv", "payments", "csv"),
    ("hr/employees.csv", "employees", "csv"),
    ("ops/orders.csv", "orders", "csv"),
]

PURPOSES = [
    "Customer onboarding and account servicing",
    "Payroll and benefits administration",
    "Order fulfilment and delivery",
    "Fraud screening and payment verification",
    "Customer support and complaint handling",
    "Know-your-customer record keeping",
    "Marketing communication (opt-in customers only)",
    "Employee records administration",
]
RETENTIONS = [
    "12 months after last activity",
    "24 months after account closure",
    "36 months after contract end",
    "7 years from end of financial year",
    "Until the support case is closed plus 6 months",
    "Deleted after 90 days",
]


# ------------------------------------------------------------------ deterministic helpers


class Rng:
    """All randomness derives from random.Random.random(), seeded from SHA-256."""

    def __init__(self, seed: int, stream: str) -> None:
        digest = hashlib.sha256(f"{seed}:{stream}".encode("utf-8")).digest()
        self._r = random.Random(int.from_bytes(digest, "big"))

    def below(self, n: int) -> int:
        return int(self._r.random() * n)

    def between(self, a: int, b: int) -> int:
        return a + self.below(b - a + 1)

    def choice(self, seq):
        return seq[self.below(len(seq))]

    def shuffle(self, items: list) -> None:
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]

    def digits(self, n: int) -> str:
        return "".join(str(self.below(10)) for _ in range(n))

    def letters(self, n: int) -> str:
        return "".join(chr(ord("A") + self.below(26)) for _ in range(n))

    def fraction6(self) -> str:
        return f"{self.below(1_000_000):06d}"


_FIRST_SYL = ["ka", "ri", "vo", "la", "mi", "ten", "dra", "su", "no", "pel"]
_LAST_START = ["Valk", "Tesh", "Morn", "Quill", "Dran", "Pell", "Hesk", "Brum"]
_LAST_END = ["ane", "ish", "ova", "well", "ard", "eni"]
_CITIES = ["Testville", "Sampleton", "Mockburg", "Fakeford"]


def _first(r: Rng) -> str:
    return (r.choice(_FIRST_SYL) + r.choice(_FIRST_SYL) + r.choice(_FIRST_SYL)).capitalize()


def _last(r: Rng) -> str:
    return r.choice(_LAST_START) + r.choice(_LAST_END)


def _date(r: Rng, y0: int, y1: int) -> str:
    lo, hi = date(y0, 1, 1).toordinal(), date(y1, 12, 28).toordinal()
    return date.fromordinal(r.between(lo, hi)).isoformat()


def _money(r: Rng) -> str:
    cents = r.between(100, 999_999)
    return f"{cents // 100}.{cents % 100:02d}"


VALUE_GENERATORS: dict[str, Callable[[Rng], str]] = {
    # personal-data formats (all synthetic, see module docstring)
    "person_name": lambda r: f"{_first(r)} {_last(r)}",
    "first_name": _first,
    "last_name": _last,
    "dob": lambda r: _date(r, 1950, 2006),
    "gender": lambda r: r.choice(["female", "male", "other"]),
    "age": lambda r: str(r.between(18, 85)),
    "email": lambda r: f"{_first(r).lower()}{r.between(10, 99)}@example.invalid",
    "phone": lambda r: "0" + r.digits(9),
    "aadhaar": lambda r: "0" + r.digits(11),
    "pan": lambda r: f"{r.letters(3)}Z{r.letters(1)}{r.digits(4)}{r.letters(1)}",
    "passport": lambda r: "Z0" + r.digits(6),
    "voter": lambda r: "SYN" + r.digits(7),
    "dl": lambda r: "ZZ" + r.digits(13),
    "bank_account": lambda r: "0000" + r.digits(8),
    "ifsc": lambda r: "ZZZZ0" + r.digits(6),
    "card": lambda r: "0000" + r.digits(12),
    "upi": lambda r: f"{_first(r).lower()}{r.between(10, 99)}@synthbank",
    "address": lambda r: f"Plot {r.between(1, 400)}, Synthetic Lane {r.between(1, 60)}, {r.choice(_CITIES)}",
    "pincode": lambda r: "0" + r.digits(5),
    "lat": lambda r: f"-48.{r.fraction6()}",
    "lon": lambda r: f"-123.{r.fraction6()}",
    "geo_pair": lambda r: f"-48.{r.fraction6()},-123.{r.fraction6()}",
    # everything else
    "order_id": lambda r: "ORD" + r.digits(6),
    "date": lambda r: _date(r, 2020, 2025),
    "datetime": lambda r: f"{_date(r, 2024, 2025)}T{r.between(0, 23):02d}:{r.between(0, 59):02d}:{r.between(0, 59):02d}",
    "money": _money,
    "small_int": lambda r: str(r.between(0, 500)),
    "percent": lambda r: str(r.between(0, 100)),
    "code": lambda r: r.letters(1) + r.digits(3),
    "status": lambda r: r.choice(["open", "closed", "pending", "failed"]),
    "category": lambda r: r.choice(["standard", "premium", "basic", "trial"]),
    "bool": lambda r: r.choice(["0", "1"]),
    "text": lambda r: f"synthetic note {r.between(1, 999)}",
    "version": lambda r: f"{r.between(1, 9)}.{r.between(0, 9)}.{r.between(0, 20)}",
    "product_title": lambda r: f"Widget {r.between(1, 999)}",
    "filename": lambda r: f"report_{r.between(1, 999)}.csv",
    "company": lambda r: f"Synthco {r.between(1, 99)} Ltd",
    "city": lambda r: r.choice(_CITIES),
    "device_model": lambda r: f"model-x{r.between(1, 99)}",
    "ip": lambda r: f"192.0.2.{r.between(1, 254)}",  # RFC 5737 documentation range
    "mac": lambda r: f"00:00:5e:00:53:{r.between(0, 255):02x}",  # RFC 7042 documentation range
    "url": lambda r: f"https://example.invalid/p/{r.between(1, 999)}",
}


# ------------------------------------------------------------------ dataset construction


def _specs():
    """All column specs as (name, label, kind, group). Names must be globally unique."""
    specs = []
    for class_id in CLASS_IDS:
        standard, opaque = POSITIVE_POOLS[class_id]
        specs += [(n, class_id, k, POS_STANDARD) for n, k in standard]
        specs += [(n, class_id, k, POS_OPAQUE) for n, k in opaque]
    specs += [(n, NOT_PERSONAL, k, NEG_PLAIN) for n, k in NEGATIVE_PLAIN]
    specs += [(n, NOT_PERSONAL, k, NEG_VALUE_LOOKALIKE) for n, k in NEGATIVE_VALUE_LOOKALIKE]
    specs += [(n, NOT_PERSONAL, k, NEG_NAME_LOOKALIKE) for n, k in NEGATIVE_NAME_LOOKALIKE]
    names = [s[0] for s in specs]
    if len(set(names)) != len(names):
        raise AssertionError("column names must be unique across all pools")
    for _n, _l, kind, _g in specs:
        if kind not in VALUE_GENERATORS:
            raise AssertionError(f"unknown value kind {kind}")
    return specs


def _layout(seed: int):
    """Assign every spec to exactly one table, deterministically."""
    specs = _specs()
    Rng(seed, "layout").shuffle(specs)
    n_tables = len(SOURCES)
    base, extra = divmod(len(specs), n_tables)
    tables, start = [], 0
    for i, (source_key, table, _fmt) in enumerate(SOURCES):
        size = base + (1 if i < extra else 0)
        chunk = specs[start : start + size]
        start += size
        tables.append((source_key, table, chunk))
    return tables


def _rows(seed: int, source_key: str, table: str, chunk):
    rows = []
    for i in range(ROWS_PER_TABLE):
        row = []
        for name, _label, kind, _group in chunk:
            row.append(VALUE_GENERATORS[kind](Rng(seed, f"cell:{source_key}:{table}:{name}:{i}")))
        rows.append(row)
    return rows


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _write_sqlite(path: Path, tables) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with closing(sqlite3.connect(path)) as conn:
        for _source_key, table, chunk, rows in tables:
            cols = ", ".join(f'"{n}" {SQLITE_TYPES.get(k, "TEXT")}' for n, _l, k, _g in chunk)
            conn.execute(f'CREATE TABLE "{table}" ({cols})')
            conv = []
            for _n, _l, kind, _g in chunk:
                decl = SQLITE_TYPES.get(kind, "TEXT")
                conv.append(int if decl == "INTEGER" else float if decl == "REAL" else str)
            marks = ", ".join("?" for _ in chunk)
            conn.executemany(
                f'INSERT INTO "{table}" VALUES ({marks})',
                [[c(v) for c, v in zip(conv, row)] for row in rows],
            )
        conn.commit()


def generate_dataset(output_dir: str | Path, seed: int = DEFAULT_SEED) -> dict:
    """Write the whole dataset into `output_dir` and return the manifest dictionary."""
    out = Path(output_dir)
    layout = _layout(seed)

    built = []  # (source_key, table, chunk, rows)
    for source_key, table, chunk in layout:
        built.append((source_key, table, chunk, _rows(seed, source_key, table, chunk)))

    # data files
    sqlite_tables = [b for b in built if b[0].endswith(".db")]
    for source_key, _table, chunk, rows in built:
        if source_key.endswith(".csv"):
            _write_csv(out / source_key, [c[0] for c in chunk], rows)
    _write_sqlite(out / "app.db", sqlite_tables)

    # ground truth: group the tables of each source_key (a SQLite file has several)
    grouped: dict[str, list] = {}
    for source_key, table, chunk, _rows_ in built:
        pos = [(n, label) for n, label, _k, _g in chunk if label != NOT_PERSONAL]
        neg = [n for n, label, _k, _g in chunk if label == NOT_PERSONAL]
        grouped.setdefault(source_key, []).append((table, pos, neg))
    labels_text = _labels_yaml(grouped)
    _write_text(out / "ground_truth" / "labels.yaml", labels_text)

    # purpose / retention metadata with deliberate gaps (decided by a seeded stream)
    meta_lines = [
        "# DECLARED purpose and retention metadata for the synthetic dataset (fictional).",
        "# Format: one entry per column that declares anything. An absent `purpose` or",
        "# `retention` key means that value is NOT declared; a personal-data column with no",
        "# entry at all has neither declared. Gaps are deliberate and seeded.",
        "# These are technical declarations for testing, not legal statements.",
        "metadata_origin: team_authored_synthetic",
        "entries:",
    ]
    gap_counts = {"complete": 0, "missing_purpose": 0, "missing_retention": 0, "no_entry": 0}
    for source_key in sorted(grouped):
        for table, positives, _neg in sorted(grouped[source_key], key=lambda t: t[0]):
            for column, _class_id in sorted(positives):
                r = Rng(seed, f"meta:{source_key}:{table}:{column}")
                roll = r.below(100)
                purpose, retention = r.choice(PURPOSES), r.choice(RETENTIONS)
                if roll < 70:
                    kind = "complete"
                elif roll < 80:
                    kind = "missing_purpose"
                elif roll < 90:
                    kind = "missing_retention"
                else:
                    kind = "no_entry"
                gap_counts[kind] += 1
                if kind == "no_entry":
                    continue
                parts = [
                    f"source_key: {_q(source_key)}", f"table: {_q(table)}", f"column: {_q(column)}"
                ]
                if kind != "missing_purpose":
                    parts.append(f"purpose: {_q(purpose)}")
                if kind != "missing_retention":
                    parts.append(f"retention: {_q(retention)}")
                meta_lines.append("  - {" + ", ".join(parts) + "}")
    _write_text(out / "metadata" / "purpose_retention.yaml", "\n".join(meta_lines) + "\n")

    manifest = _manifest(seed, built, gap_counts)
    _write_text(out / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    _write_text(out / "DATASET.md", _dataset_md(manifest))
    return manifest


def _labels_yaml(merged) -> str:
    """Emit the labels file: one `sources` entry per source_key, all its tables merged.

    The labels schema has one positives / not_personal_data list per source_key and every
    entry names its table, so the tables of a SQLite file share one entry.
    """
    lines = [
        "# GROUND TRUTH for the synthetic PS-019 dataset. Generated by",
        "# scripts/generate_synthetic_dataset.py; do not edit by hand.",
        "# Every column of every source is labelled exactly once: either with an official",
        "# class (positives) or as NOT personal data (not_personal_data).",
        "label_origin: team_authored_synthetic",
        "sources:",
    ]
    for source_key in sorted(merged):
        pos_lines, neg_lines = [], []
        for table, positives, negatives in sorted(merged[source_key], key=lambda t: t[0]):
            for column, class_id in sorted(positives):
                pos_lines.append(
                    f"      - {{table: {_q(table)}, column: {_q(column)}, class_id: {_q(class_id)}}}"
                )
            for column in sorted(negatives):
                neg_lines.append(f"      - {{table: {_q(table)}, column: {_q(column)}}}")
        lines.append(f"  - source_key: {_q(source_key)}")
        lines.append("    positives:" if pos_lines else "    positives: []")
        lines += pos_lines
        lines.append("    not_personal_data:" if neg_lines else "    not_personal_data: []")
        lines += neg_lines
    return "\n".join(lines) + "\n"


def _manifest(seed: int, built, gap_counts) -> dict:
    pos_by_class = {c: 0 for c in CLASS_IDS}
    by_group: dict[str, int] = {}
    neg_total = 0
    sources = []
    for source_key, table, chunk, _rows_ in built:
        sources.append({"source_key": source_key, "table": table, "columns": len(chunk), "rows": ROWS_PER_TABLE})
        for _n, label, _k, group in chunk:
            by_group[group] = by_group.get(group, 0) + 1
            if label == NOT_PERSONAL:
                neg_total += 1
            else:
                pos_by_class[label] += 1
    return {
        "generator": "scripts/generate_synthetic_dataset.py",
        "seed": seed,
        "rows_per_table": ROWS_PER_TABLE,
        "tables": len(built),
        "columns_total": sum(s["columns"] for s in sources),
        "positives_total": sum(pos_by_class.values()),
        "positives_by_class": pos_by_class,
        "negatives_total": neg_total,
        "columns_by_group": dict(sorted(by_group.items())),
        "metadata_declarations_for_positive_columns": gap_counts,
        "sources": sorted(sources, key=lambda s: (s["source_key"], s["table"])),
    }


def _dataset_md(m: dict) -> str:
    rows = "\n".join(f"| {c} | {n} |" for c, n in m["positives_by_class"].items())
    groups = "\n".join(f"| {g} | {n} |" for g, n in m["columns_by_group"].items())
    gaps = m["metadata_declarations_for_positive_columns"]
    return f"""# Synthetic PS-019 dataset

Generated by `scripts/generate_synthetic_dataset.py` with seed `{m['seed']}`. Fully synthetic: no
real personal data, no real identifiers, no records derived from real people.

## Size

- Tables: {m['tables']} ({m['rows_per_table']} rows each): 5 CSV files (UTF-8, header row) and 5 tables in the
  SQLite database `app.db` (SQLite is used; no other SQL engine is required).
- Columns: {m['columns_total']} = {m['positives_total']} personal-data columns + {m['negatives_total']} columns labelled `NOT_PERSONAL_DATA`.

| Official class | Labelled columns |
|---|---|
{rows}

| Column group | Columns |
|---|---|
{groups}

## Labelling methodology

1. Every column is drawn from a fixed pool of column specifications; its label is the label of
   the pool it came from (the generator never labels by running the detection rules).
2. A column is labelled with an official class only if it holds the value itself, for example
   an e-mail address, a phone number or an Aadhaar number. Opaque names (for example
   `ph_no`, `uid_no`) that hold such values are labelled with the class (`positive_opaque_name`).
3. Columns that merely mention a keyword but hold something else (`email_opt_in`,
   `phone_model`, `ip_address`, `account_age_days`, `passport_expiry`) are `NOT_PERSONAL_DATA`
   (`negative_name_lookalike`). Columns with innocuous names whose values only resemble
   personal-data formats (`order_ref` with phone-like digits, `tracking_number` with
   Aadhaar-like digits) are `NOT_PERSONAL_DATA` (`negative_value_lookalike`).
4. Everything else is `NOT_PERSONAL_DATA` (`negative_plain`).
5. The pools and labels were written by the team, so results on this dataset describe this
   team-authored synthetic set only and are not general accuracy.

## Files

- `ground_truth/labels.yaml`: every column labelled exactly once (positives with class,
  `not_personal_data` for the rest). Format is read by `fieldledger.evaluation.load_labels`.
- `metadata/purpose_retention.yaml`: declared purpose and retention for personal-data
  columns. One flow-style entry per column: `{{source_key, table, column, purpose, retention}}`.
  A missing `purpose` or `retention` key means not declared; a personal-data column without
  an entry declares neither. Declarations for the {m['positives_total']} personal-data columns:
  {gaps['complete']} complete, {gaps['missing_purpose']} missing purpose, {gaps['missing_retention']} missing retention,
  {gaps['no_entry']} with no entry. (Linking and the gap report are later milestones.)
- `manifest.json`: seed and the counts above.

## Synthetic value rules

E-mail addresses use the reserved `.invalid` TLD; phone, Aadhaar-like, account-like and
card-like numbers start with 0; PAN-like values use an unassigned holder-type letter; PIN
codes start with 0; passport-like values have a 0 second character; IFSC-like values use a
fictional bank code; UPI handles use a fictional bank; coordinates fall in the open South
Pacific; IP and MAC addresses use documentation ranges. Voter-ID-like and driving-licence-like
values use fictional prefixes; a collision with a real identifier cannot be formally excluded
for those two formats, but no value is derived from any real person or record.

## Regenerating

    python scripts/generate_synthetic_dataset.py --out data/synthetic --overwrite

Output is deterministic for a given seed (compare `app.db` logically, not byte-for-byte).
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic PS-019 dataset.")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--overwrite", action="store_true", help="allow writing into a non-empty directory")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists() and any(out.iterdir()) and not args.overwrite:
        print("output directory is not empty; pass --overwrite to regenerate into it", file=sys.stderr)
        return 2
    manifest = generate_dataset(out, args.seed)
    print(
        f"generated {manifest['columns_total']} columns "
        f"({manifest['positives_total']} personal-data, {manifest['negatives_total']} not) "
        f"in {manifest['tables']} tables, seed {manifest['seed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
