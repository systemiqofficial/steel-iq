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
) -> None:
    """
    Calculate and export LCOE and LCOH statistics by country.

    For each country, calculates:
    - Average LCOE and LCOH across all grid points
    - Min and max values
    - 10th, 20th, 25th, and 50th percentiles

    Args:
        energy_prices: xarray Dataset containing iso3, power_price (LCOE in USD/kWh), and capped_lcoh (LCOH in USD/kg)
        year: Current simulation year
        output_dir: Base output directory (will save to output_dir/data/)

    Returns:
        None (saves CSV file to disk)

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

    # Save to CSV
    output_path = data_dir / f"lcoe_lcoh_stats_{year}.csv"
    stats_df.to_csv(output_path, index=False, float_format="%.4f")

    logger.info(f"Exported LCOE/LCOH statistics for {len(statistics)} countries to {output_path}")
    logger.info(
        f"Sample statistics: {len(stats_df)} countries, {stats_df['n_grid_points'].sum():.0f} total grid points"
    )
