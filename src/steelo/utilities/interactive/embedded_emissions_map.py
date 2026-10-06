"""Emissions embedded in steel trade, for the embedded emissions map (``embedded_emissions_map.html``).

Every furnace group's emissions of the year (post-processed table, one row per furnace
group after dropping the per-feedstock repeats) are spread over the tonnes it shipped in
the realised allocations and carried down the trade graph — iron furnace group → steel
furnace group → demand centre — pro rata to volume. Ore mines and scrap suppliers carry
no emissions inside the model. The result is, per year and per emissions boundary and
scope of the table (direct, direct incl. biogenic, indirect), a matrix of tCO2 by
emitting country and consuming country: its row sums are the production-based
(territorial) emissions, its column sums the consumption-based ones, and its total is
the furnace-group total.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from plotly.offline import get_plotlyjs

from .emissions import EMISSIONS_PREFIX, SCOPES, emission_boundaries
from .trade_allocations import ASSETS_DIR, DECKGL_JS
from .trade_matrix import iso3_of

FURNACE_GROUP = "Plant-FurnaceGroup"
DEMAND_CENTRE = "DemandCenter"
COLUMNS = [
    "commodity",
    "source_type",
    "source_id",
    "source_location",
    "destination_type",
    "destination_id",
    "destination_location",
    "allocated_volume",
]
# Natural Earth's ADM0_A3 codes (the polygon keys) that differ from the model's ISO3.
POLYGON_ISO3 = {"SDS": "SSD"}

Matrix = dict[tuple[str, str], float]


def furnace_group_emissions(post_processed: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """One row per furnace group and year with its emissions per boundary and scope.

    Args:
        post_processed: The run's post-processed table, which repeats a furnace group once
            per feedstock with identical emissions.

    Returns:
        ``{year: table}`` indexed by ``furnace_group_id`` with one column per
        ``emissions_<boundary>_<scope>`` column of the table, named ``"<boundary>|<scope>"``
        (scopes of :data:`~.emissions.SCOPES`), in tCO2 with missing values as zero.

    Raises:
        ValueError: If the table carries no emissions columns.
    """
    columns = {
        f"{EMISSIONS_PREFIX}{boundary}_{scope}": f"{boundary}|{scope}"
        for boundary in emission_boundaries(list(post_processed.columns))
        for scope in SCOPES
        if f"{EMISSIONS_PREFIX}{boundary}_{scope}" in post_processed.columns
    }
    if not columns:
        raise ValueError("The post-processed table has no emissions_<boundary>_<scope> columns")
    table = post_processed[["year", "furnace_group_id", *columns]].drop_duplicates(["year", "furnace_group_id"])
    table = table.rename(columns=columns).fillna(dict.fromkeys(columns.values(), 0.0))
    return {
        int(year): table[table["year"] == year].set_index("furnace_group_id").drop(columns="year")
        for year in table["year"].unique()
    }


def _shipping_order(out_volume: pd.Series, successors: dict[str, list[tuple[str, float]]]) -> list[str]:
    """The shipping furnace groups, every supplier ahead of the furnace groups it ships to."""
    indegree: dict[str, int] = defaultdict(int)
    for shipments in successors.values():
        for destination, _ in shipments:
            indegree[destination] += 1
    order = [fg for fg in out_volume.index if indegree[fg] == 0]
    for fg in order:
        for destination, _ in successors.get(fg, []):
            indegree[destination] -= 1
            if indegree[destination] == 0:
                order.append(destination)
    if len(order) != len(out_volume):
        raise ValueError("The furnace-group allocations hold a cycle or a furnace group that ships nothing onward")
    return order


def embedded_matrix(allocations: pd.DataFrame, emissions: pd.DataFrame) -> dict[str, Matrix]:
    """Emissions by emitting and consuming country for one year.

    Args:
        allocations: One year's allocation table (:data:`COLUMNS`).
        emissions: That year's table of :func:`furnace_group_emissions`.

    Returns:
        ``{key: {(emitter_iso3, consumer_iso3): tCO2}}`` for every ``"<boundary>|<scope>"``
        column of ``emissions``, zero entries left out.

    Raises:
        ValueError: If a location carries no ISO3, or the furnace-group graph is not
        acyclic with every receiving furnace group shipping onward.

    Notes:
        - A furnace group's emissions, its own plus those received with its iron, are
          split over its outbound tonnes; every furnace group ships all it produces, so
          nothing is left behind and the matrix total equals the furnace-group total.
        - The boundaries and scopes travel together as one vector per furnace group and
          emitting country, so the graph is walked once.
    """
    shipped = allocations[(allocations["source_type"] == FURNACE_GROUP) & (allocations["allocated_volume"] > 0)]
    out_volume = shipped.groupby("source_id")["allocated_volume"].sum()
    emitter = shipped.drop_duplicates("source_id").set_index("source_id")["source_location"].map(iso3_of)
    to_furnace_groups = shipped[shipped["destination_type"] == FURNACE_GROUP]
    to_demand = shipped[shipped["destination_type"] == DEMAND_CENTRE]
    consumer = to_demand["destination_location"].map(iso3_of)

    successors: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for source, destination, volume in to_furnace_groups[
        ["source_id", "destination_id", "allocated_volume"]
    ].itertuples(index=False):
        successors[source].append((destination, float(volume) / float(out_volume[source])))
    order = _shipping_order(out_volume, successors)

    keys = list(emissions.columns)
    own = emissions.reindex(out_volume.index).fillna(0.0)
    carried: dict[str, dict[str, np.ndarray]] = defaultdict(lambda: defaultdict(lambda: np.zeros(len(keys))))
    for fg in order:
        if own.loc[fg].any():
            carried[fg][emitter[fg]] += own.loc[fg].to_numpy()
        for destination, share in successors.get(fg, []):
            for iso3, tonnes in carried[fg].items():
                carried[destination][iso3] += tonnes * share
    delivered: dict[tuple[str, str], np.ndarray] = defaultdict(lambda: np.zeros(len(keys)))
    for source, volume, iso3 in zip(to_demand["source_id"], to_demand["allocated_volume"], consumer):
        share = float(volume) / float(out_volume[source])
        for emitter_iso3, tonnes in carried[source].items():
            delivered[emitter_iso3, iso3] += tonnes * share
    return {
        key: {pair: float(tonnes[k]) for pair, tonnes in delivered.items() if tonnes[k]} for k, key in enumerate(keys)
    }


def steel_flows(allocations: pd.DataFrame) -> Matrix:
    """Steel tonnes delivered to demand centres, by producing and consuming country.

    Args:
        allocations: One year's allocation table (:data:`COLUMNS`).

    Returns:
        ``{(producer_iso3, consumer_iso3): tonnes}``.
    """
    delivered = allocations[(allocations["destination_type"] == DEMAND_CENTRE) & (allocations["commodity"] == "steel")]
    producer = delivered["source_location"].map(iso3_of)
    consumer = delivered["destination_location"].map(iso3_of)
    flows: Matrix = defaultdict(float)
    for origin, destination, volume in zip(producer, consumer, delivered["allocated_volume"]):
        flows[origin, destination] += float(volume)
    return dict(flows)


def pack_years(post_processed: pd.DataFrame, files: dict[int, Path], boundary: str) -> dict[str, Any]:
    """The viewer payload of one run: sparse country matrices for every year.

    Args:
        post_processed: The run's post-processed table.
        files: ``{year: path}`` as :func:`trade_matrix.allocation_files` returns.
        boundary: The run's chosen emissions boundary, e.g. ``rs-inspired`` — the one the
            viewer opens on.

    Returns:
        ``years``, the ``countries`` label table, the ``boundaries`` of the table, the
        chosen ``boundary``, ``m[year]["<boundary>|<scope>"]`` and ``steel[year]`` as
        parallel lists ``s`` (emitter or producer index), ``d`` (consumer index) and ``t``
        (kt, entries under 0.5 kt dropped). Years without a post-processed row are left out.

    Raises:
        ValueError: As :func:`furnace_group_emissions` and :func:`embedded_matrix`, when the
        table lacks the chosen boundary, or when a file lacks the allocation columns.
    """
    by_year = furnace_group_emissions(post_processed)
    boundaries = emission_boundaries(list(post_processed.columns))
    if boundary not in boundaries:
        raise ValueError(f"The post-processed table has no emissions columns of the {boundary} boundary")
    countries: dict[str, int] = {}

    def pack(matrix: Matrix) -> dict[str, list[int]]:
        packed: dict[str, list[int]] = {"s": [], "d": [], "t": []}
        for (origin, destination), tonnes in sorted(matrix.items()):
            kilotonnes = round(tonnes / 1e3)
            if kilotonnes:
                packed["s"].append(countries.setdefault(origin, len(countries)))
                packed["d"].append(countries.setdefault(destination, len(countries)))
                packed["t"].append(kilotonnes)
        return packed

    payload: dict[str, Any] = {"years": [], "boundaries": boundaries, "boundary": boundary, "m": {}, "steel": {}}
    for year, path in files.items():
        if year not in by_year:
            continue
        allocations = pd.read_csv(path, usecols=COLUMNS, keep_default_na=False)
        emissions = by_year[year]
        payload["years"].append(year)
        payload["m"][year] = {key: pack(matrix) for key, matrix in embedded_matrix(allocations, emissions).items()}
        payload["steel"][year] = pack(steel_flows(allocations))
    payload["countries"] = list(countries)
    return payload


def country_features() -> dict[str, Any]:
    """The shipped country polygons (``world_countries.json``) as a GeoJSON collection keyed by the model's ISO3."""
    polygons = json.loads((ASSETS_DIR / "world_countries.json").read_text())["countries"]
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"iso3": POLYGON_ISO3.get(code, code)}, "geometry": geometry}
            for code, geometry in polygons.items()
        ],
    }


def write_viewer(config: dict[str, Any], data: dict[str, Any], output_path: Path) -> Path:
    """Write the embedded emissions map.

    Args:
        config: The shell config (:meth:`InteractivePlotter._config`).
        data: Per-run payloads — ``{run: {"title", "provenance", "coords", **pack_years(...)}}``.
        output_path: The HTML file to write; parent directories are created.

    Returns:
        ``output_path``.
    """
    html = (
        (ASSETS_DIR / "embedded_emissions_map.html")
        .read_text()
        .replace("__COMMON_CSS__", (ASSETS_DIR / "common.css").read_text())
        .replace("__COMMON_JS__", (ASSETS_DIR / "common.js").read_text())
        .replace("__COUNTRIES__", json.dumps(country_features(), separators=(",", ":")))
        .replace("__CONFIG__", json.dumps(config))
        .replace("__DATA__", json.dumps(data, separators=(",", ":")))
        .replace("__DECKGL__", DECKGL_JS.read_text())
        .replace("__PLOTLYJS__", get_plotlyjs())
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path
