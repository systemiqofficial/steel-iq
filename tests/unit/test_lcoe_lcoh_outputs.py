"""Tests for the all-years LCOE and LCOH output files written from the GEO pipeline's cells."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from steelo.adapters.geospatial import geospatial_calculations, geospatial_statistics
from steelo.domain.models import CountryMapping, Environment, Year
from steelo.economic_models import plant_agent
from steelo.simulation import GeoConfig, SimulationConfig

# Two rows of four cells: AAA (IRENA Europe) with two geo units in different Natural Earth regions and one cell
# outside both; BBB (IRENA Asia) in another hydrogen trade region; one cell without a country and one BBB cell
# without a hydrogen price, which no row may count.
ISO3 = [["AAA", "AAA", "AAA", "AAA"], ["BBB", "BBB", np.nan, "BBB"]]
GEO_KEY = [["AAA:AA-1", "AAA:AA-1", "AAA:AA-2", "AAA"], ["BBB", "BBB", "nan", "BBB"]]
GRID = [[0.05, 0.05, 0.08, 0.06], [0.10, 0.10, 0.10, 0.10]]  # USD/kWh
LCOE = [[0.02, 0.04, 0.03, 0.05], [0.06, 0.08, 0.07, 0.09]]
LCOH = [[2.0, 3.0, 2.5, 3.5], [4.0, 5.0, 4.5, np.nan]]
CEILING = [[3.2, 3.2, 3.2, 3.2], [9.0, 9.0, np.nan, 9.0]]
REGION = [["Europe H2", "Europe H2", "Europe H2", "Europe H2"], ["Asia H2", "Asia H2", np.nan, "Asia H2"]]
IRENA = {"AAA": "Europe", "BBB": "Asia"}
NE_REGION = {"AAA:AA-1": "North", "AAA:AA-2": "South"}
EXISTING = {"AAA": 2.6, "AAA:AA-1": 2.1, "BBB": 4.4}
FILES = {
    "lcoe_summary": "data/LCOE/lcoe_summary.csv",
    "lcoe_detailed": "data/LCOE/lcoe_detailed.csv",
    "lcoh_summary": "data/LCOH/lcoh_summary.csv",
    "lcoh_new_sites_detailed": "data/LCOH/lcoh_new_sites_detailed.csv",
}


def energy_prices(coverage: float, geo_key: list[list[str]] = GEO_KEY) -> xr.Dataset:
    """
    The GEO pipeline's per-cell dataset for the synthetic grid.

    Args:
        coverage: Baseload coverage; the dataset carries ``lcoe`` only above zero.
        geo_key: Each cell's geo key.

    Returns:
        The dataset, with power at ``(1 - c) × grid + c × lcoe`` and hydrogen capped at the ceilings.
    """
    grid, lcoe, lcoh = np.array(GRID), np.array(LCOE), np.array(LCOH)
    ds = xr.Dataset(
        {
            "iso3": (("lat", "lon"), np.array(ISO3, dtype=object)),
            "geo_key": (("lat", "lon"), np.array(geo_key, dtype=object)),
            "grid_price": (("lat", "lon"), grid),
            "power_price": (("lat", "lon"), (1 - coverage) * grid + coverage * lcoe),
            "lcoh": (("lat", "lon"), lcoh),
            "capped_lcoh": (("lat", "lon"), np.minimum(lcoh, np.array(CEILING))),
            "hydrogen_ceiling": (("lat", "lon"), np.array(CEILING)),
            "tiam_ucl_region": (("lat", "lon"), np.array(REGION, dtype=object)),
        },
        coords={"lat": [0.0, 0.25], "lon": [0.0, 0.25, 0.5, 0.75]},
    )
    if coverage > 0:
        ds["lcoe"] = (("lat", "lon"), lcoe)
    return ds


def write_outputs(
    tmp_path: Path,
    mix: str = "85% baseload + 15% grid",
    years: tuple[int, ...] = (2025,),
    **arguments: object,
) -> dict[str, pd.DataFrame]:
    """
    Write the four cell-based files for the given years, the first being the start year, and read them back.

    Args:
        tmp_path: The run's output folder.
        mix: The power mix.
        years: The simulation years written, in order.
        **arguments: Overrides of ``export_lcoe_lcoh_outputs``'s data arguments.

    Returns:
        Each file as a frame, keyed by its name without the extension.
    """
    coverage = {"85% baseload + 15% grid": 0.85, "Grid only": 0.0}[mix]
    config = GeoConfig(included_power_mix=mix, hydrogen_ceiling_percentile=100.0, intraregional_trade_allowed=False)
    data = {
        "energy_prices": energy_prices(coverage),
        "irena_region_by_iso3": IRENA,
        "ne_region_by_geo_key": NE_REGION,
        "existing_plant_lcoh": EXISTING,
        **arguments,
    }
    for year in years:
        geospatial_statistics.export_lcoe_lcoh_outputs(
            year=year,
            start_year=years[0],
            output_dir=tmp_path,
            geo_config=config,
            **data,  # type: ignore[arg-type]
        )
    return {name: pd.read_csv(tmp_path / path).fillna({"subregion": ""}) for name, path in FILES.items()}


def area(frame: pd.DataFrame, level: str, country: str, subregion: str = "") -> pd.DataFrame:
    """The rows of one area."""
    return frame[(frame["level"] == level) & (frame["country"] == country) & (frame["subregion"] == subregion)]


def test_every_area_level_appears_with_its_own_cells(tmp_path):
    """World, IRENA, country, geo unit region and geo unit rows each count exactly their priced cells."""
    summary = write_outputs(tmp_path)["lcoe_summary"]

    counts = {tuple(row): n for *row, n in summary[["level", "country", "subregion", "n_grid_points"]].values}
    assert counts == {
        ("world", "World", ""): 6,
        ("irena_region", "irena_Asia", ""): 2,
        ("irena_region", "irena_Europe", ""): 4,
        ("country", "AAA", ""): 4,
        ("country", "BBB", ""): 2,
        ("geo_unit_region", "AAA", "North"): 2,
        ("geo_unit_region", "AAA", "South"): 1,
        ("geo_unit", "AAA", "AA-1"): 2,
        ("geo_unit", "AAA", "AA-2"): 1,
    }


def test_a_cell_outside_every_geo_unit_counts_only_in_its_country(tmp_path):
    """AAA's fourth cell lifts the country's grid mean but no geo unit or geo unit region row."""
    summary = write_outputs(tmp_path)["lcoe_summary"]

    assert area(summary, "country", "AAA")["grid_avg_usd_per_mwh"].item() == pytest.approx(60.0)
    assert set(summary.loc[summary["level"].str.startswith("geo_unit"), "grid_avg_usd_per_mwh"]) == {50.0, 80.0}


def test_two_years_append_to_one_file_under_one_header(tmp_path):
    """Each file holds every year under a single header."""
    files = write_outputs(tmp_path, years=(2025, 2026))

    for name, frame in files.items():
        assert sorted(frame["year"].unique()) == [2025, 2026], name
    for path in FILES.values():
        assert (tmp_path / path).read_text().count("level,country") == 1, path


def test_the_start_year_writes_every_file_afresh(tmp_path):
    """A rerun into the same folder replaces the files an earlier run left, so no area-year appears twice."""
    first = write_outputs(tmp_path, years=(2025, 2026))
    rerun = write_outputs(tmp_path, years=(2025, 2026))

    for name, frame in rerun.items():
        keys = [column for column in ("level", "country", "subregion", "year", "price_type") if column in frame]
        assert len(frame) == len(first[name]), name
        assert not frame.duplicated(subset=keys).any(), name
    for path in FILES.values():
        assert (tmp_path / path).read_text().count("level,country") == 1, path


def test_without_a_hierarchy_no_geo_unit_rows_are_written(tmp_path):
    """Without geo_hierarchy.json cells keep country keys and no region names exist."""
    country_keys = [["AAA"] * 4, ["BBB", "BBB", "nan", "BBB"]]
    files = write_outputs(
        tmp_path,
        energy_prices=energy_prices(0.85, geo_key=country_keys),
        ne_region_by_geo_key={},
    )

    assert set(files["lcoh_summary"]["level"]) == {"world", "irena_region", "country"}
    assert geospatial_statistics.load_ne_region_by_geo_key(tmp_path / "missing.json") == {}


def test_hierarchy_regions_come_from_ne_region(tmp_path):
    """Units map to their ne_region; units with a blank ne_region are left out."""
    hierarchy = tmp_path / "geo_hierarchy.json"
    hierarchy.write_text(
        json.dumps(
            [
                {"iso3": "AAA", "geo_unit": "AA-1", "geo_key": "AAA:AA-1", "ne_region": "North"},
                {"iso3": "AAA", "geo_unit": "AA-3", "geo_key": "AAA:AA-3", "ne_region": ""},
            ],
        ),
    )

    assert geospatial_statistics.load_ne_region_by_geo_key(hierarchy) == {"AAA:AA-1": "North"}


def test_lcoe_price_types_follow_the_coverage(tmp_path):
    """Pure LCOE rows exist only with a baseload share; grid and grid_mix rows always do."""
    with_baseload = write_outputs(tmp_path / "baseload")["lcoe_detailed"]
    grid_only = write_outputs(tmp_path / "grid", mix="Grid only")

    per_area = with_baseload.groupby(["level", "country", "subregion"])["price_type"].apply(sorted)
    assert per_area.map(tuple).unique().tolist() == [("grid", "grid_mix", "pure_lcoe")]
    assert set(grid_only["lcoe_detailed"]["price_type"]) == {"grid", "grid_mix"}
    assert grid_only["lcoe_summary"]["pure_lcoe_avg_usd_per_mwh"].isna().all()
    assert (grid_only["lcoe_summary"]["baseload_coverage_pct"] == 0).all()


def test_grid_mix_mean_is_the_blend_of_the_means(tmp_path):
    """For every area, grid_mix avg = grid share × grid avg + coverage × pure LCOE avg."""
    summary = write_outputs(tmp_path)["lcoe_summary"]

    expected = 0.15 * summary["grid_avg_usd_per_mwh"] + 0.85 * summary["pure_lcoe_avg_usd_per_mwh"]
    np.testing.assert_allclose(summary["grid_mix_avg_usd_per_mwh"], expected, atol=1e-3)
    assert (summary["grid_share_pct"] == 15).all() and (summary["baseload_coverage_pct"] == 85).all()


def test_shared_settings_columns(tmp_path):
    """grid_share_pct is 0 on pure_lcoe rows; the hydrogen files carry the ceiling and trade settings."""
    files = write_outputs(tmp_path)
    detailed = files["lcoe_detailed"]

    assert set(detailed.loc[detailed["price_type"] == "pure_lcoe", "grid_share_pct"]) == {0}
    assert set(detailed.loc[detailed["price_type"] != "pure_lcoe", "grid_share_pct"]) == {15}
    for name in ("lcoh_summary", "lcoh_new_sites_detailed"):
        assert set(files[name]["hydrogen_ceiling_pct"]) == {100}
        assert set(files[name]["intraregional_trade_allowed"]) == {False}


def test_summary_columns_are_the_documented_ones(tmp_path):
    """The two summaries keep their column names, which readers of the files rely on."""
    files = write_outputs(tmp_path)

    assert list(files["lcoe_summary"].columns) == [
        "level",
        "country",
        "subregion",
        "year",
        "baseload_coverage_pct",
        "grid_share_pct",
        "pure_lcoe_avg_usd_per_mwh",
        "pure_lcoe_p50_usd_per_mwh",
        "grid_avg_usd_per_mwh",
        "grid_mix_avg_usd_per_mwh",
        "grid_mix_p10_usd_per_mwh",
        "grid_mix_p50_usd_per_mwh",
        "grid_mix_p90_usd_per_mwh",
        "n_grid_points",
    ]
    assert list(files["lcoh_summary"].columns) == [
        "level",
        "country",
        "subregion",
        "year",
        "baseload_coverage_pct",
        "grid_share_pct",
        "hydrogen_ceiling_pct",
        "intraregional_trade_allowed",
        "new_site_avg_usd_per_kg",
        "new_site_p10_usd_per_kg",
        "new_site_p50_usd_per_kg",
        "new_site_p90_usd_per_kg",
        "existing_plant_usd_per_kg",
        "n_grid_points",
    ]


def test_summary_cells_equal_their_detailed_counterparts(tmp_path):
    """Every summary statistic equals the matching cell of its detailed file."""
    files = write_outputs(tmp_path)
    keys = ["level", "country", "subregion"]
    lcoe = files["lcoe_summary"].set_index(keys)
    lcoh = files["lcoh_summary"].set_index(keys)
    lcoe_detailed = files["lcoe_detailed"].set_index(keys)
    capped = files["lcoh_new_sites_detailed"].query("price_type == 'capped'").set_index(keys)

    for price_type, stat in [("pure_lcoe", "avg"), ("pure_lcoe", "p50"), ("grid", "avg")] + [
        ("grid_mix", stat) for stat in ("avg", "p10", "p50", "p90")
    ]:
        detailed = lcoe_detailed.loc[lcoe_detailed["price_type"] == price_type, f"{stat}_usd_per_mwh"]
        pd.testing.assert_series_equal(
            lcoe[f"{price_type}_{stat}_usd_per_mwh"],
            detailed,
            check_names=False,
            check_index=True,
        )
    for stat in ("avg", "p10", "p50", "p90"):
        pd.testing.assert_series_equal(
            lcoh[f"new_site_{stat}_usd_per_kg"],
            capped[f"{stat}_usd_per_kg"],
            check_names=False,
        )


def test_statistics_match_numpy_percentiles(tmp_path):
    """pN is np.percentile with linear interpolation; p0 and p100 are the cheapest and dearest cell."""
    capped = write_outputs(tmp_path)["lcoh_new_sites_detailed"].query("price_type == 'capped'")
    world = area(capped, "world", "World")
    values = np.array([2.0, 3.0, 2.5, 3.2, 4.0, 5.0])

    assert world["p0_usd_per_kg"].item() == pytest.approx(values.min())
    assert world["p100_usd_per_kg"].item() == pytest.approx(values.max())
    assert world["p25_usd_per_kg"].item() == pytest.approx(np.percentile(values, 25), abs=1e-4)
    assert world["avg_usd_per_kg"].item() == pytest.approx(values.mean(), abs=1e-4)


def test_existing_plant_price_only_on_country_and_geo_unit_rows(tmp_path):
    """Existing-plant prices fill country and geo unit rows only, and only where that key has a price."""
    summary = write_outputs(tmp_path)["lcoh_summary"]

    filled = summary.dropna(subset=["existing_plant_usd_per_kg"])
    assert set(filled["level"]) == {"country", "geo_unit"}
    assert area(summary, "geo_unit", "AAA", "AA-1")["existing_plant_usd_per_kg"].item() == 2.1
    assert area(summary, "geo_unit", "AAA", "AA-2")["existing_plant_usd_per_kg"].isna().all()  # no own price
    assert area(summary, "country", "BBB")["existing_plant_usd_per_kg"].item() == 4.4


def test_ceiling_is_empty_for_an_area_spanning_two_trade_regions(tmp_path):
    """World spans two hydrogen trade regions and has no ceiling; a country in one region carries its ceiling."""
    detailed = write_outputs(tmp_path)["lcoh_new_sites_detailed"]

    assert area(detailed, "world", "World")["ceiling_usd_per_kg"].isna().all()
    assert set(area(detailed, "country", "AAA")["ceiling_usd_per_kg"]) == {3.2}
    assert set(area(detailed, "geo_unit", "AAA", "AA-1")["price_type"]) == {"uncapped", "capped"}
    assert area(detailed, "country", "AAA").query("price_type == 'capped'")["p100_usd_per_kg"].item() == 3.2


def test_cell_ceiling_is_the_value_each_cell_is_capped_against():
    """apply_hydrogen_price_cap stores the trade-adjusted ceiling effective_hydrogen_ceilings gives per cell."""
    ds = xr.Dataset(
        {
            "lcoh": (("lat", "lon"), np.array([[2.0, 6.0, 5.0]])),
            "tiam_ucl_region": (("lat", "lon"), np.array([["A", "A", "B"]], dtype=object)),
        },
        coords={"lat": [0.0], "lon": [0.0, 0.25, 0.5]},
    )
    config = GeoConfig(
        intraregional_trade_allowed=True,
        intraregional_trade_matrix={"A": ["B"], "B": None},
        long_dist_pipeline_transport_cost=0.5,
    )

    result = geospatial_calculations.apply_hydrogen_price_cap(
        ds, geospatial_calculations.effective_hydrogen_ceilings({"A": 4.0, "B": 3.0}, config)
    )

    np.testing.assert_allclose(result["hydrogen_ceiling"].values, [[3.5, 3.5, 3.0]])  # A: min(4, 3 + 0.5)
    np.testing.assert_allclose(result["capped_lcoh"].values, [[2.0, 3.5, 3.0]])


def test_only_the_new_files_are_written(tmp_path):
    """The per-year statistics files are gone: only the four cell-based files are written."""
    write_outputs(tmp_path, years=(2025, 2030))

    written = sorted(str(p.relative_to(tmp_path / "data")) for p in (tmp_path / "data").rglob("*.csv"))
    assert written == [
        "LCOE/lcoe_detailed.csv",
        "LCOE/lcoe_summary.csv",
        "LCOH/lcoh_new_sites_detailed.csv",
        "LCOH/lcoh_summary.csv",
    ]
    assert not hasattr(geospatial_statistics, "export_lcoe_lcoh_statistics_by_country")
    assert not hasattr(geospatial_statistics, "aggregate_lcoe_lcoh_statistics")


def test_geospatial_step_hands_the_writer_the_held_existing_plant_prices_and_stops_on_its_error(
    tmp_path,
    monkeypatch,
):
    """
    Past the input-cost horizon the writer gets existing plants' prices of the last covered year, as plants pay
    them, and an error from the writer stops the geospatial step instead of being logged and passed over.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2027),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        geo_config=GeoConfig(included_power_mix="Grid only"),  # no baseload series needed for this check
    )
    tech_switches_csv = tmp_path / "tech_switches_allowed.csv"
    tech_switches_csv.write_text("origin,BF\nBF,YES\n", encoding="utf-8")
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    env.input_costs = {"AAA": {Year(2025): {"electricity": 0.05}, Year(2026): {"electricity": 0.04}}}
    env.hydrogen_efficiency = {Year(2025): 0.05, Year(2026): 0.05}
    env.hydrogen_capex_opex = {"AAA": {Year(2025): 1.0, Year(2026): 1.0}}
    env.initiate_country_mappings(
        country_mappings=[
            CountryMapping(
                country="Aland",
                iso2="AA",
                iso3="AAA",
                irena_name="Aland",
                region_for_outputs="TestRegion",
                ssp_region="TestRegion",
                tiam_ucl_region="TestRegion",
            ),
        ],
    )
    env.initiate_capped_hydrogen_costs_by_year()
    env.year = Year(2027)
    env.geo_paths = Mock(data_dir=tmp_path)
    env.virgin_iron_demand = Mock(get_demand=lambda year: 0.0)
    monkeypatch.setattr(env, "extract_price_from_costcurve", lambda **kwargs: 500.0)
    bus = SimpleNamespace(env=env, uow=Mock())
    bus.uow.plant_groups.list.return_value = []
    cells = energy_prices(0.0)
    monkeypatch.setattr(
        plant_agent,
        "get_candidate_locations_for_opening_new_plants",
        lambda *args: ({"iron": [{}], "steel": [{}]}, cells),
    )
    received: dict[str, object] = {}

    def failing_writer(**arguments: object) -> None:
        received.update(arguments)
        raise RuntimeError("could not write the LCOE and LCOH files")

    monkeypatch.setattr(plant_agent, "export_lcoe_lcoh_outputs", failing_writer)

    with pytest.raises(RuntimeError, match="could not write the LCOE and LCOH files"):
        plant_agent.GeospatialModel.run(bus)  # type: ignore[arg-type]

    assert received["energy_prices"] is cells
    assert received["year"] == 2027
    assert received["existing_plant_lcoh"] == env.capped_hydrogen_costs_by_year[Year(2026)]
    assert received["existing_plant_lcoh"] != env.capped_hydrogen_costs_by_year[Year(2025)]
