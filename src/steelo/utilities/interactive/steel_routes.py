"""Steelmaking routes over time, shared by the static PAM chart and the steel routes viewer (``steel_routes.html``).

A route combines a steel furnace (BOF or EAF) with the iron it is charged with, so
"DRI-EAF (green H2)" is steel made in an EAF from DRI whose source furnace group runs on hydrogen.
The post-processed table repeats a furnace group's production on every feedstock row and does not
say where the iron came from, so the routes are built from two sources:

* volumes: each steel furnace group's recorded production, so the totals equal the steel
  production of the PAM plots;
* mix: the furnace group's incoming metallic charge allocations (``TM/steel_trade_allocations_<year>.csv``),
  each traced back to its source furnace group's technology and reductant.

Allocated steel volumes are not used as volumes because allocation can exceed recorded production
in some furnace groups.
"""

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from .post_processed import GEO_COLUMNS

logger = logging.getLogger(__name__)

STEEL_FURNACES = ("BOF", "EAF")
# Metallic charges that carry iron into a steel furnace; scrap is handled separately.
IRON_CHARGES = ("dri_low", "dri_mid", "dri_high", "hbi_low", "hbi_mid", "hbi_high", "hot_metal", "pig_iron")
CHARGES = (*IRON_CHARGES, "scrap")
REQUIRED_COLUMNS = ["year", "furnace_group_id", "technology", "chosen_reductant", "product", "production"]

BF_BOF_FOSSIL = "BF-BOF (coke + fossil PCI)"
BF_BOF_BIO = "BF-BOF (coke + bio-PCI)"
BF_BOF_H2 = "BF-BOF + H2 PCI"
CHARCOAL_BOF = "Charcoal BF-BOF"
CHARCOAL_BOF_CCS = "Charcoal BF-BOF + CCS"
DRI_BOF_FOSSIL = "DRI-BOF (natural gas or coal)"
DRI_BOF_H2 = "DRI-BOF (green H2)"
BF_EAF = "BF-iron-EAF (pig iron / hot metal)"
DRI_EAF_FOSSIL = "DRI-EAF (natural gas or coal)"
DRI_EAF_H2 = "DRI-EAF (green H2)"
SCRAP_EAF = "Scrap-EAF"
SCRAP_BOF = "Scrap-BOF"
UNALLOCATED = "Steel without charge allocation"

# Stack order, bottom to top, with the colour of each route (blacks and greys for blast furnaces, oranges for
# DRI-BOF, greens for DRI-EAF, blue for scrap, as in the transition charts of other models).
ROUTE_COLOURS = {
    BF_BOF_FOSSIL: "#000000",
    BF_BOF_BIO: "#4a4a4a",
    BF_BOF_H2: "#7a7a7a",
    CHARCOAL_BOF: "#a8a8a8",
    CHARCOAL_BOF_CCS: "#d9a0c8",
    DRI_BOF_FOSSIL: "#f4b24a",
    DRI_BOF_H2: "#c8642a",
    BF_EAF: "#c9d6e8",
    DRI_EAF_FOSSIL: "#8fc77f",
    DRI_EAF_H2: "#2e8b3e",
    SCRAP_BOF: "#8a6d4b",
    SCRAP_EAF: "#3b6fb5",
    UNALLOCATED: "#e0e0e0",
}
ROUTE_ORDER = list(ROUTE_COLOURS)


def iron_source(technology: str, reductant: object) -> str:
    """Name the kind of iron a source furnace group makes, from its technology and reductant.

    Args:
        technology: The source furnace group's technology (``BF``, ``DRI``, ``BF_CHARCOAL``, ...).
        reductant: Its ``chosen_reductant`` (``hydrogen``, ``natural_gas``, ``coke+bio_pci+h2``, ...); may be NaN.

    Returns:
        ``DRI-H2``, ``DRI-FOSSIL``, ``BF-FOSSIL``, ``BF-BIO``, ``BF-H2``, ``BF-CHARCOAL`` or ``BF-CHARCOAL-CCS``.
    """
    reductant = reductant if isinstance(reductant, str) else ""
    if technology == "DRI":
        return "DRI-H2" if reductant == "hydrogen" else "DRI-FOSSIL"
    if technology == "BF_CHARCOAL+CCS":
        return "BF-CHARCOAL-CCS"
    if technology == "BF_CHARCOAL":
        return "BF-CHARCOAL"
    if "h2" in reductant:
        return "BF-H2"
    return "BF-BIO" if "bio_pci" in reductant else "BF-FOSSIL"


def route_of(furnace: str, iron: str) -> str:
    """The route of steel made in ``furnace`` from ``iron`` (an :func:`iron_source` name, or ``SCRAP``)."""
    if furnace == "BOF":
        if iron == "SCRAP":
            return SCRAP_BOF
        return {
            "BF-FOSSIL": BF_BOF_FOSSIL,
            "BF-BIO": BF_BOF_BIO,
            "BF-H2": BF_BOF_H2,
            "BF-CHARCOAL": CHARCOAL_BOF,
            "BF-CHARCOAL-CCS": CHARCOAL_BOF_CCS,
            "DRI-H2": DRI_BOF_H2,
        }.get(iron, DRI_BOF_FOSSIL)
    if iron == "SCRAP":
        return SCRAP_EAF
    if iron.startswith("BF"):
        return BF_EAF
    return DRI_EAF_H2 if iron == "DRI-H2" else DRI_EAF_FOSSIL


