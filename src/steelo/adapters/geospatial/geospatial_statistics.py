"""The all-years LCOE and LCOH output files, written from the GEO pipeline's cells every simulation year."""

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import xarray as xr

from steelo.adapters.geospatial.geospatial_calculations import get_baseload_coverage
from steelo.domain.constants import PERkWh_TO_PERMWh

if TYPE_CHECKING:
    from steelo.simulation import GeoConfig

logger = logging.getLogger(__name__)


LCOE_SUMMARY_FILE = "lcoe_summary.csv"
LCOE_DETAILED_FILE = "lcoe_detailed.csv"
LCOH_SUMMARY_FILE = "lcoh_summary.csv"
LCOH_NEW_SITES_DETAILED_FILE = "lcoh_new_sites_detailed.csv"
LCOH_EXISTING_PLANTS_FILE = "lcoh_existing_plants.csv"

DETAILED_STATS: tuple[str, ...] = (
    "avg",
    "p0",
    "p10",
    "p20",
    "p25",
    "p30",
    "p40",
    "p50",
    "p60",
    "p70",
    "p75",
    "p80",
    "p90",
    "p100",
)
AREA_COLUMNS = ["level", "country", "subregion"]
AREA_LEVELS = ["world", "irena_region", "country", "geo_unit_region", "geo_unit"]


def load_ne_region_by_geo_key(hierarchy_path: Path) -> dict[str, str]:
    """
    The Natural Earth region of every declared geo unit.

    Args:
        hierarchy_path: ``<data_dir>/fixtures/geo_hierarchy.json``.

    Returns:
        Geo key to region, e.g. ``{"CHN:CN-XJ": "Northwest China"}``; empty when the file is absent, as with a
        geo-data package that has no admin-1 layer.

    Notes:
        Units whose ``ne_region`` is blank are left out, so their cells count in no geo unit region row.
    """
    if not hierarchy_path.exists():
        return {}
    return {row["geo_key"]: row["ne_region"] for row in json.loads(hierarchy_path.read_text()) if row["ne_region"]}


def _area_memberships(
    iso3: np.ndarray,
    geo_key: np.ndarray,
    irena_region_by_iso3: Mapping[str, str | None],
    ne_region_by_geo_key: Mapping[str, str],
) -> pd.DataFrame:
    """
    Every (area, cell) pair: one row per cell per area it counts in.

    Args:
        iso3: ISO3 code of each priced cell.
        geo_key: Geo key of each priced cell (its iso3, or ``ISO3:CODE`` inside a declared geo unit).
        irena_region_by_iso3: IRENA region of each country; countries without one are in no IRENA row.
        ne_region_by_geo_key: Natural Earth region of each geo unit, from geo_hierarchy.json.

    Returns:
        Columns ``level``, ``country``, ``subregion`` and ``pixel`` (index into the cell arrays).

    Notes:
        A cell outside every geo unit (geo key without ``:``) counts in the world, IRENA and country rows only.
    """
    pixel = np.arange(iso3.size)
    is_unit = np.char.find(geo_key.astype(str), ":") >= 0
    unit_code = np.array([key.partition(":")[2] for key in geo_key.astype(str)], dtype=object)
    irena = np.array([irena_region_by_iso3.get(code) for code in iso3], dtype=object)
    has_irena = np.array([isinstance(region, str) and region != "" for region in irena])
    ne_region = np.array([ne_region_by_geo_key.get(key, "") for key in geo_key.astype(str)], dtype=object)
    has_ne_region = is_unit & (ne_region != "")

    def frame(level: str, country: Any, subregion: Any, mask: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "level": level,
                "country": country[mask] if isinstance(country, np.ndarray) else country,
                "subregion": subregion[mask] if isinstance(subregion, np.ndarray) else subregion,
                "pixel": pixel[mask],
            }
        )

    everywhere = np.ones(iso3.size, dtype=bool)
    irena_country = np.array([f"irena_{region}" for region in irena], dtype=object)
    return pd.concat(
        [
            frame("world", "World", "", everywhere),
            frame("irena_region", irena_country, "", has_irena),
            frame("country", iso3, "", everywhere),
            frame("geo_unit_region", iso3, ne_region, has_ne_region),
            frame("geo_unit", iso3, unit_code, is_unit),
        ],
        ignore_index=True,
    )


