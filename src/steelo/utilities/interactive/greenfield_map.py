"""Record packing for the greenfield buildout map viewer (``greenfield_map.html``).

The map is the static ``greenfield/<product>_greenfield_map*.png`` charts made
interactive: every greenfield (GEO-origin) furnace group as a capacity-sized dot
over the trade map's world outline, with a year slider, filters for status,
technology, product, reductant and metallic charge, and hover tooltips carrying
each group's full history — the year it entered each status, technology and
reductant switches, and the shown year's production and charge allocations.
deck.gl and the world outline are inlined, so the file opens from disk with no
network access.

Group histories come from ``data/greenfield_status_timeseries.csv`` (one snapshot
row per group and year), run-length encoded per field so the viewer can read any
year's state and the switch years from the same segments. Metallic charges come
from the post-processed table's per-feedstock allocation rows, identified as
charges by the Bill of Materials exactly as the metallic charge viewer does.

``china_provinces.json`` overlays the Chinese province borders on the world
outline: Natural Earth 10m admin-1 boundaries (public domain), exported once
from the local shapefile (``ne_10m_admin_1_states_provinces``, CHN rows) as
per-province boundary lines simplified to 0.02 degrees and rounded to 2 decimal
places.
"""

import json
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from steelo.domain.models import PrimaryFeedstock

from .greenfield_status import STATUS_COLOURS
from .metallic_charge_use import CHARGE_ORDER, technology_charges
from .reductant_use import REDUCTANT_COLOURS
from .trade_allocations import ASSETS_DIR, DECKGL_JS

REQUIRED_COLUMNS = [
    "year",
    "furnace_group_id",
    "product",
    "technology",
    "reductant",
    "status",
    "geo_key",
    "lat",
    "lon",
    "capacity",
    "production",
]

# Steel groups run no reductant; the empty CSV value becomes this explicit filter
# option (REDUCTANT_COLOURS carries its colour).
NO_REDUCTANT = "none"


def _ordered(values: Iterable[str], preferred: list[str]) -> list[str]:
    """The values in the preferred order, with unknown extras appended alphabetically."""
    present = set(values)
    return [v for v in preferred if v in present] + sorted(present - set(preferred))


def greenfield_charges(
    post_processed: pd.DataFrame, primary_feedstocks: list[PrimaryFeedstock], group_ids: set[str]
) -> pd.DataFrame:
    """Per-year metallic-charge allocations of the greenfield furnace groups.

    Args:
        post_processed: The post-processed furnace-group table, whose feedstock
            rows carry each group's per-charge demand.
        primary_feedstocks: The prepared primary feedstocks (the Bill of
            Materials), whose ``metallic_charge`` fields identify which feedstock
            rows are charges.
        group_ids: The greenfield furnace-group ids to keep.

    Returns:
        Columns ``furnace_group_id, year, charge, use_mt`` — one row per group,
        year and charge (Mt). Feedstock rows that are no charge of the group's
        technology (e.g. ``bio_pci`` procurement rows) drop out; the dedicated
        metallic charge viewer warns about Bill of Materials gaps, this map does
        not repeat it.

    Raises:
        ValueError: If the table lacks a required column.
    """
    required = ["year", "furnace_group_id", "technology", "feedstock", "demand"]
    missing = [column for column in required if column not in post_processed.columns]
    if missing:
        raise ValueError(f"The post-processed table has no {', '.join(missing)} column(s)")
    table = post_processed.loc[
        post_processed["furnace_group_id"].isin(group_ids)
        & post_processed["feedstock"].notna()
        & (post_processed["demand"] > 0),
        required,
    ].drop_duplicates(subset=["furnace_group_id", "year", "feedstock"])
    matched = table.merge(technology_charges(primary_feedstocks), on=["technology", "feedstock"])
    matched["use_mt"] = matched["demand"] / 1e6
    matched = matched.rename(columns={"feedstock": "charge"})
    return matched[["furnace_group_id", "year", "charge", "use_mt"]]


