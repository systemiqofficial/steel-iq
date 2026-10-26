"""Utility functions for calculating and exporting geospatial statistics."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import xarray as xr

if TYPE_CHECKING:
    from steelo.domain import Year

logger = logging.getLogger(__name__)


def export_lcoe_lcoh_statistics_by_country(
    energy_prices: xr.Dataset,
    year: "Year",
    output_dir: Path,
    hydrogen_ceiling_percentile: float = 100.0,
) -> None:
    """
    Calculate and export LCOE and LCOH statistics by country to separate files.

    For each country, calculates:
    - Average LCOE and LCOH across all grid points
    - Min and max values
    - 10th, 20th, 25th, and 50th percentiles

    Args:
        energy_prices: xarray Dataset containing iso3, power_price (LCOE in USD/kWh), and capped_lcoh (LCOH in USD/kg)
        year: Current simulation year
        output_dir: Base output directory (will save to output_dir/data/)
        hydrogen_ceiling_percentile: Hydrogen ceiling percentile from GeoConfig (0-100).

    Returns:
        None (saves two separate CSV files to disk: lcoe_stats_{year}.csv and lcoh_stats_{year}.csv)

    Note:
        LCOE values are converted from USD/kWh to USD/MWh for easier interpretation (multiplied by 1000).
        LCOH values remain in USD/kg.
    """
    logger.info(f"Calculating LCOE/LCOH statistics by country for year {year}")

    # Extract data from xarray Dataset
    df = energy_prices.to_dataframe().reset_index()

    # Remove any rows with missing data
    df = df.dropna(subset=["iso3", "power_price", "capped_lcoh"])

    # Remove any invalid ISO3 codes (empty strings or '-')
    df = df[df["iso3"].str.strip() != ""]
    df = df[df["iso3"] != "-"]

    # Group by country (iso3)
    grouped = df.groupby("iso3")

    # Unit conversion factor: USD/kWh to USD/MWh
    KWH_TO_MWH = 1000.0

    # Calculate statistics for each country
    statistics = []
    for country, group in grouped:
        # Convert to numpy arrays to satisfy type checker
        lcoe_values_kwh = np.asarray(group["power_price"].values)
        lcoh_values = np.asarray(group["capped_lcoh"].values)

        # Skip if no valid data
        if len(lcoe_values_kwh) == 0 or len(lcoh_values) == 0:
            continue

        # Convert LCOE from USD/kWh to USD/MWh
        lcoe_values_mwh = lcoe_values_kwh * KWH_TO_MWH

        country_stats = {
            "country": country,
            "year": year,
            "hydrogen_ceiling_pct": hydrogen_ceiling_percentile,
            # LCOE statistics (in USD/MWh)
            "lcoe_avg_usd_per_mwh": float(np.mean(lcoe_values_mwh)),
            "lcoe_min_usd_per_mwh": float(np.min(lcoe_values_mwh)),
            "lcoe_max_usd_per_mwh": float(np.max(lcoe_values_mwh)),
            "lcoe_p10_usd_per_mwh": float(np.percentile(lcoe_values_mwh, 10)),
            "lcoe_p20_usd_per_mwh": float(np.percentile(lcoe_values_mwh, 20)),
            "lcoe_p25_usd_per_mwh": float(np.percentile(lcoe_values_mwh, 25)),
            "lcoe_p50_usd_per_mwh": float(np.percentile(lcoe_values_mwh, 50)),
            # LCOH statistics (in USD/kg)
            "lcoh_avg_usd_per_kg": float(np.mean(lcoh_values)),
            "lcoh_min_usd_per_kg": float(np.min(lcoh_values)),
            "lcoh_max_usd_per_kg": float(np.max(lcoh_values)),
            "lcoh_p10_usd_per_kg": float(np.percentile(lcoh_values, 10)),
            "lcoh_p20_usd_per_kg": float(np.percentile(lcoh_values, 20)),
            "lcoh_p25_usd_per_kg": float(np.percentile(lcoh_values, 25)),
            "lcoh_p50_usd_per_kg": float(np.percentile(lcoh_values, 50)),
            # Additional metadata
            "n_grid_points": len(lcoe_values_kwh),
        }
        statistics.append(country_stats)

    # Convert to DataFrame
    stats_df = pd.DataFrame(statistics)

    # Sort by country for readability
    stats_df = stats_df.sort_values("country").reset_index(drop=True)

    # Ensure data directory exists
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Create separate DataFrames for LCOE and LCOH
    lcoe_columns = [
        "country",
        "year",
        "lcoe_avg_usd_per_mwh",
        "lcoe_min_usd_per_mwh",
        "lcoe_max_usd_per_mwh",
        "lcoe_p10_usd_per_mwh",
        "lcoe_p20_usd_per_mwh",
        "lcoe_p25_usd_per_mwh",
        "lcoe_p50_usd_per_mwh",
        "n_grid_points",
    ]
    lcoh_columns = [
        "country",
        "year",
        "hydrogen_ceiling_pct",
        "lcoh_avg_usd_per_kg",
        "lcoh_min_usd_per_kg",
        "lcoh_max_usd_per_kg",
        "lcoh_p10_usd_per_kg",
        "lcoh_p20_usd_per_kg",
        "lcoh_p25_usd_per_kg",
        "lcoh_p50_usd_per_kg",
        "n_grid_points",
    ]

    lcoe_df = stats_df[lcoe_columns]
    lcoh_df = stats_df[lcoh_columns]

    # Save LCOE to its own subfolder
    lcoe_dir = data_dir / "LCOE"
    lcoe_dir.mkdir(parents=True, exist_ok=True)
    lcoe_output_path = lcoe_dir / f"lcoe_stats_{year}.csv"
    lcoe_df.to_csv(lcoe_output_path, index=False, float_format="%.4f")

    # Save LCOH to its own subfolder
    lcoh_dir = data_dir / "LCOH"
    lcoh_dir.mkdir(parents=True, exist_ok=True)
    lcoh_output_path = lcoh_dir / f"lcoh_stats_{year}.csv"
    lcoh_df.to_csv(lcoh_output_path, index=False, float_format="%.4f")

    logger.info(f"Exported LCOE statistics for {len(statistics)} countries to {lcoe_output_path}")
    logger.info(f"Exported LCOH statistics for {len(statistics)} countries to {lcoh_output_path}")
    logger.info(
        f"Sample statistics: {len(stats_df)} countries, {stats_df['n_grid_points'].sum():.0f} total grid points"
    )


def aggregate_lcoe_lcoh_statistics(
    output_dir: Path,
    start_year: int,
    end_year: int,
) -> None:
    """Concatenate per-year LCOE/LCOH stat CSVs into one stacked CSV per metric.

    Reads all ``lcoe_stats_{year}.csv`` files from *output_dir*/data/LCOE and
    ``lcoh_stats_{year}.csv`` files from *output_dir*/data/LCOH, concatenates
    them, and writes aggregated files named ``lcoe_stats_{start_year}_{end_year}.csv``
    / ``lcoh_stats_{start_year}_{end_year}.csv`` back into the same subfolders.

    Args:
        output_dir: Base output directory (expects a ``data/`` subdirectory).
        start_year: Simulation start year (used in output filename).
        end_year: Simulation end year (used in output filename).
    """
    data_dir = output_dir / "data"
    if not data_dir.exists():
        logger.warning("Data directory %s does not exist — skipping LCOE/LCOH aggregation", data_dir)
        return

    for prefix, subdir in (("lcoe_stats", "LCOE"), ("lcoh_stats", "LCOH")):
        src_dir = data_dir / subdir
        if not src_dir.exists():
            continue
        per_year_files = sorted(src_dir.glob(f"{prefix}_*.csv"))
        # Exclude any previously aggregated file (filename contains a year range like 2020_2050)
        per_year_files = [f for f in per_year_files if f.stem.removeprefix(f"{prefix}_").isdigit()]
        if not per_year_files:
            continue

        combined = pd.concat([pd.read_csv(f) for f in per_year_files], ignore_index=True)
        combined = combined.sort_values(["year", "country"]).reset_index(drop=True)
        agg_path = src_dir / f"{prefix}_{start_year}_{end_year}.csv"
        combined.to_csv(agg_path, index=False, float_format="%.4f")
        logger.info("Saved aggregated %s (%d years) to %s", prefix, len(per_year_files), agg_path)
