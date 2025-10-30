import pandas as pd
import numpy as np
import xarray as xr
import logging
from pathlib import Path
from typing import cast
from steelo.domain.models import CountryMappingService
from steelo.adapters.dataprocessing.excel_reader import read_country_mappings
from baseload_optimisation_atlas.boa_config import LIFETIMES, LEARNING_RATES
from baseload_optimisation_atlas.boa_constants import EPSILON


def load_country_mappings_standalone(excel_path: Path) -> CountryMappingService:
    """
    Load country mappings from Excel file when running standalone (without Environment).

    Args:
        excel_path: Path to master_input.xlsx. If None, uses default location.

    Returns:
        CountryMappingService instance with loaded mappings.
    """
    if not excel_path.exists():
        raise FileNotFoundError(f"Master input Excel file not found at {excel_path}")

    mappings = read_country_mappings(excel_path)
    if not mappings:
        raise ValueError("No country mappings found in Excel file")

    return CountryMappingService(mappings)


def preprocess_renewable_energy_cost_data(
    code_df: pd.DataFrame,
    code_to_irena_region_map: dict[str, str],
    master_input_path: Path,
    renewable_input_path: Path,
) -> pd.DataFrame:
    """
    Load and preprocess CAPEX, OPEX, and cost of capital data for renewables by mapping regional data to countries.

    Steps:
        1. Map regional CAPEX and worldwide OPEX data to countries
        2. Fill missing cost of capital values (about 20%) with global maximum
        3. Merge all data into single DataFrame

    Args:
        code_df: DataFrame with ISO-3 codes
        code_to_irena_region_map: Mapping from ISO-3 codes to IRENA regions
        master_input_path: Path to master input Excel file
        renewable_input_path: Path to renewable energy input Excel file

    Returns:
        DataFrame with CAPEX, OPEX, and cost of capital per country indexed by ISO-3 code

    Notes:
        - CAPEX and OPEX: Regional level (16 regions), Year 2022, Units: USD/kW, Source: IRENA
        - Cost of capital: Country level, Source: Systemiq internal
        - Missing cost of capital values assume unfavorable business conditions (filled with maximum)
    """
    logging.info("Preprocessing renewable energy cost data")

    # Load CAPEX, OPEX, and cost of capital data
    renewable_capex = pd.read_excel(renewable_input_path, sheet_name="Installation costs")
    renewable_opex = pd.read_excel(renewable_input_path, sheet_name="Operational costs")
    cost_of_capital = pd.read_excel(master_input_path, sheet_name="Cost of capital")

    # Drop units if present
    for data in [renewable_capex, renewable_opex]:
        if "Unit" in data.columns:
            data.drop(columns=["Unit"], inplace=True)

    # Select and restructure data
    renewable_capex = renewable_capex.pivot(index="Region", columns="Technology", values="Capex")
    renewable_capex.columns = [f"Capex {tech}" for tech in renewable_capex.columns]
    renewable_capex = renewable_capex.reset_index()
    renewable_opex = renewable_opex.pivot(index="Region", columns="Technology", values="Opex")
    renewable_opex.columns = [f"Opex {tech}" for tech in renewable_opex.columns]
    renewable_opex = renewable_opex.reset_index()
    cost_of_capital_renewables = cost_of_capital.copy()[["ISO-3 Code", "WACC - Renewables"]].rename(
        columns={"WACC - Renewables": "Cost of capital (%)"}
    )

    # Map regions onto iso3 codes
    codes_ = code_df.copy()
    codes_["Region"] = codes_["ISO-3 Code"].map(code_to_irena_region_map)
    renewable_capex_per_country = pd.merge(codes_, renewable_capex, on="Region", how="left")

    # Merge all costs into a single object and fill missing values with the maximum value
    renewable_opex = pd.concat([renewable_opex] * len(codes_), ignore_index=True)
    renewable_cost_per_country = pd.concat(
        [renewable_capex_per_country.reset_index(drop=True), renewable_opex.reset_index(drop=True)], axis=1
    )
    renewable_cost_per_country.drop(columns=["Region"], inplace=True)
    cost_per_country = (
        pd.merge(renewable_cost_per_country, cost_of_capital_renewables, on="ISO-3 Code", how="left")
        .set_index("ISO-3 Code")
        .sort_index()
    )
    cost_per_country.fillna(cost_per_country.select_dtypes(include=[np.number]).max(), inplace=True)

    return cost_per_country


