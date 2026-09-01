"""Tests for the interactive viewer writer (steelo.utilities.interactive.interactive_plots)."""

import json

from steelo.domain.models import CountryMapping
from steelo.utilities.interactive import InteractivePlotter, interactive_plots


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
