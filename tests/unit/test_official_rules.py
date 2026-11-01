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
    # abbreviations and concatenated spellings recognised by the *_ABBREV rules
    ("fname", PI), ("cust_nm", PI), ("d_o_b", PI), ("gndr", PI), ("yrs_old", PI),
    ("ph_no", CI), ("cell_no", CI), ("mob", CI), ("tel_no", CI), ("phonenumber", CI),
    ("dl_no", GI), ("adhar_no", GI), ("firstname", PI), ("fullname", PI),
    ("ifs_cd", FI), ("cc_num", FI), ("bk_acct", FI), ("pay_handle", FI), ("cardno", FI),
    ("addr_ln1", LA), ("res_addr", LA), ("geo_pt", LA), ("postcode", LA),
]


@pytest.mark.parametrize("name, expected", DETECTED)
def test_standard_names_are_detected(ruleset, name, expected):
    assert classes_for(ruleset, name) == {expected}


# Keyword look-alikes that the baseline rules flagged and that now carry a narrow exclusion.
# Each (name -> set()) pair is a deliberate, evidence-based exclusion in config/rules.yaml.
EXCLUDED_LOOKALIKES = [
    "ip_address", "phone_model", "email_opt_in", "account_age_days", "age_of_account",
    "passport_expiry", "pan_india_flag", "upi_enabled", "zip_archive_name", "gender_ratio",
    "mac_address", "web_address", "gps_enabled", "mobile_os", "voter_turnout_pct",
    "address_count", "latitude_precision", "email_bounce_count",
]


@pytest.mark.parametrize("name", EXCLUDED_LOOKALIKES)
def test_excluded_name_lookalikes_are_no_longer_candidates(ruleset, name):
    assert classes_for(ruleset, name) == set()


# Look-alikes with NO exclusion (exclusions are narrow on purpose), so they are still flagged.
# These document the remaining false-positive families; a name-only rule cannot do better
# without a broader, riskier exclusion.
REMAINING_KNOWN_FALSE_POSITIVES = [
    ("name", {PI}),  # exact "name": may hold product names (value-dependent)
    ("email_provider", {CI}), ("phone_type", {CI}), ("address_book_size", {LA}),
    ("aadhaar_verified_flag", {GI}), ("upi_daily_limit", {FI}), ("bluetooth_address", {LA}),
]


@pytest.mark.parametrize("name, expected", REMAINING_KNOWN_FALSE_POSITIVES)
def test_unexcluded_lookalikes_are_still_flagged_as_candidates(ruleset, name, expected):
    assert classes_for(ruleset, name) == expected


AMBIGUOUS = [
    ("email_address", {CI, LA}),  # email + address
    ("pan_card_number", {GI, FI}),  # pan + "card number"
    ("voter_card_no", {GI, FI}),  # voter + "card no"
]


@pytest.mark.parametrize("name, expected", AMBIGUOUS)
def test_ambiguous_names_keep_one_finding_per_class(ruleset, name, expected):
    assert classes_for(ruleset, name) == expected


KNOWN_MISSES = [  # real personal data under an opaque, abbreviated or variant name
    "tax_ref", "forename", "maiden_name", "date_birth", "iban", "cvv", "street_name", "latlng",
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
