"""Tests for pricing green hydrogen off the scenario power mix (baseload LCOE per geo_key)."""

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from steelo.adapters.geospatial.hydrogen_lcoe import (
    admin1_rows_of_country,
    lcoe_percentile_by_geo_key,
    neighbour_fill_sources,
)
from steelo.domain.models import CountryMapping, Environment, Year
from steelo.simulation import GeoConfig, SimulationConfig


def _grid() -> xr.DataArray:
    keys = np.array(
        [["AAA", "AAA", "BBB", "CCC"], ["CHN", "CHN:CN-XX", "CHN:CN-XX", "CCC"]],
        dtype=object,
    )
    return xr.DataArray(keys, coords={"lat": [10.0, 10.25], "lon": [0.0, 0.25, 0.5, 0.75]}, dims=("lat", "lon"))


LCOE = np.array([[20.0, 40.0, 30.0, np.nan], [10.0, 50.0, 70.0, np.nan]])


def test_percentile_per_country_spans_its_provinces_and_each_province_stands_alone() -> None:
    values = lcoe_percentile_by_geo_key(LCOE.ravel(), _grid().values.ravel(), percentile=50)
    assert values["AAA"] == 30.0  # median of 20, 40
    assert values["BBB"] == 30.0
    assert values["CHN"] == 50.0  # 10, 50, 70 — provinces count towards the country
    assert values["CHN:CN-XX"] == 60.0
    assert "CCC" not in values  # no finite pixel


def test_lower_percentile_moves_towards_the_best_sites() -> None:
    values = lcoe_percentile_by_geo_key(LCOE.ravel(), _grid().values.ravel(), percentile=25)
    assert values["CHN"] == 30.0  # 25th percentile of 10, 50, 70
    assert values["AAA"] == 25.0


def test_neighbour_fill_averages_the_nearest_finite_pixels(tmp_path: Path) -> None:
    grid = _grid()
    sources = neighbour_fill_sources(["CCC"], grid, np.isfinite(LCOE), geo_paths=None)  # type: ignore[arg-type]
    assert set(sources) == {"CCC"}
    # both CCC pixels sit at lon 0.75; every finite pixel is a neighbour on this tiny grid
    assert sorted(sources["CCC"].tolist()) == [0, 1, 2, 4, 5, 6]
    assert np.mean(LCOE.ravel()[sources["CCC"]]) == pytest.approx(np.nanmean(LCOE))


def test_admin1_rows_follow_natural_earth_filing() -> None:
    import geopandas as gpd
    from shapely.geometry import Point

    admin1 = gpd.GeoDataFrame(
        {
            "adm0_a3": ["FRA", "FRA", "NLD", "NOR", "NOR"],
            "gu_a3": ["FRA", "GLP", "NLD", "NOR", "NSV"],
            "iso_3166_2": ["FR-75", "FR-GP", "NL-BQ1", "NO-03", "NO-21"],
        },
        geometry=[Point(i, 0) for i in range(5)],
    )
    assert admin1_rows_of_country(admin1, "FRA")["iso_3166_2"].tolist() == ["FR-75", "FR-GP"]
    assert admin1_rows_of_country(admin1, "GLP")["iso_3166_2"].tolist() == ["FR-GP"]  # dependency by geounit
    assert admin1_rows_of_country(admin1, "BES")["iso_3166_2"].tolist() == ["NL-BQ1"]
    assert admin1_rows_of_country(admin1, "SJM")["iso_3166_2"].tolist() == ["NO-21"]
    assert admin1_rows_of_country(admin1, "NOR")["iso_3166_2"].tolist() == ["NO-03", "NO-21"]


def _make_mapping(country: str, iso2: str, iso3: str, region: str) -> CountryMapping:
    return CountryMapping(
        country=country,
        iso2=iso2,
        iso3=iso3,
        irena_name=country,
        region_for_outputs=region,
        ssp_region=region,
        tiam_ucl_region=region,
    )


def _make_env(tmp_path: Path, geo_config: GeoConfig) -> Environment:
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2025),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        geo_config=geo_config,
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF\nBF,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    env.year = Year(2025)
    env.input_costs = {
        "AAA": {Year(2025): {"electricity": 0.05}},
        "AAA:AA-1": {Year(2025): {"electricity": 0.08}},
        "BBB": {Year(2025): {"electricity": 0.09}},
    }
    env.hydrogen_efficiency = {Year(2025): 0.05}  # MWh/kg -> 50 kWh/kg
    env.hydrogen_capex_opex = {"AAA": {Year(2025): 1.0}, "BBB": {Year(2025): 1.5}}
    env.initiate_country_mappings(
        country_mappings=[
            _make_mapping("Aland", "AA", "AAA", "TestRegion"),
            _make_mapping("Bland", "BB", "BBB", "TestRegion"),
        ]
    )
    return env


