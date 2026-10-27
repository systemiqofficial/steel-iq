"""Tests for existing plants' hydrogen priced off the scenario power mix (baseload LCOE per geo key)."""

import csv
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import xarray as xr
from shapely.geometry import Point, box

from steelo.adapters.geospatial import geo_unit_lookup, hydrogen_lcoe
from steelo.domain import calculate_costs
from steelo.domain.models import CountryMapping, Environment, GeoDataPaths, Year
from steelo.simulation import GeoConfig, SimulationConfig
from steelo.utilities import energy_trace

BASELOAD_MIX = "85% baseload + 15% grid"
CONTRACT = {"schema_version": 1, "lcoe_basis": "per_mwh_delivered", "coverage_fraction": 0.85}
LAT = [10.0, 10.25]
LON = [0.0, 0.25, 0.5, 0.75]
# CHN:CN-XX covers the two middle cells of the second row; CCC's cells have no LCOE
ISO3 = [["AAA", "AAA", "BBB", "CCC"], ["CHN", "CHN", "CHN", "CCC"]]
LCOE_2025 = np.array([[20.0, 40.0, 30.0, np.nan], [10.0, 50.0, 70.0, np.nan]], dtype=np.float32)


def write_lcoe_file(folder: Path, lcoe_2030: np.ndarray | None = None, *, attrs: dict | None = None) -> Path:
    """
    A tiny promoted 85% coverage file for 2025 and 2030 on the fixture's lattice.

    Args:
        folder: The folder to write to.
        lcoe_2030: The 2030 grid (USD/MWh); 2025 less 5 by default.
        attrs: The file's attributes; the contract's by default.

    Returns:
        The file's path.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "optimal_lcoe_test_cov0p85_2025_2030.nc"
    later = LCOE_2025 - 5.0 if lcoe_2030 is None else lcoe_2030
    xr.Dataset(
        {"lcoe": (("year", "lat", "lon"), np.stack([LCOE_2025, later]).astype(np.float32))},
        coords={"year": [2025, 2030], "lat": LAT, "lon": LON},
        attrs=CONTRACT if attrs is None else attrs,
    ).to_netcdf(path)
    return path


def make_geo_paths(tmp_path: Path) -> GeoDataPaths:
    """
    Prepared geo data for the fixture: the iso3 grid, a hierarchy declaring CHN:CN-XX and CHN:CN-YY, and an admin-1
    layer with CN-XX, CN-YY east of the grid, which owns no cell, and the territory DDD, which owns none either.

    Args:
        tmp_path: The test's folder.

    Returns:
        The paths, with ``static_layers_dir`` holding the iso3 grid as ``add_iso3_codes`` reads it.
    """
    data_dir = tmp_path / "data"
    static_layers_dir = data_dir / "outputs" / "GEO"
    static_layers_dir.mkdir(parents=True)
    xr.DataArray(
        np.array(ISO3, dtype=object),
        coords={"lat": LAT, "lon": LON},
        dims=("lat", "lon"),
        name="iso3",
    ).to_netcdf(static_layers_dir / "global_grid_with_iso3.nc")
    (data_dir / "fixtures").mkdir()
    (data_dir / "fixtures" / "geo_hierarchy.json").write_text(
        json.dumps(
            [
                {"iso3": "CHN", "geo_unit": "CN-XX", "geo_key": "CHN:CN-XX"},
                {"iso3": "CHN", "geo_unit": "CN-YY", "geo_key": "CHN:CN-YY"},
            ],
        ),
    )
    admin1_dir = data_dir / geo_unit_lookup.ADMIN1_LAYER_NAME
    admin1_dir.mkdir()
    gpd.GeoDataFrame(
        {"adm0_a3": ["CHN", "CHN", "DDD"], "gu_a3": ["CHN", "CHN", "DDD"], "iso_3166_2": ["CN-XX", "CN-YY", "DD-1"]},
        geometry=[box(0.2, 10.2, 0.6, 10.3), box(1.1, 10.2, 1.3, 10.3), box(-0.6, 9.9, -0.4, 10.1)],
        crs="EPSG:4326",
    ).to_file(admin1_dir / f"{geo_unit_lookup.ADMIN1_LAYER_NAME}.shp")
    return GeoDataPaths(
        data_dir=data_dir,
        geo_plots_dir=None,
        terrain_nc_path=data_dir / "terrain.nc",
        rail_distance_nc_path=data_dir / "rail_distance.nc",
        railway_capex_csv_path=data_dir / "railway_capex.csv",
        lcoh_capex_csv_path=data_dir / "lcoh_capex.csv",
        regional_energy_prices_xlsx=data_dir / "regional_energy_prices.xlsx",
        countries_shapefile_dir=data_dir / "ne_110m_admin_0_countries",
        disputed_areas_shapefile_dir=data_dir / "ne_10m_admin_0_disputed_areas",
        baseload_lcoe_dir=data_dir / "baseload_lcoe",
        static_layers_dir=static_layers_dir,
        landtype_percentage_path=data_dir / "landtype_percentage.nc",
        admin1_shapefile_dir=admin1_dir,
    )


def series(tmp_path: Path, lcoe_file: Path, geo_keys: list[str], percentile: float = 50.0) -> dict:
    """The fixture's baseload LCOE series for 2025 and 2030."""
    return hydrogen_lcoe.baseload_lcoe_by_geo_key_series(
        lcoe_file=lcoe_file,
        coverage=0.85,
        percentile=percentile,
        years=[2025, 2030],
        geo_keys=geo_keys,
        geo_paths=make_geo_paths(tmp_path),
    )


