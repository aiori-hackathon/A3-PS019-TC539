"""Detection-quality guards for config/rules.yaml (abbreviations, look-alikes, ambiguity).

These tests protect FieldLedger's name-only detection from silent regressions. They are NOT an
accuracy claim: the benchmark is a team-authored synthetic set, and the abbreviation aliases and
exclusions were written after inspecting it (in-sample). Real-world accuracy is unmeasured.
"""

from pathlib import Path

import pytest

from fieldledger.classes import load_class_registry
from fieldledger.detector import ambiguous_columns, detect_candidates, explain_column
from fieldledger.discovery import discover_sources
from fieldledger.evaluation import evaluate, load_labels
from fieldledger.findings import source_key_for
from fieldledger.profiler import profile_source
from fieldledger.profiles import (
    BroadType,
    ColumnProfile,
    SourceProfile,
    SourceType,
    TableProfile,
    TypeBasis,
)
from fieldledger.rules import load_rule_set, tokenize_name

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "data" / "synthetic"
PI, CI, GI, FI, LA = (
    "PERSONAL_IDENTITY",
    "CONTACT_INFORMATION",
    "GOVERNMENT_IDENTIFIERS",
    "FINANCIAL_INFORMATION",
    "LOCATION_ADDRESS",
)
OFFICIAL_IDS = [PI, CI, GI, FI, LA]
RULE_PREFIX = {PI: "RULE_PI_", CI: "RULE_CI_", GI: "RULE_GI_", FI: "RULE_FI_", LA: "RULE_LA_"}


@pytest.fixture(scope="module")
def registry():
    return load_class_registry(REPO_ROOT / "config" / "classes.yaml")


@pytest.fixture(scope="module")
def ruleset(registry):
    return load_rule_set(REPO_ROOT / "config" / "rules.yaml", registry)


@pytest.fixture(scope="module")
def baseline_ruleset(registry):
    return load_rule_set(REPO_ROOT / "tests" / "fixtures" / "rules_baseline_d3ab9c2.yaml", registry)


def profile_for(*names):
    cols = tuple(ColumnProfile(n, i + 1, BroadType.STRING, 1, 0) for i, n in enumerate(names))
    return SourceProfile(
        SourceType.CSV, "s.csv", TypeBasis.OBSERVED_VALUES, (TableProfile("t", cols, 1),)
    )


def findings_for(ruleset, name):
    return detect_candidates(profile_for(name), ruleset, source_key="s.csv")


def classes_for(ruleset, name):
    return {f.class_id for f in findings_for(ruleset, name)}


def scan_benchmark(ruleset):
    profiles, findings = {}, []
    for path in discover_sources(DATASET):
        key = source_key_for(path, DATASET)
        profiles[key] = profile_source(path, DATASET)
        findings.extend(detect_candidates(profiles[key], ruleset, source_key=key))
    return profiles, findings


@pytest.fixture(scope="module")
def bench(registry, ruleset):
    profiles, findings = scan_benchmark(ruleset)
    labels = load_labels(DATASET / "ground_truth" / "labels.yaml", registry)
    return profiles, findings, labels, evaluate(findings, labels, profiles, registry=registry, ruleset=ruleset)


@pytest.fixture(scope="module")
def bench_baseline(baseline_ruleset):
    return scan_benchmark(baseline_ruleset)[1]


def truth_of(labels):
    positives = {(s.source_key, t, c): cls for s in labels.sources for (t, c, cls) in s.positives}
    return positives


# ------------------------------------------------------------------ taxonomy and rule ids


def test_the_five_taxonomy_ids_are_unchanged_and_in_order(registry):
    assert list(registry.ids()) == OFFICIAL_IDS


def test_every_rule_targets_an_official_class_and_its_id_names_that_class(ruleset):
    for rule in ruleset.rules:
        assert rule.class_id in OFFICIAL_IDS
        assert rule.id.startswith(RULE_PREFIX[rule.class_id]), rule.id


PROVISIONAL_RULES = {
    "RULE_CI_CONTACT_GENERIC_PROVISIONAL",
    "RULE_CI_EMAIL_ABBREV_PROVISIONAL",
    "RULE_FI_ACCOUNT_ABBREV_PROVISIONAL",
    "RULE_GI_AADHAAR_ABBREV_PROVISIONAL",
    "RULE_GI_PASSPORT_ABBREV_PROVISIONAL",
    "RULE_GI_VOTER_ABBREV_PROVISIONAL",
    "RULE_LA_COORDS_PROVISIONAL",
    "RULE_LA_PIN_PROVISIONAL",
}


