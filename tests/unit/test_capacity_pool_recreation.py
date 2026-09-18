"""Round-trip tests for the capacity pool recreation functions.

Workbooks are built in-process with only the needed sheets. The recreation
functions read the technology roster and reductant vocabulary from the same
workbook, so a minimal ``Techno-economic details`` and ``Bill of Materials``
sheet accompany the capacity pool sheets. The province completeness reference
is monkeypatched to a small set except where the real 31-unit construction is
the point.
"""

import json

import pandas as pd
import pytest

import steelo.data.recreation_functions as recreation_functions
from steelo.adapters.repositories.json_repository import (
    CapacityPoolOpeningCreditJsonRepository,
    CapacityPoolProvinceJsonRepository,
    CapacityPoolTechnologyJsonRepository,
)
from steelo.data.recreation_functions import (
    chinese_capacity_pool_geo_keys,
    recreate_capacity_pool_opening_credits_data,
    recreate_capacity_pool_provinces_data,
    recreate_capacity_pool_technologies_data,
)

PROVINCES_SHEET = "Capacity pool - CHN provinces"
TECHNOLOGIES_SHEET = "Capacity pool - technologies"
OPENING_CREDITS_SHEET = "Capacity pool - opening credits"

TECHNOLOGIES = pd.DataFrame(
    {
        "technology": ["BF", "EAF", "DRI", "DRI", "DRI"],
        "product": ["iron", "steel", "iron", "iron", "iron"],
        "reductant": [None, None, None, "Coal", "Hydrogen"],
        "is_emission_intense": [True, False, None, True, False],
        "switching_to": [None, None, None, None, None],
        "swap_ratio": [None, None, None, None, None],
        "notes": [None, None, "depends on reductant", None, None],
    }
)


@pytest.fixture
def small_china(monkeypatch):
    """Shrink the province completeness reference to two units."""
    monkeypatch.setattr(recreation_functions, "chinese_capacity_pool_geo_keys", lambda: {"CHN:CN-HE", "CHN:CN-SD"})


def _write_workbook(path, capacity_sheets: dict[str, pd.DataFrame]) -> None:
    """Write the given capacity sheets plus the reference sheets the recreation reads."""
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"Technology": ["BF", "EAF", "DRI"]}).to_excel(
            writer, sheet_name="Techno-economic details", index=False
        )
        pd.DataFrame({"Reductant": ["Coal", "Hydrogen"], "Technology": ["DRI", "DRI"]}).to_excel(
            writer, sheet_name="Bill of Materials", index=False
        )
        for sheet_name, df in capacity_sheets.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)


def test_recreate_provinces_roundtrip(tmp_path, small_china):
    """The fixture is written and reads back as the authored rows."""
    excel_path = tmp_path / "master.xlsx"
    json_path = tmp_path / "capacity_pool_provinces.json"
    provinces = pd.DataFrame(
        {
            "geo_key": ["CHN:CN-HE", "CHN:CN-SD"],
            "region_name": ["Jing-Jin-Ji", "Shandong"],
            "type": ["key", None],
        }
    )
    _write_workbook(excel_path, {PROVINCES_SHEET: provinces})

    repo = recreate_capacity_pool_provinces_data(json_path, excel_path)

    assert isinstance(repo, CapacityPoolProvinceJsonRepository)
    assert json_path.exists()
    rows = CapacityPoolProvinceJsonRepository(json_path).list()
    assert [(row.geo_key, row.type) for row in rows] == [("CHN:CN-HE", "key"), ("CHN:CN-SD", None)]


def test_recreate_provinces_with_real_chinese_units(tmp_path):
    """The full 31-unit enumeration passes completeness without monkeypatching."""
    excel_path = tmp_path / "master.xlsx"
    geo_keys = sorted(chinese_capacity_pool_geo_keys())
    assert len(geo_keys) == 31
    provinces = pd.DataFrame({"geo_key": geo_keys, "region_name": ["x"] * 31, "type": [None] * 31})
    _write_workbook(excel_path, {PROVINCES_SHEET: provinces})

    repo = recreate_capacity_pool_provinces_data(tmp_path / "capacity_pool_provinces.json", excel_path)

    assert repo is not None and len(repo.list()) == 31


def test_recreate_provinces_incomplete_enumeration_raises(tmp_path, small_china):
    """A missing Chinese unit fails recreation loudly."""
    excel_path = tmp_path / "master.xlsx"
    provinces = pd.DataFrame({"geo_key": ["CHN:CN-HE"], "region_name": ["Jing-Jin-Ji"], "type": ["key"]})
    _write_workbook(excel_path, {PROVINCES_SHEET: provinces})

    with pytest.raises(ValueError, match="missing: CHN:CN-SD"):
        recreate_capacity_pool_provinces_data(tmp_path / "capacity_pool_provinces.json", excel_path)


