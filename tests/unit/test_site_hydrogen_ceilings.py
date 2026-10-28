"""Unit tests for new sites' precomputed hydrogen ceilings and the yearly raster capped at them."""

import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from steelo.adapters.geospatial import boa_lcoe, geospatial_calculations, geospatial_layers
from steelo.domain.models import CountryMapping, CountryMappingService, Environment, Year
from steelo.simulation import GeoConfig, SimulationConfig

COVERAGE = 0.85
COORDS = {"lat": [0.0, 0.25], "lon": [0.0, 0.25, 0.5]}
YEARS = range(2025, 2028)
GEO_CONFIG = GeoConfig(
    hydrogen_ceiling_percentile=50.0,
    intraregional_trade_allowed=True,
    intraregional_trade_matrix={"North": ["South"], "South": None},
    long_dist_pipeline_transport_cost=0.1,
)


def base_layers() -> xr.Dataset:
    """
    The geospatial step's base layers on a 2 x 3 grid.

    Returns:
        ``iso3`` and ``geo_key``: three AAA cells (one in its unit A1), two BBB cells and one cell of no country.
    """
    iso3 = np.array([["AAA", "AAA", "BBB"], ["AAA", "BBB", "nan"]], dtype=object)
    geo_key = np.array([["AAA:A1", "AAA", "BBB"], ["AAA", "BBB", "nan"]], dtype=object)
    return xr.Dataset({"iso3": (("lat", "lon"), iso3), "geo_key": (("lat", "lon"), geo_key)}, coords=COORDS)


def input_costs(last_year: int = 2027) -> dict:
    """Grid prices (USD/kWh) that differ by key and rise each year, 2025 to ``last_year``."""
    base = {"AAA": 0.08, "AAA:A1": 0.11, "BBB": 0.05}
    return {
        key: {Year(y): {"electricity": price + 0.004 * (y - 2025)} for y in range(2025, last_year + 1)}
        for key, price in base.items()
    }


def electrolyser(last_year: int = 2027) -> tuple[dict, dict]:
    """The electrolyser's use (MWh/kg) and cost (USD/kg), with a unit row for A1, 2025 to ``last_year``."""
    years = range(2025, last_year + 1)
    efficiency = {Year(y): 0.052 - 0.001 * (y - 2025) for y in years}
    capex_opex = {
        "AAA": {Year(y): 1.2 for y in years},
        "AAA:A1": {Year(y): 1.6 for y in years},
        "BBB": {Year(y): 0.9 for y in years},
    }
    return efficiency, capex_opex


def mappings() -> CountryMappingService:
    """AAA in region North, BBB in region South."""
    return CountryMappingService(
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
            for name, iso3, region in (("Aland", "AAA", "North"), ("Bland", "BBB", "South"))
        ]
    )


def write_lcoe_file(folder: Path) -> Path:
    """
    A promoted LCOE file for 2025 and 2027 on the base layers' grid, so 2026 is interpolated.

    Args:
        folder: Folder to write into.

    Returns:
        The file's path; the cell of no country has no LCOE.
    """
    lcoe = np.array(
        [
            [[40.0, 55.0, 30.0], [62.0, 35.0, np.nan]],
            [[36.0, 47.0, 28.0], [50.0, 31.0, np.nan]],
        ],
        dtype=np.float32,
    )
    ds = xr.Dataset(
        {"lcoe": (("year", "lat", "lon"), lcoe)},
        coords={"year": np.array([2025, 2027], dtype=np.int32), **COORDS},
        attrs={"schema_version": 1, "lcoe_basis": "per_mwh_delivered", "coverage_fraction": COVERAGE},
    )
    path = folder / "optimal_lcoe_test_cov0p85_2025_2027.nc"
    ds.to_netcdf(path)
    return path


