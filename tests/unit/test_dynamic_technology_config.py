"""Tests for the dynamic technology configuration system."""

import json
import tempfile
from pathlib import Path

import pytest

from steelo.core.parse import normalize_code, parse_bool_strict, parse_int_strict
from steelo.simulation_types import TechnologySettings
from steelo.validation import SimulationConfigError, validate_technology_settings
from steelo.adapters.repositories.technology_repository import TechnologyRepository
from steelo.domain.models import is_technology_allowed, UnknownTechnologyError, tech_key


class TestTechnologySettings:
    """Test the TechnologySettings dataclass."""

    def test_technology_settings_creation(self):
        """Test creating a TechnologySettings instance."""
        ts = TechnologySettings(allowed=True, from_year=2025, to_year=2050)
        assert ts.allowed is True
        assert ts.from_year == 2025
        assert ts.to_year == 2050

    def test_technology_settings_to_dict(self):
        """Test converting TechnologySettings to dict."""
        ts = TechnologySettings(allowed=True, from_year=2025, to_year=None)
        result = ts.to_dict()
        assert result == {"allowed": True, "from_year": 2025, "to_year": None}

    def test_technology_settings_repr(self):
        """Test string representation of TechnologySettings."""
        ts1 = TechnologySettings(allowed=True, from_year=2025, to_year=2050)
        assert str(ts1) == "Tech(✓ 2025-2050)"

        ts2 = TechnologySettings(allowed=False, from_year=2030, to_year=None)
        assert str(ts2) == "Tech(✗ 2030)"


class TestValidation:
    """Test the validation module."""

    def test_empty_config_rejected(self):
        """Empty technology_settings should be rejected."""
        with pytest.raises(SimulationConfigError, match="empty"):
            validate_technology_settings({}, {"BF"}, year_min=2025, year_max=2050)

    def test_unknown_tech_rejected(self):
        """Unknown technologies in config should be rejected."""
        with pytest.raises(SimulationConfigError, match="Unknown"):
            validate_technology_settings(
                {"UNKNOWN": TechnologySettings(True, 2025)}, {"BF"}, year_min=2025, year_max=2050
            )

    def test_missing_tech_rejected(self):
        """Missing technologies should be rejected."""
        with pytest.raises(SimulationConfigError, match="Missing"):
            validate_technology_settings(
                {"BF": TechnologySettings(True, 2025)},
                {"BF", "BOF"},  # BOF missing
                year_min=2025,
                year_max=2050,
            )

    def test_invalid_year_range(self):
        """Invalid year ranges should be rejected."""
        with pytest.raises(SimulationConfigError, match="invalid year range"):
            validate_technology_settings(
                {"BF": TechnologySettings(True, 2030, 2025)},  # to_year < from_year
                {"BF"},
                year_min=2025,
                year_max=2050,
            )

    def test_years_outside_scenario(self):
        """Individual technologies can START before or END after scenario, but must cover all scenario years."""
        # Technology starts before scenario and covers entire scenario period - should pass
        validate_technology_settings(
            {"BF": TechnologySettings(True, 2020, None)},  # Starts 2020, covers 2025-2050+
            {"BF"},
            year_min=2025,  # Scenario starts 2025
            year_max=2050,
        )

        # Technology starts before and ends after scenario - should pass
        validate_technology_settings(
            {"BF": TechnologySettings(True, 2020, 2060)},  # 2020-2060 covers 2025-2050
            {"BF"},
            year_min=2025,
            year_max=2050,
        )

        # Multiple technologies can combine to provide full coverage
        # BF covers 2025-2030, EAF covers 2030-2050 (overlapping at 2030)
        validate_technology_settings(
            {
                "BF": TechnologySettings(True, 2025, 2030),
                "EAF": TechnologySettings(True, 2030, None),
            },
            {"BF", "EAF"},
            year_min=2025,
            year_max=2050,
        )

    def test_all_technologies_outside_scenario_rejected(self):
        """All technologies outside scenario range should be rejected."""
        # All technologies completely before scenario - should fail
        with pytest.raises(SimulationConfigError, match="No technologies are available"):
            validate_technology_settings(
                {"BF": TechnologySettings(True, 2010, 2020)},  # Ends before scenario
                {"BF"},
                year_min=2025,
                year_max=2050,
            )

        # All technologies completely after scenario - should fail
        with pytest.raises(SimulationConfigError, match="No technologies are available"):
            validate_technology_settings(
                {"BF": TechnologySettings(True, 2060, None)},  # Starts after scenario
                {"BF"},
                year_min=2025,
                year_max=2050,
            )

        # All technologies disabled - should fail
        with pytest.raises(SimulationConfigError, match="No technologies are available"):
            validate_technology_settings(
                {
                    "BF": TechnologySettings(False, 2025, 2050),  # Disabled
                    "BOF": TechnologySettings(False, 2025, None),  # Disabled
                },
                {"BF", "BOF"},
                year_min=2025,
                year_max=2050,
            )

    def test_technology_gap_in_scenario_rejected(self):
        """Technologies with gaps in coverage should be rejected."""
        # BF ends before scenario starts, EAF starts 5 years into scenario
        # This leaves years 2025-2029 without any technology
        with pytest.raises(
            SimulationConfigError, match="No technologies are available for years 2025, 2026, 2027, 2028, 2029"
        ):
            validate_technology_settings(
                {
                    "BF": TechnologySettings(True, 2020, 2024),  # Ends before scenario
                    "EAF": TechnologySettings(True, 2030, None),  # Starts 5 years in
                },
                {"BF", "EAF"},
                year_min=2025,
                year_max=2050,
            )

        # Single year gap
        with pytest.raises(SimulationConfigError, match="No technologies are available for year 2030"):
            validate_technology_settings(
                {
                    "BF": TechnologySettings(True, 2025, 2029),  # Ends at 2029
                    "EAF": TechnologySettings(True, 2031, None),  # Starts at 2031
                },
                {"BF", "EAF"},
                year_min=2025,
                year_max=2050,
            )

    def test_valid_config_accepted(self):
        """Valid configuration should pass validation."""
        # Should not raise
        validate_technology_settings(
            {
                "BF": TechnologySettings(True, 2025, 2050),
                "BOF": TechnologySettings(False, 2030, None),
            },
            {"BF", "BOF"},
            year_min=2025,
            year_max=2050,
        )


