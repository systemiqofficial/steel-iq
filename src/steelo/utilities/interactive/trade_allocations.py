"""Record packing for the trade-allocations map viewer (``trade_allocations.html``).

One HTML file per run showing every year's trade-LP allocations as commodity arcs over
a world map, with a year slider, commodity toggles, the shared geography filter (an
arc shows while either end lies in the selected geography) and hover tooltips, built
from the per-year allocation files (``TM/steel_trade_allocations_<year>.csv``).
deck.gl and the world outline are inlined, so the file opens from disk with no network
access and no API key — unlike the per-year pydeck maps, whose basemap tiles and
mapbox-gl come from the network.

``world_outline.json`` is the basemap: Natural Earth 110m land polygons and admin-0
boundary lines (public domain), exported once from cartopy's local shapefiles
(``ne_110m_land`` + ``ne_110m_admin_0_boundary_lines_land``) with coordinates rounded
to 2 decimal places.
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).parent
DECKGL_JS = (
    ASSETS_DIR.parents[2] / "steeloweb" / "static" / "vendor" / "mapping-libs" / "deck.gl@8.9.35" / "dist.min.js"
)

# Arc colours as plot_detailed_trade_map intended them, keyed by the underscore names
# the allocations actually carry (the old map's "hot metal"/"pig iron" keys never
# matched, so those commodities and bio_pci all fell back to blue).
COMMODITY_COLOURS: dict[str, list[int]] = {
    "steel": [0, 100, 255],
    "hot_metal": [255, 85, 0],
    "dri_low": [200, 0, 0],
    "dri_mid": [255, 40, 40],
    "dri_high": [255, 90, 90],
    "pellets_high": [255, 215, 0],
    "pellets_mid": [240, 180, 0],
    "pellets_low": [200, 140, 0],
    "pig_iron": [100, 100, 100],
    "sinter_low": [128, 64, 0],
    "sinter_mid": [160, 82, 45],
    "sinter_high": [210, 105, 30],
    "liquid_steel": [0, 200, 255],
    "liquid_iron": [255, 140, 0],
    "electrolytic_iron": [123, 104, 238],
    "hbi_low": [102, 0, 204],
    "hbi_mid": [153, 51, 255],
    "hbi_high": [204, 153, 255],
    "scrap": [80, 80, 80],
    "io_high": [0, 153, 76],
    "io_mid": [0, 204, 102],
    "io_low": [153, 255, 204],
    "bio_pci": [154, 205, 50],
}
FALLBACK_COLOUR = [0, 0, 255]

# Node categories, in the order the packed ``t`` indices refer to. Suppliers split on
# what they ship: scrap suppliers sit at their country's demand centre, while ore and
# biomass suppliers are raw-material supply sites ("mine").
NODE_KINDS = ("plant", "centre", "mine")

# The location columns hold Location reprs; a mine's iso3 is empty.
_COORDS = re.compile(r"\blat=([-+0-9.eE]+), lon=([-+0-9.eE]+)")
_ISO3 = re.compile(r"\biso3='([^']*)'")


def _endpoint(location: str, endpoint_type: str, endpoint_id: str) -> tuple[str, str, float, float]:
    """Name, iso3, lat and lon of one allocation endpoint.

    Args:
        location: The row's ``Location(...)`` repr.
        endpoint_type: The row's source or destination type.
        endpoint_id: The row's source or destination id; a Plant-FurnaceGroup id carries
            a furnace-group suffix (``P100000120001_3``), and the map names the plant.

    Returns:
        ``(name, iso3, lat, lon)`` — iso3 is empty for mines.

    Raises:
        ValueError: When the repr carries no coordinates or no iso3 field.
    """
    coords = _COORDS.search(location)
    iso3 = _ISO3.search(location)
    if coords is None or iso3 is None:
        raise ValueError(f"No coordinates or iso3 in location {location!r}")
    name = endpoint_id.rsplit("_", 1)[0] if endpoint_type == "Plant-FurnaceGroup" else endpoint_id
    return name, iso3.group(1), float(coords.group(1)), float(coords.group(2))


def _node_info(entries: dict[tuple[str, str], dict[str, Any]], inflow: dict[str, float]) -> str:
    """The combined tooltip of the endpoints sharing one coordinate, as the old map wrote it.

    Args:
        entries: The location's unique endpoints, keyed by ``(type, id)``.
        inflow: Total allocated volume per demand centre id, for the "Allocated" line.

    Returns:
        Per-type blocks (suppliers, demand centres, plants) joined by blank lines;
        a single endpoint gets its plain one-block form.
    """
    suppliers = [e for e in entries.values() if e["type"] == "supplier"]
    demands = [e for e in entries.values() if e["type"] == "demand"]
    plants = [e for e in entries.values() if e["type"] == "plant"]
    parts = []
    if suppliers:
        capacity = sum(e["capacity"] for e in suppliers)
        if len(suppliers) == 1:
            parts.append(f"Supplier: {suppliers[0]['id']}\nCapacity: {capacity:,.0f} t")
        else:
            ids = ", ".join(e["id"] for e in suppliers)
            parts.append(f"Suppliers ({len(suppliers)}): {ids}\nTotal Capacity: {capacity:,.0f} t")
    if demands:
        demand = sum(e["demand"] for e in demands)
        allocated = sum(inflow[e["id"]] for e in demands)
        if len(demands) == 1:
            parts.append(f"Demand Center: {demands[0]['id']}\nDemand: {demand:,.0f} t\nAllocated: {allocated:,.0f} t")
        else:
            ids = ", ".join(e["id"] for e in demands)
            parts.append(
                f"Demand Centers ({len(demands)}): {ids}\nTotal Demand: {demand:,.0f} t"
                f"\nTotal Allocated: {allocated:,.0f} t"
            )
    if plants:
        if len(plants) == 1:
            parts.append(f"Plant: {plants[0]['id']}")
        else:
            parts.append(f"Plants ({len(plants)}): {', '.join(e['id'] for e in plants)}")
    return "\n\n".join(parts)


def _node_kind(entries: dict[tuple[str, str], dict[str, Any]]) -> str:
    """The node's category — with mixed endpoints the demand-side colour wins (never seen in real exports).

    Args:
        entries: The location's unique endpoints, keyed by ``(type, id)``.

    Returns:
        One of :data:`NODE_KINDS`.
    """
    kinds = {entry["kind"] for entry in entries.values()}
    return next(kind for kind in ("centre", "mine", "plant") if kind in kinds)


def records_for_year(csv_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Arc and node records of one year's allocation file.

    Arcs are the file's rows verbatim (furnace-group level, so a plant serving one
    destination from several furnace groups contributes one arc each, as the per-year
    maps drew it). Nodes group the endpoints sharing an exact coordinate: plants,
    suppliers (capacity = the file's supply at source) and demand centres (demand at
    destination, plus the total volume allocated to them).

    Args:
        csv_path: A ``steel_trade_allocations_<year>.csv``; a header-only file (a year
            whose trade LP failed) yields no records.

    Returns:
        ``(arc_records, node_records)``. Arcs carry source/dest name and iso3,
        lat1/lon1/lat2/lon2, commodity, volume and cost; nodes carry lat, lon, the
        combined info text, the count of endpoints at that coordinate and their
        category (plant, demand/scrap centre, or mine).

    Raises:
        ValueError: When a row's location repr or numeric field cannot be read.
    """
    arcs: list[dict[str, Any]] = []
    located: dict[tuple[float, float], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    inflow: dict[str, float] = defaultdict(float)
    with csv_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            source = _endpoint(row["source_location"], row["source_type"], row["source_id"])
            dest = _endpoint(row["destination_location"], row["destination_type"], row["destination_id"])
            volume = float(row["allocated_volume"])
            arcs.append(
                {
                    "source": source[0],
                    "source_iso3": source[1],
                    "lat1": source[2],
                    "lon1": source[3],
                    "dest": dest[0],
                    "dest_iso3": dest[1],
                    "lat2": dest[2],
                    "lon2": dest[3],
                    "commodity": row["commodity"],
                    "volume": volume,
                    "cost": float(row["allocation_cost"]),
                }
            )
            if row["source_type"] == "Plant-FurnaceGroup":
                located[source[2], source[3]].setdefault(
                    ("plant", source[0]), {"type": "plant", "kind": "plant", "id": source[0]}
                )
            else:
                located[source[2], source[3]].setdefault(
                    ("supplier", source[0]),
                    {
                        "type": "supplier",
                        "kind": "centre" if row["commodity"] == "scrap" else "mine",
                        "id": source[0],
                        "capacity": float(row["supply_at_source"]),
                    },
                )
            if row["destination_type"] == "Plant-FurnaceGroup":
                located[dest[2], dest[3]].setdefault(
                    ("plant", dest[0]), {"type": "plant", "kind": "plant", "id": dest[0]}
                )
            else:
                located[dest[2], dest[3]].setdefault(
                    ("demand", dest[0]),
                    {"type": "demand", "kind": "centre", "id": dest[0], "demand": float(row["demand_at_destination"])},
                )
                inflow[dest[0]] += volume
    nodes = [
        {
            "lat": lat,
            "lon": lon,
            "info": _node_info(entries, inflow),
            "count": len(entries),
            "kind": _node_kind(entries),
        }
        for (lat, lon), entries in located.items()
    ]
    return arcs, nodes


def pack_years(years: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]]) -> dict[str, Any]:
    """Compact all-years payload for the template.

    Shared tables keep the file small: arc endpoints (name, iso3, lat, lon), node
    positions and node texts are stored once and referenced by index. Colours and arc
    widths are derived client-side (width = volume / the year's max volume × 10, as
    the per-year maps drew it). Coordinates are rounded to 4 decimal places, volumes
    and costs to whole tonnes/dollars — the precision the tooltips show.

    Args:
        years: ``{year: (arc_records, node_records)}`` as built by
            :func:`records_for_year`.

    Returns:
        The viewer's ``DATA``: sorted ``years``, ``commodities`` in first-seen order,
        shared ``endpoints``/``points``/``infos`` tables and the ``kinds`` order, and
        per-year parallel arrays ``arcs[year]`` (s/d = endpoint indices, c = commodity
        index, v = volume, k = cost) and ``nodes[year]`` (p = point index, n = endpoint
        count, i = info index, t = kind index).
    """

    def index(table: dict[Any, int], key: Any) -> int:
        return table.setdefault(key, len(table))

    endpoints: dict[tuple[str, str, float, float], int] = {}
    points: dict[tuple[float, float], int] = {}
    infos: dict[str, int] = {}
    commodities: dict[str, int] = {}
    arcs_by_year: dict[int, dict[str, list[Any]]] = {}
    nodes_by_year: dict[int, dict[str, list[Any]]] = {}
    for year in sorted(years):
        arc_records, node_records = years[year]
        arcs: dict[str, list[Any]] = {"s": [], "d": [], "c": [], "v": [], "k": []}
        for arc in arc_records:
            source = (arc["source"], arc["source_iso3"], round(arc["lat1"], 4), round(arc["lon1"], 4))
            dest = (arc["dest"], arc["dest_iso3"], round(arc["lat2"], 4), round(arc["lon2"], 4))
            arcs["s"].append(index(endpoints, source))
            arcs["d"].append(index(endpoints, dest))
            arcs["c"].append(index(commodities, arc["commodity"]))
            arcs["v"].append(round(arc["volume"]))
            arcs["k"].append(round(arc["cost"]))
        nodes: dict[str, list[Any]] = {"p": [], "n": [], "i": [], "t": []}
        for node in node_records:
            nodes["p"].append(index(points, (round(node["lat"], 4), round(node["lon"], 4))))
            nodes["n"].append(int(node["count"]))
            nodes["i"].append(index(infos, node["info"]))
            nodes["t"].append(NODE_KINDS.index(node["kind"]))
        arcs_by_year[year] = arcs
        nodes_by_year[year] = nodes
    return {
        "years": sorted(years),
        "commodities": list(commodities),
        "kinds": list(NODE_KINDS),
        "endpoints": [list(key) for key in endpoints],
        "points": [list(key) for key in points],
        "infos": list(infos),
        "arcs": arcs_by_year,
        "nodes": nodes_by_year,
    }


def write_viewer(config: dict[str, Any], data: dict[str, Any], output_path: Path) -> Path:
    """Write the all-years trade-allocations viewer.

    Args:
        config: The shell config (:meth:`InteractivePlotter._config`) plus this
            viewer's ``commodityColours`` and ``fallbackColour``.
        data: Per-run payloads — ``{run: {"title", "provenance", **pack_years(...)}}``.
        output_path: The HTML file to write; parent directories are created.

    Returns:
        ``output_path``.
    """
    html = (
        (ASSETS_DIR / "trade_allocations.html")
        .read_text()
        .replace("__COMMON_CSS__", (ASSETS_DIR / "common.css").read_text())
        .replace("__COMMON_JS__", (ASSETS_DIR / "common.js").read_text())
        .replace("__WORLD__", (ASSETS_DIR / "world_outline.json").read_text())
        .replace("__CONFIG__", json.dumps(config))
        .replace("__DATA__", json.dumps(data, separators=(",", ":")))
        .replace("__DECKGL__", DECKGL_JS.read_text())
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path
