"""Unit tests for the energy-price trace (steelo.utilities.energy_trace)."""

import copy
import csv
import logging
import random
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import xarray as xr

from steelo import bootstrap
from steelo.domain.models import Location, Year
from steelo.logging_config import LoggingConfig
from steelo.utilities import energy_trace

ACTIVE = ["operating", "operating pre-retirement", "operating switching technology"]
COVERAGE = 0.85
BASELOAD_MIX = "85% baseload + 15% grid"
HYDROGEN_EFFICIENCY = {Year(2030): 0.05}
HYDROGEN_CAPEX_OPEX = {"CHN": {Year(2030): 1.0}, "CHN:CN-LN": {Year(2030): 1.5}, "FRA": {Year(2030): 0.5}}
GRID_INPUT_COSTS = {
    "CHN": {Year(2030): {"electricity": 0.06}},
    "CHN:CN-LN": {Year(2030): {"electricity": 0.09}},
    "DEU:DE-BY": {Year(2030): {"electricity": 0.5}},
    "FRA": {Year(2030): {"electricity": 0.08}},
}


@pytest.fixture(autouse=True)
def trace_off_after_each_test():
    """Leave the trace off for every other test, whatever a test switched on."""
    yield
    energy_trace.configure(False, (), (), None)


@pytest.fixture
def trace_log(caplog):
    """Capture the trace's INFO and WARNING lines."""
    caplog.set_level(logging.INFO, logger=energy_trace.logger.name)
    return caplog


class Untouchable:
    """An argument that fails the test on any read, call or operation."""

    def __getattribute__(self, name):
        raise AssertionError(f"read attribute {name}")

    def __getitem__(self, key):
        raise AssertionError("read item")

    def __iter__(self):
        raise AssertionError("iterated")

    def __len__(self):
        raise AssertionError("measured")

    def __bool__(self):
        raise AssertionError("tested for truth")

    def __float__(self):
        raise AssertionError("converted to float")

    def __eq__(self, other):
        raise AssertionError("compared")

    def __add__(self, other):
        raise AssertionError("added")

    __radd__ = __add__
    __hash__ = None  # type: ignore[assignment]


def make_furnace_group(
    furnace_group_id: str,
    *,
    electricity: float,
    hydrogen: float,
    status: str = "operating",
    technology: str = "EAF",
    operating_start: int = 2030,
) -> SimpleNamespace:
    """A furnace group with the fields the trace reads, paying the no-subsidy prices."""
    prices = {"electricity": electricity, "hydrogen": hydrogen}
    return SimpleNamespace(
        furnace_group_id=furnace_group_id,
        technology=SimpleNamespace(name=technology),
        status=status,
        energy_costs=dict(prices),
        energy_costs_no_subsidy=dict(prices),
        lifetime=SimpleNamespace(time_frame=SimpleNamespace(start=Year(operating_start))),
    )


def make_plant(
    plant_id: str,
    iso3: str,
    furnace_groups: list,
    *,
    parent_gem_id: str = "G100",
    geo_unit: str | None = None,
) -> SimpleNamespace:
    """A plant with a real Location, so geo keys resolve as in the model."""
    return SimpleNamespace(
        plant_id=plant_id,
        parent_gem_id=parent_gem_id,
        location=Location(lat=41.5, lon=123.4, country=iso3, region="", iso3=iso3, geo_unit=geo_unit),
        furnace_groups=furnace_groups,
    )


def make_repository(*plants: SimpleNamespace) -> SimpleNamespace:
    """A plant repository exposing list(), as bus.uow.plants does."""
    return SimpleNamespace(list=lambda: list(plants))


def read_rows(output_dir: Path) -> list[dict[str, str]]:
    """The run's energy_trace.csv as dicts."""
    with (output_dir / energy_trace.CSV_NAME).open(newline="") as f:
        return list(csv.DictReader(f))


def rows_for(rows: list[dict[str, str]], **columns: str) -> list[dict[str, str]]:
    """The rows whose columns hold the given values."""
    return [row for row in rows if all(row[column] == value for column, value in columns.items())]