def _area_statistics(memberships: pd.DataFrame, values: np.ndarray) -> pd.DataFrame:
    """
    The mean and the detailed percentiles of one per-cell value over every area.

    Args:
        memberships: (area, cell) pairs from ``_area_memberships``.
        values: The per-cell value, indexed like the cell arrays.

    Returns:
        One row per area (``level``, ``country``, ``subregion``) with a column per entry of ``DETAILED_STATS``
        and ``n_grid_points``.

    Notes:
        Percentiles interpolate linearly, as ``np.percentile`` does by default.
    """
    grouped = memberships.assign(value=values[memberships["pixel"].to_numpy()]).groupby(AREA_COLUMNS, sort=False)[
        "value"
    ]
    percentiles = [int(stat[1:]) for stat in DETAILED_STATS if stat != "avg"]
    stats = grouped.quantile(np.array(percentiles) / 100).unstack()
    stats.columns = [f"p{p}" for p in percentiles]
    stats.insert(0, "avg", grouped.mean())
    stats["n_grid_points"] = grouped.size()
    return stats.reset_index()


def _append_csv(frame: pd.DataFrame, path: Path, overwrite: bool) -> None:
    """
    Append rows to a CSV, or start it afresh with a header.

    Args:
        frame: Rows to write.
        path: Target CSV; its folder is created when missing.
        overwrite: Replace any existing file instead of appending to it.

    Returns:
        None.

    Notes:
        Floats are written with four decimals and NaN as an empty cell.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    append = path.exists() and not overwrite
    frame.to_csv(path, mode="a" if append else "w", header=not append, index=False, float_format="%.4f")


def export_lcoe_lcoh_outputs(
    energy_prices: xr.Dataset,
    year: int,
    start_year: int,
    output_dir: Path,
    geo_config: "GeoConfig",
    irena_region_by_iso3: Mapping[str, str | None],
    ne_region_by_geo_key: Mapping[str, str],
    existing_plant_lcoh: Mapping[str, float],
) -> None:
    """
    Append one year's rows to the four cell-based LCOE and LCOH output files.

    Writes ``data/LCOE/lcoe_detailed.csv`` and ``lcoe_summary.csv``, and ``data/LCOH/lcoh_new_sites_detailed.csv``
    and ``lcoh_summary.csv``, one row per area (world, IRENA region, country, geo unit region, geo unit) and, in
    the detailed files, per price type.

    Args:
        energy_prices: The GEO pipeline's per-cell dataset: ``iso3``, ``geo_key``, ``grid_price``,
            ``power_price`` and (with a baseload mix) ``lcoe`` in USD/kWh; ``lcoh``, ``capped_lcoh`` and
            ``hydrogen_ceiling`` in USD/kg; ``tiam_ucl_region``.
        year: Simulation year.
        start_year: The run's first year, whose rows replace any files an earlier run left in ``output_dir``
            (an app rerun reuses its folder).
        output_dir: Run output directory; files go under ``output_dir/data``.
        geo_config: The run's GeoConfig (power mix, hydrogen ceiling percentile, trade setting).
        irena_region_by_iso3: IRENA region of each country.
        ne_region_by_geo_key: Natural Earth region of each declared geo unit; empty without geo_hierarchy.json.
        existing_plant_lcoh: Existing plants' capped hydrogen price (USD/kg) per geo key for ``year``.

    Returns:
        None. The start year writes each file afresh with its header; later years append.

    Notes:
        Cells count when their iso3, power price and capped LCOH are all set, the same cells for every price
        type of an area. Statistics weight every cell equally. Logs one INFO line per year.
    """
    coverage = get_baseload_coverage(geo_config.included_power_mix)
    raw_iso3 = energy_prices["iso3"].values.ravel()
    iso3 = raw_iso3.astype(str)
    power_price = energy_prices["power_price"].values.ravel()
    capped_lcoh = energy_prices["capped_lcoh"].values.ravel()
    keep = (
        ~pd.isna(raw_iso3)
        & (np.char.strip(iso3) != "")
        & (iso3 != "-")
        & np.isfinite(power_price)
        & np.isfinite(capped_lcoh)
    )

    def pixels(name: str) -> np.ndarray:
        return energy_prices[name].values.ravel()[keep]

    memberships = _area_memberships(
        iso3[keep], pixels("geo_key").astype(str), irena_region_by_iso3, ne_region_by_geo_key
    )
    settings = {
        "year": int(year),
        "baseload_coverage_pct": coverage * 100,
        "grid_share_pct": (1 - coverage) * 100,
    }
    hydrogen_settings = {
        "hydrogen_ceiling_pct": geo_config.hydrogen_ceiling_percentile,
        "intraregional_trade_allowed": geo_config.intraregional_trade_allowed,
    }

    def detailed(price_type: str, values: np.ndarray, unit: str, extra: dict[str, Any]) -> pd.DataFrame:
        stats = _area_statistics(memberships, values)
        stats = stats.rename(columns={stat: f"{stat}_{unit}" for stat in DETAILED_STATS})
        for position, (column, value) in enumerate({**settings, **extra, "price_type": price_type}.items()):
            stats.insert(len(AREA_COLUMNS) + position, column, value)
        return stats

    lcoe_prices = [("grid", pixels("grid_price")), ("grid_mix", power_price[keep])]
    if coverage > 0:
        lcoe_prices.insert(0, ("pure_lcoe", pixels("lcoe")))
    lcoe_rows = {
        price_type: detailed(
            price_type,
            values * PERkWh_TO_PERMWh,
            "usd_per_mwh",
            {"grid_share_pct": 0.0} if price_type == "pure_lcoe" else {},
        )
        for price_type, values in lcoe_prices
    }

    # An area's ceiling is defined only when all its cells sit in one hydrogen trade region
    pixel_index = memberships["pixel"].to_numpy()
    ceilings = (
        memberships.assign(
            region=pixels("tiam_ucl_region")[pixel_index], ceiling=pixels("hydrogen_ceiling")[pixel_index]
        )
        .groupby(AREA_COLUMNS, sort=False)
        .agg(n_regions=("region", "nunique"), ceiling=("ceiling", "first"))
    )
    ceilings["ceiling_usd_per_kg"] = ceilings["ceiling"].where(ceilings["n_regions"] == 1)
    lcoh_rows = {}
    for price_type, values in (("uncapped", pixels("lcoh")), ("capped", capped_lcoh[keep])):
        rows = detailed(price_type, values, "usd_per_kg", hydrogen_settings)
        rows.insert(
            list(rows.columns).index("price_type") + 1,
            "ceiling_usd_per_kg",
            ceilings["ceiling_usd_per_kg"].reindex(pd.MultiIndex.from_frame(rows[AREA_COLUMNS])).to_numpy(),
        )
        lcoh_rows[price_type] = rows

    area_settings = AREA_COLUMNS + list(settings)
    lcoe_summary = lcoe_rows["grid"][area_settings].copy()
    for price_type, stats in (
        ("pure_lcoe", ("avg", "p50")),
        ("grid", ("avg",)),
        ("grid_mix", ("avg", "p10", "p50", "p90")),
    ):
        for stat in stats:
            column = f"{price_type}_{stat}_usd_per_mwh"
            lcoe_summary[column] = lcoe_rows[price_type][f"{stat}_usd_per_mwh"] if price_type in lcoe_rows else np.nan
    lcoe_summary["n_grid_points"] = lcoe_rows["grid"]["n_grid_points"]

    capped = lcoh_rows["capped"]
    lcoh_summary = capped[area_settings + list(hydrogen_settings)].copy()
    for stat in ("avg", "p10", "p50", "p90"):
        lcoh_summary[f"new_site_{stat}_usd_per_kg"] = capped[f"{stat}_usd_per_kg"]
    existing_key = np.where(
        capped["level"] == "geo_unit", capped["country"] + ":" + capped["subregion"], capped["country"]
    )
    # A geo unit without a price of its own pays its country's, which only the country row shows
    lcoh_summary["existing_plant_usd_per_kg"] = [
        existing_plant_lcoh[key] if level in ("country", "geo_unit") and key in existing_plant_lcoh else np.nan
        for level, key in zip(capped["level"], existing_key)
    ]
    lcoh_summary["n_grid_points"] = capped["n_grid_points"]

    data_dir = output_dir / "data"
    for frame, path in (
        (lcoe_summary, data_dir / "LCOE" / LCOE_SUMMARY_FILE),
        (pd.concat(lcoe_rows.values(), ignore_index=True), data_dir / "LCOE" / LCOE_DETAILED_FILE),
        (lcoh_summary, data_dir / "LCOH" / LCOH_SUMMARY_FILE),
        (pd.concat(lcoh_rows.values(), ignore_index=True), data_dir / "LCOH" / LCOH_NEW_SITES_DETAILED_FILE),
    ):
        _append_csv(_sorted_by_area(frame), path, overwrite=int(year) == int(start_year))
    logger.info("Appended %d LCOE/LCOH areas for %d to %s", len(lcoe_summary), int(year), data_dir)


def _sorted_by_area(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Order output rows by area.

    Args:
        frame: Rows with ``level``, ``country`` and ``subregion`` columns.

    Returns:
        The rows ordered world, IRENA regions, countries, geo unit regions, geo units, then by name.

    Notes:
        The sort is stable, so the rows of one area keep their price-type order.
    """
    order = frame["level"].map({level: i for i, level in enumerate(AREA_LEVELS)})
    return (
        frame.assign(_order=order).sort_values(["_order", "country", "subregion"], kind="stable").drop(columns="_order")
    )