def yearly_step_ceilings(year: int, folder: Path, last_year: int = 2027) -> dict[str, float]:
    """
    The ceilings the yearly step computed from a year's raster before the precompute: its power layers, LCOH and
    regional percentile, then the trade option as ``apply_hydrogen_price_cap`` applied it.

    Args:
        year: The year.
        folder: The folder holding the promoted LCOE file.
        last_year: Last year of the input costs and the electrolyser data.

    Returns:
        Region to effective ceiling (USD/kg).
    """
    efficiency, capex_opex = electrolyser(last_year=last_year)
    ds = base_layers()
    ds["feasibility_mask"] = (("lat", "lon"), np.ones((2, 3)))
    geo_paths = SimpleNamespace(baseload_lcoe_dir=folder, geo_plots_dir=None)
    with (
        patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"),
        patch("steelo.adapters.geospatial.geospatial_layers.plot_value_histogram"),
    ):
        ds = geospatial_layers.add_power_price(ds, year, input_costs(last_year=last_year), COVERAGE, geo_paths)
    ds = geospatial_calculations.calculate_lcoh_from_power_price(ds, year, efficiency, capex_opex)
    raw = geospatial_calculations.calculate_regional_hydrogen_ceiling(
        ds, mappings(), GEO_CONFIG.hydrogen_ceiling_percentile
    )
    return {
        "North": min(raw["North"], raw["South"] + GEO_CONFIG.long_dist_pipeline_transport_cost),
        "South": raw["South"],
    }


def environment(tmp_path: Path, *, efficiency_last_year: int, power_mix: str = "Grid only") -> Environment:
    """
    An environment for a run from 2025 to 2030, whose electrolyser efficiency ends in a given year.

    Args:
        tmp_path: Run folder.
        efficiency_last_year: Last year of the electrolyser efficiency series; the other inputs run to 2035.
        power_mix: The run's power mix.

    Returns:
        The environment in its first year.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        geo_config=GeoConfig(
            included_power_mix=power_mix,
            hydrogen_ceiling_percentile=50.0,
            intraregional_trade_matrix={"North": None, "South": None},
        ),
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,DRI\nDRI,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    env.year = Year(2025)
    env.input_costs = input_costs(last_year=2035)
    env.hydrogen_efficiency, _ = electrolyser(last_year=efficiency_last_year)
    _, env.hydrogen_capex_opex = electrolyser(last_year=2035)
    return env


@pytest.mark.parametrize("year", list(YEARS))
def test_precomputed_ceilings_equal_the_ones_the_yearly_raster_gave(tmp_path, year):
    """For a file year and an interpolated one, the precompute gives each region the ceiling of that year's raster."""
    path = write_lcoe_file(tmp_path)
    efficiency, capex_opex = electrolyser()

    ceilings = geospatial_layers.site_hydrogen_ceilings(
        base_layers(),
        YEARS,
        input_costs(),
        COVERAGE,
        boa_lcoe.load_lcoe_grid(path, COVERAGE),
        efficiency,
        capex_opex,
        mappings(),
        GEO_CONFIG,
    )

    assert ceilings[Year(year)] == yearly_step_ceilings(year, tmp_path)


def test_precompute_logs_its_wall_time(tmp_path, caplog):
    """One INFO line names the site hydrogen ceilings, their years and the wall time in seconds."""
    efficiency, capex_opex = electrolyser()
    caplog.set_level(logging.INFO, logger="steelo.adapters.geospatial.geospatial_layers")

    geospatial_layers.site_hydrogen_ceilings(
        base_layers(), YEARS, input_costs(), 0.0, None, efficiency, capex_opex, mappings(), GEO_CONFIG
    )

    (line,) = [r.getMessage() for r in caplog.records if "site hydrogen ceilings" in r.getMessage()]
    assert "2025 to 2027 (3 years)" in line
    assert re.search(r"in \d+\.\d s$", line)


def test_precompute_stops_at_the_data_and_later_lock_years_hold_its_last_year(tmp_path):
    """
    With the efficiency ending in 2031, before the last lock year 2033, the ceilings stop at 2031 and 2032 and 2033
    read 2031's; a year missing inside the range raises.
    """
    env = environment(tmp_path, efficiency_last_year=2031)
    years = env.site_hydrogen_ceiling_years()
    assert years == range(2025, 2032)

    env.initiate_site_hydrogen_ceilings(
        geospatial_layers.site_hydrogen_ceilings(
            base_layers(),
            years,
            env.input_costs,
            0.0,
            None,
            env.hydrogen_efficiency,
            env.hydrogen_capex_opex,
            mappings(),
            env.config.geo_config,
        ),
        geospatial_calculations.tiam_ucl_region_by_iso3(mappings()),
    )

    assert sorted(env.site_hydrogen_ceilings) == list(years)
    assert env.site_hydrogen_ceilings_for_year(Year(2032)) == env.site_hydrogen_ceilings[Year(2031)]
    assert env.site_hydrogen_ceilings_for_year(Year(2033)) == env.site_hydrogen_ceilings[Year(2031)]
    assert env.site_hydrogen_ceilings[Year(2031)] != env.site_hydrogen_ceilings[Year(2025)]
    del env.site_hydrogen_ceilings[Year(2028)]
    with pytest.raises(KeyError):
        env.site_hydrogen_ceilings_for_year(Year(2028))