def test_ambiguous_abbreviation_rules_stay_marked_provisional(ruleset):
    """Adding or removing a provisional rule must be a deliberate edit of this list."""
    got = {r.id for r in ruleset.rules if r.id.endswith("_PROVISIONAL")}
    assert got == PROVISIONAL_RULES
    assert ruleset.rules_status == "provisional"


PROVISIONAL_CASES = [
    ("pin", LA, "RULE_LA_PIN_PROVISIONAL"),
    ("uid_no", GI, "RULE_GI_AADHAAR_ABBREV_PROVISIONAL"),
    ("em_id", CI, "RULE_CI_EMAIL_ABBREV_PROVISIONAL"),
    ("acc_num", FI, "RULE_FI_ACCOUNT_ABBREV_PROVISIONAL"),
    ("ppt_no", GI, "RULE_GI_PASSPORT_ABBREV_PROVISIONAL"),
    ("epic_no", GI, "RULE_GI_VOTER_ABBREV_PROVISIONAL"),
    ("loc_coords", LA, "RULE_LA_COORDS_PROVISIONAL"),
    ("contact_details", CI, "RULE_CI_CONTACT_GENERIC_PROVISIONAL"),
]


@pytest.mark.parametrize("name, cls, rule_id", PROVISIONAL_CASES)
def test_ambiguous_abbreviations_are_candidates_explained_by_a_provisional_rule(
    ruleset, name, cls, rule_id
):
    (finding,) = findings_for(ruleset, name)
    assert finding.class_id == cls
    assert finding.rule_ids == (rule_id,)


@pytest.mark.parametrize(
    "name", ["atm_pin", "pin_hash", "pin_attempts", "pinned", "login_pin", "otp_pin", "pin_no"]
)
def test_pin_is_matched_only_as_a_bare_name_never_inside_other_names(ruleset, name):
    """A secret PIN column must not be reported as a postal PIN code."""
    assert classes_for(ruleset, name) == set()


def test_pin_code_is_explained_by_the_postal_rule_not_the_provisional_one(ruleset):
    (finding,) = findings_for(ruleset, "pin_code")
    assert finding.rule_ids == ("RULE_LA_POSTAL_CODE",)


# ------------------------------------------------------------------ abbreviations: positives


ABBREVIATIONS = [
    # (name, class, rule id that must explain it)
    ("ph_no", CI, "RULE_CI_PHONE_ABBREV"), ("tel_num", CI, "RULE_CI_PHONE_ABBREV"),
    ("cell_num", CI, "RULE_CI_PHONE_ABBREV"), ("mobile_no", CI, "RULE_CI_PHONE"),
    ("mob", CI, "RULE_CI_PHONE_ABBREV_EXACT"), ("phonenumber", CI, "RULE_CI_PHONE_ABBREV"),
    ("d_o_b", PI, "RULE_PI_DOB_ABBREV"), ("dt_of_birth", PI, "RULE_PI_DOB_ABBREV"),
    ("dateofbirth", PI, "RULE_PI_DOB_ABBREV"), ("cust_nm", PI, "RULE_PI_NAME_ABBREV"),
    ("fname", PI, "RULE_PI_NAME_ABBREV"), ("lastname", PI, "RULE_PI_NAME_ABBREV"),
    ("gndr", PI, "RULE_PI_GENDER_ABBREV"), ("years_old", PI, "RULE_PI_AGE_ABBREV"),
    ("adhar_no", GI, "RULE_GI_AADHAAR_ABBREV"), ("dl_no", GI, "RULE_GI_LICENCE_ABBREV"),
    ("drv_lic_no", GI, "RULE_GI_LICENCE_ABBREV"), ("pspt_no", GI, "RULE_GI_PASSPORT_ABBREV"),
    ("ifs_cd", FI, "RULE_FI_IFSC_ABBREV"), ("bk_acct", FI, "RULE_FI_BANK_ACCOUNT_ABBREV"),
    ("acct_number", FI, "RULE_FI_BANK_ACCOUNT_ABBREV"), ("cc_num", FI, "RULE_FI_CARD_ABBREV"),
    ("cardno", FI, "RULE_FI_CARD_ABBREV"), ("pay_handle", FI, "RULE_FI_UPI_ABBREV"),
    ("addr_ln1", LA, "RULE_LA_ADDRESS_ABBREV"), ("res_addr", LA, "RULE_LA_ADDRESS_ABBREV"),
    ("geo_pt", LA, "RULE_LA_GEO_ABBREV"), ("post_code", LA, "RULE_LA_POSTAL_ABBREV"),
]


