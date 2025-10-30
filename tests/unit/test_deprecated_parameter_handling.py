"""Test handling of deprecated parameters in SimulationConfig."""

import warnings
from pathlib import Path
from steelo.simulation import SimulationConfig
from steelo.domain import Year
from steelo.simulation_types import get_default_technology_settings, TechnologySettings


def test_deprecated_global_bf_ban_parameter_shows_warning(tmp_path):
    """Test that global_bf_ban parameter triggers deprecation warning and is ignored."""
    # Test that passing global_bf_ban shows a deprecation warning
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path("test.xlsx"),
            output_dir=tmp_path,
            technology_settings=get_default_technology_settings(),
            global_bf_ban=True,  # This should trigger deprecation warning
        )

        # Should have exactly one deprecation warning
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "global_bf_ban is deprecated" in str(w[0].message)
        assert "technology_settings" in str(w[0].message)


def test_simulation_config_works_without_deprecated_parameter(tmp_path):
    """Test that SimulationConfig works normally without deprecated parameter."""
    # Test that normal instantiation works without warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path("test.xlsx"),
            output_dir=tmp_path,
            technology_settings=get_default_technology_settings(),
        )

        # Should have no warnings
        assert len(w) == 0


def test_bf_exclusion_via_allowed_techs(tmp_path):
    """Test that BF can be excluded using the modern allowed_techs system."""
    # Create technology settings that exclude BF
    tech_settings = get_default_technology_settings()

    # Modify BF settings to be disallowed
    bf_settings = TechnologySettings(
        allowed=False,  # This excludes BF technology
        from_year=2025,
        to_year=None,
    )
    tech_settings["BF"] = bf_settings

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path,
        technology_settings=tech_settings,
    )

    # Verify BF is excluded via technology settings
    assert config.technology_settings["BF"].allowed is False


def test_backward_compatibility_with_legacy_config_dict(tmp_path):
    """Test that legacy configuration dictionaries work with deprecation warnings."""
    # Simulate a legacy config dict that might be stored in database
    legacy_config = {
        "start_year": 2025,
        "end_year": 2050,
        "master_excel_path": "test.xlsx",
        "output_dir": str(tmp_path),
        "technology_settings": get_default_technology_settings(),
        "global_bf_ban": True,  # Legacy parameter
    }

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        config = SimulationConfig(**legacy_config)

        # Should show deprecation warning
        assert len(w) == 1
        assert "global_bf_ban is deprecated" in str(w[0].message)

        # Configuration should be created successfully
        assert config.start_year == Year(2025)
        assert config.end_year == Year(2050)


def test_multiple_deprecated_parameters_future_proofing(tmp_path):
    """Test that the system can handle multiple deprecated parameters."""
    # This test ensures the deprecation system is extensible
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path("test.xlsx"),
            output_dir=tmp_path,
            technology_settings=get_default_technology_settings(),
            global_bf_ban=True,  # Currently the only deprecated parameter
        )

        # Should show one warning for the one deprecated parameter
        assert len(w) == 1
        deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 1
