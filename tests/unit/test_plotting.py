"""
Unit tests for plotting functions.
These tests use minimal sample data to quickly verify plotting functions work correctly.
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import tempfile

from steelo.utilities.plotting import (
    plot_added_capacity_by_technology,
    plot_year_on_year_technology_development,
    plot_cost_curve_step_from_dataframe,
    plot_area_chart_of_column_by_region_or_technology,
    _copy_deckgl_to_output_dir,
)
from steelo.domain.models import PlotPaths


@pytest.fixture
def sample_output_df():
    """Create sample output dataframe that mimics real simulation output."""
    # Create sample data for years 2025-2030
    years = [2025, 2026, 2027, 2028, 2029, 2030]
    technologies = ["BFBOF", "DRI-EAF", "Scrap-EAF"]
    regions = ["Europe", "China", "India", "North America"]
    locations = ["DEU", "CHN", "IND", "USA"] * 3  # Map to regions

    data = []
    for year in years:
        for i, tech in enumerate(technologies):
            for j, region in enumerate(regions):
                data.append(
                    {
                        "year": year,
                        "furnace_type": tech,
                        "technology": tech,
                        "region": region,
                        "location": locations[j],
                        "product": "steel",
                        "capacity": np.random.randint(100, 500),
                        "production": np.random.randint(80, 450),
                        "direct_emissions": np.random.randint(50, 200),
                        "unit_vopex": np.random.uniform(100, 300),
                        "unit_fopex": np.random.uniform(20, 50),
                        "unit_production_cost": np.random.uniform(150, 400),
                        "furnace_group_id": f"FG_{i}_{j}_{year}",
                        "cumulative_capacity": np.random.randint(1000, 5000),
                    }
                )

    return pd.DataFrame(data)


@pytest.fixture
def temp_plot_dir():
    """Create temporary directory for test plots."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir)
        # Create subdirectories for plots
        (temp_path / "pam").mkdir(parents=True, exist_ok=True)
        (temp_path / "geo").mkdir(parents=True, exist_ok=True)
        yield temp_path


def test_plot_added_capacity_by_technology(sample_output_df, temp_plot_dir):
    """Test added capacity by technology plot."""
    # Create PlotPaths object for the test
    plot_paths = PlotPaths(pam_plots_dir=temp_plot_dir / "pam")

    # Should not raise any exceptions
    plot_added_capacity_by_technology(sample_output_df, units="Mtpa", plot_paths=plot_paths)

    # Check that plot file was created
    plot_file = temp_plot_dir / "pam" / "year2year_added_capacity_by_technology.png"
    assert plot_file.exists(), f"Plot file not created at {plot_file}"


def test_plot_year_on_year_technology_development(sample_output_df, temp_plot_dir):
    """Test year-on-year technology development plot."""
    # Create PlotPaths object for the test
    plot_paths = PlotPaths(pam_plots_dir=temp_plot_dir / "pam")

    plot_year_on_year_technology_development(sample_output_df, units="Mtpa", plot_paths=plot_paths)

    plot_file = temp_plot_dir / "pam" / "Capacity_development_by_technology.png"
    assert plot_file.exists(), f"Plot file not created at {plot_file}"


def test_plot_cost_curve_with_missing_year(sample_output_df, temp_plot_dir):
    """Test cost curve plot handles missing years gracefully."""
    # Create PlotPaths object for the test
    plot_paths = PlotPaths(pam_plots_dir=temp_plot_dir / "pam")

    # Test with a year that doesn't exist (2035)
    plot_cost_curve_step_from_dataframe(
        sample_output_df,
        "steel",
        product_demand=1000,
        year=2035,
        capacity_limit=0.95,
        units="Mt",
        plot_paths=plot_paths,
    )

    # Should create plot with closest available year (2030)
    plot_files = list((temp_plot_dir / "pam").glob("steel_cost_curve_by_*.png"))
    assert len(plot_files) > 0, "No cost curve plot created"
    assert "2030" in plot_files[0].name, "Should use year 2030 when 2035 not available"


