"""Tests for the interactive viewer writer (steelo.utilities.interactive.interactive_plots)."""

import json
from pathlib import Path

import pandas as pd

from steelo.domain.models import CountryMapping
from steelo.utilities.interactive import InteractivePlotter, clearing_config, interactive_plots

BOUNDARY = "worldsteel_opt_credits"
CLEARING = clearing_config(
    capacity_limit=0.95, steel_share=0.95, steel_buffer=200.0, iron_share=0.95, iron_buffer=200.0
)


def sample_country_mappings() -> list[CountryMapping]:
    """Three countries with fixed and dynamically added bloc memberships."""
    common = {"irena_name": "", "ssp_region": "", "tiam_ucl_region": ""}
    return [
        CountryMapping(
            country="Germany",
            iso2="DE",
            iso3="DEU",
            region_for_outputs="Europe",
            EU=True,
            OECD=True,
            G20=True,
            **common,
        ),
        CountryMapping(
            country="China", iso2="CN", iso3="CHN", region_for_outputs="China", RCEP=True, G20=True, **common
        ),
        CountryMapping(country="Aruba", iso2="AW", iso3="ABW", region_for_outputs="Latin America", **common),
    ]


def sample_post_processed() -> pd.DataFrame:
    """Two furnace groups over one year, with a sub-national geo_key."""
    columns = ["year", "geo_key", "furnace_group_id", "technology", "product", "production"]
    rows = [[2025, "CHN:CN-HE", "P1_0", "BF", "iron", 2_000_000.0], [2025, "DEU", "P2_0", "EAF", "steel", 1_000_000.0]]
    table = pd.DataFrame(rows, columns=columns)
    table["capacity"] = [3_000_000.0, 1_200_000.0]
    table[f"emissions_{BOUNDARY}_direct_ghg"] = [5_000_000.0, 100_000.0]
    table[f"emissions_{BOUNDARY}_indirect_ghg"] = [1_000_000.0, 300_000.0]
    return table


def test_trade_bloc_members_from_boolean_attributes() -> None:
    """Every True boolean attribute counts as a bloc, including ones added dynamically; empty blocs vanish."""
    blocs = interactive_plots.trade_bloc_members(sample_country_mappings())

    assert blocs == {"EU": ["DEU"], "G20": ["CHN", "DEU"], "OECD": ["DEU"], "RCEP": ["CHN"]}


def test_geo_unit_names_from_geo_hierarchy(tmp_path) -> None:
    """Sub-national display names are keyed by geo_key; a missing hierarchy leaves the units unnamed."""
    hierarchy = tmp_path / "geo_hierarchy.json"
    hierarchy.write_text(
        json.dumps([{"iso3": "CHN", "geo_unit": "CN-HE", "geo_key": "CHN:CN-HE", "display_name": "Hebei"}])
    )

    assert interactive_plots.geo_unit_names(hierarchy) == {"CHN:CN-HE": "Hebei"}
    assert interactive_plots.geo_unit_names(tmp_path / "absent.json") == {}
    assert interactive_plots.geo_unit_names(None) == {}

    plotter = InteractivePlotter(tmp_path / "plots", [], run_title="sim_test", geo_hierarchy_json=hierarchy)
    assert plotter._config("Emissions")["geoUnitNames"] == {"CHN:CN-HE": "Hebei"}


def test_run_display_title_names_the_run_with_its_completion_time() -> None:
    """The title is the run name (or the fallback) with the CSV's completion timestamp in brackets."""
    csv = Path("post_processed_2026-08-29_05-21.csv")

    assert (
        interactive_plots.run_display_title("china BAU", "sim_20260829_002848", csv) == "china BAU (2026-08-29 05:21)"
    )
    assert (
        interactive_plots.run_display_title(None, "sim_20260829_002848", csv)
        == "sim_20260829_002848 (2026-08-29 05:21)"
    )
    assert interactive_plots.run_display_title(None, "sim_test", Path("post_processed_test.csv")) == "sim_test"


def test_geo_info_names_and_regions_per_iso3() -> None:
    """Country names and output regions are keyed by ISO3 for the shell's labels and region filter."""
    info = interactive_plots.geo_info(sample_country_mappings())

    assert info["DEU"] == {"country": "Germany", "region": "Europe"}
    assert set(info) == {"DEU", "CHN", "ABW"}


def test_plot_emissions_writes_self_contained_viewer(tmp_path) -> None:
    """The emissions viewer lands in plots/interactive with plotly.js, the shell and the payload inlined."""
    csv_path = tmp_path / "post_processed_test.csv"
    sample_post_processed().to_csv(csv_path, index=False)
    plotter = InteractivePlotter(tmp_path / "plots", sample_country_mappings(), run_title="sim_test")

    written = plotter.plot_emissions(csv_path)

    assert written == tmp_path / "plots" / "interactive" / "emissions.html"
    html = written.read_text()
    for placeholder in ("__PLOTLYJS__", "__COMMON_JS__", "__COMMON_CSS__", "__CONFIG__", "__DATA__"):
        assert placeholder not in html
    assert "const Interactive" in html and ".box-dropdown" in html
    assert "sim_test" in html
    assert f'"{BOUNDARY}|direct_ghg"' in html
    assert '"G20": ["CHN", "DEU"]' in html
    assert '"DEU": {"country": "Germany", "region": "Europe"}' in html
    assert "CHN:CN-HE" in html


