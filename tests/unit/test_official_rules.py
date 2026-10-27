"""Behaviour of config/rules.yaml on representative column names (official taxonomy).

This table documents what the name-only rules DO, including their known limits. It is not a
claim of accuracy: opaque names are missed and keyword look-alikes are flagged.
"""

from pathlib import Path

import pytest

from fieldledger.classes import load_class_registry
from fieldledger.detector import detect_candidates
from fieldledger.profiles import (
    BroadType,
    ColumnProfile,
    SourceProfile,
    SourceType,
    TableProfile,
    TypeBasis,
)
from fieldledger.rules import load_rule_set

REPO_ROOT = Path(__file__).resolve().parents[2]
PI, CI, GI, FI, LA = (
    "PERSONAL_IDENTITY",
    "CONTACT_INFORMATION",
    "GOVERNMENT_IDENTIFIERS",
    "FINANCIAL_INFORMATION",
    "LOCATION_ADDRESS",
)


@pytest.fixture(scope="module")
def ruleset():
    registry = load_class_registry(REPO_ROOT / "config" / "classes.yaml")
    return load_rule_set(REPO_ROOT / "config" / "rules.yaml", registry)


def classes_for(ruleset, column_name):
    profile = SourceProfile(
        SourceType.CSV,
        "s.csv",
        TypeBasis.OBSERVED_VALUES,
        (TableProfile("t", (ColumnProfile(column_name, 1, BroadType.STRING, 1, 0),), 1),),
    )
    return {f.class_id for f in detect_candidates(profile, ruleset, source_key="s.csv")}


DETECTED = [  # name -> the single class expected
    ("full_name", PI), ("Surname", PI), ("name", PI), ("DOB", PI), ("birthDate", PI),
    ("date_of_birth", PI), ("gender", PI), ("customer_age", PI),
    ("email", CI), ("e_mail", CI), ("mobile_no", CI), ("Phone Number", CI),
    ("emergency_contact_number", CI), ("msisdn", CI),
    ("aadhaar_number", GI), ("PAN", GI), ("passport_no", GI), ("voter_id", GI),
    ("driving_licence_number", GI), ("driver_license_no", GI),
    ("bank_account_number", FI), ("account_no", FI), ("IFSC_code", FI), ("cardNumber", FI),
    ("credit_card", FI), ("upi_id", FI), ("vpa", FI),
    ("home_address", LA), ("pincode", LA), ("pin_code", LA), ("postal_code", LA),
    ("zip", LA), ("latitude", LA), ("gps_lat", LA), ("geolocation", LA),
]


@pytest.mark.parametrize("name, expected", DETECTED)
def test_standard_names_are_detected(ruleset, name, expected):
    assert classes_for(ruleset, name) == {expected}


KNOWN_FALSE_POSITIVES = [  # keyword in the name, but the column is not personal data
    ("ip_address", {LA}), ("phone_model", {CI}), ("email_opt_in", {CI}),
    ("account_age_days", {PI}), ("age_of_account", {PI}), ("passport_expiry", {GI}),
    ("pan_india_flag", {GI}), ("upi_enabled", {FI}), ("zip_archive_name", {LA}),
    ("gender_ratio", {PI}),
]


@pytest.mark.parametrize("name, expected", KNOWN_FALSE_POSITIVES)
def test_known_name_lookalikes_are_flagged_as_candidates(ruleset, name, expected):
    assert classes_for(ruleset, name) == expected


AMBIGUOUS = [
    ("email_address", {CI, LA}),  # email + address
    ("pan_card_number", {GI, FI}),  # pan + "card number"
    ("voter_card_no", {GI, FI}),  # voter + "card no"
]


@pytest.mark.parametrize("name, expected", AMBIGUOUS)
def test_ambiguous_names_keep_one_finding_per_class(ruleset, name, expected):
    assert classes_for(ruleset, name) == expected


KNOWN_MISSES = [  # real personal data under an opaque or abbreviated name
    "fname", "cust_nm", "d_o_b", "gndr", "yrs_old", "ph_no", "em_id", "cell_no", "mob",
    "uid_no", "dl_no", "epic_no", "acc_num", "cc_num", "ifs_cd", "pin", "addr_ln1", "geo_pt",
    "col_17",
]


@pytest.mark.parametrize("name", KNOWN_MISSES)
def test_opaque_names_are_not_detected(ruleset, name):
    assert classes_for(ruleset, name) == set()


TRUE_NEGATIVES = [
    "order_id", "order_ref", "tracking_number", "invoice_code", "created_at", "status",
    "contact_preference", "account_status", "product_name", "license_key", "city_name",
    "card_type", "card_expiry", "channel",
]


@pytest.mark.parametrize("name", TRUE_NEGATIVES)
def test_unrelated_names_are_not_flagged(ruleset, name):
    assert classes_for(ruleset, name) == set()


def test_matching_is_whole_token_not_substring(ruleset):
    for name in ("manage", "package", "passage", "pancake", "message_text", "agenda", "pinned"):
        assert classes_for(ruleset, name) == set(), name