def pack_groups(status_timeseries: pd.DataFrame, charges: Optional[pd.DataFrame] = None) -> dict[str, Any]:
    """Compact per-group payload for the template.

    Args:
        status_timeseries: The greenfield status timeseries table
            (``data/greenfield_status_timeseries.csv``).
        charges: Output of :func:`greenfield_charges`, or None when the
            post-processed table or the Bill of Materials is unavailable.

    Returns:
        The viewer's per-run payload: sorted ``years``; the value tables
        ``statuses`` (lifecycle order), ``techs`` (alphabetical), ``reductants``
        and ``charges`` (house order); and ``groups`` — one record per furnace
        group with ``id``, ``g`` geo key, ``la``/``lo`` coordinates (4 decimals),
        ``p`` product, ``y1`` last snapshot year, the run-length encoded
        histories ``s``/``t``/``r``/``cap``/``pr`` (``[year, value]`` segments:
        status/technology/reductant table indices, capacity and production in Mt,
        each starting a run that lasts until the next segment) and, where
        allocations exist, ``c`` — ``{year: [[charge index, Mt], ...]}``.

    Raises:
        ValueError: If the timeseries lacks a required column.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in status_timeseries.columns]
    if missing:
        raise ValueError(f"The greenfield status timeseries has no {', '.join(missing)} column(s)")
    frame = status_timeseries.sort_values(["furnace_group_id", "year"]).copy()
    frame["reductant"] = frame["reductant"].fillna("").replace("", NO_REDUCTANT)

    statuses = _ordered(frame["status"], list(STATUS_COLOURS))
    techs = sorted(set(frame["technology"]))
    reductants = _ordered(frame["reductant"], list(REDUCTANT_COLOURS))
    charge_frame = (
        charges if charges is not None else pd.DataFrame(columns=["furnace_group_id", "year", "charge", "use_mt"])
    )
    charge_names = _ordered(charge_frame["charge"], CHARGE_ORDER)
    status_idx = {name: i for i, name in enumerate(statuses)}
    tech_idx = {name: i for i, name in enumerate(techs)}
    reductant_idx = {name: i for i, name in enumerate(reductants)}
    charge_idx = {name: i for i, name in enumerate(charge_names)}
    charges_by_group = dict(tuple(charge_frame.groupby("furnace_group_id"))) if len(charge_frame) else {}

    def rle(pairs: Iterable[tuple[int, Any]]) -> list[list[Any]]:
        segments: list[list[Any]] = []
        for year, value in pairs:
            if not segments or segments[-1][1] != value:
                segments.append([year, value])
        return segments

    groups = []
    for group_id, rows in frame.groupby("furnace_group_id", sort=True):
        years = [int(y) for y in rows["year"]]
        first = rows.iloc[0]
        record: dict[str, Any] = {
            "id": group_id,
            "g": first["geo_key"],
            "la": round(float(first["lat"]), 4),
            "lo": round(float(first["lon"]), 4),
            "p": first["product"],
            "y1": years[-1],
            "s": rle(zip(years, (status_idx[s] for s in rows["status"]))),
            "t": rle(zip(years, (tech_idx[t] for t in rows["technology"]))),
            "r": rle(zip(years, (reductant_idx[r] for r in rows["reductant"]))),
            "cap": rle(zip(years, (round(float(c) / 1e6, 4) for c in rows["capacity"]))),
            "pr": rle(zip(years, (round(float(p) / 1e6, 4) for p in rows["production"]))),
        }
        if group_id in charges_by_group:
            allocations: dict[int, list[list[Any]]] = {}
            for row in charges_by_group[group_id].to_dict("records"):
                allocations.setdefault(int(row["year"]), []).append(
                    [charge_idx[row["charge"]], round(float(row["use_mt"]), 4)],
                )
            record["c"] = {year: sorted(entries) for year, entries in allocations.items()}
        groups.append(record)

    return {
        "years": sorted(int(y) for y in set(frame["year"])),
        "statuses": statuses,
        "techs": techs,
        "reductants": reductants,
        "charges": charge_names,
        "groups": groups,
    }


def write_viewer(config: dict[str, Any], data: dict[str, Any], output_path: Path) -> Path:
    """Write the greenfield buildout map viewer.

    Args:
        config: The shell config (:meth:`InteractivePlotter._config`) plus this
            viewer's ``statusColours``, ``reductantColours`` and ``chargeColours``.
        data: Per-run payloads — ``{run: {"title", "provenance", **pack_groups(...)}}``.
        output_path: The HTML file to write; parent directories are created.

    Returns:
        ``output_path``.
    """
    html = (
        (ASSETS_DIR / "greenfield_map.html")
        .read_text()
        .replace("__COMMON_CSS__", (ASSETS_DIR / "common.css").read_text())
        .replace("__COMMON_JS__", (ASSETS_DIR / "common.js").read_text())
        .replace("__WORLD__", (ASSETS_DIR / "world_outline.json").read_text())
        .replace("__PROVINCES__", (ASSETS_DIR / "china_provinces.json").read_text())
        .replace("__CONFIG__", json.dumps(config))
        .replace("__DATA__", json.dumps(data, separators=(",", ":")))
        .replace("__DECKGL__", DECKGL_JS.read_text())
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path
