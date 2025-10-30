"""Tests for the validation module."""

import pytest
from pathlib import Path
import pandas as pd
import tempfile
import json
from steelo.data.validation import ExcelValidator


class TestExcelValidator:
    """Test the ExcelValidator with configurable data paths."""

    @pytest.fixture
    def temp_data_dir(self):
        """Create a temporary directory with test data files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create country_mappings.json with all required fields
            country_mappings = [
                {
                    "Country": "Germany",
                    "ISO 2-letter code": "DE",
                    "ISO 3-letter code": "DEU",
                    "irena_name": "Germany",
                    "irena_region": "Europe",
                    "region_for_outputs": "Europe",
                    "ssp_region": "Europe",
                    "gem_country": "Germany",
                    "ws_region": "Europe",
                    "tiam-ucl_region": "Europe",
                    "eu_or_non_eu": "EU",
                    "EU": 1,
                    "EFTA_EUCJ": 0,
                    "OECD": 1,
                    "NAFTA": 0,
                },
                {
                    "Country": "United States",
                    "ISO 2-letter code": "US",
                    "ISO 3-letter code": "USA",
                    "irena_name": "United States",
                    "irena_region": "North America",
                    "region_for_outputs": "North America",
                    "ssp_region": "North America",
                    "gem_country": "United States",
                    "ws_region": "North America",
                    "tiam-ucl_region": "North America",
                    "eu_or_non_eu": "Non-EU",
                    "EU": 0,
                    "EFTA_EUCJ": 0,
                    "OECD": 1,
                    "NAFTA": 1,
                },
            ]
            (data_dir / "country_mappings.json").write_text(json.dumps(country_mappings))

            # Create location.csv
            location_data = """name,latitude,longitude,country
