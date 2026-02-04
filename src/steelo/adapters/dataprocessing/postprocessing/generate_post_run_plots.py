import logging
import pandas as pd

from typing import Optional, TYPE_CHECKING

from steelo.domain.constants import T_TO_KT, T_TO_MT
from steelo.utilities.plotting import (
    plot_area_chart_of_column_by_region_or_technology,
    plot_added_capacity_by_technology,
    plot_year_on_year_technology_development,
    plot_cost_curve_step_from_dataframe,
    plot_cost_curve_with_breakdown,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from steelo.domain.models import PlotPaths


def _sum_unique_furnace_output(df: pd.DataFrame, product: str, year: int) -> float:
    """Return production summed once per furnace group for a given product/year."""
    subset = df[(df["product"] == product) & (df["year"] == year)]
    if subset.empty:
        return 0.0

    if "furnace_group_id" in subset.columns:
        per_furnace = subset.groupby("furnace_group_id")["production"].max()
        return float(per_furnace.sum())

    return float(subset["production"].sum())


def generate_post_run_cap_prod_plots(
    file_path,
    capacity_limit,
    steel_demand,
    iron_demand,
    iso3_to_region_map: dict[str, str],
    plot_paths: Optional["PlotPaths"] = None,
):
    """
    Generate and store plots related to the post_process collected data file path

    """
    # output_df = pd.read_csv(settings.output_dir / "post_processed_2025-06-02 21-41.csv")
    output_df = pd.read_csv(file_path)
    output_df = output_df.copy()
    output_df = output_df.sort_values(by="year").reset_index(drop=True)

    output_df["region"] = output_df["location"].map(iso3_to_region_map)

    # Check order of magnitude and convert if needed for better readability
    # If values are in millions (tonnes), convert to kt for better readability
    capacity_mean = output_df["capacity"].mean()

    # If average capacity is > 1e3 and 1e6 tonnes, want to converted capacity and production to kt or mt
    if 1e6 > capacity_mean > 1e3:
        output_df["capacity"] = output_df["capacity"] * T_TO_KT
        output_df["production"] = output_df["production"] * T_TO_KT
        steel_demand = steel_demand * T_TO_KT
        iron_demand = iron_demand * T_TO_KT
        units = "kt"
        units_pa = "ktpa"
    elif capacity_mean >= 1e6:
        output_df["capacity"] = output_df["capacity"] * T_TO_MT
        output_df["production"] = output_df["production"] * T_TO_MT
        steel_demand = steel_demand * T_TO_MT
        iron_demand = iron_demand * T_TO_MT
        units = "Mt"
        units_pa = "Mtpa"
    else:
        units = "t"
        units_pa = "tpa"

    plot_added_capacity_by_technology(output_df, units_pa, plot_paths=plot_paths)
    plot_year_on_year_technology_development(output_df, units_pa, plot_paths=plot_paths)

    # Get the first and last years available in the data for cost curves
    if "year" in output_df.columns:
        first_year = output_df["year"].min()
        last_year = output_df["year"].max()
    elif "year" in output_df.index.names:
        first_year = output_df.index.get_level_values("year").min()
        last_year = output_df.index.get_level_values("year").max()
    else:
        # Skip cost curve if we can't determine the year
        first_year = None
        last_year = None

    if last_year:
        # Recompute demand with duplicate furnace rows collapsed so the cost curve lines are accurate.
        steel_demand = _sum_unique_furnace_output(output_df, "steel", last_year)
        iron_demand = _sum_unique_furnace_output(output_df, "iron", last_year)

        # Generate cost curve plots in 5-year increments
        years_to_plot = []

        # Start from the first simulation year and add 5-year increments
        current_year = first_year
        while current_year <= last_year:
            years_to_plot.append(current_year)
            current_year += 5

        # Always include the last year if not already included
        if last_year not in years_to_plot:
            years_to_plot.append(last_year)

        # Generate cost curve plots with breakdown for each selected year
        for year in years_to_plot:
            # Check if data exists for this year
            if "year" in output_df.columns:
                year_data = output_df[output_df["year"] == year]
            else:
                year_data = output_df.loc[output_df.index.get_level_values("year") == year]

            if not year_data.empty:
                # Generate both steel and iron cost curves with breakdown
                for product in ["steel", "iron"]:
                    try:
                        # Plot with breakdown
                        plot_cost_curve_with_breakdown(
                            output_df, product, year, plot_paths=plot_paths, show_breakdown=True
                        )
                        logger.info(f"Generated cost curve with breakdown for {product} in {year}")
                    except Exception as e:
                        logger.warning(f"Could not generate cost curve for {product} in {year}: {e}")

        # Also generate the simple cost curve for the last year (backward compatibility)
        plot_cost_curve_step_from_dataframe(
            output_df,
            "steel",
            steel_demand,
            last_year,
            capacity_limit,
            units,
            aggregation="region",
            plot_paths=plot_paths,
        )
        plot_cost_curve_step_from_dataframe(
            output_df,
            "iron",
            iron_demand,
            last_year,
            capacity_limit,
            units,
            aggregation="region",
            plot_paths=plot_paths,
        )
        plot_cost_curve_step_from_dataframe(
            output_df,
            "steel",
            steel_demand,
            last_year,
            capacity_limit,
            units,
            aggregation="technology",
            plot_paths=plot_paths,
        )
        plot_cost_curve_step_from_dataframe(
            output_df,
            "iron",
            iron_demand,
            last_year,
            capacity_limit,
            units,
            aggregation="technology",
            plot_paths=plot_paths,
        )

    # BY REGION
    plot_area_chart_of_column_by_region_or_technology(
        dataframe=output_df,
        column_name="production",
        title="Steel Production Volume by Region",
        units=units_pa,
        pivot_columns=["region"],
        product_type="steel",
        plot_paths=plot_paths,
    )
    plot_area_chart_of_column_by_region_or_technology(
        dataframe=output_df,
        column_name="production",
        title="Iron Production Volume by Region",
        units=units_pa,
        pivot_columns=["region"],
        product_type="iron",
        plot_paths=plot_paths,
    )
    plot_area_chart_of_column_by_region_or_technology(
        dataframe=output_df,
        column_name="capacity",
        title="Steel Capacity Volume by Region",
        units=units_pa,
        pivot_columns=["region"],
        product_type="steel",
        plot_paths=plot_paths,
    )
    plot_area_chart_of_column_by_region_or_technology(
        dataframe=output_df,
        column_name="capacity",
        title="Iron Capacity Volume by Region",
        units=units_pa,
        pivot_columns=["region"],
        product_type="iron",
        plot_paths=plot_paths,
    )

    # BY TECHNOLOGY
    plot_area_chart_of_column_by_region_or_technology(
        dataframe=output_df,
        column_name="production",
        title="Steel Production Volume by Technology",
        units=units_pa,
        pivot_columns=["technology"],
        product_type="steel",
        plot_paths=plot_paths,
    )
    plot_area_chart_of_column_by_region_or_technology(
        dataframe=output_df,
        column_name="production",
        title="Iron Production Volume by Technology",
        units=units_pa,
        pivot_columns=["technology"],
        product_type="iron",
        plot_paths=plot_paths,
    )
    plot_area_chart_of_column_by_region_or_technology(
        dataframe=output_df,
        column_name="capacity",
        title="Steel Capacity Volume by Technology",
        units=units_pa,
        pivot_columns=["technology"],
        product_type="steel",
        plot_paths=plot_paths,
    )
    plot_area_chart_of_column_by_region_or_technology(
        dataframe=output_df,
        column_name="capacity",
        title="Iron Capacity Volume by Technology",
        units=units_pa,
        pivot_columns=["technology"],
        product_type="iron",
        plot_paths=plot_paths,
    )


def generate_cost_breakdown_plots(file_path):
    """
    Generate and store plots for cost breakdown sideways bar plots
    """
    from steelo.utilities.plotting import plot_cost_curve_with_breakdown
    import pandas as pd
    from pathlib import Path

    # Load the data
    if isinstance(file_path, str):
        file_path = Path(file_path)

    # Check if the file exists
    if not file_path.exists():
        logger.warning(f"File not found: {file_path}")
        return

    # Load data based on file type
    if file_path.suffix == ".csv":
        df = pd.read_csv(file_path)
    elif file_path.suffix in [".xlsx", ".xls"]:
        df = pd.read_excel(file_path)
    elif file_path.suffix == ".parquet":
        df = pd.read_parquet(file_path)
    else:
        logger.warning(f"Unsupported file type: {file_path.suffix}")
        return

    # Get unique years and products
    if "year" in df.columns and "product" in df.columns:
        years = df["year"].unique()
        products = df["product"].unique()

        # Generate plots for each product and year combination
        for product in products:
            for year in years:
                try:
                    plot_cost_curve_with_breakdown(data_file=df, product_type=product, year=year, show_breakdown=True)
                    logger.info(f"Generated cost breakdown plot for {product} in {year}")
                except Exception as e:
                    logger.error(f"Error generating plot for {product} in {year}: {e}")
    else:
        logger.warning("Required columns 'year' and 'product' not found in data")


def generate_material_flow_plots(file_path):
    """
    Generate and store sankey charts for the material flow
    """

    pass
