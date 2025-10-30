import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import geopandas as gpd

from baseload_optimisation_atlas.boa_config import BaseloadPowerConfig


def plot_design_distributions(designs: list[dict[str, float]]) -> None:
    """
    Plot histograms showing distribution of wind, solar, and battery overscale factors across designs.

    Args:
        designs: List of system designs with wind, solar, and battery overscale factors

    Side Effects:
        Displays plot with 3 histogram subplots
    """
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    design_keys = ["wind", "solar", "battery"]

    for ax, key in zip(axes, design_keys):
        ax.hist([design[key] for design in designs], bins=30, edgecolor="black", alpha=0.7)
        ax.set_xlabel(key)
        ax.set_title(f"{key} overscale factor")

    plt.tight_layout()
    plt.show()
    plt.close()


def plot_state_of_charge_and_cost(
    costs: list[float],
    designs: list[dict[str, float]],
    opt_design: dict[str, float],
    opt_soc: np.ndarray,
) -> None:
    """
    Plot battery state of charge histogram and cost scatter plot comparing all accepted designs with optimal design highlighted.

    Args:
        costs: List of costs for each design
        designs: List of design dictionaries with solar, wind, battery factors
        opt_design: Optimal design dictionary
        opt_soc: Pre-computed state of charge array for optimal design (MWh)

    Side Effects:
        Displays plot with 2 subplots: state of charge histogram and cost vs overscale factors scatter plot (optimal design marked in red)
    """
    plt.figure(figsize=(12, 4))
    plt.subplot(1, 2, 1)
    plt.hist(opt_soc, bins=30)
    plt.title("Battery State of Charge of optimal design (MWh)")

    plt.subplot(1, 2, 2)
    scatter = plt.scatter(
        [design["solar"] for design in designs],
        [design["wind"] for design in designs],
        c=costs,
        cmap="viridis",
        alpha=0.7,
    )
    plt.colorbar(scatter, label="Total Cost")
    plt.xlabel("F_s (Solar Overscale Factor)")
    plt.ylabel("F_w (Wind Overscale Factor)")
    plt.title("Cost vs. Sampled Overscale Factors for Wind and Solar")

    # Mark the optimal point
    plt.scatter(opt_design["solar"], opt_design["wind"], marker="o", color="red", label="Optimum")
    plt.legend()

    plt.tight_layout()
    plt.show()
    plt.close()


def plot_regional_optimum_baseload_power_simulation_map(year: int, region: str, p: int, config: BaseloadPowerConfig):
    """
    Plot baseload power simulation results for a single region showing LCOE and optimal design factors.

    Args:
        year: Investment year
        region: Region name
        p: Percentile threshold for demand coverage
        config: Baseload power configuration

    Side Effects:
        Saves 4 plots (lcoe, solar_factor, wind_factor, battery_factor)
    """

    plots_path = config.geo_plots_dir / "baseload_power_simulation" / f"p{str(p)}" / region
    plots_path.mkdir(parents=True, exist_ok=True)
    optimal_sol = xr.open_dataset(
        config.geo_output_dir
        / "baseload_power_simulation"
        / f"p{str(p)}"
        / region
        / f"optimal_sol_{region}_{str(year)}_p{str(p)}.nc"
    )
    optimal_sol = optimal_sol.where(optimal_sol != 0)

    # Load country boundaries
    geo_boundaries = gpd.read_file(config.countries_shapefile_path)

    for var in ["lcoe", "solar_factor", "wind_factor", "battery_factor"]:
        lat_lon_ratio = len(optimal_sol.lat) / len(optimal_sol.lon)
        fig, ax = plt.subplots(figsize=(10, 10 * lat_lon_ratio))
        # Adapt the colorbar range for LCOE
        if var == "lcoe":
            vmin, vmax = 0, 200
        else:
            vmin, vmax = optimal_sol[var].min().item(), optimal_sol[var].max().item()
        optimal_sol[var].plot(vmin=vmin, vmax=vmax, ax=ax)  # type: ignore[call-arg]
        geo_boundaries.plot(ax=ax, edgecolor="black", facecolor="none", linewidth=0.5)
        plt.title(f"Optimal {var}")
        plt.savefig(plots_path / f"{var}_{region}_{str(year)}_p{str(p)}.png", dpi=300)
        plt.close()


def plot_global_optimum_baseload_power_simulation_map(
    optimal_sol: xr.Dataset, year: int, p: int, config: BaseloadPowerConfig
):
    """
    Plot global baseload power simulation results showing LCOE and optimal design factors.

    Args:
        optimal_sol: Global optimal solution dataset
        year: Investment year
        p: Percentile threshold for demand coverage
        config: Baseload power configuration

    Side Effects:
        Saves 4 plots (lcoe, solar_factor, wind_factor, battery_factor)
    """

    plots_path = config.geo_plots_dir / "baseload_power_simulation" / f"p{str(p)}" / "GLOBAL"
    plots_path.mkdir(parents=True, exist_ok=True)
    optimal_sol = optimal_sol.where(optimal_sol != 0)

    # Load country boundaries
    geo_boundaries = gpd.read_file(config.countries_shapefile_path)

    for var in ["lcoe", "solar_factor", "wind_factor", "battery_factor"]:
        lat_lon_ratio = len(optimal_sol.lat) / len(optimal_sol.lon)
        fig, ax = plt.subplots(figsize=(10, 10 * lat_lon_ratio))

        # Adapt the colorbar range for LCOE
        if var == "lcoe":
            vmin, vmax = 0, 200
        else:
            vmin, vmax = optimal_sol[var].min().item(), optimal_sol[var].max().item()
        optimal_sol[var].plot(vmin=vmin, vmax=vmax, ax=ax)  # type: ignore[call-arg]
        geo_boundaries.plot(ax=ax, edgecolor="black", facecolor="none", linewidth=0.5)
        plt.title(f"Optimal {var} for {year} at {str(100 - p)}% coverage")
        plt.savefig(plots_path / f"{var}_GLOBAL_{str(year)}_p{str(p)}.png", dpi=300)
        plt.close()
