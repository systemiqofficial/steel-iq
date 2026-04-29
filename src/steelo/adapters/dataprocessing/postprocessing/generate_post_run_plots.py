import logging
import pandas as pd

from typing import Optional, TYPE_CHECKING

from steelo.domain.constants import T_TO_KT, T_TO_MT
from steelo.utilities.plotting import plot_added_capacity_by_technology
from steelo.utilities.steeliq_plotter import SteelPlotter, PlotConfig

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
    steel_market_clearing_share: float,
    iron_market_clearing_share: float,
    steel_price_buffer: float,
    iron_price_buffer: float,
    plot_paths: Optional["PlotPaths"] = None,
):
    """
    Generate and store plots related to the post_process collected data file path.

    Args:
        steel_market_clearing_share: Fraction of steel cumulative capacity that participates in
            market clearing on the cost-curve plots; must match the engine value so plot and engine
            agree on the displayed clearing price.
        iron_market_clearing_share: Same for iron.
        steel_price_buffer: USD/tonne shortage premium added when demand exceeds the dispatchable
            slice; must match the engine value.
        iron_price_buffer: Same for iron.
    """
    # output_df = pd.read_csv(settings.output_dir / "post_processed_2025-06-02 21-41.csv")
    output_df = pd.read_csv(file_path)
    output_df = output_df.copy()
    output_df = output_df.sort_values(by="year").reset_index(drop=True)

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

    # Use SteelPlotter for capacity development to get consistent styling and footer
    plotter = SteelPlotter(config=PlotConfig(), plot_paths=plot_paths)
    plotter.plot_capacity_development_by_technology(data_file=output_df, units=units_pa)

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

        # Generate cost curve plots for each selected year
        for year in years_to_plot:
            # Compute demand for this specific year
            year_steel_demand = _sum_unique_furnace_output(output_df, "steel", year)
            year_iron_demand = _sum_unique_furnace_output(output_df, "iron", year)

            # Generate cost curves by region and technology for both steel and iron
            for product_type, year_demand in [("steel", year_steel_demand), ("iron", year_iron_demand)]:
                share = steel_market_clearing_share if product_type == "steel" else iron_market_clearing_share
                buffer = steel_price_buffer if product_type == "steel" else iron_price_buffer
                for aggregation in ["region", "technology"]:
                    try:
                        plotter.plot_cost_curve_step(
                            data_file=output_df,
                            product_type=product_type,
                            product_demand=year_demand,
                            year=year,
                            capacity_limit=capacity_limit,
                            units=units,
                            clearing_share=share,
                            price_buffer=buffer,
                            aggregation=aggregation,
                        )
                        logger.info(f"Generated {product_type} cost curve by {aggregation} for {year}")
                    except Exception as e:
                        logger.warning(f"Could not generate {product_type} cost curve by {aggregation} for {year}: {e}")

        # Also generate the simple cost curve for the last year (backward compatibility)
        plotter.plot_cost_curve_step(
            data_file=output_df,
            product_type="steel",
            product_demand=steel_demand,
            year=last_year,
            capacity_limit=capacity_limit,
            units=units,
            clearing_share=steel_market_clearing_share,
            price_buffer=steel_price_buffer,
            aggregation="region",
        )
        plotter.plot_cost_curve_step(
            data_file=output_df,
            product_type="iron",
            product_demand=iron_demand,
            year=last_year,
            capacity_limit=capacity_limit,
            units=units,
            clearing_share=iron_market_clearing_share,
            price_buffer=iron_price_buffer,
            aggregation="region",
        )
        plotter.plot_cost_curve_step(
            data_file=output_df,
            product_type="steel",
            product_demand=steel_demand,
            year=last_year,
            capacity_limit=capacity_limit,
            units=units,
            clearing_share=steel_market_clearing_share,
            price_buffer=steel_price_buffer,
            aggregation="technology",
        )
        plotter.plot_cost_curve_step(
            data_file=output_df,
            product_type="iron",
            product_demand=iron_demand,
            year=last_year,
            capacity_limit=capacity_limit,
            units=units,
            clearing_share=iron_market_clearing_share,
            price_buffer=iron_price_buffer,
            aggregation="technology",
        )

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


def generate_material_flow_plots(file_path):
    """
    Generate and store sankey charts for the material flow
    """

    pass