@pytest.mark.parametrize("name, cls, rule_id", ABBREVIATIONS)
def test_legitimate_abbreviations_are_detected_and_explained_by_the_expected_rule(
    ruleset, name, cls, rule_id
):
    (finding,) = findings_for(ruleset, name)
    assert finding.class_id == cls
    assert finding.rule_ids == (rule_id,)


# ------------------------------------------------------------------ abbreviations: negatives


ABBREVIATION_LOOKALIKES = [
    "ph_level", "mob_spawn_rate", "mob_app_version", "dl_speed_kbps", "cell_voltage", "cell_count",
    "x_coordinate", "screen_coordinates", "pixel_coords", "acc_x", "epic_story_points", "ppt_slides",
    "uid", "tel_aviv_office", "file_name", "pdf_name", "stage_name_id", "account_status",
]


@pytest.mark.parametrize("name", ABBREVIATION_LOOKALIKES)
def test_abbreviation_aliases_do_not_match_unrelated_names(ruleset, name):
    assert classes_for(ruleset, name) == set()


def test_matching_stays_whole_token_for_the_new_aliases(ruleset):
    for name in ("addressed", "adder_id", "mobster", "cellar_id", "pinkcode", "ccnumber_x_y_z"):
        assert not [m for m in explain_column(name, ruleset) if not m.suppressed], name


# ------------------------------------------------------------------ look-alike exclusions


@pytest.mark.parametrize(
    "name",
    [
        "phone_model", "gps_enabled", "ip_address", "mac_address", "web_address", "age_of_account",
        "passport_expiry", "pan_india_flag", "address_type", "mobile_app_version",
    ],
)
def test_investigated_lookalikes_are_not_candidates(ruleset, name):
    assert classes_for(ruleset, name) == set()


@pytest.mark.parametrize(
    "name, cls",
    [
        ("home_address", LA), ("address", LA), ("billing_address", LA), ("postal_address", LA),
        ("phone", CI), ("phone_number", CI), ("mobile", CI), ("age", PI), ("customer_age", PI),
        ("passport_no", GI), ("passport", GI), ("gps_lat", LA), ("gps", LA),
        ("zip_code", LA), ("voter_id", GI), ("email", CI), ("gender", PI), ("upi_id", FI),
    ],
)
def test_exclusions_do_not_remove_the_true_columns_they_sit_next_to(ruleset, name, cls):
    assert classes_for(ruleset, name) == {cls}


@pytest.mark.parametrize("name, cls", [("email_count", CI), ("phone_count", CI), ("age_count", PI), ("gps_count", LA)])
def test_count_is_not_a_blanket_exclusion(ruleset, name, cls):
    """Only enumerated phrases (for example 'address count') are excluded, never the word 'count'."""
    assert classes_for(ruleset, name) == {cls}


def test_network_address_exclusion_covers_the_abbreviated_rule_too(ruleset):
    by_id = {r.id: r for r in ruleset.rules}
    full = {p[:-1] for p in by_id["RULE_LA_ADDRESS"].exclude_patterns if p[-1] == "address"}
    abbr = {p[:-1] for p in by_id["RULE_LA_ADDRESS_ABBREV"].exclude_patterns if p[-1] == "addr"}
    for kind in (("ip",), ("mac",), ("web",), ("url",), ("server",), ("host",)):
        assert kind in full and kind in abbr, kind


def test_every_exclusion_is_live_and_suppresses_its_own_rule(ruleset):
    """Each exclusion phrase, used as a column name, must match its rule and be suppressed.

    Catches dead exclusions (typos, phrases the rule can never match) when rules are edited.
    """
    for rule in ruleset.rules:
        for phrase in rule.exclude_patterns:
            assert len(phrase) >= 2
            name = "_".join(phrase)
            matches = {m.rule_id: m for m in explain_column(name, ruleset)}
            assert rule.id in matches, f"{rule.id} cannot match its own exclusion {name!r}"
            assert matches[rule.id].suppressed, f"{rule.id} not suppressed for {name!r}"
            assert phrase in matches[rule.id].excluded_by


# ------------------------------------------------------------------ ambiguity is preserved


AMBIGUOUS = [
    ("email_address", {CI, LA}),
    ("pan_card_number", {GI, FI}),
    ("voter_card_no", {GI, FI}),
    ("eml_addr", {CI, LA}),  # same behaviour as email_address, by design (not special-cased)
]


@pytest.mark.parametrize("name, expected", AMBIGUOUS)
def test_ambiguous_names_keep_one_finding_per_class(ruleset, name, expected):
    assert classes_for(ruleset, name) == expected


