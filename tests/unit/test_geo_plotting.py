# tests/unit/test_geo_plotting.py

import xarray as xr
import numpy as np
from steelo.utilities.plotting import plot_geo_layers
from steelo.domain.models import PlotPaths


def test_plot_geo_layers_uses_plot_paths(tmp_path):
    """
    Tests that a dedicated geo plotting function correctly uses the PlotPaths object.
    """
    # Arrange
    plot_paths = PlotPaths(geo_plots_dir=tmp_path)

    # Create a minimal xarray dataset
    lat = np.linspace(-10, 10, 5)
    lon = np.linspace(-20, 20, 5)
    mock_dataset = xr.Dataset(
        {
            "lcoe": (["lat", "lon"], np.random.rand(5, 5) * 200),
            "feasibility_mask": (["lat", "lon"], np.ones((5, 5))),
        },
        coords={"lat": lat, "lon": lon},
    )

    # Act
    plot_geo_layers(mock_dataset, plot_paths=plot_paths, year=2025)

    # Assert
    # Check that plot files were created in the correct directory
    assert (tmp_path / "optimal_lcoe_2025.png").exists()


def test_plot_geo_layers_auto_detects_top5_priority(tmp_path):
    """
    Test that plot_geo_layers auto-detects top5 priority locations.
    """
    # Arrange
    plot_paths = PlotPaths(geo_plots_dir=tmp_path)

    # Create dataset with top5 priority variables
    lat = np.linspace(-10, 10, 5)
    lon = np.linspace(-20, 20, 5)
    mock_dataset = xr.Dataset(
        {
            "lcoe": (["lat", "lon"], np.random.rand(5, 5) * 200),
            "feasibility_mask": (["lat", "lon"], np.ones((5, 5))),
            "top5_iron": (["lat", "lon"], np.random.randint(0, 2, (5, 5))),
            "top5_steel": (["lat", "lon"], np.random.randint(0, 2, (5, 5))),
        },
        coords={"lat": lat, "lon": lon},
    )

    # Act
    plot_geo_layers(mock_dataset, plot_paths=plot_paths, year=2025)

    # Assert
    # Check that priority location plots were created with correct filenames
    assert (tmp_path / "top5_priority_locations_iron.png").exists()
    assert (tmp_path / "top5_priority_locations_steel.png").exists()


def test_plot_geo_layers_auto_detects_top20_priority(tmp_path):
    """
    Test that plot_geo_layers auto-detects top20 priority locations.
    """
    # Arrange
    plot_paths = PlotPaths(geo_plots_dir=tmp_path)

    # Create dataset with top20 priority variables
    lat = np.linspace(-10, 10, 5)
    lon = np.linspace(-20, 20, 5)
    mock_dataset = xr.Dataset(
        {
            "lcoe": (["lat", "lon"], np.random.rand(5, 5) * 200),
            "feasibility_mask": (["lat", "lon"], np.ones((5, 5))),
            "top20_iron": (["lat", "lon"], np.random.randint(0, 2, (5, 5))),
            "top20_steel": (["lat", "lon"], np.random.randint(0, 2, (5, 5))),
        },
        coords={"lat": lat, "lon": lon},
    )

    # Act
    plot_geo_layers(mock_dataset, plot_paths=plot_paths, year=2025)

    # Assert
    # Check that priority location plots were created with correct filenames
    assert (tmp_path / "top20_priority_locations_iron.png").exists()
    assert (tmp_path / "top20_priority_locations_steel.png").exists()


def test_plot_geo_layers_auto_detects_different_percentages(tmp_path):
    """
    Test that plot_geo_layers correctly handles different percentages for iron and steel.
    """
    # Arrange
    plot_paths = PlotPaths(geo_plots_dir=tmp_path)

    # Create dataset with different priority percentages
    lat = np.linspace(-10, 10, 5)
    lon = np.linspace(-20, 20, 5)
    mock_dataset = xr.Dataset(
        {
            "lcoe": (["lat", "lon"], np.random.rand(5, 5) * 200),
            "feasibility_mask": (["lat", "lon"], np.ones((5, 5))),
            "top15_iron": (["lat", "lon"], np.random.randint(0, 2, (5, 5))),
            "top25_steel": (["lat", "lon"], np.random.randint(0, 2, (5, 5))),
        },
        coords={"lat": lat, "lon": lon},
    )

    # Act
    plot_geo_layers(mock_dataset, plot_paths=plot_paths, year=2025)

    # Assert
    # Check that plots were created with correct percentages
    assert (tmp_path / "top15_priority_locations_iron.png").exists()
    assert (tmp_path / "top25_priority_locations_steel.png").exists()


def test_plot_geo_layers_no_priority_variables(tmp_path):
    """
    Test that plot_geo_layers handles datasets without priority location variables gracefully.
    """
    # Arrange
    plot_paths = PlotPaths(geo_plots_dir=tmp_path)

    # Create dataset without priority variables
    lat = np.linspace(-10, 10, 5)
    lon = np.linspace(-20, 20, 5)
    mock_dataset = xr.Dataset(
        {
            "lcoe": (["lat", "lon"], np.random.rand(5, 5) * 200),
            "feasibility_mask": (["lat", "lon"], np.ones((5, 5))),
        },
        coords={"lat": lat, "lon": lon},
    )

    # Act - should not raise an error
    plot_geo_layers(mock_dataset, plot_paths=plot_paths, year=2025)

    # Assert
    # LCOE plot should exist, but no priority plots
    assert (tmp_path / "optimal_lcoe_2025.png").exists()
    # Priority plots should not exist
    priority_plots = list(tmp_path.glob("top*_priority_locations_*.png"))
    assert len(priority_plots) == 0
