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
                        "location": loc,
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


@patch("steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.plot_added_capacity_by_technology")
@patch("steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.plot_year_on_year_technology_development")
@patch("steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.plot_cost_curve_step_from_dataframe")
@patch(
    "steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.plot_area_chart_of_column_by_region_or_technology"
)
def test_generate_post_run_cap_prod_plots_calls_all_functions_with_plot_paths(
    mock_area_chart, mock_cost_curve, mock_year_on_year, mock_added_capacity, temp_csv_file, mock_plot_paths
):
    """Test that generate_post_run_cap_prod_plots passes plot_paths to all plotting functions."""
    # Call the function with plot_paths and mock iso3_to_region_map
    mock_iso3_to_region_map = {"USA": "Americas", "DEU": "Europe"}
    generate_post_run_cap_prod_plots(
        temp_csv_file,
        capacity_limit=0.95,
        steel_demand=1000,
        iron_demand=800,
        iso3_to_region_map=mock_iso3_to_region_map,
        plot_paths=mock_plot_paths,
    )

    # Verify that all plotting functions were called with plot_paths
    mock_added_capacity.assert_called_once()
    assert mock_added_capacity.call_args.kwargs["plot_paths"] == mock_plot_paths

    mock_year_on_year.assert_called_once()
    assert mock_year_on_year.call_args.kwargs["plot_paths"] == mock_plot_paths

    # plot_cost_curve is called multiple times (for different years/products)
    assert mock_cost_curve.call_count >= 1
    for call_args in mock_cost_curve.call_args_list:
        assert call_args.kwargs["plot_paths"] == mock_plot_paths

    # plot_area_chart_of_column_by_region is called 8 times
    assert mock_area_chart.call_count == 8
    for call_args in mock_area_chart.call_args_list:
        assert call_args.kwargs["plot_paths"] == mock_plot_paths


@patch("steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.plot_added_capacity_by_technology")
@patch("steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.plot_year_on_year_technology_development")
@patch("steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.plot_cost_curve_step_from_dataframe")
@patch(
    "steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots.plot_area_chart_of_column_by_region_or_technology"
)
def test_generate_post_run_cap_prod_plots_works_without_plot_paths(
    mock_area_chart, mock_cost_curve, mock_year_on_year, mock_added_capacity, temp_csv_file
):
    """Test that generate_post_run_cap_prod_plots works when plot_paths is None."""
    # Call the function without plot_paths but with mock iso3_to_region_map
    mock_iso3_to_region_map = {"USA": "Americas", "DEU": "Europe"}
    generate_post_run_cap_prod_plots(
        temp_csv_file,
        capacity_limit=0.95,
        steel_demand=1000,
        iron_demand=800,
        iso3_to_region_map=mock_iso3_to_region_map,
    )

    # Verify that all plotting functions were called with plot_paths=None
    mock_added_capacity.assert_called_once()
    assert mock_added_capacity.call_args.kwargs["plot_paths"] is None

    mock_year_on_year.assert_called_once()
    assert mock_year_on_year.call_args.kwargs["plot_paths"] is None

    # plot_cost_curve is called multiple times (for different years/products)
    assert mock_cost_curve.call_count >= 1
    for call_args in mock_cost_curve.call_args_list:
        assert call_args.kwargs["plot_paths"] is None

    # plot_area_chart_of_column_by_region is called 8 times
    assert mock_area_chart.call_count == 8
    for call_args in mock_area_chart.call_args_list:
        assert call_args.kwargs["plot_paths"] is None


def test_plotting_function_signatures():
    """Test that all plotting functions have consistent signatures with plot_paths parameter."""
    from steelo.utilities.plotting import (
        plot_added_capacity_by_technology,
        plot_year_on_year_technology_development,
        plot_cost_curve_step_from_dataframe,
        plot_area_chart_of_column_by_region_or_technology,
    )

    # Check that all functions accept plot_paths parameter
    import inspect

    functions_to_check = [
        plot_added_capacity_by_technology,
        plot_year_on_year_technology_development,
        plot_cost_curve_step_from_dataframe,
        plot_area_chart_of_column_by_region_or_technology,
    ]

    for func in functions_to_check:
        sig = inspect.signature(func)
        params = sig.parameters
        assert "plot_paths" in params, f"{func.__name__} should accept plot_paths parameter"

        # Check that plot_paths has a default value of None
        plot_paths_param = params["plot_paths"]
        assert plot_paths_param.default is None, f"{func.__name__} plot_paths should default to None"
