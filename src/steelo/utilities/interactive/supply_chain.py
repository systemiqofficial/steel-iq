"""Edge packing for the supply-chain viewer (``supply_chain.html``).

The supply chain of the steel demand of one country or one region is traced in the
browser from the furnace-group-level allocations: steel furnace groups serving its
demand centres, then the iron products and scrap those furnace groups took in, then
the ore the iron-making furnace groups took in. Upstream volumes are attributed pro rata to
the share of each furnace group's output that went down the chain, so the trace needs
every allocation edge with its endpoint identities; this module packs them once per
run, all years, from ``TM/steel_trade_allocations_<year>.csv``.
"""

from pathlib import Path
from typing import Any

import pandas as pd

from .trade_matrix import COMMODITY_PRODUCTS, COUNTRY_PATTERN, EXCLUDED_COMMODITIES, ISO3_PATTERN

# Endpoint kinds, in the order the packed ``k`` indices refer to: a plant furnace group,
# a demand centre, a scrap supplier (geo = its country's ISO3) and an ore mine (geo =
# the mine sheet's region label, as mines carry no resolved ISO3).
ENDPOINT_KINDS = ("furnace_group", "demand_centre", "scrap_supplier", "mine")
COLUMNS = [
    "commodity",
    "source_type",
    "source_id",
    "source_location",
    "source_tech",
    "destination_type",
    "destination_id",
    "destination_location",
    "allocated_volume",
]


def _kind(endpoint_type: str, commodity: str) -> int:
    if endpoint_type == "Plant-FurnaceGroup":
        return ENDPOINT_KINDS.index("furnace_group")
    if endpoint_type == "DemandCenter":
        return ENDPOINT_KINDS.index("demand_centre")
    return ENDPOINT_KINDS.index("scrap_supplier" if commodity == "scrap" else "mine")


def _geo(location: str, kind: int) -> str:
    pattern = COUNTRY_PATTERN if ENDPOINT_KINDS[kind] == "mine" else ISO3_PATTERN
    match = pattern.search(location)
    if match is None or not match.group(1):
        raise ValueError(f"No {'country label' if pattern is COUNTRY_PATTERN else 'iso3'} in location {location!r}")
    return match.group(1)


def pack_edges(files: dict[int, Path]) -> dict[str, Any]:
    """Every metal allocation edge of every year, with shared endpoint and label tables.

    Args:
        files: ``{year: path}`` as :func:`trade_matrix.allocation_files` returns.

    Returns:
        ``kinds`` (:data:`ENDPOINT_KINDS`), ``commodities`` and ``techs`` label tables,
        ``endpoints`` as ``[kind index, geo, id]`` rows and ``edges[year]`` as
        parallel arrays ``c`` commodity index, ``s``/``d`` endpoint indices, ``t`` source
        technology index and ``v`` tonnes (rounded; zero-tonne edges dropped). Only the
        commodities of :data:`trade_matrix.COMMODITY_PRODUCTS` are kept, so the payload
        matches the country-level flows of the other views.

    Raises:
        ValueError: If a file lacks the allocation columns or a location lacks the field
            its endpoint kind needs.
    """
    endpoints: dict[str, int] = {}
    endpoint_rows: list[list[Any]] = []
    commodities: dict[str, int] = {}
    techs: dict[str, int] = {}
    edges: dict[int, dict[str, list[int]]] = {}

    def endpoint(endpoint_id: str, endpoint_type: str, location: str, commodity: str) -> int:
        if endpoint_id not in endpoints:
            kind = _kind(endpoint_type, commodity)
            endpoints[endpoint_id] = len(endpoint_rows)
            endpoint_rows.append([kind, _geo(location, kind), endpoint_id])
        return endpoints[endpoint_id]

    for year, path in files.items():
        table = pd.read_csv(path, usecols=COLUMNS, keep_default_na=False)
        metal = table[table["commodity"].isin(COMMODITY_PRODUCTS)]
        unknown = set(table["commodity"]) - COMMODITY_PRODUCTS.keys() - EXCLUDED_COMMODITIES
        if unknown:
            raise ValueError(f"Unmapped commodities in {path.name}: {', '.join(sorted(unknown))}")
        packed: dict[str, list[int]] = {"c": [], "s": [], "d": [], "t": [], "v": []}
        for row in metal.astype(str).to_dict("records"):
            volume = round(float(row["allocated_volume"]))
            if volume <= 0:
                continue
            commodity = row["commodity"]
            packed["c"].append(commodities.setdefault(commodity, len(commodities)))
            packed["s"].append(endpoint(row["source_id"], row["source_type"], row["source_location"], commodity))
            packed["d"].append(
                endpoint(row["destination_id"], row["destination_type"], row["destination_location"], commodity)
            )
            packed["t"].append(techs.setdefault(row["source_tech"], len(techs)))
            packed["v"].append(volume)
        if packed["v"]:
            edges[year] = packed
    return {
        "kinds": list(ENDPOINT_KINDS),
        "commodities": list(commodities),
        "techs": list(techs),
        "endpoints": endpoint_rows,
        "edges": edges,
    }