def test_percentile_per_country_spans_its_provinces_and_each_province_stands_alone():
    """A country's percentile counts its provinces' cells; a province takes its own cells only."""
    grid = np.array(ISO3, dtype=object)
    grid[1, 1:3] = "CHN:CN-XX"

    values = hydrogen_lcoe.lcoe_percentile_by_geo_key(LCOE_2025.ravel(), grid.ravel(), percentile=50)

    assert values["AAA"] == 30.0
    assert values["BBB"] == 30.0
    assert values["CHN"] == 50.0
    assert values["CHN:CN-XX"] == 60.0
    assert "CCC" not in values


def test_lower_percentile_moves_towards_the_cheapest_cells():
    """The 25th percentile interpolates linearly between cells, as pandas' quantile does."""
    values = hydrogen_lcoe.lcoe_percentile_by_geo_key(
        LCOE_2025.ravel(),
        np.array(ISO3, dtype=object).ravel(),
        percentile=25,
    )

    assert values["CHN"] == 30.0
    assert values["AAA"] == 25.0


def test_admin1_rows_follow_natural_earth_filing():
    """Dependencies resolve by geounit; BES sits under NLD as NL-BQ*, and SJM under NOR as geounit NSV."""
    admin1 = gpd.GeoDataFrame(
        {
            "adm0_a3": ["FRA", "FRA", "NLD", "NOR", "NOR"],
            "gu_a3": ["FRA", "GLP", "NLD", "NOR", "NSV"],
            "iso_3166_2": ["FR-75", "FR-GP", "NL-BQ1", "NO-03", "NO-21"],
        },
        geometry=[Point(i, 0) for i in range(5)],
    )

    assert hydrogen_lcoe.admin1_rows_of_country(admin1, "FRA")["iso_3166_2"].tolist() == ["FR-75", "FR-GP"]
    assert hydrogen_lcoe.admin1_rows_of_country(admin1, "GLP")["iso_3166_2"].tolist() == ["FR-GP"]
    assert hydrogen_lcoe.admin1_rows_of_country(admin1, "BES")["iso_3166_2"].tolist() == ["NL-BQ1"]
    assert hydrogen_lcoe.admin1_rows_of_country(admin1, "SJM")["iso_3166_2"].tolist() == ["NO-21"]
    assert hydrogen_lcoe.admin1_rows_of_country(admin1, "NOR")["iso_3166_2"].tolist() == ["NO-03", "NO-21"]


def test_series_prices_countries_over_their_provinces_and_provinces_on_their_own_cells_in_usd_per_kwh(tmp_path):
    """Each year's median of a geo key's cells, from a file in USD/MWh, comes out in USD/kWh."""
    lcoe_file = write_lcoe_file(tmp_path / "lcoe")

    result = series(tmp_path, lcoe_file, ["AAA", "BBB", "CHN", "CHN:CN-XX"])

    assert result[Year(2025)] == {
        "AAA": pytest.approx(0.030),
        "BBB": pytest.approx(0.030),
        "CHN": pytest.approx(0.050),
        "CHN:CN-XX": pytest.approx(0.060),
    }
    assert result[Year(2030)]["CHN:CN-XX"] == pytest.approx(0.055)


