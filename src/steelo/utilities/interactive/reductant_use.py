"""Row packing for the reductant and energy use viewer (``reductant_use.html``).

The viewer shows iron production by the reductant each furnace group runs on,
and the absolute use of the reductants themselves, stacked by reductant,
technology or region, with the shell's geography and technology filters. Only
iron rows count — steelmaking never uses a reductant, so BOF and EAF are out
of scope — and only the chosen reductant's components count as use: auxiliary
energy inputs of the same process (a BF's electricity, say) do not.

Production comes straight from the post-processed table. Reductant quantities
are reconstructed from the Bill of Materials: each feedstock row's allocated
tonnage divided by the matching primary feedstock's required quantity gives the
product tonnage attributable to that (technology, metallic charge, reductant)
entry, which is then multiplied by the entry's per-tonne intensities of the
reductant components named in its ``reductant`` (``+``-separated).
"""

import logging
from typing import Any, Optional

import pandas as pd

from steelo.domain.models import PrimaryFeedstock

from .post_processed import GEO_COLUMNS

logger = logging.getLogger(__name__)

AGGREGATION_KEYS = ["year", "geo", "technology", "reductant"]

# Carrier consumed per component of a Bill of Materials reductant name
# (components are the "+"-separated parts, e.g. coke+bio_pci+h2).
REDUCTANT_COMPONENT_CARRIERS = {
    "coke": "coking_coal",  # coke is booked as the coking coal it is made from
    "pci": "pci",
    "bio_pci": "bio_pci",
    "h2": "hydrogen",
    "hydrogen": "hydrogen",
    "natural_gas": "natural_gas",
    "coal": "coal",
    "electricity": "electricity",
}

# Unit per carrier as stored in the primary feedstocks fixture (the excel reader
# converts energy-metric rows to kWh/t product and mass-metric rows to t/t product),
# aggregated here to TWh and Mt respectively.
CARRIER_UNITS = {
    "coking_coal": "Mt",
    "pci": "Mt",
    "bio_pci": "Mt",
    "hydrogen": "Mt",
    "natural_gas": "TWh",
    "coal": "TWh",
    "electricity": "TWh",
}
CARRIER_SCALE = {"TWh": 1e9, "Mt": 1e6}  # kWh → TWh, t → Mt

CARRIER_LABELS = {
    "coking_coal": "Coking coal",
    "pci": "PCI",
    "bio_pci": "Bio-PCI",
    "hydrogen": "Hydrogen",
    "natural_gas": "Natural gas",
    "coal": "Coal",
    "electricity": "Electricity",
}


def reductant_components(reductant: Optional[str]) -> list[str]:
    """The ``+``-separated components of a Bill of Materials reductant name.

    Args:
        reductant: The entry's reductant; None, ``""`` and ``"none"`` mean no reductant.

    Returns:
        The component names in entry order (empty for reductant-less entries).
    """
    return [] if reductant in (None, "", "none") else str(reductant).split("+")


# House colours per reductant; "none" is the label for furnace groups without one
# (BOF, EAF). Blends not listed here get the shell's stable fallback colours.
REDUCTANT_COLOURS = {
    "coke+pci": "#4d4d4d",
    "coke+bio_pci": "#2e8b57",
    "coke+pci+h2": "#5c6bc0",
    "coke+bio_pci+h2": "#26a69a",
    "pci": "#8c8c8c",
    "coal": "#1a1a1a",
    "bio_pci": "#66bb6a",
    "charcoal": "#8d6e63",
    "natural_gas": "#e69f00",
    "hydrogen": "#56b4e9",
    "electricity": "#e6c229",
    "none": "#c9c5bd",
}


def carrier_meta(carriers: list[str]) -> list[dict[str, str]]:
    """Display metadata of the embedded carriers, for the viewer's metric selector.

    Args:
        carriers: Carrier keys in the order of every packed row's ``e`` list.

    Returns:
        One ``{"key", "label", "unit"}`` record per carrier, in the same order.
    """
    return [
        {"key": c, "label": CARRIER_LABELS.get(c, c.replace("_", " ").capitalize()), "unit": CARRIER_UNITS[c]}
        for c in carriers
    ]