def correct_ssp_projections_with_current_capacity(
    proj_renewable_generation_capacity: pd.DataFrame,
    hist_capacity_iso3: pd.DataFrame,
    base_year: int = 2022,
) -> pd.DataFrame:
    """
    Correct magnitude of SSP capacity projections using historical data while preserving SSP growth gradients.

    Args:
        proj_renewable_generation_capacity: Projected capacity data from SSP scenarios
        hist_capacity_iso3: Historical capacity data indexed by ISO-3 code
        base_year: Year of historical capacity data (default: 2022)

    Returns:
        Corrected capacity projections with SSP growth rates applied to historical baseline
    """
    logging.info("Correcting SSP projections with historical capacity data.")

    # Calculate the gradient (ratio of growth between consecutive years)
    capacity_gradient = proj_renewable_generation_capacity.pct_change(axis=1, fill_method=None)  # type: ignore[call-arg]

    # Replace zero values with a small epsilon to avoid division by zero
    capacity_gradient.replace(0, EPSILON, inplace=True)

    projections = []
    for tech in capacity_gradient.index.get_level_values("Technology").unique():
        # Filter the current technology and relevant years
        gradient_tech = capacity_gradient.reset_index()
        gradient_tech = gradient_tech[gradient_tech["Technology"] == tech]
        relevant_years = [col for col in gradient_tech.columns if isinstance(col, int) and col > base_year]
        gradient_tech = gradient_tech[["ISO-3 Code", "Technology"] + relevant_years]
        hist_capacity = hist_capacity_iso3[f"Capacity {tech}"].reset_index()

        # Project future years
        projection = pd.merge(hist_capacity, gradient_tech, on="ISO-3 Code", how="left")
        projection = projection.rename(columns={f"Capacity {tech}": base_year})
        projection = projection.set_index(["ISO-3 Code", "Technology"])
        projection_years = cast(list[int], sorted([col for col in projection.columns if isinstance(col, int)]))
        for i in range(1, len(projection_years)):
            year = projection_years[i]
            prev_year = projection_years[i - 1]
            projection[year] = projection[prev_year] * (1 + projection[year])
        projections.append(projection)
    final_projection = pd.concat(projections)

    return final_projection


def preprocess_renewable_energy_capacity_data(
    base_year: int,
    code_df: pd.DataFrame,
    code_to_irena_map: dict[str, str],
    code_to_ssp_region_map: dict[str, str],
    renewable_input_path: Path,
) -> pd.DataFrame:
    """
    Load and preprocess capacity projections for solar PV and onshore wind by combining historical and SSP scenario data.

    Steps:
        1. Map country names and regions across different data sources
        2. Fill missing values with global average
        3. Correct SSP projection magnitudes using historical capacity data

    Args:
        base_year: Year of historical capacity data
        code_df: DataFrame with ISO-3 codes
        code_to_irena_map: Mapping from ISO-3 codes to IRENA country names
        code_to_ssp_region_map: Mapping from ISO-3 codes to SSP regions
        renewable_input_path: Path to renewable energy input Excel file

    Returns:
        Corrected capacity projections indexed by ISO-3 code and technology

    Notes:
        - Historical capacity: Yearly, 2024, Country/regional level (244 countries), Units: MW, Source: IRENASTAT
        - Capacity projections: SSP3-baseline scenario (Regional Rivalry, high emissions BAU), Decadal 2005-2100, Regional (5 regions), Units: GW, Source: IIASA
    """
    logging.info("Processing capacity data")

    # Load historical and projected capacity data
    hist_renewable_capacity = pd.read_excel(renewable_input_path, sheet_name="Historical capacity")
    proj_renewable_capacity = pd.read_excel(renewable_input_path, sheet_name="Projected capacity")

    # Drop units
    for data in [hist_renewable_capacity, proj_renewable_capacity]:
        if "Unit" in data.columns:
            data.drop(columns=["Unit"], inplace=True)

    # Map IRENA countries and SSP regions to ISO3 codes
    code_df_ = code_df.copy()
    code_df_["Country"] = code_df_["ISO-3 Code"].map(code_to_irena_map)
    hist_capacity_iso3 = (
        pd.merge(code_df_, hist_renewable_capacity, on="Country", how="left")
        .set_index("ISO-3 Code")
        .sort_index()
        .drop(columns=["Country"])
    )
    code_df_ = code_df.copy()
    code_df_["Region"] = code_df_["ISO-3 Code"].map(code_to_ssp_region_map)
    proj_renewable_capacity = (
        pd.merge(proj_renewable_capacity, code_df_, on="Region", how="left")
        .set_index(["ISO-3 Code", "Technology"])
        .sort_index()
        .drop(columns=["Region"])
    )

    # Fill missing values with mean and clean up
    hist_capacity_iso3 = hist_capacity_iso3.apply(pd.to_numeric, errors="coerce")
    proj_renewable_capacity = proj_renewable_capacity.apply(pd.to_numeric, errors="coerce")
    hist_capacity_iso3 = hist_capacity_iso3.apply(lambda col: col.fillna(col.mean()), axis=0)
    proj_renewable_capacity.fillna(proj_renewable_capacity.select_dtypes(include=[np.number]).mean(), inplace=True)

    # Correct the magnitude of the SSP projections with the historical capacity data
    corrected_capacity_projections = correct_ssp_projections_with_current_capacity(
        proj_renewable_capacity,
        hist_capacity_iso3,
        base_year,
    )

    return corrected_capacity_projections


