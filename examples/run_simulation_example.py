"""
Example: Running Steel Model Simulation with Custom Configuration

This example demonstrates how to run the steel model simulation
using SimulationConfig with custom paths and parameters.
"""

from pathlib import Path
from steelo.simulation import SimulationConfig, SimulationRunner
from steelo.domain import Year
import logging


# Example 1: Basic simulation with default paths
def run_basic_simulation():
    """Run a basic simulation with mostly default settings."""
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        log_level=logging.INFO,
    )

    runner = SimulationRunner(config)
    results = runner.run()

    logging.info("Simulation completed!")
    logging.info(f"Final prices: {results.get('price', {})}")
    logging.info(f"Production levels: {results.get('production', {})}")


# Example 2: Custom paths simulation
def run_custom_paths_simulation():
    """Run simulation with custom input/output paths."""
    # Define custom paths
    my_output_dir = Path("./my_simulation_outputs")
    my_data_dir = Path("./my_custom_data")

    config = SimulationConfig(
        # Custom output paths
        output_dir=my_output_dir,
        plots_dir=my_output_dir / "custom_plots",
        # Custom input data paths
        plants_json_path=my_data_dir / "my_plants.json",
        demand_center_xlsx=my_data_dir / "my_demand.xlsx",
        location_csv=my_data_dir / "my_locations.csv",
        cost_of_x_csv=my_data_dir / "my_cost_of_x.json",
        # Time configuration
        start_year=Year(2025),
        end_year=Year(2035),
        # Other parameters
        scrap_generation_scenario="high_recycling",
        log_level=logging.DEBUG,
    )

    runner = SimulationRunner(config)
    results = runner.run()

    logging.info(f"Results saved to: {config.output_dir}")
    return results


# Example 3: Technology-constrained simulation
def run_technology_constrained_simulation():
    """Run simulation with specific technology constraints."""
    from steelo.simulation_types import get_default_technology_settings, TechnologySettings

    # Create technology settings with specific constraints
    tech_settings = get_default_technology_settings()

    # Ban blast furnaces by setting allowed=False
    tech_settings["BF"] = TechnologySettings(allowed=False, from_year=2025, to_year=None)

    # Allow hydrogen DRI only from 2030
    tech_settings["DRIH2"] = TechnologySettings(allowed=True, from_year=2030, to_year=None)

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2040),
        technology_settings=tech_settings,
        # Output configuration
        output_dir=Path("./tech_constrained_outputs"),
        log_level=logging.WARNING,
    )

    runner = SimulationRunner(config)
    results = runner.run()

    logging.info("Technology-constrained simulation completed!")
    return results


# Example 4: Jupyter Notebook usage
def notebook_example():
    """
    Example for use in Jupyter notebooks with progress tracking.

    In a notebook cell:
    ```python
    from pathlib import Path
    from steelo.simulation import SimulationConfig, SimulationRunner
    from steelo.domain import Year
    from IPython.display import display, HTML
    import logging

    # Define progress callback for notebook display
    def progress_callback(progress):
        display(HTML(f"<b>Year {progress.current_year} of {progress.end_year}</b>"))

    # Configure simulation
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        output_dir=Path("./notebook_outputs"),
        log_level=logging.INFO,
    )

    # Run with progress tracking
    runner = SimulationRunner(
        config,
        progress_callback=progress_callback
    )
    results = runner.run()

    # Visualize results
    import pandas as pd
    import matplotlib.pyplot as plt

    # Plot capacity over time
    capacity_df = pd.DataFrame(results['capacity'])
    capacity_df.plot(title="Steel Capacity Over Time")
    plt.show()
    ```
    """
    pass


# Example 5: Programmatic configuration from data preparation
def run_from_data_preparation():
    """
    Example running simulation using paths from a data preparation process.
    This mimics how Django might set up paths.
    """
    # Simulate getting paths from a data preparation system
    prepared_data_dir = Path("/path/to/prepared/data")
    media_root = Path("/path/to/media")

    # Create config using prepared paths
    config = SimulationConfig(
        # All input paths point to prepared data
        plants_json_path=prepared_data_dir / "plants.json",
        demand_centers_json_path=prepared_data_dir / "demand_centers.json",
        suppliers_json_path=prepared_data_dir / "suppliers.json",
        plant_groups_json_path=prepared_data_dir / "plant_groups.json",
        tariffs_json_path=prepared_data_dir / "tariffs.json",
        carbon_costs_json_path=prepared_data_dir / "carbon_costs.json",
        # Output goes to media directory
        output_dir=media_root / "model_runs" / "run_123",
        # Simulation parameters
        start_year=Year(2025),
        end_year=Year(2050),
        # Note: Use technology_settings to control technology availability
    )

    runner = SimulationRunner(config)
    results = runner.run()

    return results


if __name__ == "__main__":
    # Run the basic example
    logging.info("Running basic simulation example...")
    run_basic_simulation()

    # Uncomment to run other examples:
    # run_custom_paths_simulation()
    # run_technology_constrained_simulation()