def test_series_fills_keys_without_cells_from_their_nearest_cells(tmp_path, monkeypatch):
    """
    A key whose own cells have no LCOE and a territory with only an admin-1 shape take the mean of their nearest
    finite cells, and keep those cells in later years.
    """
    monkeypatch.setattr(hydrogen_lcoe, "NEIGHBOUR_PIXELS", 2)
    lcoe_file = write_lcoe_file(tmp_path / "lcoe")

    result = series(tmp_path, lcoe_file, ["AAA", "CCC", "DDD"])

    # CCC's nearest cells are BBB's and CN-XX's eastern one (30, 70); DDD's are the western column (20, 10)
    assert result[Year(2025)]["CCC"] == pytest.approx(0.050)
    assert result[Year(2025)]["DDD"] == pytest.approx(0.015)
    assert result[Year(2030)]["CCC"] == pytest.approx(0.045)
    assert result[Year(2030)]["DDD"] == pytest.approx(0.010)


def test_series_fills_a_province_without_cells_around_its_own_shape(tmp_path, monkeypatch):
    """
    A declared province that owns no cell takes the cells nearest its own admin-1 shape, not its country's; one
    without an admin-1 row stops the series.
    """
    monkeypatch.setattr(hydrogen_lcoe, "NEIGHBOUR_PIXELS", 2)
    lcoe_file = write_lcoe_file(tmp_path / "lcoe")

    result = series(tmp_path / "a", lcoe_file, ["CHN", "CHN:CN-YY"])

    # CN-YY lies east of the grid, so its nearest cells are the eastern priced column (30, 70); China's own shapes
    # would centre on CN-XX, whose nearest cells are 50 and 70
    assert result[Year(2025)]["CHN:CN-YY"] == pytest.approx(0.050)
    with pytest.raises(ValueError, match="No LCOE cell and no admin-1 geometry for CHN:CN-ZZ"):
        series(tmp_path / "b", lcoe_file, ["CHN", "CHN:CN-ZZ"])


def test_series_raises_for_a_key_without_cells_or_admin1_geometry(tmp_path):
    """A key with neither its own cells nor an admin-1 shape cannot be placed, so it is never priced silently."""
    lcoe_file = write_lcoe_file(tmp_path / "lcoe")

    with pytest.raises(ValueError, match="No LCOE cell and no admin-1 geometry for EEE"):
        series(tmp_path, lcoe_file, ["AAA", "EEE"])


def test_series_raises_for_a_key_that_loses_its_cells_in_a_later_year(tmp_path):
    """A key priced from its own cells in the first year that has none in a later year stops the series."""
    lcoe_2030 = LCOE_2025.copy()
    lcoe_2030[0, 2] = np.nan
    lcoe_file = write_lcoe_file(tmp_path / "lcoe", lcoe_2030)

    with pytest.raises(ValueError, match="No baseload LCOE for geo keys BBB in 2030"):
        series(tmp_path, lcoe_file, ["AAA", "BBB"])


def test_series_raises_when_a_neighbour_source_turns_nan(tmp_path, monkeypatch):
    """A key filled from its neighbours whose source cell has no LCOE in a later year stops the series."""
    monkeypatch.setattr(hydrogen_lcoe, "NEIGHBOUR_PIXELS", 2)
    lcoe_2030 = LCOE_2025.copy()
    lcoe_2030[0, 2] = np.nan
    lcoe_file = write_lcoe_file(tmp_path / "lcoe", lcoe_2030)

    with pytest.raises(ValueError, match="Baseload LCOE not finite for geo keys CCC in 2030"):
        series(tmp_path, lcoe_file, ["AAA", "CCC"])