def update_capex_using_learning_curve(capacity_0: float, capacity_t: float, capex_0: float, lr: float) -> float:
    """
    Update CAPEX using learning curve based on capacity change.

    Formula:
        capex_t = capex_0 * (capacity_t / capacity_0)^b, where b = log(1 - lr) / log(2)

    Args:
        capacity_0: Initial capacity
        capacity_t: Target capacity
        capex_0: Initial CAPEX
        lr: Learning rate

    Returns:
        Updated CAPEX at target capacity

    Notes:
        - Capacity units are irrelevant since they cancel out
        - Cases with capacity_0 == 0 are replaced by small epsilon to avoid division by zero
    """
    b = np.log(1 - lr) / np.log(2)
    capacity_0 = max(capacity_0, EPSILON)
    capex_t = capex_0 * (capacity_t / capacity_0) ** b

    return capex_t


def project_capex(
    costs: pd.DataFrame,
    capacity_projections: pd.DataFrame,
    base_year: int,
) -> pd.DataFrame:
    """
    Project CAPEX across countries and years using learning curve based on capacity projections.

    Args:
        costs: DataFrame with initial CAPEX per country and technology
        capacity_projections: DataFrame with capacity projections indexed by country and technology
        base_year: Base year for capacity projections

    Returns:
        DataFrame with projected CAPEX values indexed by country and technology
    """
    logging.info("Projecting CAPEX using the learning curve")
    capex_projection = []
    for (country, tech), row in capacity_projections.groupby(level=[0, 1]):
        initial_capex = costs.get(f"Capex {tech}", pd.Series(index=[country])).reindex([country]).values[0]
        tech_years = capacity_projections.columns
        capex_values = [initial_capex]
        for i in range(1, len(tech_years)):
            year = tech_years[i]
            prev_year = tech_years[i - 1]
            capex_0_ = capex_values[-1] if prev_year != base_year else initial_capex
            capacity_0_ = row[prev_year]
            if isinstance(capacity_0_, pd.Series):
                capacity_0_ = capacity_0_.iloc[0]
            capacity_t_ = row[year]
            if isinstance(capacity_t_, pd.Series):
                capacity_t_ = capacity_t_.iloc[0]
            capex_t = update_capex_using_learning_curve(
                float(capacity_0_),
                float(capacity_t_),
                float(capex_0_),
                float(LEARNING_RATES[tech]),
            )
            capex_values.append(capex_t)
        tech_capex = pd.DataFrame(
            [capex_values],
            columns=tech_years,
            index=pd.MultiIndex.from_tuples([(country, tech)], names=["ISO-3 Code", "Technology"]),
        )
        capex_projection.append(tech_capex)
    capex_projection_df = pd.concat(capex_projection).sort_index()
    return capex_projection_df


def preprocess_storage_costs(
    years: list[int],
    renewable_input_path: Path,
) -> dict[str, np.ndarray]:
    """
    Load battery storage costs and convert to dictionary format.

    Args:
        years: List of years for cost projection
        renewable_input_path: Path to renewable energy input Excel file

    Returns:
        Dictionary containing:
            - battery_cost_per_installed_unit: Total installed unit cost array
            - average_implied_storage: Average implied storage array
    """
    storage_costs_df = pd.read_excel(renewable_input_path, sheet_name="Storage costs")
    storage_costs_df.set_index("Metric", inplace=True)
    if "Unit" in storage_costs_df.columns:
        storage_costs_df.drop(columns=["Unit"], inplace=True)
    storage_cost_dict = {
        "battery_cost_per_installed_unit": storage_costs_df.loc["Total installed unit cost"].loc[years].to_numpy(),
        "average_implied_storage": storage_costs_df.loc["Average implied storage"].loc[years].to_numpy(),
    }
    return storage_cost_dict


