"""Row packing and market clearing for the cost-curve viewer (``cost_curves.html``).

The viewer draws the merit-order cost curve of one product and year — one bar per
furnace group sorted by unit production cost — with the demand and market-clearing
lines of the static ``cost_curve_<product>_by_<aggregation>_<year>.png`` charts.
The clearing rule lives in :func:`~steelo.utilities.plotting._compute_market_clearing`
(the engine's rule); :func:`market_clearing` wraps it and the template ports it so the
curve can re-form for a filtered selection.
"""

import logging
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from steelo.utilities.plotting import _compute_market_clearing

from .post_processed import GEO_COLUMNS

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "year",
    "product",
    "technology",
    "furnace_group_id",
    "capacity",
    "production",
    "unit_production_cost",
]
STEEL_DEMAND_COLUMN = "steel_demand_t"


def clearing_config(
    capacity_limit: float,
    steel_share: float,
    steel_buffer: float,
    iron_share: float,
    iron_buffer: float,
) -> dict[str, Any]:
    """The clearing parameters the viewer embeds, keyed as the template reads them.

    Args:
        capacity_limit: Multiplier applied to furnace-group capacity before cumulating.
        steel_share: Fraction of steel capacity that participates in clearing.
        steel_buffer: Shortage premium ($/t) added when steel demand exceeds that slice.
        iron_share: Same as ``steel_share`` for iron.
        iron_buffer: Same as ``steel_buffer`` for iron.

    Returns:
        ``{"capacityLimit", "steel": {"share", "buffer"}, "iron": {"share", "buffer"}}``.
    """
    return {
        "capacityLimit": capacity_limit,
        "steel": {"share": steel_share, "buffer": steel_buffer},
        "iron": {"share": iron_share, "buffer": iron_buffer},
    }


def furnace_group_rows(post_processed: pd.DataFrame) -> pd.DataFrame:
    """One row per furnace group and year with the quantities the cost curve needs.

    Args:
        post_processed: The post-processed furnace-group table.

    Returns:
        Columns ``year, geo, technology, product, furnace_group_id, capacity_mt,
        production_mt, cost`` (Mt to four decimals, $/t to two). Feedstock rows collapse
        to one per furnace group and year. Every furnace group is kept, so the viewer can
        account the production of those it leaves off the curve; a missing cost or
        capacity packs as 0 and is dropped from the curve the way the static chart drops it.

    Raises:
        ValueError: If a required column is absent.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in post_processed.columns]
    if missing:
        raise ValueError(f"The post-processed table has no {', '.join(missing)} column")
    geo_column = next(column for column in GEO_COLUMNS if column in post_processed.columns)
    per_fg = post_processed.drop_duplicates(subset=["furnace_group_id", "year"])
    rows = pd.DataFrame(
        {
            "year": per_fg["year"].astype(int),
            "geo": per_fg[geo_column],
            "technology": per_fg["technology"],
            "product": per_fg["product"],
            "furnace_group_id": per_fg["furnace_group_id"],
            "capacity_mt": (per_fg["capacity"].fillna(0.0) / 1e6).round(4),
            "production_mt": (per_fg["production"].fillna(0.0) / 1e6).round(4),
            "cost": per_fg["unit_production_cost"].fillna(0.0).round(2),
        }
    )
    return rows.reset_index(drop=True)


def pack_rows(fgs: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact furnace-group rows for embedding in the viewer.

    Args:
        fgs: Output of :func:`furnace_group_rows`.

    Returns:
        One short-keyed record per row: ``y`` year, ``g`` geo, ``t`` technology, ``p``
        product, ``fg`` furnace-group id, ``cap`` capacity and ``pr`` production (Mt) and
        ``c`` unit production cost ($/t).
    """
    return [
        {
            "y": int(row["year"]),
            "g": row["geo"],
            "t": row["technology"],
            "p": row["product"],
            "fg": row["furnace_group_id"],
            "cap": float(row["capacity_mt"]),
            "pr": float(row["production_mt"]),
            "c": float(row["cost"]),
        }
        for row in fgs.to_dict("records")
    ]


def market_clearing(
    costs: Iterable[float],
    capacities: Iterable[float],
    demand: float,
    clearing_share: float,
    price_buffer: float,
) -> tuple[float, float, float]:
    """Clearing price, demand marker and total capacity of a merit-order curve.

    Args:
        costs: Unit production cost per furnace group, in any order.
        capacities: Dispatchable capacity per furnace group (already scaled by the
            capacity limit), aligned with ``costs``.
        demand: Demand in the units of ``capacities``.
        clearing_share: Fraction of total capacity that participates in clearing.
        price_buffer: Shortage premium added when demand exceeds that slice.

    Returns:
        ``(clearing_price, demand_line_x, total_capacity)`` from
        :func:`~steelo.utilities.plotting._compute_market_clearing`, after dropping
        furnace groups without a positive cost and capacity and sorting by cost as the
        static chart does. An empty curve returns zeros.
    """
    curve = pd.DataFrame({"production_cost": list(costs), "capacity": list(capacities)})
    curve = curve[(curve["production_cost"] > 0) & (curve["capacity"] > 0)].sort_values("production_cost")
    curve["clearing_capacity"] = curve["capacity"].cumsum()
    return _compute_market_clearing(curve, demand, clearing_share, price_buffer)


def steel_demand_by_year(market_prices_csv: Path) -> Optional[dict[int, float]]:
    """The engine's steel demand per year (t) from the market-prices CSV.

    Args:
        market_prices_csv: The run's ``data/market_prices_<start>_<end>.csv``.

    Returns:
        ``{year: steel demand in tonnes}``, or None (with a warning) when the file or its
        ``steel_demand_t`` column is missing — runs from before the column was written.
    """
    if not market_prices_csv.is_file():
        logger.warning("No market-prices file at %s — steel clears against realised production", market_prices_csv)
        return None
    prices = pd.read_csv(market_prices_csv)
    if STEEL_DEMAND_COLUMN not in prices.columns:
        logger.warning(
            "No %s column in %s — steel clears against realised production", STEEL_DEMAND_COLUMN, market_prices_csv
        )
        return None
    return {int(year): float(demand) for year, demand in zip(prices["year"], prices[STEEL_DEMAND_COLUMN])}


def clearing_table(
    fgs: pd.DataFrame,
    steel_demand_t: Optional[dict[int, float]],
    clearing: dict[str, Any],
) -> dict[str, dict[int, dict[str, float]]]:
    """Demand and full-curve clearing price per product and year, as the static charts draw them.

    Args:
        fgs: Output of :func:`furnace_group_rows`.
        steel_demand_t: The engine's steel demand per year in tonnes, or None.
        clearing: Output of :func:`clearing_config`.

    Returns:
        ``{product: {year: {"d": demand_mt, "c": clearing_price}}}``. Iron demand is the
        year's realised production; steel demand is the engine's, falling back to
        production when none is recorded or it is not positive.
    """
    table: dict[str, dict[int, dict[str, float]]] = {}
    for (product, year), group in fgs.groupby(["product", "year"]):
        production = float(group["production_mt"].sum())
        demand = production
        if product == "steel" and steel_demand_t is not None and steel_demand_t[year] > 0:
            demand = steel_demand_t[year] / 1e6
        price, _, _ = market_clearing(
            group["cost"],
            group["capacity_mt"] * clearing["capacityLimit"],
            demand,
            clearing[product]["share"],
            clearing[product]["buffer"],
        )
        table.setdefault(product, {})[int(year)] = {"d": round(demand, 4), "c": round(price, 2)}
    return table
