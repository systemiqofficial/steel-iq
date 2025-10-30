import logging
import numpy as np
import pandas as pd
import xarray as xr
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from steelo.simulation import GeoConfig
    from steelo.domain.models import GeoDataPaths

from steelo.utilities.plotting import plot_screenshot
from steelo.domain.models import PlotPaths
from steelo.domain.constants import (
    USD_TO_BioUSD,
    MWH_TO_KWH,
)

logger = logging.getLogger(__name__)


def calculate_outgoing_cashflow_proxy(
    data: xr.Dataset,
    year: int,
    capex: float,
    product: str,
    capex_share: float,
    energy_consumption_per_t: float,
    baseload_coverage: float,
    steel_plant_capacity: float,
    plant_lifetime: int,
    geo_config: "GeoConfig",
    geo_paths: "GeoDataPaths",
) -> xr.DataArray:
    """
    Calculate an outgoing cashflow proxy for each location in the dataset. Combines all cost variables into a single KPI using the CAPEX multiplication factor logic.

    Formula:
        Outgoing cashflow = CAPEX contributions (one-off) + OPEX contributions (recurring over full operation time)
                          = [CAPEX portion * landtype_factor + rail_cost] + [power_price portion + feedstock_transportation_cost + demand_transportation_cost]

    Args:
        data: Dataset containing power prices, rail costs, transportation costs, and landtype factors
        year: Simulation year
        capex: Capital expenditure per tonne of capacity (USD/t)
        product: Product type ("iron" or "steel")
        capex_share: Share of total CAPEX allocated to this product (0.0 to 1.0)
        energy_consumption_per_t: Energy consumption in MWh for plant lifetime
        baseload_coverage: Baseload power coverage percentage (0.0 to 1.0)
        steel_plant_capacity: Plant capacity in tonnes
        plant_lifetime: Plant operational lifetime in years
        geo_config: Configuration for geospatial calculations (transport costs, LULC, infrastructure)
        geo_paths: Paths to geospatial data files for plotting outputs

    Returns:
        DataArray containing outgoing cashflow proxy values (USD) for each location

    Side Effects:
        Generates and saves plots of outgoing cashflow and priority heatmaps
    """
    logger.info(
        f"Calculating outgoing cashflow for {product} with capex share {capex_share * 100}% and energy consumption {energy_consumption_per_t} TWh."
    )
    cost_components = {}
    if geo_config.included_power_mix != "Not included":
        energy_consumption = energy_consumption_per_t * steel_plant_capacity  # MWh
        cost_components["power consumption"] = (
            data["power_price"] * energy_consumption * MWH_TO_KWH * plant_lifetime
        )  # USD = USD/kWh * kWh * years
    if geo_config.include_infrastructure_cost:
        cost_components["rail buildout"] = data["rail_cost"]  # USD
    if geo_config.include_transport_cost:
        cost_components["feedstock transport"] = (
            data[f"feedstock_transportation_cost_per_ton_{product}"] * steel_plant_capacity * plant_lifetime
        )  # USD = USD/t * t * years
        cost_components["demand transport"] = (
            data[f"demand_transportation_cost_per_ton_{product}"] * steel_plant_capacity * plant_lifetime
        )  # USD = USD/t * t * years
        # Correct for iron ore requirement being larger than iron, steel, and demand
        if product == "iron":
            cost_components["feedstock transport"] *= geo_config.iron_ore_steel_ratio
    if geo_config.include_lulc_cost:
        cost_components["CAPEX"] = (
            capex * capex_share * steel_plant_capacity * data["landtype_factor"]
        )  # USD = USD/t * t * dimensionless factor
    else:
        cost_components["CAPEX"] = (
            capex * capex_share * steel_plant_capacity * xr.ones_like(data["feasibility_mask"])
        )  # USD = USD/t * t

    # Sum all cost components to get the outgoing cashflow proxy
    outgoing_cashflow = sum(cost_components.values())
    if not isinstance(outgoing_cashflow, xr.DataArray):
        outgoing_cashflow = xr.DataArray(outgoing_cashflow)
    if outgoing_cashflow.all() == 0:
        raise ValueError("All locations have zero outgoing cashflow!")
    logger.info("Global average contribution to outgoing cashflow:")
    for component, value in cost_components.items():
        logger.info(
            f"       -{component} : {((value / outgoing_cashflow).mean(dim=['lat', 'lon'], skipna=True).values * 100):.2f}%"
        )

    # Plot outgoing cashflow and priority KPI map (inverse of standardized outgoing cashflow)
    plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
    masked_cashflow = outgoing_cashflow.where(data["feasibility_mask"] > 0)
    ## Check if all data is NaN/masked and raise error if so
    if masked_cashflow.isnull().all():
        raise ValueError(
            "No top locations identified to build new plants - all data is NaN/masked. "
            "This may indicate invalid input data or over-aggressive masking."
        )
    plot_screenshot(
        masked_cashflow * USD_TO_BioUSD,
        title=f"Outgoing cashflow for {product} in {year} (Bio USD)",
        var_type="sequential",
        max_val=40,
        save_name=f"outgoing_cashflow_proxy_{product}_{str(year)}_p{str(int((1 - baseload_coverage) * 100))}",
        plot_paths=plot_paths_obj,
    )
    min_val = masked_cashflow.min(skipna=True)
    max_val = masked_cashflow.max(skipna=True)
    ## Handle edge case where all values are identical (avoid division by zero by setting all to 0.5 - standardized mid-point)
    if min_val == max_val:
        standardized_cashflow = masked_cashflow * 0 + 0.5
    else:
        standardized_cashflow = (masked_cashflow - min_val) / (max_val - min_val)
    inversed_cashflow = 1 - standardized_cashflow
    plot_screenshot(
        inversed_cashflow,
        title=f"Priority map for new {product} plants in {year}",
        var_type="binary",
        max_val=1,
        min_val=0.7,
        save_name=f"priority_heatmap_{product}_{str(year)}_p{str(int((1 - baseload_coverage) * 100))}",
        plot_paths=plot_paths_obj,
    )

    # Ensure return type is always xr.DataArray
    if isinstance(outgoing_cashflow, xr.DataArray):
        return outgoing_cashflow
    else:
        return xr.DataArray(outgoing_cashflow)