def bom_table(primary_feedstocks: list[PrimaryFeedstock], carriers: list[str]) -> pd.DataFrame:
    """The Bill of Materials as one row per (technology, feedstock, reductant) entry.

    Args:
        primary_feedstocks: The prepared primary feedstocks (``fixtures/primary_feedstocks.json``).
        carriers: Carrier keys to carry as intensity columns.

    Returns:
        Columns ``technology, feedstock, reductant, required_quantity`` and one intensity
        column per carrier, filled only from each entry's own reductant components (all
        other carriers stay 0, so auxiliary energy inputs never count as reductant use).
        Technologies are upper-cased to match the post-processed table (repository-loaded
        feedstocks carry them lower-cased); reductant-less entries are keyed ``"none"``,
        matching the aggregated table; entries without a positive required quantity are
        dropped (production cannot be attributed to them). Entries whose reductant names
        a component with no intensity of its own are warned about (its use reads 0).
    """
    records = []
    missing: set[str] = set()
    for fs in primary_feedstocks:
        if not fs.required_quantity_per_ton_of_product:
            continue
        intensities = dict.fromkeys(carriers, 0.0)
        for component in reductant_components(fs.reductant):
            carrier = REDUCTANT_COMPONENT_CARRIERS.get(component)
            if carrier is None:
                continue  # aggregate_reductant_use has already rejected unknown components
            if carrier in (fs.energy_requirements or {}):
                intensities[carrier] = fs.energy_requirements[carrier]
            else:
                missing.add(fs.name)
        records.append(
            {
                "technology": fs.technology.upper(),
                "feedstock": fs.metallic_charge,
                "reductant": fs.reductant or "none",
                "required_quantity": fs.required_quantity_per_ton_of_product,
                **intensities,
            }
        )
    if missing:
        logger.warning(
            "%d Bill of Materials entr%s name a reductant component without its own intensity — that use reads 0: %s",
            len(missing),
            "y" if len(missing) == 1 else "ies",
            ", ".join(sorted(missing)[:8]),
        )
    return pd.DataFrame(records, columns=["technology", "feedstock", "reductant", "required_quantity", *carriers])


