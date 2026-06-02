"""
Unit tests for postprocessing plotting functions.
These tests ensure that all plotting functions accept consistent parameters.
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import tempfile
from unittest.mock import patch, MagicMock

from steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots import (
    generate_post_run_cap_prod_plots,
)
from steelo.domain.models import PlotPaths


@pytest.fixture
def sample_output_df():
    """Create sample output dataframe for testing."""
    years = [2025, 2026]
    technologies = ["BFBOF", "DRI-EAF"]
    locations = ["DEU", "CHN", "USA", "IND"]

    data = []
    for year in years:
        for tech in technologies:
            for loc in locations:
                data.append(
                    {
                        "year": year,
                        "technology": tech,
                        "iso3": loc,
                        "product": "steel",
                        "capacity": np.random.randint(100, 500),
                        "production": np.random.randint(80, 450),
                        "production_cost": np.random.uniform(300, 600),
                        "furnace_group_id": f"FG_{tech}_{loc}_{year}",
                    }
                )

    return pd.DataFrame(data)


@pytest.fixture
def temp_csv_file(sample_output_df):
    """Create a temporary CSV file with sample data."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        sample_output_df.to_csv(f, index=False)
        temp_path = f.name

    yield temp_path

    # Cleanup
    Path(temp_path).unlink()


@pytest.fixture
def mock_plot_paths():
    """Create a mock PlotPaths object."""
    mock_paths = MagicMock(spec=PlotPaths)
    mock_paths.output_dir = Path("/tmp/test_plots")
    return mock_paths


@patch("steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.SteelPlotter")
def test_generate_post_run_cap_prod_plots_calls_all_functions_with_plot_paths(
    mock_plotter_class, temp_csv_file, mock_plot_paths
):
    """Test that generate_post_run_cap_prod_plots passes plot_paths to all plotting functions."""
    mock_plotter_instance = mock_plotter_class.return_value

    generate_post_run_cap_prod_plots(
        temp_csv_file,
        capacity_limit=0.95,
        steel_demand=1000,
        iron_demand=800,
        steel_market_clearing_share=0.95,
        iron_market_clearing_share=0.95,
        steel_price_buffer=200.0,
        iron_price_buffer=200.0,
        steel_demand_by_year={2025: 1000.0, 2026: 1000.0},
        plot_paths=mock_plot_paths,
    )

    # Verify SteelPlotter was instantiated with plot_paths
    mock_plotter_class.assert_called_once()
    assert mock_plotter_class.call_args.kwargs["plot_paths"] == mock_plot_paths

    # Verify SteelPlotter methods were called
    assert mock_plotter_instance.plot_capacity_development_by_technology.call_count == 2
    assert mock_plotter_instance.plot_area_chart_by_region_or_technology.call_count == 8

    # plot_cost_curve_step is called multiple times (for different years/products/aggregations)
    assert mock_plotter_instance.plot_cost_curve_step.call_count >= 1


@patch("steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.SteelPlotter")
def test_generate_post_run_cap_prod_plots_works_without_plot_paths(mock_plotter_class, temp_csv_file):
    """Test that generate_post_run_cap_prod_plots works when plot_paths is None."""
    mock_plotter_instance = mock_plotter_class.return_value

    generate_post_run_cap_prod_plots(
        temp_csv_file,
        capacity_limit=0.95,
        steel_demand=1000,
        iron_demand=800,
        steel_market_clearing_share=0.95,
        iron_market_clearing_share=0.95,
        steel_price_buffer=200.0,
        iron_price_buffer=200.0,
        steel_demand_by_year={2025: 1000.0, 2026: 1000.0},
    )

    # Verify SteelPlotter was instantiated with plot_paths=None
    mock_plotter_class.assert_called_once()
    assert mock_plotter_class.call_args.kwargs["plot_paths"] is None

    # Verify SteelPlotter methods were called
    assert mock_plotter_instance.plot_capacity_development_by_technology.call_count == 2
    assert mock_plotter_instance.plot_area_chart_by_region_or_technology.call_count == 8

    # plot_cost_curve_step is called multiple times (for different years/products/aggregations)
    assert mock_plotter_instance.plot_cost_curve_step.call_count >= 1


def test_plotting_function_signatures():
    """Test that all plotting functions have consistent signatures with plot_paths parameter."""
    from steelo.utilities.plotting import (
        plot_added_capacity_by_technology,
        plot_year_on_year_technology_development,
        plot_area_chart_of_column_by_region_or_technology,
    )

    # Check that all functions accept plot_paths parameter
    import inspect

    functions_to_check = [
        plot_added_capacity_by_technology,
        plot_year_on_year_technology_development,
        plot_area_chart_of_column_by_region_or_technology,
    ]

    for func in functions_to_check:
        sig = inspect.signature(func)
        params = sig.parameters
        assert "plot_paths" in params, f"{func.__name__} should accept plot_paths parameter"

        # Check that plot_paths has a default value of None
        plot_paths_param = params["plot_paths"]
        assert plot_paths_param.default is None, f"{func.__name__} plot_paths should default to None"
