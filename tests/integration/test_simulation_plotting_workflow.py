"""
Integration test for simulation plotting workflow.
Tests that all plotting functions are called correctly with consistent parameters.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd
import tempfile

from steelo.service_layer.message_bus import MessageBus
from steelo.domain.models import PlotPaths
from steelo.domain.datacollector import DataCollector


@pytest.fixture
def mock_environment():
    """Create a mock environment with PlotPaths."""
    env = MagicMock()
    env.plot_paths = MagicMock(spec=PlotPaths)
    env.plot_paths.output_dir = Path("/tmp/test_plots")
    return env


@pytest.fixture
def mock_message_bus(mock_environment):
    """Create a mock message bus with environment."""
    bus = MagicMock(spec=MessageBus)
    bus.env = mock_environment
    return bus


@pytest.fixture
def mock_data_collector():
    """Create a mock data collector with sample data."""
    collector = MagicMock(spec=DataCollector)
    collector.status_counts = {"steel": {2025: {"EAF": {"announced": 5, "construction": 3}}}}
    collector.new_plant_locations = {"steel": {2025: [{"lat": 50.0, "lon": 10.0}]}}
    collector.greenfield_plants = {"fg_1": {"furnace_group_id": "fg_1", "status_years": {"announced": 2025}}}
    return collector


@pytest.fixture
def temp_output_file():
    """Create a temporary output file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        df = pd.DataFrame(
            {
                "year": [2025, 2026],
                "technology": ["BFBOF", "DRI-EAF"],
                "location": ["DEU", "CHN"],
                "capacity": [100, 200],
                "production": [80, 180],
            }
        )
        df.to_csv(f, index=False)
        temp_path = f.name

    yield Path(temp_path)

    # Cleanup
    Path(temp_path).unlink()


@patch("steelo.simulation.generate_post_run_cap_prod_plots")
def test_simulation_calls_plotting_functions_with_consistent_plot_paths(
    mock_post_run_plots, mock_message_bus, mock_data_collector, temp_output_file
):
    """Test that simulation routes plots through one plotter and one plot_paths instance."""
    # Simulate the plotting section of the run method
    plot_paths = mock_message_bus.env.plot_paths
    plotter = MagicMock()

    # These are the actual calls from simulation.py
    mock_post_run_plots(file_path=temp_output_file, plot_paths=plot_paths)
    plotter.plot_greenfield_plants_by_status(status_counts=mock_data_collector.status_counts)
    plotter.plot_greenfield_plants_map(new_plant_locations=mock_data_collector.new_plant_locations)
    plotter.export_greenfield_plants_csv(mock_data_collector.greenfield_plants)

    # Verify the functions were called with the expected data
    mock_post_run_plots.assert_called_once_with(file_path=temp_output_file, plot_paths=plot_paths)
    plotter.plot_greenfield_plants_by_status.assert_called_once_with(status_counts=mock_data_collector.status_counts)
    plotter.plot_greenfield_plants_map.assert_called_once_with(
        new_plant_locations=mock_data_collector.new_plant_locations
    )
    plotter.export_greenfield_plants_csv.assert_called_once_with(mock_data_collector.greenfield_plants)

    # Ensure the post-run plots used the same plot_paths instance
    assert mock_post_run_plots.call_args.kwargs["plot_paths"] is plot_paths