def test_precompute_years_stop_at_the_last_lock_year_when_the_data_run_further(tmp_path):
    """With every input to 2035, a run to 2030 stores 2025 to 2033, its end year plus the consideration time."""
    env = environment(tmp_path, efficiency_last_year=2035)

    assert env.site_hydrogen_ceiling_years() == range(2025, 2034)


def test_precompute_runs_past_the_lcoe_files_last_year_holding_its_lcoe(tmp_path):
    """
    With an LCOE file ending in 2027 and every other input running to 2035, the ceilings run to 2033, the last lock
    year, and 2030's equal the ones its yearly raster gives with the LCOE held at 2027.
    """
    path = write_lcoe_file(tmp_path)
    env = environment(tmp_path, efficiency_last_year=2035, power_mix="85% baseload + 15% grid")
    env.initiate_baseload_lcoe_grid(boa_lcoe.load_lcoe_grid(path, COVERAGE))
    years = env.site_hydrogen_ceiling_years()

    ceilings = geospatial_layers.site_hydrogen_ceilings(
        base_layers(),
        years,
        env.input_costs,
        COVERAGE,
        env.baseload_lcoe_grid,
        env.hydrogen_efficiency,
        env.hydrogen_capex_opex,
        mappings(),
        GEO_CONFIG,
    )

    assert years == range(2025, 2034)
    assert ceilings[Year(2030)] == yearly_step_ceilings(2030, tmp_path, last_year=2035)
    assert ceilings[Year(2030)] != ceilings[Year(2027)]


def test_precompute_with_a_baseload_share_raises_without_the_lcoe_grid():
    """A power mix with a baseload share cannot price its cells without the LCOE grid."""
    efficiency, capex_opex = electrolyser()

    with pytest.raises(ValueError, match="needs the baseload LCOE grid"):
        geospatial_layers.site_hydrogen_ceilings(
            base_layers(), YEARS, input_costs(), COVERAGE, None, efficiency, capex_opex, mappings(), GEO_CONFIG
        )


def test_yearly_raster_caps_at_the_stored_ceilings_and_adds_the_region_layer():
    """The yearly step caps each cell at its region's stored ceiling, not the raster's own percentile."""
    efficiency, capex_opex = electrolyser()
    ds = base_layers()
    ds["power_price"] = (("lat", "lon"), np.array([[0.05, 0.07, 0.04], [0.09, 0.03, np.nan]]))
    ds["feasibility_mask"] = (("lat", "lon"), np.ones((2, 3)))
    stored = {"North": 4.0, "South": 2.2}

    with patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"):
        result = geospatial_layers.add_capped_hydrogen_price(
            ds,
            2025,
            efficiency,
            capex_opex,
            mappings(),
            COVERAGE,
            GEO_CONFIG,
            geo_paths=SimpleNamespace(geo_plots_dir=None),
            hydrogen_ceiling=stored,
        )

    regions = result["tiam_ucl_region"].values
    assert regions[0].tolist() == ["North", "North", "South"]
    assert regions[1, :2].tolist() == ["North", "South"]
    assert pd.isna(regions[1, 2])
    ceiling = result["hydrogen_ceiling"].values
    np.testing.assert_array_equal(ceiling[regions == "North"], 4.0)
    np.testing.assert_array_equal(ceiling[regions == "South"], 2.2)
    lcoh = result["lcoh"].values
    priced = np.isfinite(lcoh)
    np.testing.assert_allclose(result["capped_lcoh"].values[priced], np.minimum(lcoh, ceiling)[priced])
    assert (result["capped_lcoh"].values[priced] < lcoh[priced]).any()
