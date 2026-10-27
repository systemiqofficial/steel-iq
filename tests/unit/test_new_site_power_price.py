"""Tests for new sites' grid price and electrolyser cost, resolved at the cell's geo unit, then its country."""

import csv
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest
import xarray as xr

from steelo.adapters.geospatial import geospatial_calculations, geospatial_layers, hydrogen_lcoe
from steelo.domain.models import CountryMapping, CountryMappingService, Year
from steelo.simulation import GeoConfig
from steelo.utilities import energy_trace

LAT = [0.0, 0.25, 0.5]
LON = [0.0]
INPUT_COSTS = {
    "CHN": {2025: {"electricity": 0.0615}},
    "CHN:CN-XJ": {2025: {"electricity": 0.0333}},
    "DEU": {2025: {"electricity": 0.2}},
    "DEU:DE-BY": {2025: {"electricity": 0.5}},
}
CAPEX_OPEX = {
    "CHN": {Year(2025): 0.366},
    "CHN:CN-XJ": {Year(2025): 0.311},
    "DEU": {Year(2025): 0.5},
}
EFFICIENCY = {Year(2025): 0.05}  # MWh/kg, so 50 kWh/kg


def cells(iso3: list[str], geo_key: list[str] | None = None, power_price: list[float] | None = None) -> xr.Dataset:
    """
    A one-column dataset of three feasible cells.

    Args:
        iso3: Each cell's iso3.
        geo_key: Each cell's geo key; no ``geo_key`` variable when None.
        power_price: Each cell's power price (USD/kWh); no ``power_price`` variable when None.

    Returns:
        The dataset.
    """
    ds = xr.Dataset(
        {
            "iso3": (("lat", "lon"), np.array(iso3, dtype=object).reshape(3, 1)),
            "feasibility_mask": (("lat", "lon"), np.ones((3, 1))),
        },
        coords={"lat": LAT, "lon": LON},
    )
    if geo_key is not None:
        ds["geo_key"] = (("lat", "lon"), np.array(geo_key, dtype=object).reshape(3, 1))
    if power_price is not None:
        ds["power_price"] = (("lat", "lon"), np.array(power_price, dtype=float).reshape(3, 1))
    return ds


def grid_prices(ds: xr.Dataset) -> np.ndarray:
    """The grid price ``add_grid_power_price`` gives each cell of the fixture for 2025."""
    with patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"):
        return geospatial_layers.add_grid_power_price(ds, INPUT_COSTS, 2025, geo_paths=Mock())["grid_price"].values


def lcoh(ds: xr.Dataset) -> np.ndarray:
    """The LCOH ``calculate_lcoh_from_power_price`` gives each cell of the fixture for 2025."""
    return geospatial_calculations.calculate_lcoh_from_power_price(ds, 2025, EFFICIENCY, CAPEX_OPEX)["lcoh"].values