def process_global_baseload_simulation_costs(
    investment_year: int,
    master_input_path: Path,
    renewable_input_path: Path,
    geo_output_dir: Path,
) -> tuple[xr.Dataset, dict[str, np.ndarray], int]:
    """
    Process renewable energy cost inputs for global baseload simulation by combining historical capacity with SSP projections and applying learning curves.

    Args:
        investment_year: Year for investment
        master_input_path: Path to master input Excel file
        renewable_input_path: Path to renewable energy input Excel file
        geo_output_dir: Path to output directory for saving processed costs

    Returns:
        Tuple containing:
            - projected_cost_per_country: Dataset with CAPEX projections for solar, wind (USD/MW), OPEX percentages, and cost of capital per country
            - storage_costs: Dictionary with battery costs and capacities (USD/kWh and h/GWh)
            - investment_horizon: Maximum lifetime of technologies (years)

    Side Effects:
        - Saves projected costs to NetCDF file in geo_output_dir/costs/
        - Loads from file if already exists

    Note:
        CAPEX projections extend to end of investment horizon using learning curve and capacity growth from SSP scenarios
    """

    investment_horizon = max(LIFETIMES["solar"], LIFETIMES["wind"], LIFETIMES["battery"])
    years = list(range(investment_year, investment_year + investment_horizon + 1))

    # Check if the data already exists
    renewables_costs_file = geo_output_dir / "costs" / f"cost_of_renewables_{investment_year}_investment_year.nc"
    renewables_costs_file.parent.mkdir(parents=True, exist_ok=True)
    if renewables_costs_file.exists():
        logging.info(f"Loading cost of renewables data from {renewables_costs_file}. Skipping processing.")
        projected_cost_per_country = xr.open_dataset(renewables_costs_file)

    else:
        logging.info("Processing cost of renewables data globally.")

        # Load country mappings from Excel file
        country_mappings = load_country_mappings_standalone(master_input_path)
        code_to_irena_region_map_raw = country_mappings.code_to_irena_region_map
        code_to_irena_map = country_mappings.code_to_irena_map
        code_to_ssp_region_map = country_mappings.code_to_ssp_region_map

        # Filter out None values from the region map
        code_to_irena_region_map = {k: v for k, v in code_to_irena_region_map_raw.items() if v is not None}

        # Process costs and capacity data and project CAPEX using the learning curve (if not done yet)
        base_year = 2022  # Year of historical capacity data
        iso3_df = pd.DataFrame({"ISO-3 Code": list(code_to_irena_region_map.keys())})
        cost_per_country = preprocess_renewable_energy_cost_data(
            iso3_df, code_to_irena_region_map, master_input_path, renewable_input_path
        )
        corrected_capacity_projections = preprocess_renewable_energy_capacity_data(
            base_year, iso3_df, code_to_irena_map, code_to_ssp_region_map, renewable_input_path
        )
        capex_projection = project_capex(cost_per_country, corrected_capacity_projections, base_year)

        # Extract variables in the correct format and save to file
        capex_projection.columns = capex_projection.columns.astype(int)
        projected_cost_per_country = xr.Dataset(
            coords={
                "iso3": list(cost_per_country.index),
                "year": years,
            },
            data_vars={
                "Capex solar": (
                    ("iso3", "year"),
                    capex_projection.xs("solar", level="Technology").loc[:, years].to_numpy() * 1000,  # type: ignore[operator]  # Convert USD/kW to USD/MW
                    {"units": "USD/MW"},
                ),
                "Capex wind": (
                    ("iso3", "year"),
                    capex_projection.xs("wind", level="Technology").loc[:, years].to_numpy() * 1000,  # type: ignore[operator]  # Convert USD/kW to USD/MW
                    {"units": "USD/MW"},
                ),
                "Opex solar": (("iso3",), cost_per_country["Opex solar"].values, {"units": "%"}),
                "Opex wind": (("iso3",), cost_per_country["Opex wind"].values, {"units": "%"}),
                "Opex battery": (("iso3",), cost_per_country["Opex battery"].values, {"units": "%"}),
                "Cost of capital": (("iso3",), cost_per_country["Cost of capital (%)"].values, {"units": "%"}),
            },
        )

        # Save to file
        projected_cost_per_country.to_netcdf(renewables_costs_file, mode="w", format="NETCDF4")

    # Special treatment for the battery CAPEX since all capacities and costs need to be passed until the sampling in
    # the simulation
    storage_costs = preprocess_storage_costs(years, renewable_input_path)

    return projected_cost_per_country, storage_costs, investment_horizon