def test_plot_cost_curve_with_existing_year(sample_output_df, temp_plot_dir):
    """Test cost curve plot with existing year."""
    # Create PlotPaths object for the test
    plot_paths = PlotPaths(pam_plots_dir=temp_plot_dir / "pam")

    plot_cost_curve_step_from_dataframe(
        sample_output_df,
        "steel",
        product_demand=1000,
        year=2028,
        capacity_limit=0.95,
        units="Mt",
        plot_paths=plot_paths,
    )

    plot_file = temp_plot_dir / "pam" / "steel_cost_curve_by_region_2028.png"
    assert plot_file.exists(), f"Plot file not created at {plot_file}"


def test_plot_area_chart_by_region(sample_output_df, temp_plot_dir):
    """Test area chart plotting by region."""
    # Create PlotPaths object for the test
    plot_paths = PlotPaths(pam_plots_dir=temp_plot_dir / "pam")

    plot_area_chart_of_column_by_region_or_technology(
        dataframe=sample_output_df,
        column_name="production",
        title="Steel Production Volume by Region",
        units="Mtpa",
        pivot_columns=["region"],
        product_type="steel",
        plot_paths=plot_paths,
    )

    plot_file = temp_plot_dir / "pam" / "steel_production_development_by_region.png"
    assert plot_file.exists(), f"Plot file not created at {plot_file}"


def test_plot_with_empty_dataframe(temp_plot_dir):
    """Test plots handle empty dataframes gracefully."""
    empty_df = pd.DataFrame()
    plot_paths = PlotPaths(pam_plots_dir=temp_plot_dir / "pam")

    # These should not crash, but might not create files
    try:
        plot_added_capacity_by_technology(empty_df, units="Mtpa", plot_paths=plot_paths)
    except ValueError as e:
        # Should handle gracefully - check for missing columns error
        assert "must contain" in str(e).lower()


def test_plot_with_missing_columns(temp_plot_dir):
    """Test plots handle missing columns gracefully."""
    incomplete_df = pd.DataFrame(
        {
            "year": [2025, 2026],
            "technology": ["BFBOF", "DRI-EAF"],
            # Missing other required columns
        }
    )
    plot_paths = PlotPaths(pam_plots_dir=temp_plot_dir / "pam")

    # Should not crash completely
    with pytest.raises(ValueError):
        plot_added_capacity_by_technology(incomplete_df, units="Mtpa", plot_paths=plot_paths)


def test_copy_deckgl_to_output_dir(temp_plot_dir):
    """Test that deck.gl is copied to output directory for standalone HTML files."""
    # Call the function to copy deck.gl
    result_path = _copy_deckgl_to_output_dir(temp_plot_dir)

    # Check that it returns a relative path
    assert result_path is not None, "Should return a path when vendor file exists"
    assert result_path == "./deck.gl.min.js", f"Expected './deck.gl.min.js', got {result_path}"

    # Check that the file was actually copied
    copied_file = temp_plot_dir / "deck.gl.min.js"
    assert copied_file.exists(), f"Deck.gl file should be copied to {copied_file}"

    # Check that it's not empty (deck.gl should be ~1.5MB)
    assert copied_file.stat().st_size > 100000, "Deck.gl file should be substantial in size (>100KB)"


def test_copy_deckgl_with_nonexistent_directory():
    """Test deck.gl copy handles nonexistent directory gracefully."""
    nonexistent_dir = Path("/tmp/nonexistent_test_dir_12345")

    # Should return None if directory doesn't exist (graceful degradation)
    result = _copy_deckgl_to_output_dir(nonexistent_dir)
    assert result is None, "Should return None when output directory doesn't exist"


if __name__ == "__main__":
    # Allow running tests directly
    pytest.main([__file__, "-v"])
