"""Reader tests for the three optional ``Capacity pool - …`` master-excel sheets.

The sheets are optional: an absent sheet reads as an empty list. Within a
present sheet, structural problems (missing columns, unparseable flags or
numbers) raise rather than skip — semantic checks live in
``steelo.capacity_policy.validation`` and are tested separately.
"""

from pathlib import Path

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.excel_reader import (
    read_capacity_pool_opening_credits,
    read_capacity_pool_provinces,
    read_capacity_pool_technologies,
)

PROVINCES_SHEET = "Capacity pool - CHN provinces"
TECHNOLOGIES_SHEET = "Capacity pool - technologies"
OPENING_CREDITS_SHEET = "Capacity pool - opening credits"


def _write_workbook(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    """Write only the given sheets, plus a filler so the workbook is never empty."""
    with pd.ExcelWriter(path) as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
        if not sheets:
            pd.DataFrame({"filler": [1]}).to_excel(writer, sheet_name="Other", index=False)


def _provinces_df(**overrides) -> pd.DataFrame:
    data = {
        "geo_key": ["CHN:CN-HE", "CHN:CN-QH", "CHN:CN-SC"],
        "region_name": ["Jing-Jin-Ji", "Qinghai", "Sichuan"],
        "type": ["key", "exempt", None],
        # A retired schema column: the reader must tolerate leftovers, since the
        # workbook keeps dead columns until they are deleted manually
        "from_year": [None, None, 2030],
        "notes": ["sourced", None, None],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def _technologies_df(**overrides) -> pd.DataFrame:
    data = {
        "technology": ["BF", "DRI", "BF"],
        "product": ["iron", "iron", None],
        "reductant": [None, "Hydrogen", None],
        "is_emission_intense": [True, False, None],
        "switching_to": [None, None, "EAF"],
        "swap_ratio": [None, None, 1.0],
        "notes": ["sourced", None, None],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def _opening_credits_df(**overrides) -> pd.DataFrame:
    data = {
        "vintage_year": [2019, 2021],
        "capacity_mt": [0.5, 1.2],
        "geo_key": ["CHN:CN-HE", "CHN:CN-SD"],
        "product": ["iron", "steel"],
        "technology": ["BF", None],
        "plant_group_id": [None, "E100000000155"],
        "source": ["synthetic (testing)", None],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def test_read_provinces_happy_path(tmp_path):
    """Rows come back in sheet order with blanks as None; legacy columns are ignored."""
    path = tmp_path / "master.xlsx"
    _write_workbook(path, {PROVINCES_SHEET: _provinces_df()})

    rows = read_capacity_pool_provinces(path)

    assert [row.geo_key for row in rows] == ["CHN:CN-HE", "CHN:CN-QH", "CHN:CN-SC"]
    assert rows[0].type == "key" and rows[0].region_name == "Jing-Jin-Ji"
    assert rows[2].type is None


def test_read_technologies_happy_path(tmp_path):
    """Classification and override rows parse, with blank flags as None (unauthored)."""
    path = tmp_path / "master.xlsx"
    _write_workbook(path, {TECHNOLOGIES_SHEET: _technologies_df()})

    rows = read_capacity_pool_technologies(path)

    assert [row.technology for row in rows] == ["BF", "DRI", "BF"]
    assert rows[0].is_emission_intense is True
    assert not rows[0].is_override
    assert rows[2].is_override and rows[2].switching_to == "EAF" and rows[2].swap_ratio == 1.0
    assert rows[2].is_emission_intense is None


def test_read_opening_credits_happy_path(tmp_path):
    """Rows parse with vintage as int and blank owner/technology as None."""
    path = tmp_path / "master.xlsx"
    _write_workbook(path, {OPENING_CREDITS_SHEET: _opening_credits_df()})

    rows = read_capacity_pool_opening_credits(path)

    assert [(row.vintage_year, row.capacity_mt) for row in rows] == [(2019, 0.5), (2021, 1.2)]
    assert rows[0].plant_group_id is None and rows[0].technology == "BF"
    assert rows[1].plant_group_id == "E100000000155" and rows[1].technology is None


def test_absent_sheets_read_as_empty(tmp_path):
    """The sheets are optional: a workbook without them yields empty lists."""
    path = tmp_path / "master.xlsx"
    _write_workbook(path, {})

    assert read_capacity_pool_provinces(path) == []
    assert read_capacity_pool_technologies(path) == []
    assert read_capacity_pool_opening_credits(path) == []


def test_flag_parsing_accepts_excel_spellings(tmp_path):
    """TRUE/FALSE strings in either case and 1/0 numerics all parse to booleans."""
    path = tmp_path / "master.xlsx"
    df = _technologies_df(
        technology=["BF", "DRI", "EAF", "BOF", "MOE", "BF+CCS"],
        product=["iron", "iron", "steel", "steel", "iron", "iron"],
        reductant=[None] * 6,
        is_emission_intense=["TRUE", 1, 0.0, "false", 0, 1.0],
        switching_to=[None] * 6,
        swap_ratio=[None] * 6,
        notes=[None] * 6,
    )
    _write_workbook(path, {TECHNOLOGIES_SHEET: df})

    rows = read_capacity_pool_technologies(path)

    assert [row.is_emission_intense for row in rows] == [True, True, False, False, False, True]


def test_unparseable_flag_raises(tmp_path):
    """A mistyped flag must not silently become unauthored."""
    path = tmp_path / "master.xlsx"
    _write_workbook(path, {TECHNOLOGIES_SHEET: _technologies_df(is_emission_intense=["maybe", False, None])})

    with pytest.raises(ValueError, match="is_emission_intense must be TRUE/FALSE or blank"):
        read_capacity_pool_technologies(path)


def test_missing_column_raises(tmp_path):
    """A present sheet missing a required column is a structural error."""
    path = tmp_path / "master.xlsx"
    _write_workbook(path, {TECHNOLOGIES_SHEET: _technologies_df().drop(columns=["switching_to"])})

    with pytest.raises(ValueError, match="missing required column"):
        read_capacity_pool_technologies(path)


def test_non_integer_vintage_raises(tmp_path):
    """A fractional or non-numeric vintage_year is a structural error."""
    path = tmp_path / "master.xlsx"
    _write_workbook(path, {OPENING_CREDITS_SHEET: _opening_credits_df(vintage_year=[2019.5, 2021])})

    with pytest.raises(ValueError, match="vintage_year must be an integer"):
        read_capacity_pool_opening_credits(path)