def extract_priority_locations(
    ds: xr.Dataset, var_name: str, top_pct: int, random_seed: int, invert: bool = False
) -> tuple[xr.DataArray, pd.DataFrame]:
    """
    Extract the locations of the top X% values for a given variable in the dataset.

    Steps:
        1. Flatten the data, remove zeros and NaNs, sort, and assign priority order
        2. Select the top locations based on the distribution of values:
           - For uniform distributions: random selection
           - For low-variance distributions (< 20 unique values): sample from chunks
           - For continuous distributions: use quantile threshold
        3. Create DataFrame with coordinates of top locations

    Args:
        ds: Dataset containing the variable to extract priority locations from (must include 'feasibility_mask')
        var_name: Name of the variable to use for priority ranking
        top_pct: Percentage of top locations to extract (0-100)
        random_seed: Random seed for reproducible sampling
        invert: If True, lower values have higher priority (e.g., for cost data). Default: False

    Returns:
        top_values: DataArray with binary values (1 for top locations, 0 otherwise)
        locations: DataFrame containing latitude and longitude of top locations
    """
    # Step 1: Flatten the data, remove zeros and NaNs, sort, and assign priority order
    feasible_values = ds[var_name].where(ds["feasibility_mask"] > 0, np.nan)
    non_zero_values = (feasible_values.where(feasible_values != 0, np.nan)).values.flatten()
    valid_mask = np.isfinite(non_zero_values)
    values = non_zero_values[valid_mask]
    n_total = len(values)
    n_top = max(1, int(np.ceil(top_pct / 100 * n_total)))
    if invert:  # Lower values = higher priority
        sort_idx = np.argsort(values)
        sorted_values = values[sort_idx]
        unique_vals, inverse_idx = np.unique(sorted_values, return_inverse=True)
    else:  # Higher values = higher priority
        sort_idx = np.argsort(-values)
        sorted_values = values[sort_idx]
        unique_vals, inverse_idx = np.unique(-sorted_values, return_inverse=True)

    # Step 2: Select the top locations based on the distribution of values
    ## Case 1: Low-variance distribution
    if len(unique_vals) < 20:
        rng = np.random.default_rng(seed=random_seed)
        if len(unique_vals) == 1:
            # Uniform distribution: select random points
            chosen_idx = rng.choice(np.where(valid_mask)[0], size=n_top, replace=False)
        else:
            # Stepwise distribution: sample from chunks
            chunk_indices = [np.where(inverse_idx == i)[0] for i in range(len(unique_vals))]
            chosen_idx_sorted: list[int] = []
            remaining = n_top
            for indices in chunk_indices:
                if remaining <= 0:
                    break
                if len(indices) <= remaining:
                    chosen_idx_sorted.extend(indices)
                    remaining -= len(indices)
                else:
                    chosen = rng.choice(indices, size=remaining, replace=False)
                    chosen_idx_sorted.extend(chosen)
                    remaining = 0
            sort_idx_subset = sort_idx[chosen_idx_sorted]
            chosen_idx = np.where(valid_mask)[0][sort_idx_subset]
        flat_indices = np.where(valid_mask)[0]
        rel_positions = np.nonzero(np.isin(flat_indices, chosen_idx))[0]
        top_values_flat = np.zeros_like(flat_indices, dtype=int)
        top_values_flat[rel_positions] = 1
        top_values_flat_full = np.zeros(ds[var_name].size, dtype=int)
        top_values_flat_full[flat_indices] = top_values_flat
        top_values_reshaped = top_values_flat_full.reshape(ds[var_name].shape)
        top_values = xr.DataArray(top_values_reshaped, coords=ds[var_name].coords, dims=ds[var_name].dims)
    ## Case 2: Continuous distribution: use quantile threshold
    else:
        valid_data = ds[var_name].where((ds[var_name] != 0) & np.isfinite(ds[var_name]))
        if invert:
            threshold = valid_data.quantile(top_pct / 100)
            top_values = xr.where(ds[var_name] <= threshold, 1, 0)
        else:
            threshold = valid_data.quantile(1 - (top_pct / 100))
            top_values = xr.where(ds[var_name] >= threshold, 1, 0)

    # Step 3: Create DataFrame with the coordinates of top locations
    non_zero_data_array: xr.DataArray = (
        top_values.where(top_values != 0, np.nan).dropna(dim="lat", how="all").dropna(dim="lon", how="all")
    )
    non_nan_indices = np.where(~np.isnan(non_zero_data_array.values))
    latitudes = non_zero_data_array.coords["lat"].values[non_nan_indices[0]]
    longitudes = non_zero_data_array.coords["lon"].values[non_nan_indices[1]]
    locations = pd.DataFrame(list(zip(latitudes, longitudes)), columns=["Latitude", "Longitude"])
    return top_values, locations


