"""Tests for the interactive viewer writer (steelo.utilities.interactive.interactive_plots)."""

import json
from pathlib import Path

import pandas as pd

from steelo.adapters.repositories.json_repository import (
    BiomassAvailabilityJsonRepository,
    PrimaryFeedstockJsonRepository,
    SupplierJsonRepository,
)
from steelo.domain.models import BiomassAvailability, CountryMapping, Location, PrimaryFeedstock, Supplier, Year
from steelo.domain.models import Volumes
from steelo.utilities.interactive import InteractivePlotter, clearing_config, interactive_plots
from steelo.utilities.interactive import supply_demand

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
    table["chosen_reductant"] = ["coke+pci", None]
    table["feedstock"] = ["io_low", "scrap"]
    table["demand"] = [3_000_000.0, 1_100_000.0]
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


def test_plot_trade_network_writes_self_contained_viewer(tmp_path) -> None:
    """The trade-network viewer embeds the same years and flows as the matrix; no files → no viewer."""
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

    written = plotter.plot_trade_network(tm_dir)

    assert written == tmp_path / "plots" / "interactive" / "trade_network.html"
    html = written.read_text()
    for placeholder in ("__PLOTLYJS__", "__COMMON_JS__", "__COMMON_CSS__", "__CONFIG__", "__DATA__"):
        assert placeholder not in html
    assert "const Interactive" in html
    assert '"chartTitle": "Trade network"' in html
    assert '"years": [2025, 2026]' in html
    assert '{"y": 2025, "p": "steel", "c": "steel", "o": "CHN", "d": "IND", "t": "BOF", "v": 2.0}' in html
    assert '"coords": {"CHN": [1.0, 2.0], "IND": [1.0, 2.0]}' in html
    assert plotter.plot_trade_network(tmp_path / "absent") is None


def supply_demand_country_mappings() -> list[CountryMapping]:
    """Three countries with TIAM-UCL regions, one of them commonly labelled with an ampersand."""
    common = {"irena_name": "", "ssp_region": ""}
    return [
        CountryMapping(
            country="China", iso2="CN", iso3="CHN", region_for_outputs="China", tiam_ucl_region="CHI", **common
        ),
        CountryMapping(
            country="Germany", iso2="DE", iso3="DEU", region_for_outputs="Europe", tiam_ucl_region="WEU", **common
        ),
        CountryMapping(
            country="Bosnia and Herzegovina",
            iso2="BA",
            iso3="BIH",
            region_for_outputs="Europe",
            tiam_ucl_region="EEU",
            **common,
        ),
    ]


def write_supply_demand_allocations(tm_dir: Path) -> None:
    """One allocation file with steel to a demand centre, scrap, a label-only ore mine and bio-PCI."""
    tm_dir.mkdir(parents=True, exist_ok=True)
    header = "commodity,source_type,source_id,source_location,capacity_at_source,source_tech,destination_type,"
    header += (
        "destination_id,destination_location,allocated_volume,allocation_cost,demand_at_destination,supply_at_source"
    )
    loc = (
        "Location(lat=1.0, lon=2.0, country='{0}', region='R', iso3='{1}', distance_to_other_iso3=None, geo_unit=None)"
    )
    deu, chn = loc.format("Germany", "DEU"), loc.format("China", "CHN")
    mine = loc.format("Bosnia & Herzegovina", "")
    rows = [
        # Two plants supply the same demand centre: its demand counts once, deliveries sum.
        f'steel,Plant-FurnaceGroup,P1_0,"{deu}",1e6,EAF,DemandCenter,China_2,"{chn}",1000000.0,0,2000000.0,N/A',
        f'steel,Plant-FurnaceGroup,P2_0,"{chn}",1e6,BOF,DemandCenter,China_2,"{chn}",500000.0,0,2000000.0,N/A',
        f'scrap,Supplier,Germany_scrap,"{deu}",N/A,N/A,Plant-FurnaceGroup,P2_0,"{chn}",300000.0,0,N/A,1000000.0',
        f'io_mid,Supplier,sup_1,"{mine}",N/A,N/A,Plant-FurnaceGroup,P2_0,"{chn}",200000.0,0,N/A,500000.0',
        f'bio_pci,Supplier,bio_pci_supply,"{loc.format("virtual", "XXX")}",N/A,N/A,Plant-FurnaceGroup,P2_0,"{chn}",'
        "100000.0,0,N/A,1e9",
    ]
    (tm_dir / "steel_trade_allocations_2030.csv").write_text("\n".join([header, *rows]) + "\n")


