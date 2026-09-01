"""Row packing for the metallic charge use viewer (``metallic_charge_use.html``).

The viewer shows the metallic charges steelmakers and ironmakers feed into
their products — scrap, hot metal, pig iron and the DRI/HBI grades into
steel; the iron ore grades into iron — stacked by charge, technology or
region, with the shell's geography and technology filters. A furnace group
can run on several charges at different shares, so quantities come from the
table's per-feedstock allocation rows (one row per furnace group, year and
feedstock) rather than from the deduplicated furnace-group rows the other
viewers aggregate.

Which feedstock rows count as metallic charges is decided by the Bill of
Materials, as the static chart's collector decides it: a row counts when its
feedstock is the ``metallic_charge`` of one of its technology's primary
feedstock entries. Reductant procurement rows (e.g. ``bio_pci``) are no
technology's metallic charge and drop out. An optional overlay compares the
chart against the local scrap supply — the prepared scrap suppliers'
capacity, which is country-level.
"""

import logging
from typing import Any, Iterable

import pandas as pd

from steelo.domain.models import CountryMapping, PrimaryFeedstock, Supplier

from .post_processed import GEO_COLUMNS
from .supply_demand import geo_resolver

logger = logging.getLogger(__name__)

AGGREGATION_KEYS = ["year", "geo", "technology", "product", "charge"]

# House colours per charge: the static plotter's metallic-charge palette for the
# steel-side charges and its ore-quality palette for the iron-side ones. Charges
# not listed get the shell's stable fallback colours.
CHARGE_COLOURS = {
    "scrap": "#708090",
    "hot_metal": "#696969",
    "liquid_iron": "#4A4A4A",
    "pig_iron": "#2F4F4F",
    "dri_high": "#1F4E79",
    "dri_mid": "#4682B4",
    "dri_low": "#B0C4DE",
    "hbi_high": "#3A8FBF",
    "hbi_mid": "#87CEEB",
    "hbi_low": "#CFE7F5",
    "electrolytic_iron": "#7B68EE",
    "io_high": "#654321",
    "io_mid": "#A0522D",
    "io_low": "#CD853F",
}

# Tick, stacking and legend order (grades grouped high → low rather than
# alphabetically); charges not listed follow alphabetically.
CHARGE_ORDER = list(CHARGE_COLOURS)


def technology_charges(primary_feedstocks: Iterable[PrimaryFeedstock]) -> pd.DataFrame:
    """The Bill of Materials' (technology, metallic charge) pairs, for matching feedstock rows.

    Args:
        primary_feedstocks: The prepared primary feedstocks (``fixtures/primary_feedstocks.json``).

    Returns:
        Columns ``technology, feedstock``, one row per distinct pair. Technologies are
        upper-cased to match the post-processed table (repository-loaded feedstocks
        carry them lower-cased).
    """
    pairs = {(fs.technology.upper(), fs.metallic_charge) for fs in primary_feedstocks if fs.metallic_charge}
    return pd.DataFrame(sorted(pairs), columns=["technology", "feedstock"])


