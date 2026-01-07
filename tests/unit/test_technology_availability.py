"""Tests for technology availability configuration parameters."""

import pytest
from steelo.domain.models import Environment, is_technology_allowed
from steelo.domain import Year
from steelo.simulation import SimulationConfig
from steelo.simulation_types import TechnologySettings
# Import get_default_technology_settings from parent conftest


def get_default_technology_settings():
    """Get default technology settings for tests."""
    return {
        # Primary technologies with normalized codes
        "BF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "BFBOF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "BOF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRING": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRINGEAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRIH2": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRIH2EAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "EAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "ESF": TechnologySettings(allowed=False, from_year=2025, to_year=None),
        "ESFEAF": TechnologySettings(allowed=False, from_year=2025, to_year=None),
        "MOE": TechnologySettings(allowed=False, from_year=2025, to_year=None),
        "DRI": TechnologySettings(allowed=True, from_year=2025, to_year=None),
    }


@pytest.fixture
def tech_switches_csv(tmp_path):
    """Create a temporary tech_switches CSV file for testing."""
    csv_content = """Tech,BF-BOF,BOF,DRI-NG-EAF,DRI-H2-EAF,EAF,ESF-EAF,MOE
BF-BOF,NO,YES,YES,YES,YES,YES,YES
BOF,YES,NO,YES,YES,YES,YES,YES
DRI-NG-EAF,YES,YES,NO,YES,YES,YES,YES
DRI-H2-EAF,YES,YES,YES,NO,YES,YES,YES
EAF,YES,YES,YES,YES,NO,YES,YES
ESF-EAF,YES,YES,YES,YES,YES,NO,YES
MOE,YES,YES,YES,YES,YES,YES,NO"""

    csv_path = tmp_path / "tech_switches_allowed.csv"
    csv_path.write_text(csv_content)
    return csv_path


def _filtered_targets_for_year(env: Environment, tech_settings: dict, year: int) -> dict[str, list[str]]:
    """Apply year-based availability to a static transition graph.

    The transition graph in `env.allowed_furnace_transitions` is loaded once from CSV and contains
    all *known* technologies. The simulation applies `from_year`/`to_year`/`allowed` constraints at
    decision time by intersecting with the technologies available for the current year.
    """
    all_raw_techs: set[str] = set(env.allowed_furnace_transitions.keys())
    for targets in env.allowed_furnace_transitions.values():
        all_raw_techs.update(targets)

    allowed_in_year = {tech for tech in all_raw_techs if is_technology_allowed(tech_settings, tech, year)}
    return {
        origin: [tech for tech in targets if tech in allowed_in_year]
        for origin, targets in env.allowed_furnace_transitions.items()
    }


def test_bf_allowed_false_removes_bf_transitions(tech_switches_csv, tmp_path):
    """Test that disabling BF prevents BF-BOF as a switch target."""
    # Get default settings and disable BF technologies
    tech_settings = get_default_technology_settings()
    tech_settings["BF"] = TechnologySettings(allowed=False, from_year=2025, to_year=None)
    tech_settings["BFBOF"] = TechnologySettings(allowed=False, from_year=2025, to_year=None)

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path=tmp_path / "dummy.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=tech_settings,
    )

    env = Environment(config=config, tech_switches_csv=tech_switches_csv)

    filtered = _filtered_targets_for_year(env, tech_settings, 2025)
    for origin, allowed_techs in filtered.items():
        assert "BF-BOF" not in allowed_techs, f"BF-BOF found in filtered transitions from {origin}"


def test_dri_h2_from_year_restricts_early_availability(tech_switches_csv, tmp_path):
    """Test that DRI-H2 is only available as a switch target from its from_year onward."""
    # Get default settings and set DRI-H2 to start from 2030
    tech_settings = get_default_technology_settings()
    tech_settings["DRIH2"] = TechnologySettings(allowed=True, from_year=2030, to_year=None)
    tech_settings["DRIH2EAF"] = TechnologySettings(allowed=True, from_year=2030, to_year=None)

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2040),
        master_excel_path=tmp_path / "dummy.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=tech_settings,
    )

    env = Environment(config=config, tech_switches_csv=tech_switches_csv)

    filtered_2025 = _filtered_targets_for_year(env, tech_settings, 2025)
    for origin, allowed_techs in filtered_2025.items():
        assert "DRI-H2-EAF" not in allowed_techs, f"DRI-H2-EAF found in filtered transitions from {origin} for 2025"

    filtered_2030 = _filtered_targets_for_year(env, tech_settings, 2030)
    has_dri_h2 = any("DRI-H2-EAF" in allowed for allowed in filtered_2030.values())
    assert has_dri_h2, "DRI-H2-EAF not available in filtered transitions for 2030"