def _charge_shares(allocations: pd.DataFrame, iron_by_furnace_group: pd.Series) -> pd.DataFrame:
    """Share of each steel furnace group's metallic charge per iron source, from one year's allocations.

    Returns:
        Columns ``furnace_group_id, iron, share``; shares sum to one per furnace group.
    """
    charges = allocations[allocations["commodity"].isin(CHARGES)]
    iron = charges["source_id"].map(iron_by_furnace_group)
    iron = iron.where(charges["commodity"] != "scrap", "SCRAP").fillna("UNKNOWN")
    shares = (
        charges.assign(iron=iron).groupby(["destination_id", "iron"])["allocated_volume"].sum().reset_index()
    ).rename(columns={"destination_id": "furnace_group_id"})
    shares["share"] = shares["allocated_volume"] / shares.groupby("furnace_group_id")["allocated_volume"].transform(
        "sum"
    )
    return shares[["furnace_group_id", "iron", "share"]]


def _bof_scrap_to_iron_routes(shares: pd.DataFrame, furnace_by_group: pd.Series) -> pd.DataFrame:
    """Spread the scrap charged in a BOF over the BOF's iron routes.

    A BOF cannot run on scrap alone, so its scrap belongs to the route of its hot metal or DRI, not to a
    route of its own. A BOF charged with scrap only keeps its scrap share (the ``Scrap-BOF`` route).
    """
    is_bof = shares["furnace_group_id"].map(furnace_by_group) == "BOF"
    is_scrap = shares["iron"] == "SCRAP"
    iron_share = shares[~is_scrap].groupby("furnace_group_id")["share"].sum()
    iron_total = shares["furnace_group_id"].map(iron_share)
    spread = is_bof & iron_total.notna()  # BOFs that also take iron
    rescaled = shares.assign(share=shares["share"].where(~spread, shares["share"] / iron_total))
    return rescaled[~(spread & is_scrap)]


def steel_route_rows(post_processed: pd.DataFrame, tm_dir: Path) -> pd.DataFrame:
    """Steel production per year, geography, steel furnace and route, in Mt.

    Args:
        post_processed: The post-processed furnace-group table (see :data:`REQUIRED_COLUMNS`, plus ``geo_key``
            or ``iso3``).
        tm_dir: The run's ``TM`` folder holding ``steel_trade_allocations_<year>.csv``. A year without
            that file is skipped with a warning.

    Returns:
        Columns ``year, geo, technology, route, production_mt``, where ``technology`` is the steel furnace
        (``BOF`` or ``EAF``). A furnace group with several charges contributes its production split by
        charge share to each route. Steel from a furnace group with no charge allocation is reported as
        ``Steel without charge allocation`` rather than guessed. Empty when no year has allocations.

    Raises:
        ValueError: If a required column is missing.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in post_processed.columns]
    geo_column = next((c for c in GEO_COLUMNS if c in post_processed.columns), None)
    if missing or geo_column is None:
        raise ValueError(f"The post-processed table lacks the columns {missing or list(GEO_COLUMNS)}")

    per_fg = post_processed.drop_duplicates(subset=["furnace_group_id", "year"])
    frames = []
    for year_key, table in per_fg.groupby("year"):
        year = int(str(year_key))
        path = tm_dir / f"steel_trade_allocations_{year}.csv"
        if not path.is_file():
            logger.warning("No trade allocations at %s — year %s left out of the steel routes", path, year)
            continue
        allocations = pd.read_csv(
            path, usecols=["commodity", "source_id", "destination_id", "allocated_volume"], low_memory=False
        )
        iron = pd.Series(
            [iron_source(t, r) for t, r in zip(table["technology"], table["chosen_reductant"])],
            index=table["furnace_group_id"].values,
        )
        iron = iron[~iron.index.duplicated()]
        steel = table[(table["product"] == "steel") & table["technology"].isin(STEEL_FURNACES)]
        steel = steel[steel["production"] > 0]
        furnace_by_group = pd.Series(steel["technology"].values, index=steel["furnace_group_id"].values)

        shares = _bof_scrap_to_iron_routes(_charge_shares(allocations, iron), furnace_by_group)
        merged = steel[["furnace_group_id", "technology", geo_column, "production"]].merge(
            shares, on="furnace_group_id", how="left"
        )
        merged["iron"] = merged["iron"].fillna("UNALLOCATED")
        merged["share"] = merged["share"].fillna(1.0)
        merged["production_mt"] = merged["production"] * merged["share"] / 1e6
        merged["route"] = [
            UNALLOCATED if i in ("UNALLOCATED", "UNKNOWN") else route_of(f, i)
            for f, i in zip(merged["technology"], merged["iron"])
        ]
        merged["year"] = year
        frames.append(merged.rename(columns={geo_column: "geo"}))
    if not frames:
        return pd.DataFrame(columns=["year", "geo", "technology", "route", "production_mt"])
    totals = pd.concat(frames).groupby(["year", "geo", "technology", "route"])["production_mt"].sum().reset_index()
    return totals.sort_values(by=["year", "geo", "technology", "route"], ignore_index=True)


def pack_rows(route_rows: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact rows for embedding in the viewer: ``y`` year, ``g`` geo, ``t`` steel furnace, ``r`` route, ``v`` Mt."""
    return [
        {
            "y": int(row["year"]),
            "g": row["geo"],
            "t": row["technology"],
            "r": row["route"],
            "v": round(float(row["production_mt"]), 4),
        }
        for row in route_rows.to_dict("records")
    ]