def test_series_raises_on_an_lcoe_grid_off_the_iso3_lattice(tmp_path):
    """A file on another lattice than the iso3 grid is refused rather than read cell by cell out of place."""
    folder = tmp_path / "lcoe"
    folder.mkdir()
    lcoe_file = folder / "optimal_lcoe_test_cov0p85_2025_2030.nc"
    xr.Dataset(
        {"lcoe": (("year", "lat", "lon"), LCOE_2025[np.newaxis])},
        coords={"year": [2025], "lat": LAT, "lon": [0.0, 0.25, 0.5, 1.0]},
        attrs=CONTRACT,
    ).to_netcdf(lcoe_file)

    with pytest.raises(ValueError, match="does not match the iso3 grid"):
        series(tmp_path, lcoe_file, ["AAA"])


@pytest.mark.parametrize("percentile", [-1.0, 100.5])
def test_series_refuses_a_percentile_outside_0_to_100(tmp_path, percentile):
    """The configured percentile must lie between 0 and 100."""
    lcoe_file = write_lcoe_file(tmp_path / "lcoe")

    with pytest.raises(ValueError, match="within 0 to 100"):
        series(tmp_path, lcoe_file, ["AAA"], percentile=percentile)


def test_series_refuses_a_file_without_the_lcoe_basis(tmp_path):
    """The series opens its file through the reader's contract check, so a file without lcoe_basis stops it."""
    lcoe_file = write_lcoe_file(tmp_path / "lcoe", attrs={"schema_version": 1, "coverage_fraction": 0.85})

    with pytest.raises(ValueError, match="lcoe_basis missing"):
        series(tmp_path, lcoe_file, ["AAA"])


def test_series_reads_a_file_stored_longitude_first_the_same(tmp_path):
    """A file stored as (year, lon, lat) gives every geo key the same LCOE as one stored latitude first."""
    lat_first = write_lcoe_file(tmp_path / "lat_first")
    lon_first_folder = tmp_path / "lon_first"
    lon_first_folder.mkdir()
    lon_first = lon_first_folder / lat_first.name
    with xr.open_dataset(lat_first) as ds:
        ds.transpose("year", "lon", "lat").to_netcdf(lon_first)
    keys = ["AAA", "BBB", "CHN", "CHN:CN-XX"]

    expected = series(tmp_path / "a", lat_first, keys)

    assert series(tmp_path / "b", lon_first, keys) == expected


def make_mapping(country: str, iso2: str, iso3: str, region: str) -> CountryMapping:
    """A country mapping with one region for every grouping."""
    return CountryMapping(
        country=country,
        iso2=iso2,
        iso3=iso3,
        irena_name=country,
        region_for_outputs=region,
        ssp_region=region,
        tiam_ucl_region=region,
    )


def make_env(tmp_path: Path, power_mix: str) -> Environment:
    """
    An Environment priced for 2025: two countries in one region and a province of the first without its own
    electrolyser cost, at 50 kWh/kg and the 100th percentile ceiling.

    Args:
        tmp_path: The test's folder, also the run's output folder.
        power_mix: The run's power mix.

    Returns:
        The Environment, without a baseload LCOE series.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2025),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        geo_config=GeoConfig(included_power_mix=power_mix, hydrogen_ceiling_percentile=100.0),
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
    env.hydrogen_efficiency = {Year(2025): 0.05}
    env.hydrogen_capex_opex = {"AAA": {Year(2025): 1.0}, "BBB": {Year(2025): 1.5}}
    env.initiate_country_mappings(
        country_mappings=[
            make_mapping("Aland", "AA", "AAA", "TestRegion"),
            make_mapping("Bland", "BB", "BBB", "TestRegion"),
        ],
    )
    return env


@pytest.fixture
def trace_off_afterwards():
    """Leave the energy-price trace off for every other test."""
    yield
    energy_trace.configure(False, (), (), None)


@pytest.mark.parametrize("power_mix", ["Grid only", "Not included"])
def test_a_mix_without_baseload_prices_hydrogen_at_the_grid_and_needs_no_series(tmp_path, power_mix):
    """Without a baseload share each geo key's LCOH comes from its grid price alone, exactly as from the grid."""
    env = make_env(tmp_path, power_mix)

    prices = env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))

    from_grid = calculate_costs.calculate_lcoh_from_electricity_country_level(
        electricity_by_country={"AAA": 0.05, "AAA:AA-1": 0.08, "BBB": 0.09},
        hydrogen_efficiency=env.hydrogen_efficiency,
        hydrogen_capex_opex=env.hydrogen_capex_opex,
        year=Year(2025),
    )
    assert prices == from_grid
    assert env.hydrogen_price_inputs_by_year[Year(2025)]["AAA"]["lcoe"] is None


