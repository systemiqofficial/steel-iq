"""Sub-national Input-costs rows fall back to the parent country per commodity."""

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.excel_reader import read_regional_input_prices_from_master_excel
from steelo.domain.constants import PERMWh_TO_PERkWh


def write_master(tmp_path, rows):
    """Write an Input costs sheet with the given rows and return its path."""
    path = tmp_path / "master.xlsx"
    pd.DataFrame(rows).to_excel(path, sheet_name="Input costs", index=False)
    return path


def by_key(result):
    return {(entry.iso3, entry.geo_unit, int(entry.year)): entry.costs for entry in result}


@pytest.fixture
def master_with_province_rows(tmp_path):
    """Fully authored country rows plus a province row authoring only Electricity."""
    return write_master(
        tmp_path,
        [
            {"ISO-3 code": "CHN", "Commodity": "Electricity", "Unit": "USD/MWh", 2025: 60.0, 2026: 61.0},
            {"ISO-3 code": "CHN", "Commodity": "Coal", "Unit": "USD/t", 2025: 100.0, 2026: 101.0},
            {"ISO-3 code": "CHN:CN-AH", "Commodity": "Electricity", "Unit": "USD/MWh", 2025: 48.5, 2026: 49.0},
        ],
    )


def test_province_inherits_unauthored_commodities_from_country(master_with_province_rows):
    """A province row keeps its authored electricity but takes coal from the country row."""
    costs = by_key(read_regional_input_prices_from_master_excel(master_with_province_rows))

    province_2025 = costs[("CHN", "CN-AH", 2025)]
    assert province_2025["electricity"] == pytest.approx(48.5 * PERMWh_TO_PERkWh)
    assert province_2025["coal"] == pytest.approx(100.0)

    province_2026 = costs[("CHN", "CN-AH", 2026)]
    assert province_2026["electricity"] == pytest.approx(49.0 * PERMWh_TO_PERkWh)
    assert province_2026["coal"] == pytest.approx(101.0)


def test_country_rows_unchanged_by_fallback(master_with_province_rows):
    """Country rows keep their authored values."""
    costs = by_key(read_regional_input_prices_from_master_excel(master_with_province_rows))

    chn_2025 = costs[("CHN", None, 2025)]
    assert chn_2025["electricity"] == pytest.approx(60.0 * PERMWh_TO_PERkWh)
    assert chn_2025["coal"] == pytest.approx(100.0)


def test_country_level_gap_raises(tmp_path):
    """A commodity unpriced even after the country fallback fails loudly, never a silent 0.0."""
    master = write_master(
        tmp_path,
        [
            {"ISO-3 code": "CHN", "Commodity": "Electricity", "Unit": "USD/MWh", 2025: 60.0},
            {"ISO-3 code": "CHN", "Commodity": "Coal", "Unit": "USD/t", 2025: 100.0},
            {"ISO-3 code": "IND", "Commodity": "Electricity", "Unit": "USD/MWh", 2025: 70.0},
        ],
    )
    with pytest.raises(ValueError, match=r"IND has no authored value in 2025 .* \['coal'\]"):
        read_regional_input_prices_from_master_excel(master)
