"""Tests for the trade-allocations map viewer (steelo.utilities.interactive.trade_allocations)."""

import csv
from pathlib import Path


from steelo.utilities.interactive import trade_allocations

HEADER = [
    "commodity",
    "source_type",
    "source_id",
    "source_location",
    "capacity_at_source",
    "source_tech",
    "destination_type",
    "destination_id",
    "destination_location",
    "allocated_volume",
    "allocation_cost",
    "demand_at_destination",
    "supply_at_source",
]


def location(lat: float, lon: float, iso3: str) -> str:
    """A Location repr as the allocation export writes it — a mine's iso3 is empty."""
    return (
        f"Location(lat={lat}, lon={lon}, country='Somewhere', region='Region', iso3='{iso3}', "
        "distance_to_other_iso3=None, geo_unit=None)"
    )


def allocation(
    commodity: str,
    source_type: str,
    source_id: str,
    source_location: str,
    destination_type: str,
    destination_id: str,
    destination_location: str,
    volume: float,
    cost: float = 0.0,
    demand: str = "N/A",
    supply: str = "N/A",
) -> list:
    """One allocation row with the export's full column set."""
    return [
        commodity,
        source_type,
        source_id,
        source_location,
        "1000000.0" if source_type == "Plant-FurnaceGroup" else "N/A",
        "EAF",
        destination_type,
        destination_id,
        destination_location,
        volume,
        cost,
        demand,
        supply,
    ]


def write_allocations(tmp_path: Path, rows: list[list], year: int = 2025) -> Path:
    """Write ``steel_trade_allocations_<year>.csv`` under tmp_path."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"steel_trade_allocations_{year}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def test_records_for_year_builds_arcs(tmp_path: Path) -> None:
    """Arcs carry endpoint names (plant id without the furnace-group suffix), iso3s, coordinates, volume and cost."""
    path = write_allocations(
        tmp_path,
        [
            allocation(
                "steel",
                "Plant-FurnaceGroup",
                "P1_3",
                location(24.5, 54.5, "ARE"),
                "DemandCenter",
                "United Arab Emirates",
                location(24.2, 54.3, "ARE"),
                volume=1330000.0,
                cost=12.5,
                demand="6816000.0",
            ),
            allocation(
                "scrap",
                "Supplier",
                "Albania_scrap",
                location(41.0, 20.0, "ALB"),
                "Plant-FurnaceGroup",
                "P2_0",
                location(41.3, 19.8, "ALB"),
                volume=405000.0,
                supply="405000.0",
            ),
        ],
    )
    arcs, _ = trade_allocations.records_for_year(path)
    assert arcs == [
        {
            "source": "P1",
            "source_iso3": "ARE",
            "lat1": 24.5,
            "lon1": 54.5,
            "dest": "United Arab Emirates",
            "dest_iso3": "ARE",
            "lat2": 24.2,
            "lon2": 54.3,
            "commodity": "steel",
            "volume": 1330000.0,
            "cost": 12.5,
        },
        {
            "source": "Albania_scrap",
            "source_iso3": "ALB",
            "lat1": 41.0,
            "lon1": 20.0,
            "dest": "P2",
            "dest_iso3": "ALB",
            "lat2": 41.3,
            "lon2": 19.8,
            "commodity": "scrap",
            "volume": 405000.0,
            "cost": 0.0,
        },
    ]


def test_records_for_year_classifies_ore_suppliers_as_mines(tmp_path: Path) -> None:
    """Ore suppliers are mines whether or not their Location resolved an ISO3 — the export
    carries real codes for most mines, so the split runs on the commodity shipped."""
    path = write_allocations(
        tmp_path,
        [
            allocation(
                "io_high",
                "Supplier",
                "sup_abc123",
                location(-22.0, 118.0, ""),
                "Plant-FurnaceGroup",
                "P1_0",
                location(31.0, 121.0, "CHN"),
                volume=39000000.0,
                supply="39000000.0",
            ),
            allocation(
                "io_low",
                "Supplier",
                "sup_def456",
                location(-22.5, 119.0, "AUS"),
                "Plant-FurnaceGroup",
                "P1_0",
                location(31.0, 121.0, "CHN"),
                volume=1000000.0,
                supply="1000000.0",
            ),
        ],
    )
    arcs, nodes = trade_allocations.records_for_year(path)
    assert arcs[0]["source_iso3"] == ""
    assert arcs[1]["source_iso3"] == "AUS"
    mine_node = next(n for n in nodes if "sup_abc123" in n["info"])
    assert mine_node["info"] == "Supplier: sup_abc123\nCapacity: 39,000,000 t"
    assert mine_node["kind"] == "mine"
    assert next(n for n in nodes if "sup_def456" in n["info"])["kind"] == "mine"
    assert next(n for n in nodes if n["info"] == "Plant: P1")["kind"] == "plant"


def test_records_for_year_combines_nodes_at_shared_coordinates(tmp_path: Path) -> None:
    """Endpoints at one coordinate merge into a single node whose info stacks their blocks."""
    supplier_and_demand = location(24.2, 54.3, "ARE")
    path = write_allocations(
        tmp_path,
        [
            allocation(
                "steel",
                "Plant-FurnaceGroup",
                "P1_0",
                location(24.5, 54.5, "ARE"),
                "DemandCenter",
                "United Arab Emirates",
                supplier_and_demand,
                volume=1000000.0,
                demand="6816000.0",
            ),
            allocation(
                "steel",
                "Plant-FurnaceGroup",
                "P1_1",
                location(24.5, 54.5, "ARE"),
                "DemandCenter",
                "United Arab Emirates",
                supplier_and_demand,
                volume=330000.0,
                demand="6816000.0",
            ),
            allocation(
                "scrap",
                "Supplier",
                "United Arab Emirates_scrap",
                supplier_and_demand,
                "Plant-FurnaceGroup",
                "P1_0",
                location(24.5, 54.5, "ARE"),
                volume=500000.0,
                supply="3827000.0",
            ),
        ],
    )
    _, nodes = trade_allocations.records_for_year(path)
    assert len(nodes) == 2
    combined = next(n for n in nodes if n["lat"] == 24.2)
    # The two furnace groups collapse into one plant entry; supplier block comes first,
    # and "Allocated" is the demand centre's total inflow.
    assert combined["count"] == 2
    assert combined["kind"] == "centre"  # a scrap supplier is a centre, not a mine
    assert combined["info"] == (
        "Supplier: United Arab Emirates_scrap\nCapacity: 3,827,000 t"
        "\n\nDemand Center: United Arab Emirates\nDemand: 6,816,000 t\nAllocated: 1,330,000 t"
    )
    plant = next(n for n in nodes if n["lat"] == 24.5)
    assert plant["count"] == 1
    assert plant["info"] == "Plant: P1"


def test_records_for_year_header_only_file_yields_nothing(tmp_path: Path) -> None:
    """A year whose trade LP failed leaves a header-only file, which packs to empty records."""
    path = write_allocations(tmp_path, [])
    assert trade_allocations.records_for_year(path) == ([], [])


def sample_years(tmp_path: Path) -> dict[int, tuple[list, list]]:
    """Two years' records, sharing the plant and demand-centre endpoints."""
    years = {}
    for year, volume in ((2026, 500000.4), (2025, 1330000.0)):
        path = write_allocations(
            tmp_path,
            [
                allocation(
                    "steel",
                    "Plant-FurnaceGroup",
                    "P1_0",
                    location(24.51234567, 54.5, "ARE"),
                    "DemandCenter",
                    "United Arab Emirates",
                    location(24.2, 54.3, "ARE"),
                    volume=volume,
                    cost=12.5,
                    demand="6816000.0",
                ),
            ],
            year=year,
        )
        years[year] = trade_allocations.records_for_year(path)
    return years


