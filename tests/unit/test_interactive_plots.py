"""Tests for the interactive viewer writer (steelo.utilities.interactive.interactive_plots)."""

import json

import pandas as pd

from steelo.domain.models import CountryMapping
from steelo.utilities.interactive import InteractivePlotter, interactive_plots

BOUNDARY = "worldsteel_opt_credits"


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
