"""Test GeoDataPaths integration."""

from pathlib import Path

from steelo.simulation_types import get_default_technology_settings

from steelo.simulation import SimulationConfig
from steelo.domain.models import GeoDataPaths


def test_geo_data_paths_creation():
    """Test that GeoDataPaths can be created properly."""
    geo_paths = GeoDataPaths(
        data_dir=Path("/test/data"),
        atlite_dir=Path("/test/data/atlite"),
        geo_plots_dir=Path("/test/data/output/plots/GEO"),
        terrain_nc_path=Path("/test/data/terrain.nc"),
        rail_distance_nc_path=Path("/test/data/rail_distance.nc"),
        railway_capex_csv_path=Path("/test/data/railway_capex.csv"),
        lcoh_capex_csv_path=Path("/test/data/lcoh_capex.csv"),
        regional_energy_prices_xlsx=Path("/test/data/energy_prices.xlsx"),
        countries_shapefile_dir=Path("/test/data/ne_110m_admin_0_countries"),
        disputed_areas_shapefile_dir=Path("/test/data/ne_10m_admin_0_disputed_areas"),
        baseload_power_sim_dir=Path("/test/data/outputs/GEO/baseload_power_simulation"),
        static_layers_dir=Path("/test/data/outputs/GEO"),
        landtype_percentage_path=Path("/test/data/landtype_percentage.nc"),
    )

    assert geo_paths.data_dir == Path("/test/data")
    assert geo_paths.terrain_nc_path == Path("/test/data/terrain.nc")
    assert geo_paths.geo_plots_dir == Path("/test/data/output/plots/GEO")


def test_simulation_config_geo_paths(tmp_path):
    """Test that SimulationConfig properly initializes output paths."""
    config = SimulationConfig(
        start_year=2025,
        end_year=2030,
        master_excel_path=tmp_path / "master.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
    )

    # Check that output paths are properly set
    assert config.output_dir == tmp_path / "output"
    assert config.plots_dir == tmp_path / "output" / "plots"
    assert config.geo_plots_dir == tmp_path / "output" / "plots" / "GEO"
    assert config.pam_plots_dir == tmp_path / "output" / "plots" / "PAM"
    assert config.tm_output_dir == tmp_path / "output" / "TM"

    # Check that directories were created
    assert config.output_dir.exists()
    assert config.plots_dir.exists()
    assert config.geo_plots_dir.exists()
    assert config.pam_plots_dir.exists()
    assert config.tm_output_dir.exists()


def test_custom_geo_paths_in_config(tmp_path):
    """Test that custom geo paths can be set in SimulationConfig."""
    custom_terrain = Path("/custom/terrain.nc")

    config = SimulationConfig(
        start_year=2025,
        end_year=2030,
        master_excel_path=tmp_path / "master.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
        terrain_nc_path=custom_terrain,
    )

    assert config.terrain_nc_path == custom_terrain