def test_pack_years_shares_endpoints_and_rounds(tmp_path: Path) -> None:
    """Endpoints repeat across years but are stored once; coordinates round to 4 dp, volumes and costs to integers."""
    packed = trade_allocations.pack_years(sample_years(tmp_path))
    assert packed["years"] == [2025, 2026]
    assert packed["commodities"] == ["steel"]
    assert packed["endpoints"] == [
        ["P1", "ARE", 24.5123, 54.5],
        ["United Arab Emirates", "ARE", 24.2, 54.3],
    ]
    assert packed["arcs"][2025] == {"s": [0], "d": [1], "c": [0], "v": [1330000], "k": [12]}
    assert packed["arcs"][2026]["v"] == [500000]
    assert len(packed["points"]) == 2
    assert packed["nodes"][2025]["n"] == [1, 1]
    assert packed["kinds"] == ["plant", "centre", "mine"]
    assert packed["nodes"][2025]["t"] == [0, 1]  # the plant node, then the demand centre
    # The plant info text is identical in both years, so the shared table holds it once.
    assert packed["infos"].count("Plant: P1") == 1


def test_write_viewer_is_self_contained(tmp_path: Path) -> None:
    """The written page loads nothing from the network: no external script/style tags, every URL string is deck.gl's own."""
    data = {
        "run under test": {
            "title": "run under test",
            "provenance": "from the test fixture",
            **trade_allocations.pack_years(sample_years(tmp_path)),
        },
    }
    config = {
        "chartTitle": "Trade allocations",
        "runs": ["run under test"],
        "defaultRun": "run under test",
        "techColours": {},
        "regionColours": {},
        "tradeBlocs": {},
        "geoInfo": {},
        "geoUnitNames": {},
        "commodityColours": trade_allocations.COMMODITY_COLOURS,
        "fallbackColour": trade_allocations.FALLBACK_COLOUR,
    }
    path = trade_allocations.write_viewer(config, data, tmp_path / "out" / "trade_allocations.html")
    html = path.read_text()
    # deck.gl's minified source mentions URLs and script tags in its own error strings;
    # the page around it must add none, so check with the inlined library stripped out.
    page = html.replace(trade_allocations.DECKGL_JS.read_text(), "")
    assert len(page) < len(html)
    assert "<script src=" not in page
    assert "<link" not in page
    assert "https://" not in page
    assert "http://" not in page
    for placeholder in ("__DECKGL__", "__WORLD__", "__CONFIG__", "__DATA__", "__COMMON_CSS__", "__COMMON_JS__"):
        assert placeholder not in page
    assert '"FeatureCollection"' in page  # inlined world outline
    assert '"run under test"' in page


def test_plot_trade_allocations_writes_viewer_under_plots_interactive(tmp_path: Path) -> None:
    """The plotter method packs the TM dir's allocation files into plots/interactive/trade_allocations.html."""
    from steelo.utilities.interactive import InteractivePlotter

    tm_dir = tmp_path / "TM"
    write_allocations(
        tm_dir,
        [
            allocation(
                "steel",
                "Plant-FurnaceGroup",
                "P1_0",
                location(24.5, 54.5, "ARE"),
                "DemandCenter",
                "United Arab Emirates",
                location(24.2, 54.3, "ARE"),
                volume=1330000.0,
                demand="6816000.0",
            ),
        ],
    )
    plotter = InteractivePlotter(tmp_path / "plots", [], run_title="sim_test")
    written = plotter.plot_trade_allocations(tm_dir)
    assert written == tmp_path / "plots" / "interactive" / "trade_allocations.html"
    assert '"sim_test"' in written.read_text()
    assert plotter.plot_trade_allocations(tmp_path / "absent") is None