def test_baseload_mix_blends_each_geo_keys_grid_price_with_its_own_lcoe(tmp_path):
    """A province blends its own grid price with its own LCOE; an LCOE above the grid is not replaced by the grid."""
    env = make_env(tmp_path, BASELOAD_MIX)
    env.initiate_baseload_lcoe_by_geo_key({Year(2025): {"AAA": 0.03, "AAA:AA-1": 0.02, "BBB": 0.12}})

    prices = env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))

    assert prices["AAA"] == pytest.approx(50 * (0.15 * 0.05 + 0.85 * 0.03) + 1.0)
    assert prices["AAA:AA-1"] == pytest.approx(50 * (0.15 * 0.08 + 0.85 * 0.02) + 1.0)
    assert prices["BBB"] == pytest.approx(50 * (0.15 * 0.09 + 0.85 * 0.12) + 1.5)


def test_price_inputs_record_coverage_grid_lcoe_electricity_and_both_lcohs(tmp_path):
    """Every precomputed price keeps the inputs it was computed from."""
    env = make_env(tmp_path, BASELOAD_MIX)
    env.initiate_baseload_lcoe_by_geo_key({Year(2025): {"AAA": 0.03, "AAA:AA-1": 0.02, "BBB": 0.12}})

    env.initiate_capped_hydrogen_costs_by_year()

    row = env.hydrogen_price_inputs_by_year[Year(2025)]["AAA"]
    assert (row["coverage"], row["grid"], row["lcoe"]) == (0.85, 0.05, 0.03)
    assert row["electricity"] == pytest.approx(0.15 * 0.05 + 0.85 * 0.03)
    assert row["lcoh"] == pytest.approx(50 * row["electricity"] + 1.0)
    assert row["capped_lcoh"] == env.capped_hydrogen_costs_by_year[Year(2025)]["AAA"]
    inputs = env.hydrogen_price_inputs_by_year[Year(2025)]
    assert row["ceiling"] == pytest.approx(max(inputs[key]["lcoh"] for key in ("AAA", "BBB")))  # 100th percentile


def test_existing_plant_file_lists_every_price_and_its_inputs(tmp_path):
    """lcoh_existing_plants.csv has one row per geo key and year, power in USD/MWh and four decimals."""
    env = make_env(tmp_path, BASELOAD_MIX)
    env.initiate_baseload_lcoe_by_geo_key({Year(2025): {"AAA": 0.03, "AAA:AA-1": 0.02, "BBB": 0.12}})
    env.initiate_capped_hydrogen_costs_by_year()
    row = env.hydrogen_price_inputs_by_year[Year(2025)]["AAA"]
    out = tmp_path / "data" / "LCOH" / "lcoh_existing_plants.csv"

    env.export_lcoh_existing_plants(out)

    lines = out.read_text().splitlines()
    assert lines[0] == (
        "level,country,subregion,year,baseload_coverage_pct,grid_share_pct,hydrogen_ceiling_pct,"
        "intraregional_trade_allowed,hydrogen_lcoe_percentile,grid_usd_per_mwh,pure_lcoe_usd_per_mwh,"
        "grid_mix_usd_per_mwh,electrolyser_kwh_per_kg,capex_opex_usd_per_kg,uncapped_usd_per_kg,"
        "ceiling_usd_per_kg,capped_usd_per_kg"
    )
    aaa = next(line for line in lines if line.startswith("country,AAA,,2025")).split(",")
    assert aaa[4:9] == ["85.0000", "15.0000", "100.0000", "False", "50.0000"]
    assert [float(v) for v in aaa[9:14]] == pytest.approx([50.0, 30.0, 0.15 * 50 + 0.85 * 30, 50.0, 1.0])
    assert float(aaa[14]) == pytest.approx(row["lcoh"]) and float(aaa[16]) == pytest.approx(row["capped_lcoh"])
    province = next(line for line in lines if line.startswith("geo_unit,AAA,AA-1,2025")).split(",")
    assert float(province[13]) == 1.0  # no sub-national CAPEX/OPEX row: the country's applies