def find_top_locations_per_country(
    ds: xr.Dataset, top_locations: dict[str, pd.DataFrame], product: str, priority_pct: int, random_seed: int
) -> tuple[xr.DataArray, pd.DataFrame]:
    """
    Add the top X/10% locations for each country to ensure all countries have representation.
    This function gives countries that have no locations in the global top X% a chance to participate.
    The number of locations selected per country is proportional to the country size.

    Args:
        ds: Dataset containing outgoing cashflow data and ISO3 codes
        top_locations: Dictionary mapping product types to DataFrames of global top locations
        product: Product type ("iron" or "steel")
        priority_pct: Global priority percentage (the per-country percentage is priority_pct/10)
        random_seed: Random seed for reproducible sampling

    Returns:
        top_values: DataArray with binary values (1 for top locations including country lottery, 0 otherwise)
        top_locations_wlottery: DataFrame containing all top locations including country-specific additions
    """

    top_wlottery = ds[f"top{str(priority_pct)}_{product}"].copy().astype(float)
    top_locations_wlottery = top_locations[product].copy()

    # List of unique ISO3 codes
    iso3_values = ds["iso3"].values.flatten()
    unique_iso3 = np.unique(iso3_values[(~pd.isnull(iso3_values)) & (iso3_values != "nan")])

    for iso3 in unique_iso3:
        # Get the top locations for the current ISO3 code (if not empty)
        ds_iso3 = ds.where(ds["iso3"] == iso3)
        if np.any(~np.isnan(ds_iso3[f"outgoing_cashflow_{product}"].values)):
            top_values_iso3, top_locations_iso3 = extract_priority_locations(
                ds_iso3,
                f"outgoing_cashflow_{product}",
                top_pct=int(priority_pct / 10),
                random_seed=random_seed,
                invert=True,
            )
            # Add to the global top locations if not nan
            top_wlottery += top_values_iso3
            dfs_to_concat = [df for df in [top_locations_wlottery, top_locations_iso3] if not df.empty]
            if dfs_to_concat:
                top_locations_wlottery = pd.concat(dfs_to_concat, ignore_index=True)
    top_values = top_wlottery.astype(bool)

    return top_values, top_locations_wlottery