def warnings_logged(caplog) -> list[str]:
    """The trace's WARNING messages."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == energy_trace.logger.name and record.levelno == logging.WARNING
    ]


def power_dataset(blend: str) -> xr.Dataset:
    """
    A 2 x 3 raster with grid prices, LCOE and a power price blended one of two ways.

    Args:
        blend: ``demand`` for ``grid × (1 - c) + lcoe``, ``delivered`` for ``(1 - c) × grid + c × lcoe``.

    Returns:
        The dataset; the cell with no LCOE has no power price.
    """
    iso3 = np.array([["CHN", "CHN", "IND"], ["FRA", "FRA", "IND"]], dtype=object)
    grid = np.array([[0.08, 0.08, 0.06], [0.10, 0.10, 0.06]])
    lcoe = np.array([[0.04, 0.05, 0.03], [0.06, np.nan, 0.02]])
    if blend == "demand":
        power = grid * (1 - COVERAGE) + lcoe
    else:
        power = (1 - COVERAGE) * grid + COVERAGE * lcoe
    return xr.Dataset(
        {
            "iso3": (("lat", "lon"), iso3),
            "grid_price": (("lat", "lon"), grid),
            "lcoe": (("lat", "lon"), lcoe),
            "power_price": (("lat", "lon"), power),
        },
        coords={"lat": [0.0, 1.0], "lon": [0.0, 1.0, 2.0]},
    )


def hydrogen_dataset() -> tuple[xr.Dataset, dict[str, float]]:
    """
    A 2 x 3 raster of LCOH capped at regional ceilings, with one priced cell that has no LCOH.

    Returns:
        The dataset and the regional ceilings (USD/kg). Each LCOH is 50 kWh/kg × the cell's power price plus the
        electrolyser cost of ``HYDROGEN_CAPEX_OPEX`` at its geo key, else its iso3; the second Chinese cell lies in
        CN-LN, which has its own cost.
    """
    ceilings = {"China": 5.0, "Western Europe": 4.0}
    iso3 = np.array([["CHN", "CHN", "CHN"], ["FRA", "FRA", "nan"]], dtype=object)
    geo_key = np.array([["CHN", "CHN:CN-LN", "CHN"], ["FRA", "FRA", "nan"]], dtype=object)
    regions = np.array([["China", "China", "China"], ["Western Europe", "Western Europe", np.nan]], dtype=object)
    power = np.array([[0.06, 0.09, np.nan], [0.05, 0.08, np.nan]])
    lcoh = np.array([[4.0, 6.0, np.nan], [3.0, 4.5, np.nan]])
    capped = np.array([[4.0, 5.0, 5.0], [3.0, 4.0, np.nan]])
    ds = xr.Dataset(
        {
            "iso3": (("lat", "lon"), iso3),
            "geo_key": (("lat", "lon"), geo_key),
            "tiam_ucl_region": (("lat", "lon"), regions),
            "power_price": (("lat", "lon"), power),
            "lcoh": (("lat", "lon"), lcoh),
            "capped_lcoh": (("lat", "lon"), capped),
        },
        coords={"lat": [0.0, 1.0], "lon": [0.0, 1.0, 2.0]},
    )
    return ds, ceilings


def grid_dataset() -> xr.Dataset:
    """
    A 2 x 3 raster of grid prices as ``GRID_INPUT_COSTS`` resolve them.

    Returns:
        The dataset: a CN-LN cell at its province's price, a cell of a province without a row and a country cell at
        China's, a French cell, and two cells without a price at the highest country price (FRA's 0.08 USD/kWh,
        not DE-BY's 0.5), one of them infeasible.
    """
    iso3 = np.array([["CHN", "CHN", "CHN"], ["FRA", "ZZZ", "nan"]], dtype=object)
    geo_key = np.array([["CHN:CN-LN", "CHN:CN-XZ", "CHN"], ["FRA", "ZZZ", "nan"]], dtype=object)
    grid = np.array([[0.09, 0.06, 0.06], [0.08, 0.08, 0.08]])
    return xr.Dataset(
        {
            "iso3": (("lat", "lon"), iso3),
            "geo_key": (("lat", "lon"), geo_key),
            "feasibility_mask": (("lat", "lon"), np.array([[1.0, 1.0, 1.0], [1.0, 1.0, 0.0]])),
            "grid_price": (("lat", "lon"), grid),
        },
        coords={"lat": [0.0, 1.0], "lon": [0.0, 1.0, 2.0]},
    )


def lcoe_case() -> tuple[xr.DataArray, xr.DataArray, dict[str, float], dict[str, np.ndarray]]:
    """
    One year's LCOE grid, its iso3 grid and the baseload LCOE series values that agree with them at the median.

    Returns:
        The LCOE grid (USD/MWh, float32 as in the files), the iso3 grid, geo key to LCOE (USD/kWh) and the geo keys
        priced from their nearest cells to their source cells.
    """
    coords = {"lat": [0.0, 1.0], "lon": [0.0, 1.0, 2.0]}
    iso3 = xr.DataArray(np.array([["CHN", "CHN", "IND"], ["CHN", "IND", "IND"]], dtype=object), coords=coords)
    lcoe = xr.DataArray(np.array([[30.0, 40.0, 50.0], [60.0, np.nan, 70.0]], dtype=np.float32), coords=coords)
    lcoe_by_geo_key = {"CHN": 0.040, "CHN:CN-LN": 0.035, "IND": 0.060, "BTN": 0.045}
    return lcoe, iso3, lcoe_by_geo_key, {"BTN": np.array([1, 2])}


def country_hydrogen_arguments(power_mix: str) -> dict[str, object]:
    """
    Arguments of ``country_hydrogen`` for 2030 that agree with each other, as the Environment prices them.

    Args:
        power_mix: ``BASELOAD_MIX`` or "Grid only".

    Returns:
        A country, its province and a second country: electricity at the power mix from the grid and the LCOE
        series, the LCOH from it at 50 kWh/kg plus 1 USD/kg, and capped prices at the lower of the LCOH and the
        region's ceiling. Under "Grid only" the LCOH is 5, 6 and 3 USD/kg, against ceilings of 5.5 (China) and 4
        (Western Europe).
    """
    year = Year(2030)
    coverage = COVERAGE if power_mix == BASELOAD_MIX else 0.0
    grid = {"CHN": 0.08, "CHN:CN-LN": 0.10, "DEU": 0.04}
    lcoe = {"CHN": 0.03, "CHN:CN-LN": 0.05, "DEU": 0.06}
    ceilings = {"China": 5.5, "Western Europe": 4.0}
    regions = {"CHN": "China", "DEU": "Western Europe"}
    electricity = {key: (1 - coverage) * grid[key] + coverage * lcoe[key] for key in grid}
    lcoh = {key: 50.0 * electricity[key] + 1.0 for key in grid}
    capped = {key: min(lcoh[key], ceilings[regions[key.split(":")[0]]]) for key in grid}
    price_inputs = {
        key: {
            "coverage": coverage,
            "grid": grid[key],
            "lcoe": lcoe[key] if coverage else None,
            "electricity": electricity[key],
            "lcoh": lcoh[key],
            "capped_lcoh": capped[key],
        }
        for key in grid
    }
    return {
        "year": year,
        "power_mix": power_mix,
        "input_costs": {key: {year: {"electricity": price}} for key, price in grid.items()},
        "baseload_lcoe_by_geo_key": {year: lcoe} if coverage else {},
        "hydrogen_efficiency": {year: 0.05},
        "hydrogen_capex_opex": {"CHN": {year: 1.0}, "DEU": {year: 1.0}},
        "price_inputs": price_inputs,
        "lcoh_by_geo_key": lcoh,
        "regional_ceilings": ceilings,
        "country_to_region": regions,
        "capped_prices": capped,
    }


def built_new_site(output_dir: Path) -> tuple[SimpleNamespace, SimpleNamespace]:
    """
    A new site re-valued at its raster prices in 2027 and entering construction in 2028.

    Args:
        output_dir: The run folder the trace writes to.

    Returns:
        The plant and its furnace group, now operating at the reference prices.
    """
    energy_trace.configure(True, ["CHN"], [], output_dir)
    fg = make_furnace_group("indi_7_1", electricity=0.05, hydrogen=4000.0, status="announced", operating_start=2032)
    plant = make_plant("indi_7", "CHN", [fg], parent_gem_id="indi_7")
    energy_trace.greenfield_revalue(
        plant,
        fg,
        Year(2027),
        np.array(0.05),
        np.array(4.0),
        {"electricity": 0.05, "hydrogen": 4000.0},
    )
    env = SimpleNamespace(capped_hydrogen_costs_for_year=lambda year: {"CHN": 4.5})
    energy_trace.greenfield_build(plant, fg, Year(2028), env)
    fg.status = "operating"
    return plant, fg


def test_trace_off_writes_no_log_line_and_no_csv(tmp_path, trace_log):
    """With the switch off, neither the public calls nor a stage log anything or create the CSV."""
    energy_trace.configure(False, ["CHN"], ["P1"], tmp_path)

    energy_trace.trace("brownfield", 2030, {"electricity_paid": 0.08}, plant_id="P1")
    energy_trace.check("brownfield", 2030, "electricity", False, 0.08, 0.07, plant_id="P1")
    energy_trace.site_power(power_dataset("demand"), 2030, COVERAGE, None)

    assert [record for record in trace_log.records if record.name == energy_trace.logger.name] == []
    assert not (tmp_path / energy_trace.CSV_NAME).exists()


def test_stage_functions_touch_no_argument_when_off():
    """Off, every stage returns before reading, converting or comparing any argument."""
    energy_trace.configure(False, ["CHN"], ["P1"], None)
    u = Untouchable()

    energy_trace.lcoe_source(u, u, u)
    energy_trace.lcoe_source_medians(u, u)
    energy_trace.site_grid(u, u, u)
    energy_trace.site_power(u, u, u, u)
    energy_trace.site_hydrogen(u, u, u, u, u)
    energy_trace.hydrogen_lcoe(u, u, u, u, u, u)
    energy_trace.country_hydrogen(u, u, u, u, u, u, u, u, u, u, u)
    energy_trace.brownfield(u, u, u, u, u)
    energy_trace.greenfield_operating(u, u, u)
    energy_trace.greenfield_select(u, u, u, u, u, u, u)
    energy_trace.greenfield_revalue(u, u, u, u, u, u)
    energy_trace.greenfield_build(u, u, u, u)
    energy_trace.greenfield_summary(u)


def test_listed_plant_listed_country_and_new_site_predicates():
    """A listed plant, a plant in a listed country, a new site and an untracked plant are told apart."""
    energy_trace.configure(True, ["CHN"], ["P1"], None)
    listed = make_plant("P1", "SWE", [])
    in_country = make_plant("P2", "CHN", [])
    new_site = make_plant("indi_3", "FRA", [], parent_gem_id="indi_3")
    untracked = make_plant("P4", "FRA", [])

    assert energy_trace.is_listed_plant(listed) is True
    assert energy_trace.in_listed_country(listed.location.iso3) is False
    assert energy_trace.is_listed_plant(in_country) is False
    assert energy_trace.in_listed_country(in_country.location.iso3) is True
    assert energy_trace.is_new_site(new_site) is True
    assert energy_trace.is_new_site(untracked) is False
    assert energy_trace.is_listed_plant(untracked) is False
    assert energy_trace.in_listed_country(untracked.location.iso3) is False


def test_failed_check_writes_one_warning_and_a_failed_row(tmp_path, trace_log):
    """A failed check logs one WARNING and writes a row with check_failed 1 and the expectation."""
    energy_trace.configure(True, [], [], tmp_path)

    ok = energy_trace.check("brownfield", 2030, "electricity", False, 0.08, 0.07, plant_id="P1")

    assert ok is False
    assert len(warnings_logged(trace_log)) == 1
    assert "[ENERGY CHECK] stage=brownfield year=2030 plant_id=P1 electricity=0.07" in warnings_logged(trace_log)[0]
    (row,) = read_rows(tmp_path)
    assert row["quantity"] == "electricity"
    assert float(row["value"]) == 0.07
    assert float(row["expected"]) == 0.08
    assert row["unit"] == "USD/kWh"
    assert row["check_failed"] == "1"


def test_passed_check_writes_no_warning(tmp_path, trace_log):
    """A passed check writes its row with check_failed 0 and no WARNING."""
    energy_trace.configure(True, [], [], tmp_path)

    ok = energy_trace.check("brownfield", 2030, "electricity", True, 0.08, 0.08, plant_id="P1")

    assert ok is True
    assert warnings_logged(trace_log) == []
    (row,) = read_rows(tmp_path)
    assert row["check_failed"] == "0"


def test_csv_appends_two_years_under_one_header(tmp_path):
    """Rows of two years land in one file with a single header line."""
    energy_trace.configure(True, [], [], tmp_path)

    energy_trace.trace("brownfield", 2030, {"electricity_paid": 0.08}, plant_id="P1")
    energy_trace.trace("brownfield", 2031, {"electricity_paid": 0.09}, plant_id="P1")

    lines = (tmp_path / energy_trace.CSV_NAME).read_text().splitlines()
    assert lines[0] == ",".join(energy_trace.CSV_COLUMNS)
    assert sum(line.startswith("year,") for line in lines) == 1
    assert [row["year"] for row in read_rows(tmp_path)] == ["2030", "2031"]
    assert all(row["check_failed"] == "0" and row["expected"] == "" for row in read_rows(tmp_path))


def test_configure_resets_references_and_csv_path(tmp_path, trace_log):
    """A second configure forgets the first run's references and writes to its own folder."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    plant, _ = built_new_site(first)
    first_rows = read_rows(first)

    energy_trace.configure(True, ["CHN"], [], second)
    energy_trace.greenfield_operating(make_repository(plant), Year(2032), ACTIVE)

    assert read_rows(first) == first_rows
    operating = rows_for(read_rows(second), stage="greenfield_operating")
    assert {row["quantity"] for row in operating} == {"electricity", "hydrogen", "electricity_paid", "hydrogen_paid"}
    assert all(row["expected"] == "" for row in operating)
    assert "checked 0, failed 0, skipped 2 (no reference)" in trace_log.text