def test_existing_plant_file_leaves_the_lcoe_empty_without_baseload(tmp_path):
    """Without a baseload share the file prices power at the grid and leaves the pure LCOE empty."""
    env = make_env(tmp_path, "Grid only")
    env.initiate_capped_hydrogen_costs_by_year()
    out = tmp_path / "lcoh_existing_plants.csv"

    env.export_lcoh_existing_plants(out)

    aaa = next(line for line in out.read_text().splitlines() if line.startswith("country,AAA,,2025")).split(",")
    assert aaa[4:6] == ["0.0000", "100.0000"]
    assert aaa[9:12] == ["50.0000", "", "50.0000"]


def test_existing_plant_ceiling_is_the_cap_applied_and_a_province_inherits_it(tmp_path):
    """HydrogenPriceInputs.ceiling is the value each price was capped against, inherited by a province."""
    env = make_env(tmp_path, "Grid only")  # no baseload series needed for this check
    env.config.geo_config.hydrogen_ceiling_percentile = 0.0

    env.initiate_capped_hydrogen_costs_by_year()

    inputs = env.hydrogen_price_inputs_by_year[Year(2025)]
    ceiling = min(inputs[key]["lcoh"] for key in ("AAA", "BBB"))  # 0th percentile of the region's countries
    for row in inputs.values():
        assert row["ceiling"] == pytest.approx(ceiling)
        assert row["capped_lcoh"] == pytest.approx(min(row["lcoh"], ceiling))


def test_existing_plant_file_keeps_only_the_simulated_years(tmp_path):
    """The precompute runs to the last input-cost year; the file stops at the end year."""
    env = make_env(tmp_path, "Grid only")  # no baseload series needed for this check
    for year_costs in env.input_costs.values():
        year_costs[Year(2026)] = year_costs[Year(2025)]
    env.hydrogen_efficiency[Year(2026)] = 0.05
    env.hydrogen_capex_opex = {
        key: {**values, Year(2026): values[Year(2025)]} for key, values in env.hydrogen_capex_opex.items()
    }
    env.initiate_capped_hydrogen_costs_by_year()
    assert Year(2026) in env.hydrogen_price_inputs_by_year
    out = tmp_path / "lcoh_existing_plants.csv"

    env.export_lcoh_existing_plants(out)

    assert {line.split(",")[3] for line in out.read_text().splitlines()[1:]} == {"2025"}


def test_baseload_mix_without_the_lcoe_series_raises(tmp_path):
    """A baseload mix never falls back to the grid price for want of a series."""
    env = make_env(tmp_path, BASELOAD_MIX)

    with pytest.raises(ValueError, match="initiate_baseload_lcoe_by_geo_key"):
        env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))


def test_baseload_mix_with_a_geo_key_missing_from_the_series_raises(tmp_path):
    """A geo key the series does not price stops the precompute, naming the key and the year."""
    env = make_env(tmp_path, BASELOAD_MIX)
    env.initiate_baseload_lcoe_by_geo_key({Year(2025): {"AAA": 0.03, "BBB": 0.12}})

    with pytest.raises(ValueError, match="No baseload LCOE for AAA:AA-1 in year 2025"):
        env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))


def test_blended_prices_pass_every_country_hydrogen_trace_check(tmp_path, trace_off_afterwards):
    """With the trace on, every geo key's electricity, LCOH and capped price pass the trace's own expectations."""
    env = make_env(tmp_path, BASELOAD_MIX)
    env.initiate_baseload_lcoe_by_geo_key({Year(2025): {"AAA": 0.03, "AAA:AA-1": 0.02, "BBB": 0.12}})
    energy_trace.configure(True, [], [], tmp_path)

    env.calculate_capped_hydrogen_costs_per_country(year=Year(2025))

    with (tmp_path / energy_trace.CSV_NAME).open(newline="") as f:
        checks = [row for row in csv.DictReader(f) if row["expected"]]
    assert sorted((row["geo_key"], row["quantity"]) for row in checks) == [
        (geo_key, quantity) for geo_key in ("AAA", "AAA:AA-1", "BBB") for quantity in ("capped", "electricity", "lcoh")
    ]
    assert {row["check_failed"] for row in checks} == {"0"}