def test_grid_only_prices_hydrogen_off_the_grid_price(tmp_path: Path) -> None:
    env = _make_env(tmp_path, GeoConfig(included_power_mix="Grid only", hydrogen_ceiling_percentile=100.0))
    prices = env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))
    assert prices["AAA"] == pytest.approx(50 * 0.05 + 1.0)


def test_baseload_mix_blends_grid_with_the_geo_keys_own_lcoe(tmp_path: Path) -> None:
    env = _make_env(
        tmp_path, GeoConfig(included_power_mix="85% baseload + 15% grid", hydrogen_ceiling_percentile=100.0)
    )
    env.initiate_baseload_lcoe_by_geo_key({Year(2025): {"AAA": 0.03, "AAA:AA-1": 0.02, "BBB": 0.12}})
    prices = env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))
    assert prices["AAA"] == pytest.approx(50 * (0.15 * 0.05 + 0.85 * 0.03) + 1.0)
    assert prices["AAA:AA-1"] == pytest.approx(50 * (0.15 * 0.08 + 0.85 * 0.02) + 1.0)  # province: own grid + own LCOE
    assert prices["BBB"] == pytest.approx(50 * (0.15 * 0.09 + 0.85 * 0.12) + 1.5)  # dearer than grid: no fallback


def test_hydrogen_power_mix_overrides_the_steel_power_mix(tmp_path: Path) -> None:
    env = _make_env(
        tmp_path,
        GeoConfig(
            included_power_mix="Grid only",
            hydrogen_power_mix="95% baseload + 5% grid",
            hydrogen_ceiling_percentile=100.0,
        ),
    )
    env.initiate_baseload_lcoe_by_geo_key({Year(2025): {"AAA": 0.03, "AAA:AA-1": 0.02, "BBB": 0.12}})
    prices = env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))
    assert prices["AAA"] == pytest.approx(50 * (0.05 * 0.05 + 0.95 * 0.03) + 1.0)


def test_price_inputs_are_recorded_and_exported(tmp_path: Path) -> None:
    env = _make_env(
        tmp_path, GeoConfig(included_power_mix="85% baseload + 15% grid", hydrogen_ceiling_percentile=100.0)
    )
    env.initiate_baseload_lcoe_by_geo_key({Year(2025): {"AAA": 0.03, "AAA:AA-1": 0.02, "BBB": 0.12}})
    env.initiate_capped_hydrogen_costs_by_year()
    row = env.hydrogen_price_inputs_by_year[Year(2025)]["AAA"]
    assert row["grid"] == 0.05 and row["lcoe"] == 0.03 and row["coverage"] == 0.85
    assert row["electricity"] == pytest.approx(0.15 * 0.05 + 0.85 * 0.03)
    assert row["capped_lcoh"] == env.capped_hydrogen_costs_by_year[Year(2025)]["AAA"]

    out = tmp_path / "data" / "hydrogen_price_inputs.csv"
    env.export_hydrogen_price_inputs(out)
    lines = out.read_text().splitlines()
    assert lines[0].startswith("geo_key,year,coverage,grid_usd_per_mwh,lcoe_usd_per_mwh,electricity_usd_per_mwh")
    aaa = next(line for line in lines if line.startswith("AAA,2025"))
    assert aaa.split(",")[3:6] == ["50.0", "30.0", str(0.15 * 50 + 0.85 * 30)]  # USD/MWh in the export


def test_grid_only_export_leaves_lcoe_empty(tmp_path: Path) -> None:
    env = _make_env(tmp_path, GeoConfig(included_power_mix="Grid only", hydrogen_ceiling_percentile=100.0))
    env.initiate_capped_hydrogen_costs_by_year()
    out = tmp_path / "hydrogen_price_inputs.csv"
    env.export_hydrogen_price_inputs(out)
    aaa = next(line for line in out.read_text().splitlines() if line.startswith("AAA,2025"))
    assert aaa.split(",")[2:5] == ["0.0", "50.0", ""]


def test_baseload_mix_without_lcoe_series_raises(tmp_path: Path) -> None:
    env = _make_env(tmp_path, GeoConfig(included_power_mix="85% baseload + 15% grid"))
    with pytest.raises(ValueError, match="initiate_baseload_lcoe_by_geo_key"):
        env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))


def test_missing_geo_key_in_lcoe_series_raises(tmp_path: Path) -> None:
    env = _make_env(tmp_path, GeoConfig(included_power_mix="85% baseload + 15% grid"))
    env.initiate_baseload_lcoe_by_geo_key({Year(2025): {"AAA": 0.03, "BBB": 0.12}})
    with pytest.raises(ValueError, match="AAA:AA-1"):
        env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))