def calculate_priority_location_kpi(
    ds: xr.Dataset,
    capex: float,
    year: int,
    baseload_coverage: float,
    steel_plant_capacity: float,
    plant_lifetime: int,
    geo_config: "GeoConfig",
    geo_paths: "GeoDataPaths",
) -> dict[str, list[dict[Any, Any]]]:
    """
    Calculate the priority location KPI for steel and iron production.

    Steps:
        1. Mask all variables with the feasibility mask
        2. Calculate outgoing cashflow for both iron and steel products
        3. Extract the top X% global locations (bottom X% of the cost distribution)
        4. Add the top X/10% locations for each country to give all countries representation
        5. For each location, extract NPV calculation inputs (ISO3, rail cost, power price, LCOH)

    Args:
        ds: Dataset containing all geospatial layers (feasibility, costs, distances, etc.)
        capex: Capital expenditure per tonne of capacity (USD/t)
        year: Simulation year
        baseload_coverage: Baseload power coverage percentage (0.0 to 1.0)
        steel_plant_capacity: Plant capacity in tonnes
        plant_lifetime: Plant operational lifetime in years
        geo_config: Configuration for geospatial calculations (priority percentage, transport costs, etc.)
        geo_paths: Paths to geospatial data files for plotting outputs

    Returns:
        Dictionary mapping product types ("iron", "steel") to lists of location dictionaries, each containing:
            - Latitude: Location latitude
            - Longitude: Location longitude
            - iso3: ISO3 country code
            - rail_cost: Railway infrastructure cost (USD)
            - power_price: Electricity price (USD/kWh)
            - capped_lcoh: Capped levelized cost of hydrogen (USD/kg)

    Side Effects:
        Generates and saves plots of outgoing cashflow, priority heatmaps, and top location maps
    """
    logger.info(f"Identifying the top {geo_config.priority_pct}% priority locations.")

    # Mask all variables with the feasibility mask and remove NaN values
    ds_masked = ds.where(ds["feasibility_mask"] > 0)

    # Combine all variables into a single KPI using the CAPEX multiplication factor logic
    top_locations = {}
    top_locations_wlottery = {}
    for product in ["iron", "steel"]:
        # Calculate outgoing cashflow
        ds_masked[f"outgoing_cashflow_{product}"] = calculate_outgoing_cashflow_proxy(
            ds_masked,
            year,
            capex,
            product,
            capex_share=geo_config.share_iron_vs_steel[product]["capex_share"],
            energy_consumption_per_t=geo_config.share_iron_vs_steel[product]["energy_consumption_per_t"],
            baseload_coverage=baseload_coverage,
            steel_plant_capacity=steel_plant_capacity,
            plant_lifetime=plant_lifetime,
            geo_config=geo_config,
            geo_paths=geo_paths,
        )

        # Get the top X% locations (bottom X% of the price distribution)
        ds_masked[f"top{str(geo_config.priority_pct)}_{product}"], top_locations[product] = extract_priority_locations(
            ds_masked,
            f"outgoing_cashflow_{product}",
            top_pct=int(geo_config.priority_pct),
            random_seed=geo_config.random_seed,
            invert=True,
        )

        # Add the top X/10 % locations for each country to "give everyone a chance". The "chance" is proportional to the size of the country.
        ds_masked[f"top{str(geo_config.priority_pct)}_{product}_wlottery"], top_locations_wlottery[product] = (
            find_top_locations_per_country(
                ds_masked, top_locations, product, geo_config.priority_pct, geo_config.random_seed
            )
        )

        plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
        plot_screenshot(
            ds_masked[f"top{str(geo_config.priority_pct)}_{product}_wlottery"],
            var_type="binary",
            title=f"Top {geo_config.priority_pct}% locations for {product} production",
            save_name=f"top{str(geo_config.priority_pct)}_priority_locations_{product}_{str(year)}",
            plot_paths=plot_paths_obj,
        )

        # For each location, pass NPV calculation inputs
        for row in top_locations_wlottery[product].itertuples():
            lat = row.Latitude
            lon = row.Longitude
            for col in ["iso3", "rail_cost", "power_price", "capped_lcoh"]:
                top_locations_wlottery[product].loc[row.Index, col] = ds_masked[col].sel(lat=lat, lon=lon).item()

    # Filter out empty records
    top_locations_wlottery_dict = {}
    for key in top_locations_wlottery:
        records = (
            top_locations_wlottery[key]
            .reset_index(drop=True)
            .fillna(np.nan)
            .replace({np.nan: None})
            .to_dict(orient="records")
        )
        top_locations_wlottery_dict[key] = [
            item for item in records if all(v is not None and v != {} for v in item.values())
        ]
    return top_locations_wlottery_dict