def test_the_benchmark_ambiguous_columns_are_unchanged(bench, bench_baseline):
    _, findings, _, _ = bench
    now = {(a.column_name, a.class_ids) for a in ambiguous_columns(findings)}
    before = {(a.column_name, a.class_ids) for a in ambiguous_columns(bench_baseline)}
    assert now == before
    assert {c for c, _ in now} == {"email_address", "pan_card_number", "voter_card_no"}


# ------------------------------------------------------------------ benchmark regression


def test_per_class_benchmark_floors(bench):
    """Floors sit below the measured in-sample values; they catch regressions, not prove accuracy."""
    *_, report = bench
    assert [m.class_id for m in report.per_class] == OFFICIAL_IDS
    for m in report.per_class:
        assert m.precision >= 0.90, (m.class_id, m.precision)
        assert m.recall >= 0.95, (m.class_id, m.recall)


def test_every_true_positive_found_by_the_baseline_rules_is_still_found(bench, bench_baseline):
    _, findings, labels, _ = bench
    truth = truth_of(labels)
    old_tp = {f.identity for f in bench_baseline if truth.get(f.column_identity) == f.class_id}
    new_ids = {f.identity for f in findings}
    assert len(old_tp) == 114
    assert old_tp <= new_ids, sorted(old_tp - new_ids)


def test_no_new_false_positive_is_introduced_on_the_benchmark(bench, bench_baseline):
    _, findings, labels, _ = bench
    truth = truth_of(labels)

    def fps(items):
        return {f.identity for f in items if truth.get(f.column_identity) != f.class_id}

    assert fps(findings) <= fps(bench_baseline)
    assert len(fps(findings)) == 4  # email_address(LA), pan_card_number(FI), voter_card_no(FI), name


def test_the_remaining_benchmark_false_negative_is_the_genuinely_opaque_one(bench):
    _, findings, labels, _ = bench
    truth = truth_of(labels)
    found = {f.identity for f in findings}
    missed = {(k, c) for k, c in truth.items() if (*k, c) not in found}
    assert [k[2] for k, _ in missed] == ["tax_ref"]


def test_benchmark_findings_are_sorted_deterministic_and_use_known_rule_ids(bench, ruleset):
    profiles, findings, _, _ = bench
    again = scan_benchmark(ruleset)[1]
    assert [f.to_dict() for f in findings] == [f.to_dict() for f in again]
    known = set(ruleset.rule_ids())
    assert all(set(f.rule_ids) <= known for f in findings)
    assert len(findings) == 142


# ------------------------------------------------------------------ explanation consistency


def all_benchmark_names(labels):
    names = {c for s in labels.sources for (_, c, _) in s.positives}
    names |= {c for s in labels.sources for (_, c) in (s.not_personal_data or ())}
    return sorted(names)


def test_rule_ids_on_findings_match_the_explanation_for_every_benchmark_column(bench, ruleset):
    _, _, labels, _ = bench
    names = all_benchmark_names(labels)
    assert len(names) > 200
    for name in names:
        tokens = tokenize_name(name)
        explained = explain_column(name, ruleset)
        by_class = {}
        for m in explained:
            if not m.suppressed:
                by_class.setdefault(m.class_id, set()).add(m.rule_id)
        got = {f.class_id: set(f.rule_ids) for f in findings_for(ruleset, name)}
        assert got == by_class, name
        for m in explained:
            for pattern in m.matched_patterns:  # the reported pattern really occurs in the name
                assert any(tokens[i : i + len(pattern)] == pattern for i in range(len(tokens))), name
            for phrase in m.excluded_by:  # so does the reported exclusion
                assert any(tokens[i : i + len(phrase)] == phrase for i in range(len(tokens))), name
        assert all(m.rule_id in set(ruleset.rule_ids()) for m in explained)


def test_a_suppressed_rule_never_appears_on_a_finding(ruleset):
    for name in ("phone_model", "ip_address", "gps_enabled", "passport_expiry", "age_of_account"):
        suppressed = {m.rule_id for m in explain_column(name, ruleset) if m.suppressed}
        assert suppressed, name
        used = {r for f in findings_for(ruleset, name) for r in f.rule_ids}
        assert not suppressed & used, name


def test_explanation_names_the_exclusion_that_removed_a_lookalike(ruleset):
    (match,) = [m for m in explain_column("phone_model", ruleset) if m.rule_id == "RULE_CI_PHONE"]
    assert match.suppressed and match.excluded_by == (("phone", "model"),)
    assert match.matched_patterns == (("phone",),)
