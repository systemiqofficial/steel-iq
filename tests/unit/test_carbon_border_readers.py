"""Tests for the bloc rows of the Carbon cost sheet and the common-price row of the CBAM sheet."""

import logging

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.excel_reader import read_carbon_border_mechanisms, read_carbon_costs
from steelo.adapters.repositories.json_repository import CarbonBorderMechanismInDb
from steelo.bootstrap import _check_common_carbon_cost_series
from steelo.domain.models import CarbonBorderMechanism, Year


def _write_sheet(path, sheet_name, frame):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name=sheet_name, index=False)


def test_read_carbon_costs_keeps_bloc_rows_under_normalised_keys(tmp_path):
    """ISO3 rows are unchanged, bloc rows are keyed by the normalised bloc name, blank codes are skipped."""
    path = tmp_path / "carbon_costs.xlsx"
    _write_sheet(
        path,
        "Carbon cost",
        pd.DataFrame(
            {
                "Country/Region_Bloc": ["Germany", "EFTA/EUCU", "China", "note row"],
                "ISO 3-letter code_Bloc": ["DEU", "EFTA/EUCU", "China", None],
                2025: [40.0, 1.5, 0.0, 99.0],
                2026: [50.0, 29.7, 0.0, 99.0],
            }
        ),
    )

    series = {entry.iso3: entry.carbon_cost for entry in read_carbon_costs(path)}

    assert set(series) == {"DEU", "EFTA_EUCU", "China"}
    assert series["DEU"] == {Year(2025): 40.0, Year(2026): 50.0}
    assert series["EFTA_EUCU"] == {Year(2025): 1.5, Year(2026): 29.7}
    assert series["China"] == {Year(2025): 0.0, Year(2026): 0.0}


def test_read_carbon_border_mechanisms_reads_common_carbon_cost_row(tmp_path):
    """Row 4 sets the common-price flag per mechanism; 1 means the bloc series prices the border."""
    path = tmp_path / "cbam.xlsx"
    _write_sheet(
        path,
        "CBAM",
        pd.DataFrame(
            {
                "Region": ["CBAM active?", "Year CBAM begins", "Year CBAM ends", "Common carbon cost across the bloc?"],
                "EFTA/EUCU": [1, 2026, None, 1],
                "China": [1, 2025, None, 0],
            }
        ),
    )

    mechanisms = {m.mechanism_name: m for m in read_carbon_border_mechanisms(path)}

    assert mechanisms["EFTA/EUCU"].common_carbon_cost is True
    assert mechanisms["China"].common_carbon_cost is False


def test_read_carbon_border_mechanisms_without_common_row_defaults_to_national(tmp_path):
    """A sheet with only the three original rows leaves every mechanism on national prices."""
    path = tmp_path / "cbam.xlsx"
    _write_sheet(
        path,
        "CBAM",
        pd.DataFrame({"Region": ["CBAM active?", "Year CBAM begins", "Year CBAM ends"], "EFTA/EUCU": [1, 2026, None]}),
    )

    mechanisms = read_carbon_border_mechanisms(path)

    assert [m.common_carbon_cost for m in mechanisms] == [False]


def test_carbon_border_mechanism_in_db_round_trip_keeps_common_carbon_cost():
    """The flag survives the prepared-JSON boundary in both directions."""
    mechanism = CarbonBorderMechanism(
        mechanism_name="EFTA/EUCU", applying_region_column="EFTA_EUCU", start_year=2026, common_carbon_cost=True
    )

    round_tripped = CarbonBorderMechanismInDb(
        **CarbonBorderMechanismInDb.from_domain(mechanism).model_dump()
    ).to_domain()

    assert round_tripped == mechanism
    assert (
        CarbonBorderMechanismInDb(mechanism_name="x", applying_region_column="X", start_year=2025)
        .to_domain()
        .common_carbon_cost
        is False
    )


def test_check_common_carbon_cost_series_raises_without_series_and_warns_on_zeros():
    """Bootstrap fails on a common-price mechanism with no bloc row and warns when the row is all zeros."""
    common = CarbonBorderMechanism(
        mechanism_name="EFTA/EUCU", applying_region_column="EFTA_EUCU", start_year=2026, common_carbon_cost=True
    )
    national = CarbonBorderMechanism(mechanism_name="China", applying_region_column="China", start_year=2025)

    with pytest.raises(ValueError, match="no 'EFTA_EUCU' row"):
        _check_common_carbon_cost_series([common, national], {"DEU": {Year(2026): 50.0}})

    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("steelo.bootstrap")
    collector = _Collector(level=logging.WARNING)
    logger.addHandler(collector)
    try:
        _check_common_carbon_cost_series([common, national], {"EFTA_EUCU": {Year(2026): 0.0, Year(2027): 0.0}})
        _check_common_carbon_cost_series([common], {"EFTA_EUCU": {Year(2026): 29.7}})
    finally:
        logger.removeHandler(collector)

    assert sum("all zeros" in record.getMessage() for record in records) == 1
