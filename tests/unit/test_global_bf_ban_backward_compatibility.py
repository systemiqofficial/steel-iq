"""Test backward compatibility for global_bf_ban parameter."""

import warnings
from pathlib import Path
from steelo.simulation import SimulationConfig
from steelo.domain import Year
from steelo.simulation_types import get_default_technology_settings


def test_global_bf_ban_true_disables_bf_in_technology_settings(tmp_path):
    """Test that global_bf_ban=True correctly translates to technology_settings."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path("test.xlsx"),
            output_dir=tmp_path,
            global_bf_ban=True,  # This should translate to technology_settings
        )

        # Should have deprecation warning
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "global_bf_ban is deprecated" in str(w[0].message)

        # Should have technology_settings created with BF disabled
        assert config.technology_settings is not None
        assert "BF" in config.technology_settings
        assert config.technology_settings["BF"].allowed is False
        assert config.technology_settings["BF"].from_year == 2025
        assert config.technology_settings["BF"].to_year is None

        # BFBOF should also be disabled since it requires BF
        assert "BFBOF" in config.technology_settings
        assert config.technology_settings["BFBOF"].allowed is False


def test_global_bf_ban_false_does_not_affect_technology_settings(tmp_path):
    """Test that global_bf_ban=False does not modify technology_settings."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path("test.xlsx"),
            output_dir=tmp_path,
            global_bf_ban=False,  # This should not affect technology_settings
        )

        # Should have deprecation warning but no changes to BF settings
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)

        # technology_settings should be populated with defaults (auto-populated in __post_init__)
        assert config.technology_settings is not None
        # BF should remain enabled (default behavior) since global_bf_ban=False
        assert config.technology_settings["BF"].allowed is True


def test_global_bf_ban_with_existing_technology_settings(tmp_path):
    """Test that global_bf_ban=True respects existing technology_settings."""
    # Start with custom technology settings
    tech_settings = get_default_technology_settings()
    # Modify EAF settings - need to create new TechnologySettings since it's frozen
    from steelo.simulation_types import TechnologySettings

    tech_settings["EAF"] = TechnologySettings(
        allowed=False,  # Custom setting
        from_year=tech_settings["EAF"].from_year,
        to_year=tech_settings["EAF"].to_year,
    )

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path("test.xlsx"),
            output_dir=tmp_path,
            technology_settings=tech_settings,
            global_bf_ban=True,  # Should override BF settings but preserve others
        )

        # Should have deprecation warning
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)

        # BF should be disabled due to global_bf_ban=True
        assert config.technology_settings["BF"].allowed is False
        assert config.technology_settings["BFBOF"].allowed is False

        # But existing custom settings should be preserved
        assert config.technology_settings["EAF"].allowed is False  # Our custom setting


def test_global_bf_ban_preserves_simulation_outcomes():
    """Test that old configurations with global_bf_ban=True still prevent BF construction."""
    # Simulate a stored configuration from before the deprecation
    legacy_config = {
        "start_year": Year(2025),
        "end_year": Year(2050),
        "master_excel_path": Path("test.xlsx"),
        "output_dir": Path("/tmp/test"),
        "global_bf_ban": True,  # Legacy parameter that must still work
    }

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        config = SimulationConfig(**legacy_config)

        # Critical: BF must be disabled to preserve simulation outcomes
        assert config.technology_settings is not None
        assert config.technology_settings["BF"].allowed is False

        # This ensures that stored configurations and external callers
        # continue to get the same simulation results as before


def test_global_bf_ban_none_no_warnings_no_changes(tmp_path):
    """Test that not providing global_bf_ban works as expected."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2050),
            master_excel_path=Path("test.xlsx"),
            output_dir=tmp_path,
            # global_bf_ban not provided
        )

        # Should have no warnings
        assert len(w) == 0

        # technology_settings should be populated with defaults (auto-populated in __post_init__)
        assert config.technology_settings is not None
        # BF should be enabled by default
        assert config.technology_settings["BF"].allowed is True