def test_no_csv_without_output_folder(tmp_path, monkeypatch, trace_log):
    """Switched on without an output folder, the trace logs but writes no file anywhere."""
    monkeypatch.chdir(tmp_path)
    energy_trace.configure(True, [], [], None)

    energy_trace.trace("brownfield", 2030, {"electricity_paid": 0.08}, plant_id="P1")

    assert "[ENERGY TRACE] stage=brownfield year=2030 plant_id=P1 electricity_paid=0.08 USD/kWh" in trace_log.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("blend", "lcoe_basis", "cells_off"),
    [
        ("demand", None, 0),
        ("delivered", energy_trace.PER_MWH_DELIVERED, 0),
        ("demand", energy_trace.PER_MWH_DELIVERED, 5),
        ("delivered", None, 5),
    ],
)
def test_site_power_checks_the_blend_the_lcoe_basis_implies(tmp_path, blend, lcoe_basis, cells_off):
    """The expected blend follows the file's basis, so a blend that does not match the basis fails every cell."""
    energy_trace.configure(True, ["CHN", "IND"], [], tmp_path)

    energy_trace.site_power(power_dataset(blend), 2030, COVERAGE, lcoe_basis)

    (all_cells,) = rows_for(read_rows(tmp_path), iso3="ALL", quantity="cells_off_blend")
    assert int(all_cells["value"]) == cells_off
    assert all_cells["check_failed"] == str(int(cells_off > 0))
    (checked,) = rows_for(read_rows(tmp_path), iso3="ALL", quantity="cells_checked")
    assert int(checked["value"]) == 5