class TestParsing:
    """Test the parsing utilities."""

    def test_normalization(self):
        """Different representations should normalize to same key."""
        # All these should normalize to 'BF'
        assert normalize_code("B.F.") == "BF"
        assert normalize_code("bf") == "BF"
        assert normalize_code("B-F") == "BF"
        assert normalize_code("b f") == "BF"
        assert normalize_code("b_f") == "BF"

    def test_parse_bool_strict(self):
        """Test strict boolean parsing."""
        # Valid true values
        assert parse_bool_strict("true") is True
        assert parse_bool_strict("1") is True
        assert parse_bool_strict("yes") is True
        assert parse_bool_strict("on") is True

        # Valid false values
        assert parse_bool_strict("false") is False
        assert parse_bool_strict("0") is False
        assert parse_bool_strict("no") is False
        assert parse_bool_strict("off") is False

        # Absent checkbox with default
        assert parse_bool_strict(None, default=False) is False
        assert parse_bool_strict("", default=False) is False

        # Invalid values should raise
        with pytest.raises(ValueError, match="invalid boolean"):
            parse_bool_strict("maybe")

        # Required without default
        with pytest.raises(ValueError, match="boolean required"):
            parse_bool_strict(None)

    def test_parse_int_strict(self):
        """Test strict integer parsing."""
        # Valid values
        assert parse_int_strict("2025", required=True, lo=2020, hi=2050) == 2025
        assert parse_int_strict("2030.0", required=True, lo=2020, hi=2050) == 2030

        # Optional empty value
        assert parse_int_strict("", required=False, lo=2020, hi=2050) is None
        assert parse_int_strict(None, required=False, lo=2020, hi=2050) is None

        # Out of range
        with pytest.raises(ValueError, match="out of range"):
            parse_int_strict("2100", required=True, lo=2020, hi=2050)

        # Invalid integer
        with pytest.raises(ValueError, match="invalid integer"):
            parse_int_strict("abc", required=True, lo=2020, hi=2050)

        # Required without value
        with pytest.raises(ValueError, match="integer required"):
            parse_int_strict("", required=True, lo=2020, hi=2050)