def test_eaf_to_year_restricts_late_availability(tech_switches_csv, tmp_path):
    """Test that EAF is only available as a switch target until its to_year."""
    # Get default settings and set EAF to end at 2035
    tech_settings = get_default_technology_settings()
    tech_settings["EAF"] = TechnologySettings(allowed=True, from_year=2025, to_year=2035)

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2040),
        master_excel_path=tmp_path / "dummy.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=tech_settings,
    )

    # Test year 2035 (EAF still available)
    env = Environment(config=config, tech_switches_csv=tech_switches_csv)
    filtered_2035 = _filtered_targets_for_year(env, tech_settings, 2035)
    has_eaf = any("EAF" in allowed for allowed in filtered_2035.values())
    assert has_eaf, "EAF not found in any transitions for year 2035"

    # Test year 2036 (after EAF ends)
    filtered_2036 = _filtered_targets_for_year(env, tech_settings, 2036)
    for origin, allowed_techs in filtered_2036.items():
        assert "EAF" not in allowed_techs, f"EAF found in {origin} transitions for year 2036"


def test_multiple_technology_restrictions(tech_switches_csv, tmp_path):
    """Test multiple technology restrictions applied simultaneously."""
    # Set multiple restrictions
    tech_settings = get_default_technology_settings()
    tech_settings["BF"] = TechnologySettings(allowed=False, from_year=2025, to_year=None)
    tech_settings["BFBOF"] = TechnologySettings(allowed=False, from_year=2025, to_year=None)
    tech_settings["DRIH2"] = TechnologySettings(allowed=True, from_year=2030, to_year=None)
    tech_settings["DRIH2EAF"] = TechnologySettings(allowed=True, from_year=2030, to_year=None)
    tech_settings["ESF"] = TechnologySettings(allowed=False, from_year=2025, to_year=None)
    tech_settings["ESFEAF"] = TechnologySettings(allowed=False, from_year=2025, to_year=None)

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2040),
        master_excel_path=tmp_path / "dummy.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=tech_settings,
    )

    env = Environment(config=config, tech_switches_csv=tech_switches_csv)

    filtered_2025 = _filtered_targets_for_year(env, tech_settings, 2025)
    for origin, allowed_techs in filtered_2025.items():
        assert "BF-BOF" not in allowed_techs, f"BF-BOF found in {origin} transitions"
        assert "DRI-H2-EAF" not in allowed_techs, f"DRI-H2-EAF found in {origin} transitions for 2025"
        assert "ESF-EAF" not in allowed_techs, f"ESF-EAF found in {origin} transitions"

    # Test year 2030 - DRI-H2 should now be available
    filtered_2030 = _filtered_targets_for_year(env, tech_settings, 2030)
    for origin, allowed_techs in filtered_2030.items():
        assert "BF-BOF" not in allowed_techs, f"BF-BOF found in {origin} transitions"
        assert "ESF-EAF" not in allowed_techs, f"ESF-EAF found in {origin} transitions"

    has_dri_h2 = any("DRI-H2-EAF" in allowed for allowed in filtered_2030.values())
    assert has_dri_h2, "DRI-H2-EAF not available in 2030"


def test_all_technologies_allowed_by_default(tech_switches_csv, tmp_path):
    """Test that all technologies are allowed when using default settings."""
    # Use all default settings (everything enabled)
    tech_settings = get_default_technology_settings()

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2040),
        master_excel_path=tmp_path / "dummy.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=tech_settings,
    )

    env = Environment(config=config, tech_switches_csv=tech_switches_csv)

    # Collect all unique technologies from transitions
    all_techs = set()
    for allowed_techs in env.allowed_furnace_transitions.values():
        all_techs.update(allowed_techs)

    # Check that major technologies are present (using actual CSV technology names)
    expected_techs = {"BF-BOF", "BOF", "DRI-NG-EAF", "DRI-H2-EAF", "EAF"}
    for tech in expected_techs:
        assert tech in all_techs, f"{tech} not found in allowed transitions"