def aggregate_charge_use(post_processed: pd.DataFrame, primary_feedstocks: list[PrimaryFeedstock]) -> pd.DataFrame:
    """Metallic charge use per year, geography, technology, product and charge.

    Args:
        post_processed: The post-processed furnace-group table.
        primary_feedstocks: The prepared primary feedstocks, whose ``metallic_charge``
            fields identify which feedstock rows are charges.

    Returns:
        Columns ``year, geo, technology, product, charge, n, use_mt``: the allocated
        tonnage of each charge (Mt) and the number of furnace groups charging it.
        Each furnace group's rows already split its demand across its charges, so a
        group running several charges at different shares contributes each share to
        its own charge — no per-group deduplication beyond the (furnace group, year,
        feedstock) grain.

    Raises:
        ValueError: If a required column is missing, or the Bill of Materials names
            no metallic charge at all.

    Notes:
        Feedstock rows that are no technology's metallic charge (e.g. ``bio_pci``
        procurement rows) are dropped silently; rows whose feedstock is a charge of
        some *other* technology only are warned about, since they point at a Bill of
        Materials gap.
    """
    required = ["year", "furnace_group_id", "technology", "product", "feedstock", "demand"]
    missing = [column for column in required if column not in post_processed.columns]
    if missing:
        raise ValueError(f"The post-processed table has no {', '.join(missing)} column(s)")
    geo_column = next((column for column in GEO_COLUMNS if column in post_processed.columns), None)
    if geo_column is None:
        raise ValueError(f"The post-processed table has none of the geo columns {', '.join(GEO_COLUMNS)}")
    charges = technology_charges(primary_feedstocks)
    if charges.empty:
        raise ValueError("The primary feedstocks name no metallic charge — cannot identify charge rows")

    table = post_processed.loc[
        post_processed["feedstock"].notna() & (post_processed["demand"] > 0), [geo_column, *required]
    ].rename(columns={geo_column: "geo"})
    table = table.drop_duplicates(subset=["furnace_group_id", "year", "feedstock"])
    merged = table.merge(charges, on=["technology", "feedstock"], how="left", indicator=True)
    matched = merged[merged["_merge"] == "both"]

    unmatched = merged[(merged["_merge"] != "both") & merged["feedstock"].isin(set(charges["feedstock"]))]
    if len(unmatched):
        combos = unmatched.groupby(["technology", "feedstock"])["demand"].sum()
        names = ", ".join("_".join(key) for key in list(combos.index)[:8])
        logger.warning(
            "%d feedstock row(s) (%.1f Mt) carry a metallic charge their technology has no Bill of "
            "Materials entry for — omitted: %s",
            len(unmatched),
            combos.sum() / 1e6,
            names,
        )

    matched = matched.drop(columns="_merge").rename(columns={"feedstock": "charge"})
    grouped = matched.groupby(AGGREGATION_KEYS, dropna=False)
    aggregated = (grouped["demand"].sum() / 1e6).to_frame("use_mt")
    aggregated["n"] = grouped["furnace_group_id"].nunique()
    return aggregated.reset_index()


def scrap_supply_rows(
    suppliers: Iterable[Supplier], country_mappings: list[CountryMapping], years: set[int]
) -> pd.DataFrame:
    """Local scrap supply per year and country, from the prepared suppliers.

    Args:
        suppliers: The prepared suppliers (``fixtures/suppliers.json``); only the
            scrap ones count.
        country_mappings: The run's country mappings, to resolve suppliers placed by
            country name rather than ISO3.
        years: The years to keep (the table's years).

    Returns:
        Columns ``year, geo, supply_mt``. Scrap suppliers carry no sub-national
        geography, so ``geo`` is always a country.
    """
    resolve = geo_resolver(country_mappings)
    rows = [
        (int(year), supplier.location.iso3 or resolve(supplier.location.country), float(capacity) / 1e6)
        for supplier in suppliers
        if supplier.commodity == "scrap"
        for year, capacity in supplier.capacity_by_year.items()
        if int(year) in years
    ]
    supply = pd.DataFrame(rows, columns=["year", "geo", "supply_mt"])
    return supply.groupby(["year", "geo"], as_index=False)[["supply_mt"]].sum()


def pack_rows(aggregated: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact aggregated rows for embedding in the viewer.

    Args:
        aggregated: Output of :func:`aggregate_charge_use`.

    Returns:
        One short-keyed record per row: ``y`` year, ``g`` geo, ``t`` technology,
        ``p`` product, ``c`` charge, ``n`` furnace groups and ``v`` use (Mt, four
        decimals).
    """
    return [
        {
            "y": int(row["year"]),
            "g": row["geo"],
            "t": row["technology"],
            "p": row["product"],
            "c": row["charge"],
            "n": int(row["n"]),
            "v": round(float(row["use_mt"]), 4),
        }
        for row in aggregated.to_dict("records")
    ]


def pack_supply(supply: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact scrap-supply rows for embedding in the viewer.

    Args:
        supply: Output of :func:`scrap_supply_rows`.

    Returns:
        One short-keyed record per row: ``y`` year, ``g`` country and ``v`` supply
        (Mt, four decimals). Rows that round to zero are dropped.
    """
    rows = []
    for row in supply.to_dict("records"):
        volume = round(float(row["supply_mt"]), 4)
        if volume > 0:
            rows.append({"y": int(row["year"]), "g": row["geo"], "v": volume})
    return rows
