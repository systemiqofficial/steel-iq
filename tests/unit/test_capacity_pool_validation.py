"""Cross-row validation tests for the capacity pool sheets.

Errors are authoring mistakes that fail data preparation; warnings are content
gaps (unauthored classification flags) that preparation reports and tolerates.
Reference sets are injected throughout, so no prepared data is needed.
"""

import tempfile
from pathlib import Path

import pandas as pd

from steelo.adapters.dataprocessing.master_excel_validator import MasterExcelValidator
from steelo.capacity_policy.inputs import OpeningCreditRow, RegionRow, TechnologyRow
from steelo.capacity_policy.validation import (
    validate_opening_credits,
    validate_provinces,
    validate_technologies,
)

ROSTER = {"BF", "EAF", "DRI"}
VOCABULARY = {"Coal", "Hydrogen"}
GEO_KEYS = {"CHN:CN-HE", "CHN:CN-SD"}


def region(geo_key, region_name=None, type=None, from_year=None):
    return RegionRow(geo_key=geo_key, region_name=region_name, type=type, from_year=from_year)


def tech(
    technology,
    product="iron",
    reductant=None,
    is_emission_intense=None,
    switching_to=None,
    swap_ratio=None,
):
    return TechnologyRow(
        technology=technology,
        product=product,
        reductant=reductant,
        is_emission_intense=is_emission_intense,
        switching_to=switching_to,
        swap_ratio=swap_ratio,
    )


def credit(geo_key="CHN:CN-HE", capacity_mt=1.0, product="iron", technology=None, vintage_year=2020):
    return OpeningCreditRow(
        vintage_year=vintage_year,
        capacity_mt=capacity_mt,
        geo_key=geo_key,
        product=product,
        technology=technology,
        plant_group_id=None,
    )


def errors(issues):
    return [issue.message for issue in issues if issue.severity == "error"]


def warnings(issues):
    return [issue.message for issue in issues if issue.severity == "warning"]


# ---------------- provinces


def test_provinces_valid_sheet_passes():
    """Key, exempt and blank (non-key) rows covering every unit exactly once pass."""
    rows = [region("CHN:CN-HE", "Jing-Jin-Ji", "key"), region("CHN:CN-SD", "Shandong")]
    assert validate_provinces(rows, chinese_geo_keys=GEO_KEYS) == []


def test_provinces_completeness_and_uniqueness():
    """A missing unit, an unknown unit, and a duplicate are all errors."""
    rows = [
        region("CHN:CN-HE", "Jing-Jin-Ji", "key"),
        region("CHN:CN-HE", "Jing-Jin-Ji", "key"),
        region("CHN:CN-XX", "Nowhere"),
    ]
    messages = errors(validate_provinces(rows, chinese_geo_keys=GEO_KEYS))
    assert any("missing: CHN:CN-SD" in m for m in messages)
    assert any("unknown geo_key(s): CHN:CN-XX" in m for m in messages)
    assert any("CHN:CN-HE appears 2 times" in m for m in messages)


def test_provinces_bad_type_and_missing_cluster_name():
    """An unknown type value and a key province without a cluster name are errors."""
    rows = [region("CHN:CN-HE", None, "key"), region("CHN:CN-SD", "Shandong", "core")]
    messages = errors(validate_provinces(rows, chinese_geo_keys=GEO_KEYS))
    assert any("key province CHN:CN-HE needs a region_name" in m for m in messages)
    assert any("got 'core'" in m for m in messages)


# ---------------- technologies


def test_technologies_valid_sheet_passes():
    """Fully-flagged classification rows and a well-formed override row pass."""
    rows = [
        tech("BF", is_emission_intense=True),
        tech("EAF", product="steel", is_emission_intense=False),
        tech("DRI", reductant="Coal", is_emission_intense=True),
        tech("DRI", reductant="Hydrogen", is_emission_intense=False),
        tech("DRI"),  # delegation row: flags live on the reductant rows
        tech("BF", product=None, switching_to="EAF", swap_ratio=1.0),
    ]
    assert validate_technologies(rows, technology_roster=ROSTER, reductant_vocabulary=VOCABULARY) == []