def trace_rows(output_dir: Path) -> list[dict[str, str]]:
    """The run's energy_trace.csv as dicts."""
    with (output_dir / energy_trace.CSV_NAME).open(newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def trace_off_afterwards():
    """Leave the energy-price trace off for every other test."""
    yield
    energy_trace.configure(False, (), (), None)


def test_grid_price_resolves_at_the_geo_unit_then_the_country():
    """A CN-XJ cell takes CN-XJ's row, and a cell of a province the master lacks takes China's."""
    ds = cells(["CHN", "CHN", "CHN"], geo_key=["CHN:CN-XJ", "CHN:CN-XZ", "CHN"])

    np.testing.assert_allclose(grid_prices(ds).ravel(), [0.0333, 0.0615, 0.0615])


def test_grid_price_fallback_is_the_highest_country_price_not_a_provinces():
    """A cell with neither row takes Germany's 0.2 USD/kWh, not DE-BY's higher province price."""
    ds = cells(["ZZZ", "DEU", "CHN"], geo_key=["ZZZ", "DEU:DE-BY", "CHN"])

    np.testing.assert_allclose(grid_prices(ds).ravel(), [0.2, 0.5, 0.0615])


def test_electrolyser_cost_resolves_at_the_geo_unit_then_the_country():
    """CAPEX/OPEX follows the same rule as the grid price; a cell with neither row gets no LCOH."""
    ds = cells(["CHN", "CHN", "ZZZ"], geo_key=["CHN:CN-XJ", "CHN:CN-XZ", "ZZZ"], power_price=[0.04, 0.04, 0.04])

    result = lcoh(ds).ravel()

    np.testing.assert_allclose(result[:2], [50 * 0.04 + 0.311, 50 * 0.04 + 0.366])
    assert np.isnan(result[2])


def test_without_geo_key_every_cell_resolves_at_its_country():
    """A dataset without geo_key prices as before: sub-national rows never reach a cell."""
    ds = cells(["CHN", "DEU", "ZZZ"], power_price=[0.04, 0.04, 0.04])

    np.testing.assert_allclose(grid_prices(ds).ravel(), [0.0615, 0.2, 0.2])
    result = lcoh(ds).ravel()
    np.testing.assert_allclose(result[:2], [50 * 0.04 + 0.366, 50 * 0.04 + 0.5])
    assert np.isnan(result[2])


def test_geo_key_grid_stays_at_country_level_without_a_hierarchy(tmp_path):
    """build_geo_key_grid refines the iso3 grid it is given, never rebuilds it, and without a hierarchy keeps it."""
    iso3 = cells(["CHN", "CHN", "DEU"])["iso3"]
    geo_paths = Mock(data_dir=tmp_path, admin1_shapefile_dir=None)

    with patch("steelo.adapters.geospatial.hydrogen_lcoe.add_iso3_codes") as rebuild:
        keys = hydrogen_lcoe.build_geo_key_grid(iso3, geo_paths)

    rebuild.assert_not_called()
    assert keys.name == "geo_key"
    assert keys.values.ravel().tolist() == ["CHN", "CHN", "DEU"]


def test_grid_only_power_price_is_the_resolved_grid_price():
    """At coverage 0 the power price is the grid price resolved at the geo unit, and no LCOE file is read."""
    ds = cells(["CHN", "CHN", "ZZZ"], geo_key=["CHN:CN-XJ", "CHN:CN-XZ", "ZZZ"])

    with (
        patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"),
        patch("steelo.adapters.geospatial.geospatial_layers.plot_value_histogram"),
    ):
        result = geospatial_layers.add_power_price(ds, 2025, INPUT_COSTS, 0.0, Mock())

    np.testing.assert_allclose(result["power_price"].values.ravel(), [0.0333, 0.0615, 0.2])


def test_grid_raster_passes_every_site_grid_check(tmp_path, trace_off_afterwards):
    """With the trace on, the raster add_grid_power_price builds matches the input costs cell by cell."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)
    ds = cells(["CHN", "CHN", "ZZZ"], geo_key=["CHN:CN-XJ", "CHN:CN-XZ", "ZZZ"])

    grid_prices(ds)

    checks = [row for row in trace_rows(tmp_path) if row["stage"] == "site_grid" and row["expected"]]
    assert {row["quantity"] for row in checks} == {"cells_off_grid", "cells_off_fallback"}
    assert {row["check_failed"] for row in checks} == {"0"}


def test_hydrogen_raster_passes_every_site_hydrogen_check(tmp_path, trace_off_afterwards):
    """With the trace on, the LCOH add_capped_hydrogen_price builds matches the electrolyser formula cell by cell."""
    energy_trace.configure(True, ["CHN"], [], tmp_path)
    ds = cells(["CHN", "CHN", "DEU"], geo_key=["CHN:CN-XJ", "CHN:CN-XZ", "DEU"], power_price=[0.03, 0.04, 0.05])
    mappings = CountryMappingService(
        [
            CountryMapping(
                country=name,
                iso2=iso3[:2],
                iso3=iso3,
                irena_name=name,
                region_for_outputs=region,
                ssp_region=region,
                tiam_ucl_region=region,
            )
            for name, iso3, region in (("China", "CHN", "China"), ("Germany", "DEU", "Western Europe"))
        ],
    )

    with patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"):
        geospatial_layers.add_capped_hydrogen_price(
            ds,
            2025,
            EFFICIENCY,
            CAPEX_OPEX,
            mappings,
            0.85,
            GeoConfig(),
            geo_paths=Mock(),
        )

    checks = [row for row in trace_rows(tmp_path) if row["stage"] == "site_hydrogen" and row["expected"]]
    assert {row["quantity"] for row in checks} == {"cells_above_ceiling", "cells_off_lcoh"}
    assert {row["check_failed"] for row in checks} == {"0"}