def test_geo_resolver_maps_labels_to_iso3() -> None:
    """ISO3 codes pass through, country names resolve (ampersand read as 'and'), unknowns stay as labels."""
    resolve = supply_demand.geo_resolver(supply_demand_country_mappings())

    assert resolve("CHN") == "CHN"
    assert resolve("Germany") == "DEU"
    assert resolve("Bosnia & Herzegovina") == "BIH"
    assert resolve("Atlantis") == "Atlantis"


def test_tiam_regions_members_by_label() -> None:
    """TIAM-UCL regions map to their member ISO3 codes for the shared regional budgets."""
    assert supply_demand.tiam_regions(supply_demand_country_mappings()) == {
        "CHI": ["CHN"],
        "EEU": ["BIH"],
        "WEU": ["DEU"],
    }


def test_read_usage_groups_use_and_steel_demand_by_country(tmp_path) -> None:
    """Steel counts at the demand centre (demand once per centre), scrap/ore at the source, bio at the consumer."""
    write_supply_demand_allocations(tmp_path / "TM")
    resolve = supply_demand.geo_resolver(supply_demand_country_mappings())

    used, steel_demand = supply_demand.read_usage({2030: tmp_path / "TM" / "steel_trade_allocations_2030.csv"}, resolve)

    used_rows = {(r.year, r.group, r.geo, r.grade): r.volume_mt for r in used.itertuples()}
    assert used_rows == {
        (2030, "steel", "CHN", ""): 1.5,
        (2030, "scrap", "DEU", ""): 0.3,
        (2030, "ore", "BIH", "io_mid"): 0.2,  # ore keeps its grade for the viewer's grade ticks
        (2030, "bio", "CHN", ""): 0.1,
    }
    assert [(r.year, r.geo, r.volume_mt) for r in steel_demand.itertuples()] == [(2030, "CHN", 2.0)]


def test_availability_rows_from_suppliers_and_constraints() -> None:
    """Scrap/ore capacity per country, CO2 limits per country, biomass budgets per prefixed TIAM region;
    a region label that is itself an ISO3 code maps to that single country, an unknown one is dropped."""
    mappings = supply_demand_country_mappings()
    scrap_location = Location(lat=1.0, lon=2.0, country="Germany", region="R", iso3="DEU")
    mine_location = Location(lat=1.0, lon=2.0, country="Bosnia & Herzegovina", region="R", iso3="")
    suppliers = [
        Supplier("Germany_scrap", scrap_location, "scrap", {2030: 1_000_000, 2031: 9e9}, {}),
        Supplier("Germany_scrap_2", scrap_location, "scrap", {2030: 500_000}, {}),
        Supplier("sup_1", mine_location, "io_mid", {2030: 400_000}, {}),
        Supplier("other", scrap_location, "coal", {2030: 7e7}, {}),
    ]
    items = [
        BiomassAvailability("WEU", "DEU", "CO2 storage capacity", "base", "t", 2030, 2_000_000.0),
        BiomassAvailability("CHI", None, "Biomass availability", "base", "t", 2030, 3_000_000.0),
        BiomassAvailability("DEU", None, "Biomass availability", "base", "t", 2030, 1_000_000.0),
        BiomassAvailability("Nowhere", None, "Biomass availability", "base", "t", 2030, 5e6),
    ]

    avail, budgets = supply_demand.availability_rows(suppliers, items, mappings, {2030})

    rows = {(r.year, r.group, r.geo, r.grade): r.volume_mt for r in avail.itertuples()}
    assert rows == {
        (2030, "scrap", "DEU", ""): 1.5,  # sub-centres summed; 2031 and non-scrap/ore commodities excluded
        (2030, "ore", "BIH", "io_mid"): 0.4,
        (2030, "co2", "DEU", ""): 2.0,
        (2030, "bio", "region:CHI", ""): 3.0,  # the unknown region's budget is dropped
        (2030, "bio", "DEU", ""): 1.0,  # region label "DEU" is an ISO3, so a single-country budget
    }
    assert budgets == {"region:CHI": ["CHN"]}