def test_technologies_unknown_names_error():
    """Unknown technology and reductant names are errors — the sheets must not be retyped."""
    rows = [
        tech("BLASTFURNACE", is_emission_intense=True),
        tech("DRI", reductant="Charcoal", is_emission_intense=True),
        tech("BF", product=None, switching_to="EFA", swap_ratio=1.0),
    ]
    messages = errors(validate_technologies(rows, technology_roster=ROSTER, reductant_vocabulary=VOCABULARY))
    assert any("unknown technology 'BLASTFURNACE'" in m for m in messages)
    assert any("unknown reductant 'Charcoal'" in m for m in messages)
    assert any("unknown technology 'EFA'" in m for m in messages)


def test_technologies_wildcard_only_on_override_rows():
    """'*' as a classification technology is an error."""
    rows = [tech("*", is_emission_intense=True)]
    messages = errors(validate_technologies(rows, technology_roster=ROSTER, reductant_vocabulary=VOCABULARY))
    assert any("'*' is only allowed on override rows" in m for m in messages)


def test_technologies_override_row_constraints():
    """Override rows must carry a positive ratio and no classification flags."""
    rows = [
        tech("BF", product=None, is_emission_intense=True, switching_to="EAF", swap_ratio=1.0),
        tech("DRI", product=None, switching_to="EAF"),
        tech("EAF", product=None, switching_to="BF", swap_ratio=-1.0),
    ]
    messages = errors(validate_technologies(rows, technology_roster=ROSTER, reductant_vocabulary=VOCABULARY))
    assert any("'BF' -> 'EAF' must not carry classification flags" in m for m in messages)
    assert any("'DRI' -> 'EAF' needs a positive swap_ratio, got None" in m for m in messages)
    assert any("'EAF' -> 'BF' needs a positive swap_ratio, got -1.0" in m for m in messages)


def test_technologies_equal_specificity_collision():
    """`BF -> *` and `* -> EAF` are equally specific for BF -> EAF, so together they error."""
    rows = [
        tech("BF", product=None, switching_to="*", swap_ratio=1.0),
        tech("*", product=None, switching_to="EAF", swap_ratio=1.0),
    ]
    messages = errors(validate_technologies(rows, technology_roster=ROSTER, reductant_vocabulary=VOCABULARY))
    assert any("collide at equal specificity" in m and "'BF' -> 'EAF'" in m for m in messages)


def test_technologies_duplicate_rows_error():
    """Duplicate classification keys and duplicate override keys are errors."""
    rows = [
        tech("BF", is_emission_intense=True),
        tech("BF", is_emission_intense=False),
        tech("DRI", product=None, switching_to="EAF", swap_ratio=1.0),
        tech("DRI", product=None, switching_to="EAF", swap_ratio=1.2),
    ]
    messages = errors(validate_technologies(rows, technology_roster=ROSTER, reductant_vocabulary=VOCABULARY))
    assert any("duplicate classification rows for technology 'BF'" in m for m in messages)
    assert any("duplicate override rows for 'DRI' -> 'EAF'" in m for m in messages)


def test_technologies_unauthored_flags_warn_not_error():
    """A missing flag warns (fixtures still build); delegation rows and covered techs do not."""
    rows = [
        tech("BF"),  # unauthored
        tech("EAF", product="steel"),  # unauthored
        tech("DRI"),  # delegation row — reductant rows below carry the flag
        tech("DRI", reductant="Coal", is_emission_intense=True),
        tech("DRI", reductant="Hydrogen", is_emission_intense=False),
    ]
    issues = validate_technologies(rows, technology_roster=ROSTER, reductant_vocabulary=VOCABULARY)
    assert errors(issues) == []
    messages = warnings(issues)
    assert len(messages) == 2
    assert any("unauthored is_emission_intense for technology 'BF'" in m for m in messages)
    assert any("unauthored is_emission_intense for technology 'EAF'" in m for m in messages)


def test_technologies_missing_roster_technology_warns():
    """A roster technology with no classification row at all is a content gap."""
    rows = [tech("BF", is_emission_intense=True)]
    messages = warnings(validate_technologies(rows, technology_roster=ROSTER, reductant_vocabulary=VOCABULARY))
    assert any("technology 'DRI' has no classification row" in m for m in messages)
    assert any("technology 'EAF' has no classification row" in m for m in messages)


