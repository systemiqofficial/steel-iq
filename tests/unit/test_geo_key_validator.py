"""Tests for the master-Excel geo-key validator.

Exercises `MasterExcelValidator._check_geo_key_column` against synthetic DataFrames with injected
reference sets. The iso3 part of each value is cross-checked twice — against the independent ISO
authority (`pycountry`-derived `valid_countries`) and against the model's `modelled_countries` — and a
full geo-key is checked against the recognised sub-national `valid_geo_keys`. Checks whose reference
data is unavailable degrade to a no-op. A guarded regression confirms the validator runs clean on the
real master Excel (the acceptance gate).
"""

from pathlib import Path

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.master_excel_validator import MasterExcelValidator

# GRL (Greenland) is a real ISO-3 code that is intentionally NOT modelled, to exercise the cross-check.
VALID_COUNTRIES = {"CHN", "DEU", "USA", "XKX", "GRL"}
MODELLED_COUNTRIES = {"CHN", "DEU", "USA", "XKX"}
VALID_GEO_KEYS = {"CHN:CN-HE", "CHN:CN-SH"}


def make_validator():
    """Build a validator with all reference sets injected (no prepared-data dependency)."""
    return MasterExcelValidator(
        valid_countries=set(VALID_COUNTRIES),
        modelled_countries=set(MODELLED_COUNTRIES),
        valid_geo_keys=set(VALID_GEO_KEYS),
    )


def geo_key_errors(validator):
    """The INVALID_GEO_KEY issues collected on the validator's report."""
    return [issue for issue in validator.report.all_issues() if issue.error_type == "INVALID_GEO_KEY"]


def test_passes_recognised_country_and_geo_key():
    """Bare modelled countries and recognised sub-national geo-keys all pass."""
    validator = make_validator()
    df = pd.DataFrame({"ISO-3 code": ["CHN", "DEU", "XKX", "CHN:CN-HE", "CHN:CN-SH"]})

    validator._check_geo_key_column(df, "Input costs", "ISO-3 code")

    assert geo_key_errors(validator) == []


def test_flags_non_iso_code():
    """A code that is not a real ISO-3 code is flagged by the independent (pycountry) check."""
    validator = make_validator()
    df = pd.DataFrame({"ISO-3 code": ["CHN", "ZZZ"]})

    validator._check_geo_key_column(df, "Input costs", "ISO-3 code")

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "ZZZ" in errors[0].message
    assert "ISO-3" in errors[0].message
    assert errors[0].severity == "ERROR"


def test_flags_real_but_unmodelled_country():
    """A real ISO-3 code that the model does not carry is flagged by the country-mapping cross-check."""
    validator = make_validator()
    df = pd.DataFrame({"ISO-3 code": ["GRL"]})

    validator._check_geo_key_column(df, "Input costs", "ISO-3 code")

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "country mapping" in errors[0].message


def test_flags_unknown_province():
    """A geo-key with a modelled country but an unrecognised sub-national unit is flagged."""
    validator = make_validator()
    df = pd.DataFrame({"ISO-3 code": ["CHN:CN-XX"]})

    validator._check_geo_key_column(df, "Input costs", "ISO-3 code")

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "CHN:CN-XX" in errors[0].message
    assert "geo_hierarchy" in errors[0].message


def test_flags_geo_key_with_non_iso_country():
    """A geo-key whose iso3 part is not a real ISO-3 code is flagged."""
    validator = make_validator()
    df = pd.DataFrame({"ISO-3 code": ["XX:CN-HE"]})

    validator._check_geo_key_column(df, "Input costs", "ISO-3 code")

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "XX" in errors[0].message


def test_flags_bare_subnational_code_without_iso3_prefix():
    """A bare sub-national code (no iso3 prefix, no colon) is not a valid ISO-3 code, so it is flagged."""
    validator = make_validator()
    df = pd.DataFrame({"ISO-3 code": ["CN-HE"]})

    validator._check_geo_key_column(df, "Input costs", "ISO-3 code")

    assert len(geo_key_errors(validator)) == 1


def test_ignores_blank_and_missing_values():
    """Blank cells and NaNs are skipped, not flagged."""
    validator = make_validator()
    df = pd.DataFrame({"ISO-3 code": ["CHN", None, "", "  "]})

    validator._check_geo_key_column(df, "Input costs", "ISO-3 code")

    assert geo_key_errors(validator) == []


def test_missing_column_is_not_flagged_here():
    """A missing geo-key column is reported elsewhere, not by the geo-key check."""
    validator = make_validator()
    df = pd.DataFrame({"Some other column": ["CHN"]})

    validator._check_geo_key_column(df, "Input costs", "ISO-3 code")

    assert geo_key_errors(validator) == []


def test_soft_degrades_when_prepared_data_unavailable():
    """Without prepared data, only the independent ISO check runs; modelled and sub-national are skipped."""
    validator = MasterExcelValidator(
        valid_countries=set(VALID_COUNTRIES),
        modelled_countries=set(),
        valid_geo_keys=set(),
    )
    df = pd.DataFrame({"ISO-3 code": ["GRL", "CHN:CN-XX", "ZZZ"]})

    validator._check_geo_key_column(df, "Input costs", "ISO-3 code")

    # GRL (real ISO) and CHN:CN-XX (real iso3) pass; only the non-ISO ZZZ is flagged.
    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "ZZZ" in errors[0].message


MASTER_EXCEL = Path(__file__).resolve().parents[2] / "master_input" / "master_input_v2.1_mini.xlsx"


@pytest.mark.skipif(not MASTER_EXCEL.exists(), reason="master Excel not present in this checkout")
def test_clean_on_master_excel():
    """Acceptance gate: the validator reports zero INVALID_GEO_KEY on the real master Excel."""
    validator = MasterExcelValidator()
    report = validator.validate_file(MASTER_EXCEL)

    geo_errors = [issue for issue in report.all_issues() if issue.error_type == "INVALID_GEO_KEY"]
    assert geo_errors == [], f"unexpected geo-key errors on master: {geo_errors}"
