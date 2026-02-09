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
    Calculate and export LCOE and LCOH statistics by country to separate files.

    For each country, calculates:
    - Average LCOE and LCOH across all grid points
    - Min and max values
    - 10th, 20th, 25th, and 50th percentiles

    Args:
        energy_prices: xarray Dataset containing iso3, power_price (LCOE in USD/kWh), and capped_lcoh (LCOH in USD/kg)
        year: Current simulation year
        output_dir: Base output directory (will save to output_dir/data/)

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

    # Save LCOE to separate CSV
    lcoe_output_path = data_dir / f"lcoe_stats_{year}.csv"
    lcoe_df.to_csv(lcoe_output_path, index=False, float_format="%.4f")

    # Save LCOH to separate CSV
    lcoh_output_path = data_dir / f"lcoh_stats_{year}.csv"
    lcoh_df.to_csv(lcoh_output_path, index=False, float_format="%.4f")

    logger.info(f"Exported LCOE statistics for {len(statistics)} countries to {lcoe_output_path}")
    logger.info(f"Exported LCOH statistics for {len(statistics)} countries to {lcoh_output_path}")
    logger.info(
        f"Sample statistics: {len(stats_df)} countries, {stats_df['n_grid_points'].sum():.0f} total grid points"
    )


def export_overbuild_factor_statistics_by_country(
    energy_prices: xr.Dataset,
    year: "Year",
    output_dir: Path,
    factor_name: str,
) -> None:
    """
    Calculate and export overbuild factor statistics by country at LCOE percentile points.

    For each country, calculates the average overbuild factor at the same grid points that correspond to
    LCOE percentiles (average, min, max, 10th, 20th, 25th, 50th).

    Args:
        energy_prices: xarray Dataset containing iso3, power_price (LCOE in USD/kWh), and overbuild factor
        year: Current simulation year
        output_dir: Base output directory (will save to output_dir/data/)
        factor_name: Name of the overbuild factor variable (e.g., 'solar_factor', 'wind_factor', 'battery_factor')

    Returns:
        None (saves CSV file to disk)

    Note:
        The overbuild factors are dimensionless multipliers representing capacity relative to baseload demand.
        Statistics are calculated at grid points corresponding to LCOE percentiles within each country.
    """
    # Check if the overbuild factor exists in the dataset
    if factor_name not in energy_prices:
        logger.warning(
            f"Overbuild factor '{factor_name}' not found in energy_prices dataset for year {year}. "
            "This may occur when baseload_coverage is 0. Skipping export."
        )
        return

    logger.info(f"Calculating {factor_name} statistics by country for year {year}")

    # Extract data from xarray Dataset
    df = energy_prices.to_dataframe().reset_index()

    # Remove any rows with missing data
    df = df.dropna(subset=["iso3", "power_price", factor_name])

    # Remove any invalid ISO3 codes (empty strings or '-')
    df = df[df["iso3"].str.strip() != ""]
    df = df[df["iso3"] != "-"]

    # Unit conversion factor: USD/kWh to USD/MWh for LCOE
    KWH_TO_MWH = 1000.0

    # Calculate statistics for each country
    statistics = []
    for country, group in df.groupby("iso3"):
        # Convert to numpy arrays
        lcoe_values_kwh = np.asarray(group["power_price"].values)
        factor_values = np.asarray(group[factor_name].values)

        # Skip if no valid data
        if len(lcoe_values_kwh) == 0 or len(factor_values) == 0:
            continue

        # Convert LCOE from USD/kWh to USD/MWh for reference
        lcoe_values_mwh = lcoe_values_kwh * KWH_TO_MWH

        # Calculate the average overbuild factor at LCOE percentile points
        # For each LCOE percentile, find the grid points at or near that percentile and average their overbuild factors

        def get_factor_at_lcoe_percentile(
            lcoe_arr: np.ndarray, factor_arr: np.ndarray, percentile: float | str
        ) -> float:
            """Get average overbuild factor at grid points near a given LCOE percentile."""
            if percentile == "min":
                # Get factor at minimum LCOE point
                min_idx = np.argmin(lcoe_arr)
                return float(factor_arr[min_idx])
            elif percentile == "max":
                # Get factor at maximum LCOE point
                max_idx = np.argmax(lcoe_arr)
                return float(factor_arr[max_idx])
            elif percentile == "avg":
                # Simple average across all points
                return float(np.mean(factor_arr))
            else:
                # For percentile values, find grid points at that percentile and average their factors
                # At this point, percentile must be a float (type narrowing)
                if not isinstance(percentile, (int, float)):
                    raise TypeError(f"Expected numeric percentile, got {type(percentile).__name__}: {percentile}")
                lcoe_percentile_value = np.percentile(lcoe_arr, percentile)
                # Find indices of points within 5% of the percentile value
                tolerance = 0.05 * lcoe_percentile_value if lcoe_percentile_value != 0 else 0.001
                nearby_indices = np.abs(lcoe_arr - lcoe_percentile_value) <= tolerance
                if np.any(nearby_indices):
                    return float(np.mean(factor_arr[nearby_indices]))
                else:
                    # Fallback: find closest point
                    closest_idx = np.argmin(np.abs(lcoe_arr - lcoe_percentile_value))
                    return float(factor_arr[closest_idx])

        # Create short factor name for column naming (e.g., 'solar', 'wind', 'battery')
        short_name = factor_name.replace("_factor", "")

        country_stats = {
            "country": country,
            "year": year,
            # Overbuild factor statistics at LCOE percentile points
            f"{short_name}_at_avg_lcoe": get_factor_at_lcoe_percentile(lcoe_values_mwh, factor_values, "avg"),
            f"{short_name}_at_min_lcoe": get_factor_at_lcoe_percentile(lcoe_values_mwh, factor_values, "min"),
            f"{short_name}_at_max_lcoe": get_factor_at_lcoe_percentile(lcoe_values_mwh, factor_values, "max"),
            f"{short_name}_at_p10_lcoe": get_factor_at_lcoe_percentile(lcoe_values_mwh, factor_values, 10),
            f"{short_name}_at_p20_lcoe": get_factor_at_lcoe_percentile(lcoe_values_mwh, factor_values, 20),
            f"{short_name}_at_p25_lcoe": get_factor_at_lcoe_percentile(lcoe_values_mwh, factor_values, 25),
            f"{short_name}_at_p50_lcoe": get_factor_at_lcoe_percentile(lcoe_values_mwh, factor_values, 50),
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
    output_path = data_dir / f"{factor_name}_stats_{year}.csv"
    stats_df.to_csv(output_path, index=False, float_format="%.4f")

    logger.info(f"Exported {factor_name} statistics for {len(statistics)} countries to {output_path}")