def test_plot_capacity_and_production_writes_self_contained_viewer(tmp_path) -> None:
    """The capacity and production viewer reuses the table read for the emissions viewer."""
    csv_path = tmp_path / "post_processed_test.csv"
    sample_post_processed().to_csv(csv_path, index=False)
    plotter = InteractivePlotter(tmp_path / "plots", sample_country_mappings(), run_title="sim_test")

    assert plotter.plot_emissions(csv_path) is not None
    written = plotter.plot_capacity_and_production(csv_path)

    assert written == tmp_path / "plots" / "interactive" / "capacity_and_production.html"
    html = written.read_text()
    for placeholder in ("__PLOTLYJS__", "__COMMON_JS__", "__COMMON_CSS__", "__CONFIG__", "__DATA__"):
        assert placeholder not in html
    assert "const Interactive" in html
    assert '"cap": 3.0' in html and '"pr": 2.0' in html
    assert list(plotter._tables) == [csv_path]


def test_plot_cost_curves_writes_self_contained_viewer(tmp_path) -> None:
    """The cost-curve viewer embeds the clearing parameters, the furnace-group rows and the per-year demand."""
    csv_path = tmp_path / "post_processed_test.csv"
    table = sample_post_processed()
    table["unit_production_cost"] = [300.0, 500.0]
    table.to_csv(csv_path, index=False)
    prices_csv = tmp_path / "market_prices_2025_2025.csv"
    pd.DataFrame({"year": [2025], "steel_price_usd_per_t": [600.0], "steel_demand_t": [900_000.0]}).to_csv(
        prices_csv, index=False
    )
    plotter = InteractivePlotter(tmp_path / "plots", sample_country_mappings(), run_title="sim_test")

    written = plotter.plot_cost_curves(csv_path, prices_csv, CLEARING)

    assert written == tmp_path / "plots" / "interactive" / "cost_curves.html"
    html = written.read_text()
    for placeholder in ("__PLOTLYJS__", "__COMMON_JS__", "__COMMON_CSS__", "__CONFIG__", "__DATA__"):
        assert placeholder not in html
    assert "const Interactive" in html
    assert '"clearing": {"capacityLimit": 0.95, "steel": {"share": 0.95, "buffer": 200.0}' in html
    assert '"fg": "P1_0", "cap": 3.0, "pr": 2.0, "c": 300.0' in html
    assert '"steel": {"2025": {"d": 0.9, "c": 500.0}}' in html
    assert "steel demand from market_prices_2025_2025.csv" in html

    # Without a recorded demand (older runs) steel clears against its production.
    assert plotter.plot_cost_curves(csv_path, tmp_path / "absent.csv", CLEARING) == written
    assert '"steel": {"2025": {"d": 1.0, "c": 500.0}}' in written.read_text()


def test_plot_trade_matrix_writes_self_contained_viewer(tmp_path) -> None:
    """The trade-matrix viewer embeds every allocation year and the country-pair flows; no files → no viewer."""
    tm_dir = tmp_path / "TM"
    tm_dir.mkdir()
    header = "commodity,source_type,source_id,source_location,capacity_at_source,source_tech,destination_type,"
    header += (
        "destination_id,destination_location,allocated_volume,allocation_cost,demand_at_destination,supply_at_source"
    )
    loc = (
        "Location(lat=1.0, lon=2.0, country='{0}', region='R', iso3='{0}', distance_to_other_iso3=None, geo_unit=None)"
    )
    row = f'steel,Plant-FurnaceGroup,P1_0,"{loc.format("CHN")}",1e6,BOF,DemandCenter,India,"{loc.format("IND")}",2e6,0,2e6,N/A'
    (tm_dir / "steel_trade_allocations_2025.csv").write_text(f"{header}\n{row}\n")
    (tm_dir / "steel_trade_allocations_2026.csv").write_text(f"{header}\n")
    plotter = InteractivePlotter(tmp_path / "plots", sample_country_mappings(), run_title="sim_test")

    written = plotter.plot_trade_matrix(tm_dir)

    assert written == tmp_path / "plots" / "interactive" / "trade_matrix.html"
    html = written.read_text()
    for placeholder in ("__PLOTLYJS__", "__COMMON_JS__", "__COMMON_CSS__", "__CONFIG__", "__DATA__"):
        assert placeholder not in html
    assert "const Interactive" in html
    assert '"years": [2025, 2026]' in html
    assert '{"y": 2025, "p": "steel", "c": "steel", "o": "CHN", "d": "IND", "t": "BOF", "v": 2.0}' in html
    assert plotter.plot_trade_matrix(tmp_path / "absent") is None