def test_site_power_aligns_an_lcoe_raster_stored_in_another_dimension_order(tmp_path):
    """An LCOE raster stored longitude first is compared cell by cell with the rasters computed latitude first."""
    energy_trace.configure(True, [], [], tmp_path)
    ds = power_dataset("demand")
    ds["lcoe"] = ds["lcoe"].transpose("lon", "lat")

    energy_trace.site_power(ds, 2030, COVERAGE, None)

    (all_cells,) = rows_for(read_rows(tmp_path), iso3="ALL", quantity="cells_off_blend")
    assert all_cells["value"] == "0"


def test_lcoe_source_writes_the_file_and_its_attributes(tmp_path):
    """The file that prices the run is traced with its BOA run, version, basis, coverage and schema version."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)
    lcoe_ds = xr.Dataset(
        attrs={
            "schema_version": 1,
            "lcoe_basis": "per_mwh_delivered",
            "coverage_fraction": 0.85,
            "run": "default_49e8",
            "boa_version": "1.9.0",
        },
    )

    energy_trace.lcoe_source(2025, Path("/lcoe/optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc"), lcoe_ds)

    rows = {row["quantity"]: (row["value"], row["unit"]) for row in read_rows(tmp_path)}
    assert rows == {
        "file": ("/lcoe/optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc", ""),
        "run": ("default_49e8", ""),
        "boa_version": ("1.9.0", ""),
        "lcoe_basis": ("per_mwh_delivered", ""),
        "coverage_fraction": ("0.85", "fraction"),
        "schema_version": ("1", ""),
    }


def test_lcoe_source_medians_per_listed_country_in_usd_per_mwh(tmp_path):
    """Each listed country's median LCOE of the year goes out in the file's USD/MWh; no row for ALL."""
    energy_trace.configure(True, ["CHN", "IND"], [], tmp_path)
    ds = power_dataset("delivered")

    energy_trace.lcoe_source_medians(ds, 2030)

    rows = {row["iso3"]: (float(row["value"]), row["unit"]) for row in read_rows(tmp_path)}
    assert rows == {"CHN": (pytest.approx(45.0), "USD/MWh"), "IND": (pytest.approx(25.0), "USD/MWh")}


def test_site_power_flags_one_perturbed_cell(tmp_path, trace_log):
    """One cell off the blend fails its country's and the ALL check, and no other country's."""
    energy_trace.configure(True, ["CHN", "IND"], [], tmp_path)
    ds = power_dataset("demand")
    ds["power_price"].values[0, 2] += 0.001

    energy_trace.site_power(ds, 2030, COVERAGE, None)

    rows = rows_for(read_rows(tmp_path), quantity="cells_off_blend")
    assert {row["iso3"]: row["value"] for row in rows} == {"CHN": "0", "IND": "1", "ALL": "1"}
    assert len(warnings_logged(trace_log)) == 2
    assert "stage=site_power year=2030 checked 5, failed 1" in trace_log.text


def test_stages_leave_dataset_and_furnace_group_dicts_unchanged(tmp_path):
    """A traced stage reads the rasters and the furnace groups' prices without changing them."""
    energy_trace.configure(True, ["CHN", "IND"], ["P1"], tmp_path)
    power_ds = power_dataset("demand")
    power_before = power_ds.copy(deep=True)
    grid_ds = grid_dataset()
    grid_before = grid_ds.copy(deep=True)
    input_costs_before = copy.deepcopy(GRID_INPUT_COSTS)
    hydrogen_tables_before = copy.deepcopy([HYDROGEN_EFFICIENCY, HYDROGEN_CAPEX_OPEX])
    hydrogen_ds, ceilings = hydrogen_dataset()
    hydrogen_before = hydrogen_ds.copy(deep=True)
    fg = make_furnace_group("P1_1", electricity=0.08, hydrogen=4000.0)
    plant = make_plant("P1", "CHN", [fg])
    new_fg = make_furnace_group("indi_1_1", electricity=0.05, hydrogen=4000.0, status="considered")
    new_plant = make_plant("indi_1", "CHN", [new_fg], parent_gem_id="indi_1")
    prices_before = copy.deepcopy([fg.energy_costs, fg.energy_costs_no_subsidy, new_fg.energy_costs_no_subsidy])
    written = {"electricity": 0.05, "hydrogen": 4000.0}
    lcoe, iso3, lcoe_by_geo_key, neighbour_sources = lcoe_case()
    lcoe_before, iso3_before = lcoe.copy(deep=True), iso3.copy(deep=True)
    series_before = copy.deepcopy([lcoe_by_geo_key, neighbour_sources])
    country_arguments = country_hydrogen_arguments(BASELOAD_MIX)
    country_arguments_before = copy.deepcopy(country_arguments)

    energy_trace.site_grid(grid_ds, 2030, GRID_INPUT_COSTS)
    energy_trace.site_power(power_ds, 2030, COVERAGE, None)
    energy_trace.site_hydrogen(hydrogen_ds, 2030, ceilings, HYDROGEN_EFFICIENCY, HYDROGEN_CAPEX_OPEX)
    energy_trace.hydrogen_lcoe(2030, lcoe, iso3, 50.0, lcoe_by_geo_key, neighbour_sources)
    energy_trace.country_hydrogen(**country_arguments)
    energy_trace.brownfield(
        make_repository(plant), Year(2030), {"CHN": {Year(2030): {"electricity": 0.08}}}, {"CHN": 4.0}, ACTIVE
    )
    energy_trace.greenfield_revalue(new_plant, new_fg, Year(2030), np.array(0.05), np.array(4.0), written)

    xr.testing.assert_identical(power_ds, power_before)
    xr.testing.assert_identical(grid_ds, grid_before)
    assert GRID_INPUT_COSTS == input_costs_before
    assert [HYDROGEN_EFFICIENCY, HYDROGEN_CAPEX_OPEX] == hydrogen_tables_before
    xr.testing.assert_identical(hydrogen_ds, hydrogen_before)
    xr.testing.assert_identical(lcoe, lcoe_before)
    xr.testing.assert_identical(iso3, iso3_before)
    assert lcoe_by_geo_key == series_before[0]
    assert neighbour_sources.keys() == series_before[1].keys()
    assert all(np.array_equal(neighbour_sources[key], series_before[1][key]) for key in neighbour_sources)
    assert country_arguments == country_arguments_before
    assert [fg.energy_costs, fg.energy_costs_no_subsidy, new_fg.energy_costs_no_subsidy] == prices_before
    assert written == {"electricity": 0.05, "hydrogen": 4000.0}