def aggregate_reductant_use(
    post_processed: pd.DataFrame, primary_feedstocks: Optional[list[PrimaryFeedstock]] = None
) -> tuple[list[str], pd.DataFrame]:
    """Iron production and absolute carrier use per year, geography, technology and reductant.

    Args:
        post_processed: The post-processed furnace-group table. Only its iron rows count;
            steelmaking never uses a reductant.
        primary_feedstocks: The prepared primary feedstocks; None or empty omits the
            carrier quantities (the viewer then only shows production by reductant).

    Returns:
        ``(carriers, aggregated)`` where ``carriers`` are the reductant carriers the iron
        rows actually use (empty when the Bill of Materials is omitted) and ``aggregated``
        has the columns ``year, geo, technology, reductant, n, production_mt`` and one
        column per carrier (TWh for energy carriers, Mt for mass carriers, see
        :data:`CARRIER_UNITS`). Only the chosen reductant's components count as use —
        auxiliary energy inputs of the same process do not. Each furnace group's
        production counts once per year regardless of how many feedstock rows repeat it;
        carrier quantities sum over the feedstock rows.

    Raises:
        ValueError: If a required column is missing, or the Bill of Materials carries a
            reductant component that is not in :data:`REDUCTANT_COMPONENT_CARRIERS`
            (add its carrier there).

    Notes:
        Feedstock rows without a Bill of Materials entry contribute no carrier use; rows
        whose feedstock is a metallic charge somewhere in the Bill of Materials are
        warned about, others (e.g. ``bio_pci`` procurement rows, whose use is already
        in the intensities) are dropped silently. Carriers that end up all-zero (never
        deployed in the run) are dropped.
    """
    required = ["year", "furnace_group_id", "technology", "product", "production", "chosen_reductant"]
    missing = [column for column in required if column not in post_processed.columns]
    if missing:
        raise ValueError(f"The post-processed table has no {', '.join(missing)} column(s)")
    geo_column = next((column for column in GEO_COLUMNS if column in post_processed.columns), None)
    if geo_column is None:
        raise ValueError(f"The post-processed table has none of the geo columns {', '.join(GEO_COLUMNS)}")

    columns = [geo_column, *required] + [c for c in ("feedstock", "demand") if c in post_processed.columns]
    table = post_processed.loc[post_processed["product"] == "iron", columns].rename(columns={geo_column: "geo"}).copy()
    table["reductant"] = table["chosen_reductant"].fillna("").replace("", "none")

    per_fg = table.drop_duplicates(subset=["furnace_group_id", "year"])
    grouped = per_fg.groupby(AGGREGATION_KEYS, dropna=False)
    aggregated = (grouped["production"].sum() / 1e6).to_frame("production_mt")
    aggregated["n"] = grouped.size()
    aggregated = aggregated.reset_index()

    components = {c for fs in primary_feedstocks or [] for c in reductant_components(fs.reductant)}
    unknown = sorted(c for c in components if c not in REDUCTANT_COMPONENT_CARRIERS)
    if unknown:
        raise ValueError(
            f"Unknown reductant component(s) {', '.join(unknown)} in the primary feedstocks — "
            "add their carriers to REDUCTANT_COMPONENT_CARRIERS"
        )
    carriers = sorted({REDUCTANT_COMPONENT_CARRIERS[c] for c in components})
    if carriers and not {"feedstock", "demand"}.issubset(table.columns):
        logger.warning("The post-processed table has no feedstock/demand columns — carrier use omitted")
        carriers = []
    if not carriers:
        return [], aggregated

    bom = bom_table(primary_feedstocks or [], carriers)
    rows = table[table["demand"].notna() & (table["demand"] > 0) & table["feedstock"].notna()]
    merged = rows.merge(bom, on=["technology", "feedstock", "reductant"], how="left")
    unmatched = merged["required_quantity"].isna() & merged["feedstock"].isin(set(bom["feedstock"]))
    if unmatched.any():
        combos = merged.loc[unmatched].groupby(["technology", "feedstock", "reductant"])["demand"].sum()
        names = ", ".join("_".join(key) for key in list(combos.index)[:8])
        logger.warning(
            "No Bill of Materials entry for %d technology/feedstock/reductant combination(s) "
            "(%.1f Mt feedstock demand) — their carrier use is omitted: %s",
            len(combos),
            combos.sum() / 1e6,
            names,
        )

    matched = merged[merged["required_quantity"].notna()]
    attributed = matched["demand"] / matched["required_quantity"]
    quantities = matched[AGGREGATION_KEYS].copy()
    for carrier in carriers:
        quantities[carrier] = matched[carrier] * attributed / CARRIER_SCALE[CARRIER_UNITS[carrier]]
    energy = quantities.groupby(AGGREGATION_KEYS, dropna=False)[carriers].sum().reset_index()

    aggregated = aggregated.merge(energy, on=AGGREGATION_KEYS, how="outer")
    aggregated[["production_mt", "n", *carriers]] = aggregated[["production_mt", "n", *carriers]].fillna(0)
    aggregated["n"] = aggregated["n"].astype(int)
    unused = [carrier for carrier in carriers if aggregated[carrier].abs().sum() == 0]
    return [c for c in carriers if c not in unused], aggregated.drop(columns=unused)


def pack_rows(aggregated: pd.DataFrame, carriers: list[str]) -> list[dict[str, Any]]:
    """Compact aggregated rows for embedding in the viewer.

    Args:
        aggregated: Output of :func:`aggregate_reductant_use`.
        carriers: The carrier key order, matching the ``e`` list of every row.

    Returns:
        One short-keyed record per row: ``y`` year, ``g`` geo, ``t`` technology, ``r``
        reductant, ``n`` furnace groups, ``pr`` iron production (Mt) and ``e`` carrier
        quantities (TWh or Mt, four decimals) in ``carriers`` order.
    """

    def val(value: Any) -> float:
        return 0.0 if pd.isna(value) else round(float(value), 4)

    return [
        {
            "y": int(row["year"]),
            "g": row["geo"],
            "t": row["technology"],
            "r": row["reductant"],
            "n": int(row["n"]),
            "pr": val(row["production_mt"]),
            "e": [val(row[carrier]) for carrier in carriers],
        }
        for row in aggregated.to_dict("records")
    ]
