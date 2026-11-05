"""Tests for the Furnace units sheet geography check.

Exercises `check_furnace_units_geography` (shared between the advisory validator and the
prep-time reader) against synthetic DataFrames with injected reference sets. The sheet carries
the country (``iso3``) and sub-national unit (``geo_unit_or_province``) in separate columns —
there is no combined ``iso3:geo_unit`` form. The composed geo-key must be declared in
geo_hierarchy; a blank ``geo_unit_or_province`` is intentional country-level and never flagged.
"""

import pandas as pd

from steelo.adapters.dataprocessing.master_excel_validator import (
    MasterExcelValidator,
    check_furnace_units_geography,
)

# GRL (Greenland) is a real ISO-3 code that is intentionally NOT modelled, to exercise the cross-check.
VALID_COUNTRIES = {"CHN", "DEU", "USA", "XKX", "GRL"}
MODELLED_COUNTRIES = {"CHN", "DEU", "USA", "XKX"}
VALID_GEO_KEYS = {"CHN:CN-HE", "CHN:CN-SH"}


def furnace_units_df(**columns):
    """A minimal Furnace units DataFrame with plant_ids auto-filled to the column length."""
    length = len(next(iter(columns.values())))
    return pd.DataFrame({"plant_id": [f"P{i:03d}" for i in range(length)], **columns})


def check(df, modelled_countries=MODELLED_COUNTRIES):
    return check_furnace_units_geography(
        df,
        valid_countries=set(VALID_COUNTRIES),
        modelled_countries=set(modelled_countries),
        valid_geo_keys=set(VALID_GEO_KEYS),
    )


def test_valid_countries_and_geo_units_pass():
    """Bare countries and declared geo_units pass; blank geo_unit_or_province is
    intentional country-level, not flagged."""
    df = furnace_units_df(
        iso3=["CHN", "DEU", "CHN"],
        geo_unit_or_province=["CN-HE", None, ""],
    )

    assert check(df) == []


def test_flags_non_iso_country_code():
    """An iso3 value that is not a real ISO-3 code is flagged by the independent check."""
    df = furnace_units_df(iso3=["ZZZ"], geo_unit_or_province=[None])

    errors = check(df)
    assert len(errors) == 1
    assert "ZZZ" in errors[0].message


def test_flags_blank_iso3():
    """A furnace unit row with a plant_id but no iso3 is flagged."""
    df = furnace_units_df(iso3=[None], geo_unit_or_province=[None])

    errors = check(df)
    assert len(errors) == 1
    assert "no iso3" in errors[0].message


def test_flags_unmodelled_country_only_when_cross_check_enabled():
    """A real but unmodelled country is flagged when modelled_countries is provided
    (advisory validator), and skipped when it is empty (prep-time reader)."""
    df = furnace_units_df(iso3=["GRL"], geo_unit_or_province=[None])

    errors = check(df)
    assert len(errors) == 1
    assert "country mapping" in errors[0].message

    assert check(df, modelled_countries=set()) == []


def test_flags_undeclared_geo_unit():
    """A geo_unit_or_province that does not compose to a declared geo-key is flagged —
    including a raw province name where an ISO 3166-2 code is expected."""
    df = furnace_units_df(
        iso3=["CHN", "CHN"],
        geo_unit_or_province=["CN-XX", "Hebei"],
    )

    errors = check(df)
    assert len(errors) == 2
    assert "CHN:CN-XX" in errors[0].message
    assert "CHN:Hebei" in errors[1].message


def test_geo_unit_check_skipped_without_geo_hierarchy():
    """With no geo_hierarchy keys available the sub-national check soft-degrades, while the
    iso3 check still runs."""
    df = furnace_units_df(iso3=["CHN", "ZZZ"], geo_unit_or_province=["CN-XX", None])

    errors = check_furnace_units_geography(
        df,
        valid_countries=set(VALID_COUNTRIES),
        modelled_countries=set(),
        valid_geo_keys=set(),
    )
    assert len(errors) == 1
    assert "ZZZ" in errors[0].message


def test_blank_plant_id_rows_skipped():
    """Trailing empty rows (no plant_id) are never flagged."""
    df = pd.DataFrame(
        {
            "plant_id": [None, ""],
            "iso3": [None, "ZZZ"],
            "geo_unit_or_province": [None, None],
        }
    )

    assert check(df) == []


def test_missing_iso3_column_is_single_error():
    """A sheet without the iso3 column yields one MISSING_COLUMN error, not per-row noise."""
    df = furnace_units_df(geo_unit_or_province=["CN-HE"])

    errors = check(df)
    assert len(errors) == 1
    assert errors[0].error_type == "MISSING_COLUMN"


def test_validator_method_routes_errors_to_report():
    """MasterExcelValidator._validate_furnace_units adds the check's errors to its report."""
    validator = MasterExcelValidator(
        valid_countries=set(VALID_COUNTRIES),
        modelled_countries=set(MODELLED_COUNTRIES),
        valid_geo_keys=set(VALID_GEO_KEYS),
    )
    df = furnace_units_df(iso3=["ZZZ"], geo_unit_or_province=[None])

    validator._validate_furnace_units(df)

    assert validator.report.has_errors()
    assert any("ZZZ" in issue.message for issue in validator.report.errors)