def test_site_hydrogen_flags_a_cell_above_its_regional_ceiling(tmp_path, trace_log):
    """A capped price above its region's ceiling fails; the capped raster otherwise passes."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)
    ds, ceilings = hydrogen_dataset()
    ds["capped_lcoh"].values[1, 1] = 4.2

    energy_trace.site_hydrogen(ds, 2030, ceilings, HYDROGEN_EFFICIENCY, HYDROGEN_CAPEX_OPEX)

    rows = rows_for(read_rows(tmp_path), quantity="cells_above_ceiling")
    assert {row["iso3"]: row["value"] for row in rows} == {"CHN": "0", "ALL": "1"}
    assert len(warnings_logged(trace_log)) == 1


def test_site_hydrogen_counts_priced_cells_without_lcoh_without_failing(tmp_path, trace_log):
    """A cell priced at the ceiling with no LCOH is counted, not failed, and the shares are per checked cell."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)
    ds, ceilings = hydrogen_dataset()

    energy_trace.site_hydrogen(ds, 2030, ceilings, HYDROGEN_EFFICIENCY, HYDROGEN_CAPEX_OPEX)

    china = {row["quantity"]: row["value"] for row in rows_for(read_rows(tmp_path), iso3="CHN")}
    assert china["cells_checked"] == "3"
    assert china["cells_nan_lcoh_priced"] == "1"
    assert china["cells_above_ceiling"] == "0"
    assert float(china["ceiling"]) == 5.0
    assert float(china["share_capped"]) == pytest.approx(1 / 3)
    assert warnings_logged(trace_log) == []


def test_site_hydrogen_flags_an_lcoh_with_the_countrys_electrolyser_cost_inside_a_province_with_its_own(
    tmp_path,
    trace_log,
):
    """A CN-LN cell whose LCOH took China's electrolyser cost instead of CN-LN's fails; the other cells pass."""
    energy_trace.configure(True, ["CHN", "FRA"], [], tmp_path)
    ds, ceilings = hydrogen_dataset()
    ds["lcoh"].values[0, 1] = 50 * 0.09 + 1.0

    energy_trace.site_hydrogen(ds, 2030, ceilings, HYDROGEN_EFFICIENCY, HYDROGEN_CAPEX_OPEX)

    rows = rows_for(read_rows(tmp_path), quantity="cells_off_lcoh")
    assert {row["iso3"]: row["value"] for row in rows} == {"CHN": "1", "FRA": "0", "ALL": "1"}
    assert len(warnings_logged(trace_log)) == 2
    assert "stage=site_hydrogen year=2030 checked 5, failed 0; LCOH checked 4, failed 1" in trace_log.text


def test_site_grid_passes_a_raster_resolved_at_the_geo_key_and_summarises_each_province(tmp_path, trace_log):
    """
    Prices at the province, at the country for a province without a row, and the highest country price for cells
    with neither pass; each listed country and each of its geo units gets its rows, over feasible cells.
    """
    energy_trace.configure(True, ["CHN"], [], tmp_path)

    energy_trace.site_grid(grid_dataset(), 2030, GRID_INPUT_COSTS)

    rows = read_rows(tmp_path)
    assert {row["check_failed"] for row in rows} == {"0"}
    by_area = {(row["iso3"], row["geo_key"], row["quantity"]): row["value"] for row in rows}
    assert by_area[("CHN", "", "median_grid_price")] == "0.06"
    assert by_area[("CHN", "", "cells_subnational")] == "1"
    assert by_area[("CHN", "CHN:CN-LN", "median_grid_price")] == "0.09"
    assert by_area[("CHN", "CHN:CN-XZ", "cells_subnational")] == "0"
    assert by_area[("ALL", "", "cells_grid_fallback")] == "1"
    assert {(row["iso3"], row["geo_key"]) for row in rows} == {
        ("CHN", ""),
        ("CHN", "CHN:CN-LN"),
        ("CHN", "CHN:CN-XZ"),
        ("ALL", ""),
    }
    assert "stage=site_grid year=2030 checked 6, failed 0" in trace_log.text


