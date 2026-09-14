"""In-run diagnostic plots: time series, design distributions, regional maps.

Distinct from ``boa.postprocessing.plots``, which renders the client-facing charts.
"""

import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import geopandas as gpd
from pathlib import Path
from typing import Optional

from boa.config.paths import PathConfig


def plot_time_series(profile: dict[str, np.ndarray], output_path: Optional[Path] = None) -> None:
    """Time series of `profile`'s solar and wind arrays. Saved to `output_path` if given, else shown."""
    fig, axes = plt.subplots(2, 1, figsize=(20, 6))

    axes[0].plot(profile["solar"])
    axes[0].set_title("Solar Profile")
    axes[0].set_xlabel("Time (hours)")
    axes[0].set_ylabel("Power Output (normalized)")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(profile["wind"])
    axes[1].set_title("Wind Profile")
    axes[1].set_xlabel("Time (hours)")
    axes[1].set_ylabel("Power Output (normalized)")
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
    else:
        plt.show()
        plt.close()


def plot_design_distributions(designs: list[dict[str, float]], output_path: Optional[Path] = None) -> None:
    """Histograms of solar/wind/battery overscale factors across `designs`. Saved to `output_path`
    if given, else shown."""
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    design_keys = ["wind", "solar", "battery"]

    for ax, key in zip(axes, design_keys):
        ax.hist([design[key] for design in designs], bins=30, edgecolor="black", alpha=0.7)
        ax.set_xlabel(key)
        ax.set_title(f"{key} overscale factor")
        ax.grid(True, alpha=0.3)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
    else:
        plt.show()
        plt.close()


def plot_state_of_charge(
    opt_soc: np.ndarray,
    output_path: Optional[Path] = None,
) -> None:
    """Histogram of `opt_soc`, the optimal design's state of charge. Saved to `output_path` if
    given, else shown."""
    fig, ax = plt.subplots(figsize=(8, 5))

    ax.hist(opt_soc, bins=30, edgecolor="black", alpha=0.7)
    ax.set_title("Battery State of Charge (Optimal Design)")
    ax.set_xlabel("State of Charge (MWh)")
    ax.set_ylabel("Frequency")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
    else:
        plt.show()
        plt.close()


def plot_cost_scatter(
    lcoe_costs: list[float],
    designs: list[dict[str, float]],
    opt_design: dict[str, float],
    installation_costs: Optional[list[float]] = None,
    output_path: Optional[Path] = None,
) -> None:
    """
    Scatter plots comparing all accepted designs by LCOE, and by installation cost if
    `installation_costs` is given. `opt_design` is marked in red. Saved to `output_path` if
    given, else shown.
    """
    # Determine number of subplots based on whether installation_costs is provided
    n_plots = 2 if installation_costs is not None else 1
    plt.figure(figsize=(12, 5) if n_plots == 2 else (8, 5))

    # LCOE scatter plot
    plt.subplot(1, n_plots, 1)
    scatter = plt.scatter(
        [design["solar"] for design in designs],
        [design["wind"] for design in designs],
        c=lcoe_costs,
        cmap="viridis",
        alpha=0.7,
    )
    plt.colorbar(scatter, label="LCOE ($/MWh)")
    plt.xlabel("Solar Overscale Factor")
    plt.ylabel("Wind Overscale Factor")
    plt.title("LCOE vs. Overscale Factors")
    plt.scatter(
        opt_design["solar"],
        opt_design["wind"],
        marker="o",
        s=100,
        color="red",
        edgecolors="black",
        linewidths=2,
        label="Optimum",
        zorder=5,
    )
    plt.legend()
    plt.grid(True, alpha=0.3)

    # Installation cost scatter plot (if provided)
    if installation_costs is not None:
        plt.subplot(1, n_plots, 2)
        scatter = plt.scatter(
            [design["solar"] for design in designs],
            [design["wind"] for design in designs],
            c=installation_costs,
            cmap="plasma",
            alpha=0.7,
        )
        plt.colorbar(scatter, label="Installation Cost ($)")
        plt.xlabel("Solar Overscale Factor")
        plt.ylabel("Wind Overscale Factor")
        plt.title("Installation Cost vs. Overscale Factors")
        plt.scatter(
            opt_design["solar"],
            opt_design["wind"],
            marker="o",
            s=100,
            color="red",
            edgecolors="black",
            linewidths=2,
            label="Optimum",
            zorder=5,
        )
        plt.legend()
        plt.grid(True, alpha=0.3)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
    else:
        plt.show()
        plt.close()


def plot_regional_optimum_baseload_power_simulation_map(
    year: int, region: str, coverage: float, load_density: float, path_config: PathConfig, weather_year: int
):
    """
    Plot the results of the baseload power simulation for a single region: LCOE and optimal design.
    """

    plots_path = path_config.map_plots_dir(weather_year, load_density, coverage, region)
    plots_path.mkdir(parents=True, exist_ok=True)
    optimal_sol = xr.open_dataset(path_config.optimal_sol_path(weather_year, load_density, coverage, region, year))
    optimal_sol = optimal_sol.where(optimal_sol != 0)

    # Load country boundaries
    geo_boundaries = gpd.read_file(path_config.subunits_50m_shapefile_path)

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
        plt.savefig(plots_path / f"{var}_{region}_{year}_cov{coverage:g}.png", dpi=300)
        plt.close()


def plot_global_optimum_baseload_power_simulation_map(
    optimal_sol: xr.Dataset,
    year: int,
    coverage: float,
    load_density: float,
    path_config: PathConfig,
    weather_year: int,
):
    """
    Plot the global results of the baseload power simulation: LCOE and optimal design.
    """

    plots_path = path_config.map_plots_dir(weather_year, load_density, coverage, "GLOBAL")
    plots_path.mkdir(parents=True, exist_ok=True)
    optimal_sol = optimal_sol.where(optimal_sol != 0)

    # Load country boundaries
    geo_boundaries = gpd.read_file(path_config.subunits_50m_shapefile_path)

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
        plt.title(f"Optimal {var} for {year} at {coverage * 100:g}% coverage")
        plt.savefig(plots_path / f"{var}_GLOBAL_{year}_cov{coverage:g}.png", dpi=300)
        plt.close()
