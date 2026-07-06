"""Tests for the plants-sheet geography validator.

Exercises `MasterExcelValidator._check_plant_geography_columns` against synthetic DataFrames with
injected reference sets. The plants sheet's ISO3 column carries a bare country or a combined
geo-key (``CHN:CN-HE``); a bare country may instead carry its province in a separate ``geo_unit``
column. The effective unit must compose to a key declared in geo_hierarchy; conflicting forms are
a WARNING (the combined value wins, matching the reader); a blank geo_unit is intentional
country-level and is never flagged.
"""

import pandas as pd

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


def plants_df(**columns):
    """A minimal plants-sheet DataFrame with Plant IDs auto-filled to the column length."""
    length = len(next(iter(columns.values())))
    return pd.DataFrame({"Plant ID": [f"P{i:03d}" for i in range(length)], **columns})


def test_valid_forms_and_blank_geo_unit_pass():
    """Bare countries, combined geo-keys, and column-authored provinces all pass; blank
    geo_unit is intentional country-level, not flagged."""
    validator = make_validator()
    df = plants_df(
        ISO3=["CHN", "DEU", "CHN", "CHN:CN-SH"],
        geo_unit=["CN-HE", None, "", None],
    )

    validator._check_plant_geography_columns(df)

    assert geo_key_errors(validator) == []


def test_flags_combined_key_with_undeclared_unit():
    """A combined ISO3 value whose unit is not declared in geo_hierarchy is flagged."""
    validator = make_validator()
    df = plants_df(ISO3=["CHN:CN-XX"], geo_unit=[None])

    validator._check_plant_geography_columns(df)

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "CHN:CN-XX" in errors[0].message


def test_conflicting_forms_warn_and_combined_wins():
    """A combined ISO3 value plus a different geo_unit column value warns; the combined
    (valid) unit is the one validated."""
    validator = make_validator()
    df = plants_df(ISO3=["CHN:CN-HE"], geo_unit=["CN-SH"])

    validator._check_plant_geography_columns(df)

    issues = geo_key_errors(validator)
    assert len(issues) == 1
    assert issues[0].severity == "WARNING"
    assert "combined ISO3 value wins" in issues[0].message


def test_flags_non_iso_country_code():
    """An ISO3 value that is not a real ISO-3 code is flagged by the independent check."""
    validator = make_validator()
    df = plants_df(ISO3=["ZZZ"], geo_unit=[None])

    validator._check_plant_geography_columns(df)

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "ZZZ" in errors[0].message
    assert errors[0].column_name == "ISO3"


def test_flags_real_but_unmodelled_country():
    """A real ISO-3 code the model does not carry is flagged by the country-mapping cross-check."""
    validator = make_validator()
    df = plants_df(ISO3=["GRL"], geo_unit=[None])

    validator._check_plant_geography_columns(df)

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "country mapping" in errors[0].message


def test_flags_geo_unit_that_does_not_compose_to_declared_key():
    """A populated geo_unit whose composed geo_key is not in geo_hierarchy is flagged."""
    validator = make_validator()
    df = plants_df(ISO3=["CHN"], geo_unit=["CN-XX"])

    validator._check_plant_geography_columns(df)

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "CHN:CN-XX" in errors[0].message
    assert errors[0].column_name == "geo_unit"


def test_flags_geo_unit_composed_against_wrong_country():
    """A declared unit paired with the wrong country's ISO3 composes to an undeclared key."""
    validator = make_validator()
    df = plants_df(ISO3=["DEU"], geo_unit=["CN-HE"])

    validator._check_plant_geography_columns(df)

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "DEU:CN-HE" in errors[0].message


def test_flags_missing_iso3_on_plant_row():
    """A row carrying a Plant ID but no ISO3 is flagged; trailing empties are skipped."""
    validator = make_validator()
    df = pd.DataFrame(
        {
            "Plant ID": ["P001", None],
            "ISO3": [None, None],
            "geo_unit": [None, None],
        },
    )

    validator._check_plant_geography_columns(df)

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert errors[0].row_number == 2


def test_missing_iso3_column_is_a_no_op():
    """An older master without the ISO3 column skips the check entirely (reader enforces it)."""
    validator = make_validator()
    df = pd.DataFrame({"Plant ID": ["P001"], "Country": ["Germany"]})

    validator._check_plant_geography_columns(df)

    assert geo_key_errors(validator) == []


def test_geo_unit_check_soft_degrades_without_hierarchy():
    """Without a prepared geo_hierarchy the sub-national check is skipped; the ISO checks still run."""
    validator = MasterExcelValidator(
        valid_countries=set(VALID_COUNTRIES),
        modelled_countries=set(),
        valid_geo_keys=set(),
    )
    df = plants_df(ISO3=["CHN", "ZZZ"], geo_unit=["CN-XX", None])

    validator._check_plant_geography_columns(df)

    errors = geo_key_errors(validator)
    assert len(errors) == 1
    assert "ZZZ" in errors[0].message