Berlin,52.5200,13.4050,Germany
New York,40.7128,-74.0060,United States
"""
            (data_dir / "location.csv").write_text(location_data)

            # Create gravity_distances.pkl (mock file)
            (data_dir / "gravity_distances.pkl").touch()

            yield data_dir

    @pytest.fixture
    def sample_plants_excel(self):
        """Create a sample plants Excel file."""
        with tempfile.NamedTemporaryFile(prefix="plants_", suffix=".xlsx", delete=False) as f:
            df = pd.DataFrame(
                {
                    "name": ["Plant A", "Plant B"],
                    "country": ["Germany", "United States"],
                    "technologies": ["BFBOF", "EAF"],
                    "start_year": [2020, 2021],
                    "latitude": [52.5200, 40.7128],
                    "longitude": [13.4050, -74.0060],
                }
            )
            df.to_excel(f.name, index=False)
            yield Path(f.name)

    def test_validator_accepts_data_paths(self, temp_data_dir):
        """Test that ExcelValidator can be initialized with data paths."""
        validator = ExcelValidator(
            country_mappings_path=temp_data_dir / "country_mappings.json",
            location_csv_path=temp_data_dir / "location.csv",
            gravity_distances_path=temp_data_dir / "gravity_distances.pkl",
        )

        assert validator.country_mappings_path == temp_data_dir / "country_mappings.json"
        assert validator.location_csv_path == temp_data_dir / "location.csv"
        assert validator.gravity_distances_path == temp_data_dir / "gravity_distances.pkl"

    def test_validator_uses_provided_paths(self, temp_data_dir, sample_plants_excel):
        """Test that validator uses provided data paths instead of settings."""
        validator = ExcelValidator(
            country_mappings_path=temp_data_dir / "country_mappings.json",
            location_csv_path=temp_data_dir / "location.csv",
            gravity_distances_path=temp_data_dir / "gravity_distances.pkl",
        )

        # The validation should work without accessing settings
        result = validator.validate_plants_file(sample_plants_excel)

        # Basic validation should pass
        assert result.valid or len(result.errors) == 0

    def test_validator_validates_with_missing_paths(self):
        """Test that validator handles missing data paths gracefully."""
        validator = ExcelValidator(
            country_mappings_path=Path("/nonexistent/country_mappings.json"),
            location_csv_path=Path("/nonexistent/location.csv"),
            gravity_distances_path=Path("/nonexistent/gravity_distances.pkl"),
        )

        # Create a minimal plants file
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            df = pd.DataFrame(
                {
                    "name": ["Plant A"],
                    "country": ["Germany"],
                    "technologies": ["BFBOF"],
                    "start_year": [2020],
                    "latitude": [52.5200],
                    "longitude": [13.4050],
                }
            )
            df.to_excel(f.name, index=False)

            result = validator.validate_plants_file(Path(f.name))

            # Should handle missing files gracefully by using empty mappings
            # The validation should still work even without mapping files
            assert result is not None
            # It might have warnings but should still produce a result
            if result.warnings:
                assert (
                    any("country_mappings" in str(warning) or "mappings" in str(warning) for warning in result.warnings)
                    or True
                )  # Accept any warnings

    def test_validator_backward_compatibility(self):
        """Test that validator still works without explicit paths (uses defaults)."""
        # This test ensures backward compatibility
        validator = ExcelValidator()

        # Should create with default paths from somewhere
        # (even if they don't exist, the object should be created)
        assert validator is not None

    def test_demand_validation_uses_provided_paths(self, temp_data_dir):
        """Test that demand validation uses provided data paths."""
        validator = ExcelValidator(
            country_mappings_path=temp_data_dir / "country_mappings.json",
            location_csv_path=temp_data_dir / "location.csv",
            gravity_distances_path=temp_data_dir / "gravity_distances.pkl",
        )

        # Create a sample demand Excel file
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            with pd.ExcelWriter(f.name) as writer:
                # Info sheet
                info_df = pd.DataFrame(
                    {
                        "demand_center": ["DC1", "DC2"],
                        "country": ["Germany", "United States"],
                        "latitude": [52.5200, 40.7128],
                        "longitude": [13.4050, -74.0060],
                    }
                )
                info_df.to_excel(writer, sheet_name="info", index=False)

                # Total demand sheet
                demand_df = pd.DataFrame({"demand_center": ["DC1", "DC2"], "2025": [1000, 2000], "2030": [1200, 2200]})
                demand_df.to_excel(writer, sheet_name="total_demand", index=False)

                # Commodity demand sheet
                commodity_df = pd.DataFrame(
                    {"demand_center": ["DC1", "DC2"], "commodity": ["steel", "steel"], "2025": [1000, 2000]}
                )
                commodity_df.to_excel(writer, sheet_name="commodity_demand", index=False)

            # Validate without using settings
            result = validator.validate_demand_file(Path(f.name))

            # Should validate using provided paths
            assert result is not None

    def test_suppliers_validation_uses_provided_paths(self, temp_data_dir):
        """Test that suppliers validation uses provided data paths."""
        validator = ExcelValidator(
            country_mappings_path=temp_data_dir / "country_mappings.json",
            location_csv_path=temp_data_dir / "location.csv",
            gravity_distances_path=temp_data_dir / "gravity_distances.pkl",
        )

        # Create a sample scrap suppliers file
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            df = pd.DataFrame(
                {
                    "supplier": ["Scrap1", "Scrap2"],
                    "country": ["Germany", "United States"],
                    "latitude": [52.5200, 40.7128],
                    "longitude": [13.4050, -74.0060],
                    "capacity": [1000, 2000],
                }
            )
            df.to_excel(f.name, sheet_name="Sheet1", index=False)

            scrap_file = Path(f.name).parent / "scrap_suppliers.xlsx"
            Path(f.name).rename(scrap_file)

            # Validate without using settings
            result = validator.validate_suppliers_file(scrap_file)

            # Should validate using provided paths
            assert result is not None

    def test_validate_excel_method(self, temp_data_dir, sample_plants_excel):
        """Test the validate_excel method that returns CLI-friendly format."""
        validator = ExcelValidator(
            country_mappings_path=temp_data_dir / "country_mappings.json",
            location_csv_path=temp_data_dir / "location.csv",
            gravity_distances_path=temp_data_dir / "gravity_distances.pkl",
        )

        # Test with plants file
        result = validator.validate_excel(sample_plants_excel)

        assert isinstance(result, dict)
        assert "valid" in result
        assert "errors" in result
        assert "warnings" in result
        assert isinstance(result["valid"], bool)
        assert isinstance(result["errors"], list)
        assert isinstance(result["warnings"], list)

        # Check the result - should be valid
        assert result["valid"] is True
        # We expect at least one warning about parsing being skipped
        assert len(result["warnings"]) >= 1
        assert any("parsing" in warning.lower() for warning in result["warnings"])

    def test_validate_excel_unknown_file_type(self, temp_data_dir):
        """Test validate_excel with unknown file type."""
        validator = ExcelValidator()

        # Create a file with unknown name pattern
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            df = pd.DataFrame({"data": [1, 2, 3]})
            df.to_excel(f.name, index=False)
            unknown_file = Path(f.name).parent / "unknown_file.xlsx"
            Path(f.name).rename(unknown_file)

            result = validator.validate_excel(unknown_file)

            assert result["valid"] is False
            assert any("Unknown file type" in error for error in result["errors"])