def test_site_grid_flags_a_country_price_inside_a_priced_province_and_a_fallback_at_a_provinces_price(
    tmp_path,
    trace_log,
):
    """A CN-LN cell at China's price fails cells_off_grid; a fallback cell at DE-BY's price fails cells_off_fallback."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)
    ds = grid_dataset()
    ds["grid_price"].values[0, 0] = 0.06
    ds["grid_price"].values[1, 1] = 0.5

    energy_trace.site_grid(ds, 2030, GRID_INPUT_COSTS)

    rows = read_rows(tmp_path)
    off_grid = {(row["iso3"], row["geo_key"]): row["value"] for row in rows_for(rows, quantity="cells_off_grid")}
    assert off_grid == {("CHN", ""): "1", ("CHN", "CHN:CN-LN"): "1", ("CHN", "CHN:CN-XZ"): "0", ("ALL", ""): "1"}
    off_fallback = {row["iso3"]: row["value"] for row in rows_for(rows, quantity="cells_off_fallback", geo_key="")}
    assert off_fallback == {"CHN": "0", "ALL": "1"}
    assert "stage=site_grid year=2030 checked 6, failed 2" in trace_log.text


def test_country_hydrogen_passes_trade_lowered_prices_and_flags_a_price_above_the_bound(tmp_path, trace_log):
    """The capped price may sit below min(LCOH, ceiling), as trade allows, but never above it."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)
    arguments = country_hydrogen_arguments("Grid only")
    arguments["capped_prices"] = {"CHN": 5.0, "CHN:CN-LN": 5.2, "DEU": 3.5}

    energy_trace.country_hydrogen(**arguments)

    rows = rows_for(read_rows(tmp_path), quantity="capped")
    assert {row["geo_key"]: row["check_failed"] for row in rows} == {"CHN": "0", "CHN:CN-LN": "0", "DEU": "1"}
    (province,) = rows_for(rows, geo_key="CHN:CN-LN")
    assert province["iso3"] == "CHN"
    assert float(province["expected"]) == 5.5
    assert len(warnings_logged(trace_log)) == 1
    assert "geo_key=DEU" not in "".join(r.getMessage() for r in trace_log.records if r.levelno == logging.INFO)


def test_country_hydrogen_passes_the_blend_and_records_its_inputs(tmp_path, trace_log):
    """Under a baseload mix, consistent prices pass every check and each geo key gets its coverage, grid and LCOE."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)

    energy_trace.country_hydrogen(**country_hydrogen_arguments(BASELOAD_MIX))

    rows = read_rows(tmp_path)
    assert {row["check_failed"] for row in rows} == {"0"}
    province = {row["quantity"]: row for row in rows_for(rows, geo_key="CHN:CN-LN")}
    assert set(province) == {"coverage", "grid", "lcoe", "electricity", "lcoh", "ceiling", "capped"}
    assert float(province["electricity"]["expected"]) == pytest.approx(0.15 * 0.10 + 0.85 * 0.05)
    assert float(province["lcoe"]["value"]) == 0.05
    assert province["lcoe"]["unit"] == "USD/kWh"
    assert "stage=country_hydrogen year=2030 checked 9, failed 0" in trace_log.text


def test_country_hydrogen_records_no_lcoe_under_grid_only(tmp_path):
    """Without a baseload share the expected electricity is the grid price and no LCOE row is written."""
    energy_trace.configure(True, [], [], tmp_path)

    energy_trace.country_hydrogen(**country_hydrogen_arguments("Grid only"))

    rows = read_rows(tmp_path)
    assert {row["quantity"] for row in rows} == {"coverage", "grid", "electricity", "lcoh", "ceiling", "capped"}
    assert {row["check_failed"] for row in rows} == {"0"}


def test_country_hydrogen_flags_grid_only_electricity_under_a_baseload_mix(tmp_path, trace_log):
    """A price recorded and computed from the grid alone under a baseload mix fails the electricity and LCOH checks."""
    energy_trace.configure(True, [], [], tmp_path)
    blended = country_hydrogen_arguments(BASELOAD_MIX)
    grid_priced = country_hydrogen_arguments("Grid only")
    arguments = {
        **blended,
        "price_inputs": grid_priced["price_inputs"],
        "lcoh_by_geo_key": grid_priced["lcoh_by_geo_key"],
        "capped_prices": grid_priced["capped_prices"],
    }

    energy_trace.country_hydrogen(**arguments)

    rows = read_rows(tmp_path)
    electricity = {row["geo_key"]: row for row in rows_for(rows, quantity="electricity")}
    assert {key: row["check_failed"] for key, row in electricity.items()} == {"CHN": "1", "CHN:CN-LN": "1", "DEU": "1"}
    assert float(electricity["CHN"]["value"]) == 0.08
    assert float(electricity["CHN"]["expected"]) == pytest.approx(0.15 * 0.08 + 0.85 * 0.03)
    assert {row["check_failed"] for row in rows_for(rows, quantity="lcoh")} == {"1"}
    assert len(warnings_logged(trace_log)) == 6


def test_country_hydrogen_flags_an_lcoh_computed_from_the_grid_while_the_record_holds_the_blend(tmp_path, trace_log):
    """An LCOH the cap took from the grid price fails, even though the recorded electricity is the blend."""
    energy_trace.configure(True, [], [], tmp_path)
    arguments = country_hydrogen_arguments(BASELOAD_MIX)
    grid_priced = country_hydrogen_arguments("Grid only")
    arguments["lcoh_by_geo_key"] = grid_priced["lcoh_by_geo_key"]
    arguments["capped_prices"] = grid_priced["capped_prices"]

    energy_trace.country_hydrogen(**arguments)

    rows = read_rows(tmp_path)
    assert {row["check_failed"] for row in rows_for(rows, quantity="electricity")} == {"0"}
    lcoh = {row["geo_key"]: row for row in rows_for(rows, quantity="lcoh")}
    assert {key: row["check_failed"] for key, row in lcoh.items()} == {"CHN": "1", "CHN:CN-LN": "1", "DEU": "1"}
    assert float(lcoh["CHN"]["value"]) == pytest.approx(5.0)
    assert float(lcoh["CHN"]["expected"]) == pytest.approx(50.0 * (0.15 * 0.08 + 0.85 * 0.03) + 1.0)
    assert len(warnings_logged(trace_log)) == 3


def test_hydrogen_lcoe_checks_listed_countries_against_their_cells(tmp_path, trace_log):
    """
    A listed country's LCOE equals the median of its finite cells; a province and a key priced from its neighbours
    get unchecked rows, and every geo key says whether it was priced from neighbours.
    """
    energy_trace.configure(True, ["CHN", "IND"], [], tmp_path)
    lcoe, iso3, lcoe_by_geo_key, neighbour_sources = lcoe_case()

    energy_trace.hydrogen_lcoe(2030, lcoe, iso3, 50.0, lcoe_by_geo_key, neighbour_sources)

    rows = read_rows(tmp_path)
    checked = {row["geo_key"]: row for row in rows if row["quantity"] == "lcoe" and row["expected"]}
    assert set(checked) == {"CHN", "IND"}
    assert float(checked["CHN"]["expected"]) == pytest.approx(40.0)
    assert float(checked["IND"]["expected"]) == pytest.approx(60.0)
    assert {row["check_failed"] for row in rows} == {"0"}
    unchecked = {
        row["geo_key"]: float(row["value"]) for row in rows if row["quantity"] == "lcoe" and not row["expected"]
    }
    assert unchecked == {"CHN:CN-LN": pytest.approx(35.0), "BTN": pytest.approx(45.0)}
    assert {row["geo_key"]: row["value"] for row in rows_for(rows, quantity="from_neighbours")} == {
        "BTN": "1",
        "CHN": "0",
        "CHN:CN-LN": "0",
        "IND": "0",
    }
    assert "stage=hydrogen_lcoe year=2030 checked 2, failed 0" in trace_log.text


def test_hydrogen_lcoe_flags_a_perturbed_value_and_follows_the_percentile(tmp_path, trace_log):
    """A series value off its cells' percentile fails; the expectation follows the configured percentile."""
    energy_trace.configure(True, ["CHN", "IND"], [], tmp_path)
    lcoe, iso3, lcoe_by_geo_key, neighbour_sources = lcoe_case()
    lcoe_by_geo_key["IND"] = 0.061
    lcoe_by_geo_key["CHN"] = 0.035

    energy_trace.hydrogen_lcoe(2030, lcoe, iso3, 25.0, lcoe_by_geo_key, neighbour_sources)

    rows = {row["geo_key"]: row for row in rows_for(read_rows(tmp_path), quantity="lcoe") if row["expected"]}
    assert rows["CHN"]["check_failed"] == "0"
    assert float(rows["CHN"]["expected"]) == pytest.approx(35.0)
    assert rows["IND"]["check_failed"] == "1"
    assert float(rows["IND"]["expected"]) == pytest.approx(55.0)
    assert len(warnings_logged(trace_log)) == 1


