"""Test that construction_time parameter is properly configured."""

from steelo.simulation_types import get_default_technology_settings

from steelo.simulation import SimulationConfig


def test_construction_time_in_simulation_config(tmp_path):
    """Test that construction_time can be set in SimulationConfig."""

    # Create temporary paths for required parameters
    master_excel = tmp_path / "master.xlsx"
    master_excel.touch()
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Test with custom construction time
    config = SimulationConfig(
        start_year=2025,
        end_year=2050,
        master_excel_path=master_excel,
        output_dir=output_dir,
        technology_settings=get_default_technology_settings(),
        construction_time=6,  # 6 years instead of default 4
    )

    assert config.construction_time == 6

    # Test default value
    config_default = SimulationConfig(
        start_year=2025,
        end_year=2050,
        master_excel_path=master_excel,
        output_dir=output_dir,
        technology_settings=get_default_technology_settings(),
    )

    assert config_default.construction_time == 4  # Default is 4 years


def test_construction_time_with_different_values(tmp_path):
    """Test that different construction_time values are properly stored."""

    # Create temporary paths
    master_excel = tmp_path / "master.xlsx"
    master_excel.touch()
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    test_cases = [2, 4, 7, 10]

    for construction_years in test_cases:
        # Create config with specific construction time
        config = SimulationConfig(
            start_year=2025,
            end_year=2050,
            master_excel_path=master_excel,
            output_dir=output_dir,
            technology_settings=get_default_technology_settings(),
            construction_time=construction_years,
        )

        assert config.construction_time == construction_years, f"Construction time should be {construction_years}"