# ---------------- opening credits


def test_opening_credits_valid_rows_pass():
    """Positive amounts, known products, roster technologies and Chinese units pass."""
    rows = [credit(), credit(geo_key="CHN:CN-SD", product="steel", technology="EAF")]
    assert validate_opening_credits(rows, technology_roster=ROSTER, chinese_geo_keys=GEO_KEYS) == []


def test_opening_credits_bad_rows_error():
    """Non-positive amounts, bad products, unknown technologies and geo_keys are errors."""
    rows = [
        credit(capacity_mt=0.0),
        credit(product="pig iron"),
        credit(technology="*"),
        credit(geo_key="CHN:CN-XX"),
    ]
    messages = errors(validate_opening_credits(rows, technology_roster=ROSTER, chinese_geo_keys=GEO_KEYS))
    assert any("row 2: capacity_mt must be positive" in m for m in messages)
    assert any("row 3: product must be one of" in m for m in messages)
    assert any("row 4: unknown technology '*'" in m for m in messages)
    assert any("row 5: unknown geo_key 'CHN:CN-XX'" in m for m in messages)


# ---------------- master-excel validator (geo_key columns, injected reference sets)


def _run_capacity_pool_geo_validator(geo_keys: list[str]):
    """Validate temp capacity pool sheets with injected reference sets; return INVALID_GEO_KEY issues."""
    validator = MasterExcelValidator(
        valid_countries={"CHN"},
        modelled_countries={"CHN"},
        valid_geo_keys={"CHN:CN-HE", "CHN:CN-SD"},
    )
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tf:
        provinces = pd.DataFrame(
            {"geo_key": geo_keys, "region_name": ["x"] * len(geo_keys), "type": [None] * len(geo_keys)}
        )
        credits = pd.DataFrame(
            {
                "vintage_year": [2020] * len(geo_keys),
                "capacity_mt": [1.0] * len(geo_keys),
                "geo_key": geo_keys,
                "product": ["iron"] * len(geo_keys),
            }
        )
        with pd.ExcelWriter(Path(tf.name)) as writer:
            provinces.to_excel(writer, sheet_name="Capacity pool - CHN provinces", index=False)
            credits.to_excel(writer, sheet_name="Capacity pool - opening credits", index=False)
        validator._validate_capacity_pool_geo_keys(pd.ExcelFile(tf.name))
    return [issue for issue in validator.report.all_issues() if issue.error_type == "INVALID_GEO_KEY"]


def test_capacity_pool_validator_passes_declared_geo_keys():
    """Declared sub-national units pass the geo check on both sheets."""
    assert _run_capacity_pool_geo_validator(["CHN:CN-HE", "CHN:CN-SD"]) == []


def test_capacity_pool_validator_flags_bogus_geo_key_on_both_sheets():
    """An undeclared unit is INVALID_GEO_KEY on each sheet that carries it."""
    issues = _run_capacity_pool_geo_validator(["CHN:CN-XX"])
    assert len(issues) == 2
    assert {issue.sheet_name for issue in issues} == {
        "Capacity pool - CHN provinces",
        "Capacity pool - opening credits",
    }


def test_technologies_reductant_vocabulary_matches_up_to_normalisation():
    """Sheet-spelling rows must validate against a normalised vocabulary source.

    Data prep passes the workbook's Bill of Materials spellings; bootstrap
    passes the prepared primary-feedstocks fixture's normalised keys. One rule
    must serve both, so membership is checked up to normalize_name.
    """
    rows = [
        tech("BF", is_emission_intense=True),
        tech("EAF", product="steel", is_emission_intense=False),
        tech("DRI", reductant="Natural gas", is_emission_intense=False),
        tech("DRI"),  # delegation row
    ]
    normalised_vocabulary = {"natural_gas", "coal", "hydrogen"}
    assert validate_technologies(rows, technology_roster=ROSTER, reductant_vocabulary=normalised_vocabulary) == []