def test_brownfield_flags_stale_electricity_and_hydrogen_in_usd_per_kg(tmp_path, trace_log):
    """An unlisted plant in a listed country gets rows only for its failed checks."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)
    stale = make_furnace_group("P2_1", electricity=0.07, hydrogen=4.0)
    correct = make_furnace_group("P3_1", electricity=0.08, hydrogen=4000.0)
    untracked = make_furnace_group("P4_1", electricity=0.01, hydrogen=1.0)
    plants = make_repository(
        make_plant("P2", "CHN", [stale]),
        make_plant("P3", "CHN", [correct]),
        make_plant("P4", "FRA", [untracked]),
    )
    input_costs = {"CHN": {Year(2030): {"electricity": 0.08}}, "FRA": {Year(2030): {"electricity": 0.09}}}

    energy_trace.brownfield(plants, Year(2030), input_costs, {"CHN": 4.0, "FRA": 3.0}, ACTIVE)

    rows = read_rows(tmp_path)
    assert {(row["furnace_group_id"], row["quantity"]) for row in rows} == {
        ("P2_1", "electricity"),
        ("P2_1", "hydrogen"),
    }
    assert all(row["check_failed"] == "1" for row in rows)
    assert float(rows_for(rows, quantity="hydrogen")[0]["expected"]) == 4000.0
    assert len(warnings_logged(trace_log)) == 2
    assert "stage=brownfield year=2030 checked 4, failed 2" in trace_log.text


def test_brownfield_writes_rows_for_a_listed_plant_with_its_province_prices(tmp_path, trace_log):
    """A listed plant is checked against its province's prices and gets rows for every quantity."""
    energy_trace.configure(True, [], ["P1"], tmp_path)
    fg = make_furnace_group("P1_1", electricity=0.09, hydrogen=4500.0, technology="BF")
    fg.energy_costs["electricity"] = 0.06
    plant = make_plant("P1", "CHN", [fg], geo_unit="CN-LN")
    input_costs = {"CHN": {Year(2030): {"electricity": 0.08}}, "CHN:CN-LN": {Year(2030): {"electricity": 0.09}}}

    energy_trace.brownfield(make_repository(plant), Year(2030), input_costs, {"CHN": 4.0, "CHN:CN-LN": 4.5}, ACTIVE)

    rows = {row["quantity"]: row for row in read_rows(tmp_path)}
    assert set(rows) == {"electricity", "hydrogen", "electricity_paid", "hydrogen_paid"}
    assert rows["electricity"]["check_failed"] == "0"
    assert rows["hydrogen"]["check_failed"] == "0"
    assert float(rows["electricity_paid"]["value"]) == 0.06
    assert rows["electricity"]["geo_key"] == "CHN:CN-LN"
    assert rows["electricity"]["technology"] == "BF"
    assert rows["electricity"]["new_site"] == "0"
    assert warnings_logged(trace_log) == []


def test_greenfield_revalue_flags_hydrogen_written_in_usd_per_kg(tmp_path, trace_log):
    """Writing the site's capped LCOH without converting it to USD/t fails the check."""
    energy_trace.configure(True, [], [], tmp_path)
    fg = make_furnace_group("indi_1_1", electricity=0.05, hydrogen=4.0, status="considered")
    plant = make_plant("indi_1", "CHN", [fg], parent_gem_id="indi_1")

    energy_trace.greenfield_revalue(
        plant,
        fg,
        Year(2027),
        np.array(0.05),
        np.array(4.0),
        {"electricity": 0.05, "hydrogen": 4.0},
    )

    (row,) = rows_for(read_rows(tmp_path), quantity="hydrogen")
    assert row["check_failed"] == "1"
    assert float(row["value"]) == 4.0
    assert float(row["expected"]) == 4000.0
    assert row["new_site"] == "1"
    assert len(warnings_logged(trace_log)) == 1


def test_greenfield_revalue_skips_the_check_where_the_site_has_no_value(tmp_path, trace_log):
    """A site with no raster hydrogen gets its row without a check and counts as skipped."""
    energy_trace.configure(True, [], [], tmp_path)
    fg = make_furnace_group("indi_1_1", electricity=0.05, hydrogen=4000.0, status="announced")
    plant = make_plant("indi_1", "CHN", [fg], parent_gem_id="indi_1")

    energy_trace.greenfield_revalue(
        plant,
        fg,
        Year(2027),
        np.array(0.05),
        np.array(np.nan),
        {"electricity": 0.05, "hydrogen": 4000.0},
    )
    energy_trace.greenfield_summary(2027)

    (row,) = rows_for(read_rows(tmp_path), quantity="hydrogen")
    assert row["expected"] == ""
    assert row["check_failed"] == "0"
    assert "stage=greenfield_revalue year=2027 checked 0, failed 0, skipped 1 (no site value)" in trace_log.text


