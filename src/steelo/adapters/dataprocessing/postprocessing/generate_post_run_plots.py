import logging
import pandas as pd

from typing import Optional, TYPE_CHECKING

from steelo.domain.constants import T_TO_KT, T_TO_MT
from steelo.utilities.plotting import (
    plot_added_capacity_by_technology,
)
from steelo.utilities.steeliq_plotter import SteelPlotter, PlotConfig

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from steelo.domain.models import PlotPaths


def generate_post_run_cap_prod_plots(
    file_path,
    plot_paths: Optional["PlotPaths"] = None,
):
    """
    Generate and store plots related to the post_process collected data file path.

    Cost curve plots are generated separately via generate_market_cost_curve_plots()
    using traced data that matches the exact market price calculation inputs.
    """
    output_df = pd.read_csv(file_path)
    output_df = output_df.copy()
    output_df = output_df.sort_values(by="year").reset_index(drop=True)

    # Check order of magnitude and convert if needed for better readability
    capacity_mean = output_df["capacity"].mean()

    if 1e6 > capacity_mean > 1e3:
        output_df["capacity"] = output_df["capacity"] * T_TO_KT
        output_df["production"] = output_df["production"] * T_TO_KT
        units_pa = "ktpa"
    elif capacity_mean >= 1e6:
        output_df["capacity"] = output_df["capacity"] * T_TO_MT
        output_df["production"] = output_df["production"] * T_TO_MT
        units_pa = "Mtpa"
    else:
        units_pa = "tpa"

    plot_added_capacity_by_technology(output_df, units_pa, plot_paths=plot_paths)

    # Use SteelPlotter for capacity development to get consistent styling and footer
    plotter = SteelPlotter(config=PlotConfig(), plot_paths=plot_paths)
    plotter.plot_capacity_development_by_technology(data_file=output_df, units=units_pa)

    # BY REGION - Using SteelPlotter for consistent styling and footers
    plotter.plot_area_chart_by_region_or_technology(
        dataframe=output_df,
        column_name="production",
        title="Steel Production Volume by Region",
        units=units_pa,
        pivot_columns=["region"],
        product_type="steel",
    )
    plotter.plot_area_chart_by_region_or_technology(
        dataframe=output_df,
        column_name="production",
        title="Iron Production Volume by Region",
        units=units_pa,
        pivot_columns=["region"],
        product_type="iron",
    )
    plotter.plot_area_chart_by_region_or_technology(
        dataframe=output_df,
        column_name="capacity",
        title="Steel Capacity Volume by Region",
        units=units_pa,
        pivot_columns=["region"],
        product_type="steel",
    )
    plotter.plot_area_chart_by_region_or_technology(
        dataframe=output_df,
        column_name="capacity",
        title="Iron Capacity Volume by Region",
        units=units_pa,
        pivot_columns=["region"],
        product_type="iron",
    )

    # BY TECHNOLOGY - Using SteelPlotter for consistent styling and footers
    plotter.plot_area_chart_by_region_or_technology(
        dataframe=output_df,
        column_name="production",
        title="Steel Production Volume by Technology",
        units=units_pa,
        pivot_columns=["technology"],
        product_type="steel",
    )
    plotter.plot_area_chart_by_region_or_technology(
        dataframe=output_df,
        column_name="production",
        title="Iron Production Volume by Technology",
        units=units_pa,
        pivot_columns=["technology"],
        product_type="iron",
    )
    plotter.plot_area_chart_by_region_or_technology(
        dataframe=output_df,
        column_name="capacity",
        title="Steel Capacity Volume by Technology",
        units=units_pa,
        pivot_columns=["technology"],
        product_type="steel",
    )
    plotter.plot_area_chart_by_region_or_technology(
        dataframe=output_df,
        column_name="capacity",
        title="Iron Capacity Volume by Technology",
        units=units_pa,
        pivot_columns=["technology"],
        product_type="iron",
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


def generate_market_cost_curve_plots(
    trace_cost_curve: dict[int, dict[str, list[dict]]],
    trace_demand: dict[int, dict[str, float]],
    plot_paths: Optional["PlotPaths"] = None,
):
    """Generate cost curve plots using the exact data behind market price / NPV calculations.

    Plots every 5 years using the traced cost curve (same FG filtering, capacity scaling,
    and demand forecast as the market price extraction).

    Args:
        trace_cost_curve: {year: {product: [per-FG entries]}} from datacollector.
        trace_demand: {year: {product: demand_forecast}} from datacollector.
        plot_paths: PlotPaths with pam_plots_dir for saving.
    """
    from steelo.utilities.plotting import plot_cost_curve_from_trace

    if not trace_cost_curve:
        logger.warning("No traced cost curve data available. Skipping market cost curve plots.")
        return

    years = sorted(trace_cost_curve.keys())
    first_year = years[0]
    last_year = years[-1]

    years_to_plot = list(range(first_year, last_year + 1, 5))
    if last_year not in years_to_plot:
        years_to_plot.append(last_year)

    for year in years_to_plot:
        if year not in trace_cost_curve:
            continue
        demand_for_year = trace_demand.get(year, {})
        for product_type in ["steel", "iron"]:
            entries = trace_cost_curve[year].get(product_type, [])
            demand = demand_for_year.get(product_type, 0.0)
            for aggregation in ["region", "technology"]:
                try:
                    plot_cost_curve_from_trace(
                        cost_curve_entries=entries,
                        demand=demand,
                        product_type=product_type,
                        year=year,
                        aggregation=aggregation,
                        plot_paths=plot_paths,
                    )
                    logger.info(f"Generated market cost curve for {product_type} by {aggregation} in {year}")
                except Exception as e:
                    logger.warning(
                        f"Could not generate market cost curve for {product_type} by {aggregation} in {year}: {e}"
                    )


def generate_material_flow_plots(file_path):
    """
    Generate and store sankey charts for the material flow
    """

    pass