class TestTechnologyRepository:
    """Test the technology repository."""

    def test_missing_technologies_json(self):
        """Missing file should give clear error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = TechnologyRepository(Path(tmpdir))
            with pytest.raises(FileNotFoundError, match="No technologies.json"):
                repo.load_technologies()

    def test_schema_version_error(self):
        """Old schema version should give actionable error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tech_path = Path(tmpdir) / "fixtures" / "technologies.json"
            tech_path.parent.mkdir(parents=True)
            tech_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,  # Old version!
                        "technologies": {},
                    }
                )
            )

            repo = TechnologyRepository(Path(tmpdir))
            with pytest.raises(ValueError, match="rerun data preparation"):
                repo.load_technologies()

    def test_normalized_code_collision(self):
        """Duplicate normalized codes should be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tech_path = Path(tmpdir) / "fixtures" / "technologies.json"
            tech_path.parent.mkdir(parents=True)
            tech_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "technologies": {
                            "bf": {"normalized_code": "BF", "code": "BF"},
                            "b_f": {"normalized_code": "BF", "code": "B-F"},  # Collision!
                        },
                    }
                )
            )

            repo = TechnologyRepository(Path(tmpdir))
            with pytest.raises(ValueError, match="Duplicate normalized_code"):
                repo.get_normalized_codes()

    def test_valid_technologies_loaded(self):
        """Valid technologies.json should load successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tech_path = Path(tmpdir) / "fixtures" / "technologies.json"
            tech_path.parent.mkdir(parents=True)
            tech_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "generated_at": "2025-09-23T10:00:00Z",
                        "technologies": {
                            "bf": {
                                "code": "BF",
                                "normalized_code": "BF",
                                "display_name": "Blast Furnace",
                                "allowed": True,
                                "from_year": 2025,
                                "to_year": None,
                            },
                            "dri_h2": {
                                "code": "DRI-H2",
                                "normalized_code": "DRIH2",
                                "display_name": "Direct Reduced Iron (Hydrogen)",
                                "allowed": True,
                                "from_year": 2030,
                                "to_year": None,
                            },
                        },
                    }
                )
            )

            repo = TechnologyRepository(Path(tmpdir))
            techs = repo.load_technologies()
            assert len(techs) == 2
            assert "bf" in techs
            assert "dri_h2" in techs
            assert techs["bf"]["normalized_code"] == "BF"
            assert techs["dri_h2"]["normalized_code"] == "DRIH2"

            codes = repo.get_normalized_codes()
            assert codes == {"BF", "DRIH2"}


class TestDomainLogic:
    """Test domain logic for technology checking."""

    def test_unknown_tech_in_domain(self):
        """Unknown technology should raise UnknownTechnologyError."""
        config = {"BF": TechnologySettings(True, 2025)}

        with pytest.raises(UnknownTechnologyError, match="UNKNOWN"):
            is_technology_allowed(config, "UNKNOWN", 2030)

    def test_technology_allowed(self):
        """Test technology allowed logic."""
        config = {
            "BF": TechnologySettings(True, 2025, 2050),
            "DRIH2": TechnologySettings(True, 2030, None),
            "MOE": TechnologySettings(False, 2025, None),
        }

        # BF allowed in 2030
        assert is_technology_allowed(config, "BF", 2030) is True

        # BF not allowed in 2055 (past to_year)
        assert is_technology_allowed(config, "BF", 2055) is False

        # DRI-H2 not allowed in 2025 (before from_year)
        assert is_technology_allowed(config, "DRI-H2", 2025) is False

        # DRI-H2 allowed in 2035
        assert is_technology_allowed(config, "DRI-H2", 2035) is True

        # MOE not allowed (disabled)
        assert is_technology_allowed(config, "MOE", 2030) is False

    def test_tech_key_helper(self):
        """Test the tech_key helper function."""
        assert tech_key("BF-BOF") == "BFBOF"
        assert tech_key("DRI-H2") == "DRIH2"
        assert tech_key("dri_ng") == "DRING"


class TestIntegration:
    """Integration tests for the full flow."""

    def test_simulation_config_with_technology_settings(self):
        """Test that SimulationConfig properly handles technology_settings."""
        from steelo.simulation import SimulationConfig
        from steelo.domain import Year

        tech_map = {
            "BF": TechnologySettings(True, 2025, 2050),
            "BOF": TechnologySettings(True, 2025, None),
        }

        # Create config with technology_settings using temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            config = SimulationConfig(
                start_year=Year(2025),
                end_year=Year(2050),
                master_excel_path=Path(tmpdir) / "path.xlsx",
                output_dir=Path(tmpdir) / "output",
                technology_settings=tech_map,
            )

            assert config.technology_settings == tech_map
            assert "BF" in config.technology_settings
            assert config.technology_settings["BF"].allowed is True

    def test_legacy_fields_rejected_in_config(self):
        """Config with legacy fields should be rejected."""
        from steelo.simulation import SimulationConfig
        from steelo.domain import Year

        # Try to create config with legacy field - should raise AttributeError
        # since the field doesn't exist anymore
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            SimulationConfig(
                start_year=Year(2025),
                end_year=Year(2050),
                master_excel_path=Path("/fake/path.xlsx"),
                output_dir=Path("/fake/output"),
                bf_allowed=True,  # Legacy field!
                technology_settings={"BF": TechnologySettings(True, 2025)},
            )