def test_greenfield_operating_passes_the_frozen_reference_and_flags_the_regional_overwrite(tmp_path, trace_log):
    """An operating new site must keep the prices frozen at construction; the regional hydrogen price fails."""
    plant, fg = built_new_site(tmp_path)

    energy_trace.greenfield_operating(make_repository(plant), Year(2032), ACTIVE)
    assert warnings_logged(trace_log) == []

    fg.energy_costs_no_subsidy["hydrogen"] = 4500.0
    energy_trace.greenfield_operating(make_repository(plant), Year(2033), ACTIVE)

    rows = rows_for(read_rows(tmp_path), stage="greenfield_operating")
    verdicts = {(row["year"], row["quantity"]): row["check_failed"] for row in rows if row["expected"]}
    assert verdicts == {
        ("2032", "electricity"): "0",
        ("2032", "hydrogen"): "0",
        ("2033", "electricity"): "0",
        ("2033", "hydrogen"): "1",
    }
    (build_reference,) = rows_for(read_rows(tmp_path), stage="greenfield_build", quantity="reference_hydrogen")
    assert float(build_reference["value"]) == 4000.0
    (regional,) = rows_for(read_rows(tmp_path), stage="greenfield_build", quantity="regional_hydrogen")
    assert float(regional["value"]) == 4500.0
    assert len(warnings_logged(trace_log)) == 1


def test_greenfield_build_without_reference_writes_a_missing_row(tmp_path, trace_log):
    """A furnace group never re-valued is built with a reference row reading missing."""
    energy_trace.configure(True, [], [], tmp_path)
    fg = make_furnace_group("indi_2_1", electricity=0.05, hydrogen=4000.0, status="construction")
    plant = make_plant("indi_2", "CHN", [fg], parent_gem_id="indi_2")
    env = SimpleNamespace(capped_hydrogen_costs_for_year=lambda year: {"CHN": 4.5})

    energy_trace.greenfield_build(plant, fg, Year(2028), env)
    energy_trace.greenfield_summary(2028)

    rows = {row["quantity"]: row["value"] for row in read_rows(tmp_path)}
    assert rows["reference"] == "missing"
    assert "reference_electricity" not in rows
    assert rows["operating_start_year"] == "2030"
    assert "stage=greenfield_build year=2028 built 1, without reference 1" in trace_log.text


def test_greenfield_select_writes_rows_per_site_and_a_summary_per_listed_country(tmp_path, trace_log):
    """Every candidate site gets its rows; the log carries only the summaries."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)
    country_costs = {Year(2026): {"electricity": 0.08, "hydrogen": 5000.0}}
    sites = [
        ({"Latitude": 41.0, "Longitude": 123.0, "iso3": "CHN"}, {"electricity": 0.05, "hydrogen": 4000.0}),
        ({"Latitude": 42.0, "Longitude": 124.0, "iso3": "CHN"}, {"electricity": 0.9, "hydrogen": 6000.0}),
        ({"Latitude": 46.0, "Longitude": 2.0, "iso3": "FRA"}, {"electricity": 0.06, "hydrogen": 4500.0}),
    ]

    for site, site_costs in sites:
        energy_trace.greenfield_select(Year(2026), site, site["iso3"], site_costs, country_costs, 2028, 4)
    energy_trace.greenfield_summary(2026)

    rows = read_rows(tmp_path)
    assert len(rows) == 3 * len(energy_trace.UNITS["greenfield_select"])
    first = {row["quantity"]: row["value"] for row in rows_for(rows, lat="41.0")}
    assert first == {
        "site_electricity": "0.05",
        "site_hydrogen": "4000.0",
        "country_electricity": "0.08",
        "country_hydrogen": "5000.0",
        "override_reference_year": "2026",
        "operating_start_year": "2032",
    }
    assert "lat=" not in trace_log.text
    assert "stage=greenfield_select year=2026 iso3=CHN sites=2" in trace_log.text
    assert "outside_ratio=1" in trace_log.text
    assert "stage=greenfield_select year=2026 sites 3" in trace_log.text


def test_trace_draws_no_random_number(tmp_path):
    """Running every stage with the trace on leaves Python's and NumPy's random streams where they were."""
    random.seed(7)
    np.random.seed(7)
    plant, _ = built_new_site(tmp_path)
    hydrogen_ds, ceilings = hydrogen_dataset()
    lcoe, iso3, lcoe_by_geo_key, neighbour_sources = lcoe_case()
    energy_trace.site_grid(grid_dataset(), 2030, GRID_INPUT_COSTS)
    energy_trace.site_power(power_dataset("demand"), 2030, COVERAGE, None)
    energy_trace.site_hydrogen(hydrogen_ds, 2030, ceilings, HYDROGEN_EFFICIENCY, HYDROGEN_CAPEX_OPEX)
    energy_trace.hydrogen_lcoe(2030, lcoe, iso3, 50.0, lcoe_by_geo_key, neighbour_sources)
    energy_trace.country_hydrogen(**country_hydrogen_arguments(BASELOAD_MIX))
    energy_trace.greenfield_operating(make_repository(plant), Year(2032), ACTIVE)
    energy_trace.greenfield_summary(2032)
    drawn = (random.random(), np.random.random())

    random.seed(7)
    np.random.seed(7)

    assert drawn == (random.random(), np.random.random())


def test_bootstrap_configures_the_trace_with_the_run_folder(tmp_path, monkeypatch):
    """bootstrap_simulation hands the YAML switch, both lists and the run folder to configure before loading data."""
    from steelo.simulation import SimulationConfig
    from steelo.simulation_types import get_default_technology_settings

    calls = []
    monkeypatch.setattr(energy_trace, "configure", lambda *args: calls.append(args))
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2026),
        master_excel_path=tmp_path / "test.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
        data_dir=tmp_path / "no_data",
    )

    with pytest.raises(FileNotFoundError, match="Fixtures directory not found"):
        bootstrap.bootstrap_simulation(config)

    assert calls == [
        (
            LoggingConfig.ENERGY_TRACE,
            LoggingConfig.ENERGY_TRACE_COUNTRIES,
            LoggingConfig.ENERGY_TRACE_PLANTS,
            config.output_dir,
        ),
    ]
