"""Test the new technology settings fixtures to demonstrate the improved approach."""

from steelo.simulation_types import TechnologySettings


def test_default_technology_settings_fixture(default_technology_settings):
    """Test that default_technology_settings fixture provides production defaults."""
    assert "BF" in default_technology_settings
    assert "DRIH2" in default_technology_settings
    assert default_technology_settings["BF"].allowed is True
    assert default_technology_settings["ESF"].allowed is False  # ESF disabled by default


def test_make_technology_settings_with_dict_overrides(make_technology_settings):
    """Test make_technology_settings fixture with dict overrides (most common usage)."""
    tech_settings = make_technology_settings(
        {
            "BF": {"allowed": False, "from_year": 2030},
            "DRIH2": {"from_year": 2028},  # Only override specific fields
        }
    )

    # BF should be fully updated
    assert tech_settings["BF"].allowed is False
    assert tech_settings["BF"].from_year == 2030
    assert tech_settings["BF"].to_year is None  # Keeps default

    # DRIH2 should have partial update
    assert tech_settings["DRIH2"].allowed is True  # Keeps default
    assert tech_settings["DRIH2"].from_year == 2028  # Override
    assert tech_settings["DRIH2"].to_year is None  # Keeps default

    # Other technologies keep defaults
    assert tech_settings["EAF"].allowed is True
    assert tech_settings["EAF"].from_year == 2025


def test_make_technology_settings_with_technology_objects(make_technology_settings):
    """Test make_technology_settings fixture with TechnologySettings objects."""
    custom_bf = TechnologySettings(allowed=False, from_year=2035, to_year=2040)

    tech_settings = make_technology_settings(
        {"BF": custom_bf, "MOE": TechnologySettings(allowed=True, from_year=2030, to_year=None)}
    )

    assert tech_settings["BF"] == custom_bf
    assert tech_settings["MOE"].allowed is True
    assert tech_settings["MOE"].from_year == 2030


def test_technology_form_converter_fixture(technology_form_converter, make_technology_settings):
    """Test technology_form_converter fixture for Django form data."""
    tech_settings = make_technology_settings(
        {
            "BF": {"allowed": True, "from_year": 2025, "to_year": 2030},
            "DRIH2": {"allowed": False, "from_year": 2028},  # Disabled
        }
    )

    form_data = technology_form_converter(tech_settings)

    # BF should have allowed checkbox
    assert form_data["tech_BF_allowed"] == "on"
    assert form_data["tech_BF_from_year"] == "2025"
    assert form_data["tech_BF_to_year"] == "2030"

    # DRIH2 should NOT have allowed checkbox (disabled)
    assert "tech_DRIH2_allowed" not in form_data
    assert form_data["tech_DRIH2_from_year"] == "2028"
    assert form_data["tech_DRIH2_to_year"] == ""


def test_legacy_config_converter_production_function():
    """Test legacy conversion functionality.

    NOTE: This test is disabled as the legacy config converter was removed
    during the transition to the new dynamic technology system.
    """
    # Legacy conversion is no longer supported - all configurations
    # now use the new technology_settings format directly
    assert True  # Placeholder - test disabled


def test_fixtures_use_production_data(default_technology_settings):
    """Test that fixtures are using actual production data, not test-specific constants."""
    from steelo.simulation_types import get_default_technology_settings

    # Should be exactly the same as calling production function directly
    production_defaults = get_default_technology_settings()

    assert len(default_technology_settings) == len(production_defaults)

    for tech_code, settings in production_defaults.items():
        assert tech_code in default_technology_settings
        fixture_settings = default_technology_settings[tech_code]
        assert fixture_settings.allowed == settings.allowed
        assert fixture_settings.from_year == settings.from_year
        assert fixture_settings.to_year == settings.to_year
