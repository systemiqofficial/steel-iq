"""Record packing for the capacity map viewers (``capacity_world_map.html``, ``capacity_china_map.html``).

The map draws one pie per plant on a world map: area is capacity, wedges are
technologies. It has a year slider, filters for technology, status, plant origin
(existing fleet or greenfield) and geography, a statistics side panel and a PNG
export. Pies that overlap at the current zoom merge into one. The viewer is pure
SVG with its polygons and data inlined, so the file opens from disk with no
network access.

Per plant and year the map shows the furnace groups the simulation ran (the
post-processed table, which has a row exactly in the years a group's status is
active), new builds under construction (``data/greenfield_status_timeseries.csv``)
groups being rebuilt for a technology switch (``data/pam_switch_decisions.csv``),
drawn in the new technology, and the existing fleet's groups that are not operating
yet (``data/pipeline_status_timeseries.csv``): the units the input data lists as
announced or under construction, which the simulation treats as under construction
from its first year, and the expansions the PAM builds. Runs without that table
fall back to the expansions' decision years in ``data/pam_motions.csv`` and do not
show the input data's units before they operate. Coordinates and origin come from the run's
live plants; plant names and the source line from the master's Furnace units sheet.
Timelines are run-length encoded per plant.

The China map is the same template focused on one country: only that country's
plants, its provinces as the geography unit, an equirectangular view fitted to
the country and, on policy-ON runs, the capacity pool's province groups as regions.

``world_countries.json`` holds the polygons: Natural Earth (public domain), exported
once from the local shapefiles. ``countries`` are the 50m admin-0 map subunits
dissolved by ``ADM0_A3`` (Antarctica dropped), simplified to 0.05 degrees;
``units`` are the 10m admin-1 provinces of China keyed by geo key (``CHN:CN-HE``),
simplified to 0.03 degrees. Both are rounded to 2 decimal places.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from steelo.capacity_policy.inputs import RegionRow

ASSETS_DIR = Path(__file__).parent

TABLE_COLUMNS = ["year", "iso3", "geo_key", "plant_id", "furnace_group_id", "technology", "product", "capacity"]
GREENFIELD_COLUMNS = ["year", "geo_key", "plant_id", "furnace_group_id", "technology", "product", "capacity", "status"]
DECISION_COLUMNS = [
    "decision_year",
    "switch_year",
    "construction_start_year",
    "plant_id",
    "furnace_group_id",
    "geo_key",
    "product",
    "old_technology",
    "new_technology",
    "new_capacity_t",
]
MOTION_COLUMNS = [
    "year",
    "kind",
    "plant_id",
    "furnace_group_id",
    "geo_key",
    "product",
    "new_technology",
    "new_capacity_t",
]
PIPELINE_COLUMNS = [
    "year",
    "plant_id",
    "furnace_group_id",
    "geo_key",
    "product",
    "technology",
    "status",
    "capacity",
    "created_by_pam",
]
CONSTRUCTION_COLUMNS = [*GREENFIELD_COLUMNS[:-1], "note"]
STATUSES = ["operating", "construction"]
IRON_PRODUCT = "iron"


def _require(frame: pd.DataFrame, columns: list[str], name: str) -> None:
    """Check that an input table has the columns the map reads.

    Args:
        frame: The table to check.
        columns: The columns it must have.
        name: The table's name for the error message.

    Raises:
        ValueError: Naming the missing columns.
    """
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"The {name} has no {', '.join(missing)} column(s)")


def _rebuild_rows(switch_decisions: pd.DataFrame, last_year: int) -> pd.DataFrame:
    """Construction rows of the groups being rebuilt for a technology switch.

    Args:
        switch_decisions: The switch decisions table.
        last_year: The last simulated year, which caps the rebuild years.

    Returns:
        One row per decision that started construction and rebuild year
        (``construction_start_year`` to ``switch_year - 1``), in the new technology at
        the new capacity, with ``note`` naming the old technology.
    """
    rows = []
    started = switch_decisions[switch_decisions["construction_start_year"].notna()]
    for decision in started.to_dict("records"):
        last_rebuild_year = min(int(decision["switch_year"]) - 1, last_year)
        for year in range(int(decision["construction_start_year"]), last_rebuild_year + 1):
            rows.append(
                {
                    "year": year,
                    "geo_key": decision["geo_key"],
                    "plant_id": decision["plant_id"],
                    "furnace_group_id": decision["furnace_group_id"],
                    "technology": decision["new_technology"],
                    "product": decision["product"],
                    "capacity": decision["new_capacity_t"],
                    "note": f"rebuild from {decision['old_technology']}",
                },
            )
    return pd.DataFrame(rows, columns=CONSTRUCTION_COLUMNS)


def _pipeline_rows(pipeline_status: pd.DataFrame) -> pd.DataFrame:
    """Construction rows of the existing fleet's groups that are not operating yet.

    Args:
        pipeline_status: The pipeline status timeseries.

    Returns:
        Its ``construction`` rows, with ``note`` set to ``expansion`` for the groups the
        PAM built and ``input data`` for the units the input data brought along.
    """
    rows = pipeline_status.loc[pipeline_status["status"] == "construction", PIPELINE_COLUMNS]
    notes = rows["created_by_pam"].astype(bool).map({True: "expansion", False: "input data"})
    return rows.assign(note=notes)[CONSTRUCTION_COLUMNS]


def _expansion_rows(motions: pd.DataFrame, operating: pd.DataFrame, last_year: int) -> pd.DataFrame:
    """Construction rows of the expansions at existing plants.

    Args:
        motions: The PAM motions table, whose ``expansion`` rows are dated the
            decision year and carry the new group's technology and capacity.
        operating: The operating groups, giving each group's first operating year.
        last_year: The last simulated year, until which a group that never starts
            operating stays under construction.

    Returns:
        One row per expansion and year from its decision to the year before its first
        operating row, with ``note`` set to ``expansion``.
    """
    rows = []
    first_operating = operating.groupby("furnace_group_id")["year"].min()
    for motion in motions[motions["kind"] == "expansion"].to_dict("records"):
        start = int(first_operating.get(motion["furnace_group_id"], last_year + 1))
        for year in range(int(motion["year"]), start):
            rows.append(
                {
                    "year": year,
                    "geo_key": motion["geo_key"],
                    "plant_id": motion["plant_id"],
                    "furnace_group_id": motion["furnace_group_id"],
                    "technology": motion["new_technology"],
                    "product": motion["product"],
                    "capacity": motion["new_capacity_t"],
                    "note": "expansion",
                },
            )
    return pd.DataFrame(rows, columns=CONSTRUCTION_COLUMNS)


def pack_sites(
    table: pd.DataFrame,
    greenfield_status: Optional[pd.DataFrame],
    switch_decisions: Optional[pd.DataFrame],
    motions: Optional[pd.DataFrame],
    pipeline_status: Optional[pd.DataFrame],
    plants: dict[str, dict[str, Any]],
    plant_names: dict[str, str],
    iso3: Optional[str] = None,
) -> dict[str, Any]:
    """Compact per-plant payload for the template.

    Args:
        table: The post-processed furnace-group table. Its iron and steel rows,
            deduplicated across feedstock rows, are the operating groups.
        greenfield_status: The greenfield status timeseries
            (``data/greenfield_status_timeseries.csv``), whose ``construction`` rows
            are the new builds under construction; None omits them.
        switch_decisions: The switch decisions (``data/pam_switch_decisions.csv``);
            None omits the rebuilds and the switch lines.
        motions: The PAM motions (``data/pam_motions.csv``), whose ``expansion`` rows
            are the expansions at existing plants; None omits them while they are built.
            Only a fallback for runs without ``pipeline_status``.
        pipeline_status: The pipeline status timeseries
            (``data/pipeline_status_timeseries.csv``), whose ``construction`` rows are the
            existing fleet's groups that are not operating yet; None (older runs) omits
            the input data's units before they operate.
        plants: ``{plant_id: {"lat", "lon", "greenfield"}}`` of the run's plants.
        plant_names: ``{plant_id: plant_name}``; plants without an entry are named by
            their id (``New plant <id>`` for greenfield plants).
        iso3: Keep only this country's plants (the China map); None keeps the world.

    Returns:
        ``years`` (every simulated year); ``techs`` (iron technologies first, then
        alphabetical); ``ironTechs``; ``statuses``; and ``sites`` — one record per
        plant with ``id``, ``name``, ``iso``, ``geo`` (the geo key when it names a
        sub-national unit, else None), ``lat``/``lon`` (4 decimals), ``greenfield``,
        ``segs`` and ``switches``. ``segs`` are ``[first_year, last_year, units]``
        runs of identical years, a unit being ``[technology, status, capacity in
        ttpa, construction note or None, furnace_group_id]``, the note being ``rebuild from
        <old technology>``, ``expansion`` or ``input data``. ``switches``
        are ``[decision_year, switch_year, old_technology, new_technology]``.

    Raises:
        ValueError: If a table lacks a required column, a plant has no entry or no
            coordinates in ``plants``, or ``iso3`` is given and the run has no plant there.

    Notes:
        A group counts as under construction for a rebuild from its
        ``construction_start_year`` to the year before its ``switch_year``, capped at
        the last simulated year, in the new technology at the new capacity. The
        greenfield table's ``construction switching technology`` rows are ignored
        because the decisions cover them. Its ``region`` column is not used: it
        books some groups under "unknown". An expansion counts as under construction
        from its motion year to the year before its first operating row. Where two
        layers hold the same group and year (a few expansions are also in the
        greenfield table) the first in the order above wins.
    """
    _require(table, TABLE_COLUMNS, "post-processed table")
    operating = table.loc[table["product"].isin(["iron", "steel"]), TABLE_COLUMNS]
    operating = operating.drop_duplicates(["year", "furnace_group_id"]).assign(status="operating")
    years = list(range(int(operating["year"].min()), int(operating["year"].max()) + 1))

    layers = [operating]
    if greenfield_status is not None:
        _require(greenfield_status, GREENFIELD_COLUMNS, "greenfield status timeseries")
        layers.append(greenfield_status.loc[greenfield_status["status"] == "construction", GREENFIELD_COLUMNS])
    if switch_decisions is not None:
        _require(switch_decisions, DECISION_COLUMNS, "switch decisions table")
        layers.append(_rebuild_rows(switch_decisions, years[-1]).assign(status="construction"))
    if pipeline_status is not None:
        _require(pipeline_status, PIPELINE_COLUMNS, "pipeline status timeseries")
        layers.append(_pipeline_rows(pipeline_status).assign(status="construction"))
    if motions is not None:
        _require(motions, MOTION_COLUMNS, "motions table")
        layers.append(_expansion_rows(motions, operating, years[-1]).assign(status="construction"))
    groups = pd.concat([layer for layer in layers if len(layer)], ignore_index=True)
    groups = groups[groups["capacity"] > 0].drop_duplicates(["year", "furnace_group_id"]).copy()
    groups["iso3"] = groups["iso3"].fillna(groups["geo_key"].str.split(":").str[0])
    if "note" not in groups:
        groups["note"] = None
    if iso3 is not None:
        groups = groups[groups["iso3"] == iso3]
        if groups.empty:
            raise ValueError(f"The run has no plants in {iso3}")

    located = {plant_id for plant_id, plant in plants.items() if pd.notna(plant["lat"]) and pd.notna(plant["lon"])}
    missing = sorted(set(groups["plant_id"]) - located)
    if missing:
        raise ValueError(f"{len(missing)} plants on the capacity map have no coordinates, e.g. {missing[:5]}")

    switches_by_plant: dict[str, list[list[Any]]] = {}
    if switch_decisions is not None:
        for decision in switch_decisions.sort_values(["decision_year", "furnace_group_id"]).to_dict("records"):
            switches_by_plant.setdefault(decision["plant_id"], []).append(
                [
                    int(decision["decision_year"]),
                    int(decision["switch_year"]),
                    decision["old_technology"],
                    decision["new_technology"],
                ],
            )

    sites = []
    for key, rows in groups.groupby("plant_id", sort=False):
        plant_id = str(key)
        by_year: dict[int, list[list[Any]]] = {}
        for row in rows.to_dict("records"):
            note = row["note"] if pd.notna(row["note"]) else None
            by_year.setdefault(int(row["year"]), []).append(
                # t -> ttpa
                [
                    row["technology"],
                    row["status"],
                    int(round(row["capacity"] / 1000)),
                    note,
                    row["furnace_group_id"],
                ],
            )
        segs: list[list[Any]] = []
        for year in years:
            units = sorted(by_year.get(year, []), key=lambda unit: (unit[0], unit[4]))
            if segs and segs[-1][2] == units:
                segs[-1][1] = year
            else:
                segs.append([year, year, units])
        plant = plants[plant_id]
        geo_key = rows["geo_key"].iloc[0]
        sites.append(
            {
                "id": plant_id,
                "name": plant_names.get(plant_id, f"New plant {plant_id}" if plant["greenfield"] else plant_id),
                "iso": rows["iso3"].iloc[0],
                "geo": geo_key if ":" in geo_key else None,
                "lat": round(float(plant["lat"]), 4),
                "lon": round(float(plant["lon"]), 4),
                "greenfield": bool(plant["greenfield"]),
                "segs": segs,
                "switches": switches_by_plant.get(plant_id, []),
            },
        )

    iron_techs = sorted(groups.loc[groups["product"] == IRON_PRODUCT, "technology"].unique())
    steel_techs = sorted(set(groups["technology"]) - set(iron_techs))
    return {
        "years": years,
        "techs": iron_techs + steel_techs,
        "ironTechs": iron_techs,
        "statuses": STATUSES,
        "sites": sites,
    }


def focus_config(iso3: str, region_rows: list[RegionRow]) -> dict[str, Any]:
    """The template's ``focus`` config for a one-country map.

    Args:
        iso3: The country the map shows.
        region_rows: The capacity pool's province rows
            (``fixtures/capacity_pool_provinces.json``); empty for a policy-OFF run.

    Returns:
        ``{"iso3", "groups", "types"}``: ``groups`` maps a geo key to its region name
        where at least two provinces share that name (Jing-Jin-Ji, not a province's
        own name); ``types`` maps a geo key to ``key`` or ``exempt``. Both are empty
        without region rows, which leaves the map without province groups.
    """
    rows = [row for row in region_rows if row.geo_key.startswith(f"{iso3}:")]
    shared = {name for name, count in Counter(row.region_name for row in rows).items() if name and count > 1}
    return {
        "iso3": iso3,
        "groups": {row.geo_key: row.region_name for row in rows if row.region_name in shared},
        "types": {row.geo_key: row.type for row in rows if row.type},
    }


def source_line(input_sources: list[str]) -> str:
    """The map's source line, naming the input data sets behind the plants.

    Args:
        input_sources: The distinct ``source`` values of the master's Furnace units
            sheet, in the order to name them. An empty list reads as GEM only.

    Returns:
        E.g. ``"Source: Steel-IQ model (with GEM and EXTERNAL input data)"``:
        ``gem_unit`` reads as GEM (the ``_unit`` suffix is stripped), other values are
        upper-cased.
    """
    inputs = [source.removesuffix("_unit").upper() for source in input_sources] or ["GEM"]
    listed = " and ".join(filter(None, [", ".join(inputs[:-1]), inputs[-1]]))
    return f"Source: Steel-IQ model (with {listed} input data)"


def write_viewer(config: dict[str, Any], data: dict[str, Any], output_path: Path) -> Path:
    """Write a capacity map viewer.

    Args:
        config: The shell config (:meth:`InteractivePlotter._config`) plus ``focus``
            (:func:`focus_config`, or None for the world) and ``fileStem`` (the PNG
            export's file name); the viewer also reads its ``chartTitle``,
            ``techColours``, ``geoInfo``, ``geoUnitNames`` and ``defaultRun``.
        data: Per-run payloads — ``{run: {"title", "provenance", "source", **pack_sites(...)}}``.
        output_path: The HTML file to write; parent directories are created.

    Returns:
        ``output_path``.
    """
    html = (
        (ASSETS_DIR / "capacity_world_map.html")
        .read_text()
        .replace("__COMMON_CSS__", (ASSETS_DIR / "common.css").read_text())
        .replace("__WORLD__", (ASSETS_DIR / "world_countries.json").read_text())
        .replace("__CONFIG__", json.dumps(config))
        .replace("__DATA__", json.dumps(data, separators=(",", ":")))
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path