def test_recreate_technologies_roundtrip_with_unauthored_warnings(tmp_path, caplog):
    """An unauthored flag warns but the fixture and the ratio-grid diagnostic still build."""
    excel_path = tmp_path / "master.xlsx"
    json_path = tmp_path / "capacity_pool_technologies.json"
    df = TECHNOLOGIES.copy()
    df.loc[df["technology"] == "EAF", "is_emission_intense"] = None  # one TO AUTHOR cell
    _write_workbook(excel_path, {TECHNOLOGIES_SHEET: df})

    with caplog.at_level("WARNING", logger="steelo.data.recreation_functions"):
        repo = recreate_capacity_pool_technologies_data(json_path, excel_path)

    assert isinstance(repo, CapacityPoolTechnologyJsonRepository)
    assert "unauthored is_emission_intense for technology 'EAF'" in caplog.text
    rows = CapacityPoolTechnologyJsonRepository(json_path).list()
    assert len(rows) == 5 and rows[0].technology == "BF"

    grid_path = tmp_path / "capacity_pool_ratio_grid.csv"
    assert grid_path.exists()
    grid = pd.read_csv(grid_path, index_col=0, dtype=str)
    assert grid.loc["BF", "DRI|Coal"] == "1.5"  # both sides intense
    assert grid.loc["BF", "DRI|Hydrogen"] == "1"  # new side not intense
    assert grid.loc["BF", "EAF"] == "unauthored"  # the TO AUTHOR cell propagates


def test_recreate_technologies_unknown_technology_raises(tmp_path):
    """An unrecognised technology name is an error, not a fallback."""
    excel_path = tmp_path / "master.xlsx"
    df = TECHNOLOGIES.copy()
    df.loc[0, "technology"] = "BLASTFURNACE"
    _write_workbook(excel_path, {TECHNOLOGIES_SHEET: df})

    with pytest.raises(ValueError, match="unknown technology 'BLASTFURNACE'"):
        recreate_capacity_pool_technologies_data(tmp_path / "capacity_pool_technologies.json", excel_path)


def test_recreate_technologies_override_collision_raises(tmp_path):
    """`BF -> *` plus `* -> EAF` collide at equal specificity and fail recreation."""
    excel_path = tmp_path / "master.xlsx"
    overrides = pd.DataFrame(
        {
            "technology": ["BF", "*"],
            "product": [None, None],
            "reductant": [None, None],
            "is_emission_intense": [None, None],
            "switching_to": ["*", "EAF"],
            "swap_ratio": [1.0, 1.0],
            "notes": [None, None],
        }
    )
    df = pd.concat([TECHNOLOGIES, overrides], ignore_index=True)
    _write_workbook(excel_path, {TECHNOLOGIES_SHEET: df})

    with pytest.raises(ValueError, match="collide at equal specificity"):
        recreate_capacity_pool_technologies_data(tmp_path / "capacity_pool_technologies.json", excel_path)


def test_recreate_opening_credits_roundtrip(tmp_path, small_china):
    """The fixture is written and reads back with the owner intact."""
    excel_path = tmp_path / "master.xlsx"
    json_path = tmp_path / "capacity_pool_opening_credits.json"
    credits = pd.DataFrame(
        {
            "vintage_year": [2019, 2021],
            "capacity_mt": [0.5, 1.2],
            "geo_key": ["CHN:CN-HE", "CHN:CN-SD"],
            "product": ["iron", "steel"],
            "technology": ["BF", "EAF"],
            "plant_group_id": [None, "E100000000155"],
            "source": ["synthetic (testing)", "synthetic (testing)"],
        }
    )
    _write_workbook(excel_path, {OPENING_CREDITS_SHEET: credits})

    repo = recreate_capacity_pool_opening_credits_data(json_path, excel_path)

    assert isinstance(repo, CapacityPoolOpeningCreditJsonRepository)
    rows = CapacityPoolOpeningCreditJsonRepository(json_path).list()
    assert [(row.vintage_year, row.plant_group_id) for row in rows] == [(2019, None), (2021, "E100000000155")]
    # The workbook-only provenance column stays out of the fixture
    raw = json.loads(json_path.read_text())
    assert "source" not in raw[0]


def test_recreate_opening_credits_bad_row_raises(tmp_path, small_china):
    """A non-positive amount fails recreation loudly."""
    excel_path = tmp_path / "master.xlsx"
    credits = pd.DataFrame(
        {"vintage_year": [2019], "capacity_mt": [0.0], "geo_key": ["CHN:CN-HE"], "product": ["iron"]}
    )
    _write_workbook(excel_path, {OPENING_CREDITS_SHEET: credits})

    with pytest.raises(ValueError, match="capacity_mt must be positive"):
        recreate_capacity_pool_opening_credits_data(tmp_path / "capacity_pool_opening_credits.json", excel_path)


def test_absent_sheets_write_no_fixture(tmp_path):
    """Sheet absent means no fixture at all — a missing fixture must stay
    distinguishable from an empty one, per the dormancy contract."""
    excel_path = tmp_path / "master.xlsx"
    _write_workbook(excel_path, {})

    assert recreate_capacity_pool_provinces_data(tmp_path / "capacity_pool_provinces.json", excel_path) is None
    assert recreate_capacity_pool_technologies_data(tmp_path / "capacity_pool_technologies.json", excel_path) is None
    assert (
        recreate_capacity_pool_opening_credits_data(tmp_path / "capacity_pool_opening_credits.json", excel_path) is None
    )
    assert list(tmp_path.glob("capacity_pool_*")) == []


def test_repositories_read_missing_fixture_as_empty(tmp_path):
    """A missing fixture file (or no path) reads as an empty repository."""
    assert CapacityPoolProvinceJsonRepository(tmp_path / "absent.json").list() == []
    assert CapacityPoolTechnologyJsonRepository(None).list() == []
    assert CapacityPoolOpeningCreditJsonRepository(None).list() == []