def test_plot_supply_demand_writes_self_contained_viewer(tmp_path) -> None:
    """The viewer embeds usage, availability and the regional budgets; missing fixtures only omit availability."""
    write_supply_demand_allocations(tmp_path / "TM")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    scrap_location = Location(lat=1.0, lon=2.0, country="Germany", region="R", iso3="DEU")
    SupplierJsonRepository(fixtures / "suppliers.json").add_list(
        [Supplier("Germany_scrap", scrap_location, "scrap", {Year(2030): Volumes(1_000_000)}, {})]
    )
    BiomassAvailabilityJsonRepository(fixtures / "biomass_availability.json").add_list(
        [BiomassAvailability("CHI", None, "Biomass availability", "base", "t", Year(2030), 3_000_000.0)]
    )
    plotter = InteractivePlotter(tmp_path / "plots", supply_demand_country_mappings(), run_title="sim_test")

    written = plotter.plot_supply_demand(
        tmp_path / "TM",
        suppliers_json=fixtures / "suppliers.json",
        biomass_availability_json=fixtures / "biomass_availability.json",
    )

    assert written == tmp_path / "plots" / "interactive" / "supply_demand.html"
    html = written.read_text()
    for placeholder in ("__PLOTLYJS__", "__COMMON_JS__", "__COMMON_CSS__", "__CONFIG__", "__DATA__"):
        assert placeholder not in html
    assert "const Interactive" in html
    assert '{"y": 2030, "c": "scrap", "g": "DEU", "v": 0.3}' in html
    assert '{"y": 2030, "c": "steel", "g": "CHN", "v": 2.0}' in html  # the demand centre's demand
    assert '{"y": 2030, "c": "bio", "g": "region:CHI", "v": 3.0}' in html
    assert '"regionBudgets": {"region:CHI": ["CHN"]}' in html

    # Missing fixtures still produce the viewer, with usage and steel demand only.
    assert plotter.plot_supply_demand(tmp_path / "TM") == written
    html = written.read_text()
    assert '{"y": 2030, "c": "ore", "g": "BIH", "v": 0.2, "s": "io_mid"}' in html
    assert '"c": "bio", "g": "region:CHI"' not in html
    assert plotter.plot_supply_demand(tmp_path / "absent") is None


def sample_primary_feedstocks() -> list[PrimaryFeedstock]:
    """Bill of Materials entries matching sample_post_processed's furnace groups."""

    def entry(technology, metallic_charge, reductant, required, energy):
        feedstock = PrimaryFeedstock(metallic_charge=metallic_charge, reductant=reductant, technology=technology)
        feedstock.required_quantity_per_ton_of_product = required
        feedstock.energy_requirements = energy
        return feedstock

    return [
        entry("BF", "io_low", "coke+pci", 1.5, {"coking_coal": 0.4, "pci": 0.1, "electricity": 300.0}),
        entry("EAF", "scrap", "", 1.1, {"electricity": 500.0}),
    ]


def test_plot_reductant_use_writes_self_contained_viewer(tmp_path) -> None:
    """The reductant-use viewer embeds the carrier metadata and the per-reductant cube."""
    csv_path = tmp_path / "post_processed_test.csv"
    sample_post_processed().to_csv(csv_path, index=False)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    PrimaryFeedstockJsonRepository(fixtures / "primary_feedstocks.json").add_list(sample_primary_feedstocks())
    plotter = InteractivePlotter(tmp_path / "plots", sample_country_mappings(), run_title="sim_test")

    written = plotter.plot_reductant_use(csv_path, fixtures / "primary_feedstocks.json")

    assert written == tmp_path / "plots" / "interactive" / "reductant_use.html"
    html = written.read_text()
    for placeholder in ("__PLOTLYJS__", "__COMMON_JS__", "__COMMON_CSS__", "__CONFIG__", "__DATA__"):
        assert placeholder not in html
    assert "const Interactive" in html
    # Only the chosen reductant's components count — auxiliary electricity is no carrier.
    assert (
        '"carriers": [{"key": "coking_coal", "label": "Coking coal", "unit": "Mt"}, '
        '{"key": "pci", "label": "PCI", "unit": "Mt"}]'
    ) in html
    # BF: 3 Mt io_low / 1.5 t/t → 2 Mt attributed × (0.4 t/t coking coal, 0.1 t/t PCI)
    assert '"r": "coke+pci", "n": 1, "pr": 2.0, "e": [0.8, 0.2]' in html
    assert '"t": "EAF"' not in html
    assert '"reductantColours"' in html

    # A missing fixture still produces the viewer, with the production view only.
    assert plotter.plot_reductant_use(csv_path, fixtures / "absent.json") == written
    assert '"carriers": []' in written.read_text()

    # A missing table or a table without the reductant column skips the viewer.
    assert plotter.plot_reductant_use(tmp_path / "absent.csv") is None
    bare_csv = tmp_path / "post_processed_bare.csv"
    sample_post_processed().drop(columns=["chosen_reductant"]).to_csv(bare_csv, index=False)
    assert plotter.plot_reductant_use(bare_csv, fixtures / "primary_feedstocks.json") is None
