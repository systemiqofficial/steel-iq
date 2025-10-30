"""Tests for DataPreparation.get_technologies() method."""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

from steeloweb.models import DataPreparation


@pytest.mark.django_db
class TestDataPreparationGetTechnologies:
    """Test the get_technologies method of DataPreparation model."""

    def test_get_technologies_success(self):
        """Test successful retrieval of technologies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test technologies.json
            tech_data = {
                "schema_version": 3,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "excel_path": "/test/path.xlsx",
                    "sheet": "Techno-economic details",
                    "extraction_date": datetime.now(timezone.utc).isoformat(),
                    "tech_count": 2,
                    "duplicates": None,
                    "warnings_count": 0,
                },
                "technologies": {
                    "bf": {
                        "code": "BF",
                        "slug": "bf",
                        "normalized_code": "BF",
                        "display_name": "Blast Furnace",
                        "product_type": "iron",
                        "allowed": True,
                        "from_year": 2025,
                        "to_year": None,
                    },
                    "eaf": {
                        "code": "EAF",
                        "slug": "eaf",
                        "normalized_code": "EAF",
                        "display_name": "Electric Arc Furnace",
                        "product_type": "steel",
                        "allowed": True,
                        "from_year": 2025,
                        "to_year": 2050,
                    },
                },
            }

            # Create file in expected location
            tech_path = Path(tmpdir) / "data" / "fixtures" / "technologies.json"
            tech_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tech_path, "w", encoding="utf-8") as f:
                json.dump(tech_data, f)

            # Create DataPreparation instance
            prep = DataPreparation(data_directory=tmpdir)
            technologies = prep.get_technologies()

            assert len(technologies) == 2
            assert "bf" in technologies
            assert "eaf" in technologies
            assert technologies["bf"]["display_name"] == "Blast Furnace"
            assert technologies["eaf"]["to_year"] == 2050

    def test_get_technologies_no_data_directory(self):
        """Test when data_directory is not set."""
        prep = DataPreparation(data_directory=None)
        technologies = prep.get_technologies()

        assert technologies == {}

    def test_get_technologies_file_not_found(self):
        """Test when technologies.json doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prep = DataPreparation(data_directory=tmpdir)
            technologies = prep.get_technologies()

            assert technologies == {}

    def test_get_technologies_invalid_json(self):
        """Test handling of invalid JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create invalid JSON file
            tech_path = Path(tmpdir) / "data" / "fixtures" / "technologies.json"
            tech_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tech_path, "w") as f:
                f.write("invalid json content {")

            prep = DataPreparation(data_directory=tmpdir)
            technologies = prep.get_technologies()

            assert technologies == {}

    def test_get_technologies_validation_error(self):
        """Test handling of data that doesn't match schema."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create JSON with invalid schema
            tech_data = {
                "technologies": {
                    "bf": {
                        "code": "BF",
                        # Missing required fields like slug, display_name
                    }
                }
            }

            tech_path = Path(tmpdir) / "data" / "fixtures" / "technologies.json"
            tech_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tech_path, "w", encoding="utf-8") as f:
                json.dump(tech_data, f)

            prep = DataPreparation(data_directory=tmpdir)
            technologies = prep.get_technologies()

            assert technologies == {}

    def test_get_technologies_caching(self):
        """Test that technologies are cached and not re-read unnecessarily."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test technologies.json
            tech_data = {
                "schema_version": 3,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "excel_path": "/test/path.xlsx",
                    "sheet": "Techno-economic details",
                    "extraction_date": datetime.now(timezone.utc).isoformat(),
                    "tech_count": 1,
                    "duplicates": None,
                    "warnings_count": 0,
                },
                "technologies": {
                    "bf": {
                        "code": "BF",
                        "slug": "bf",
                        "normalized_code": "BF",
                        "display_name": "Blast Furnace",
                        "product_type": "iron",
                        "allowed": True,
                        "from_year": 2025,
                        "to_year": None,
                    }
                },
            }

            tech_path = Path(tmpdir) / "data" / "fixtures" / "technologies.json"
            tech_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tech_path, "w", encoding="utf-8") as f:
                json.dump(tech_data, f)

            prep = DataPreparation(data_directory=tmpdir)

            # First call should read from file
            with patch("builtins.open", wraps=open) as mock_open:
                technologies1 = prep.get_technologies()
                assert mock_open.call_count == 1

            # Second call should use cache (same mtime)
            with patch("builtins.open", wraps=open) as mock_open:
                technologies2 = prep.get_technologies()
                assert mock_open.call_count == 0  # Should not read file again

            assert technologies1 == technologies2

    def test_get_technologies_cache_invalidation(self):
        """Test that cache is invalidated when file changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tech_data1 = {
                "schema_version": 3,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "excel_path": "/test/path.xlsx",
                    "sheet": "Techno-economic details",
                    "extraction_date": datetime.now(timezone.utc).isoformat(),
                    "tech_count": 1,
                    "duplicates": None,
                    "warnings_count": 0,
                },
                "technologies": {
                    "bf": {
                        "code": "BF",
                        "slug": "bf",
                        "normalized_code": "BF",
                        "display_name": "Blast Furnace V1",
                        "product_type": "iron",
                        "allowed": True,
                        "from_year": 2025,
                        "to_year": None,
                    }
                },
            }

            tech_path = Path(tmpdir) / "data" / "fixtures" / "technologies.json"
            tech_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tech_path, "w", encoding="utf-8") as f:
                json.dump(tech_data1, f)

            prep = DataPreparation(data_directory=tmpdir)
            technologies1 = prep.get_technologies()
            assert technologies1["bf"]["display_name"] == "Blast Furnace V1"

            # Update the file
            import time

            time.sleep(0.01)  # Ensure different mtime
            tech_data1["technologies"]["bf"]["display_name"] = "Blast Furnace V2"
            with open(tech_path, "w", encoding="utf-8") as f:
                json.dump(tech_data1, f)

            # Should read updated file
            technologies2 = prep.get_technologies()
            assert technologies2["bf"]["display_name"] == "Blast Furnace V2"

    def test_get_technologies_includes_ccs_ccu(self):
        """Test that technologies with CCS and CCU suffixes are included."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tech_data = {
                "schema_version": 3,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "excel_path": "/test/path.xlsx",
                    "sheet": "Techno-economic details",
                    "extraction_date": datetime.now(timezone.utc).isoformat(),
                    "tech_count": 4,
                    "duplicates": None,
                    "warnings_count": 0,
                },
                "technologies": {
                    "bf": {
                        "code": "BF",
                        "slug": "bf",
                        "normalized_code": "BF",
                        "display_name": "Blast Furnace",
                        "product_type": "iron",
                        "allowed": True,
                        "from_year": 2025,
                        "to_year": None,
                    },
                    "bfccs": {
                        "code": "BF+CCS",
                        "slug": "bfccs",
                        "normalized_code": "BFCCS",
                        "display_name": "Blast Furnace with CCS",
                        "product_type": "iron",
                        "allowed": True,
                        "from_year": 2025,
                        "to_year": None,
                    },
                    "bfccu": {
                        "code": "BF+CCU",
                        "slug": "bfccu",
                        "normalized_code": "BFCCU",
                        "display_name": "Blast Furnace with CCU",
                        "product_type": "iron",
                        "allowed": True,
                        "from_year": 2030,
                        "to_year": None,
                    },
                    "eaf": {
                        "code": "EAF",
                        "slug": "eaf",
                        "normalized_code": "EAF",
                        "display_name": "Electric Arc Furnace",
                        "product_type": "steel",
                        "allowed": True,
                        "from_year": 2025,
                        "to_year": None,
                    },
                },
            }

            tech_path = Path(tmpdir) / "data" / "fixtures" / "technologies.json"
            tech_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tech_path, "w", encoding="utf-8") as f:
                json.dump(tech_data, f)

            prep = DataPreparation(data_directory=tmpdir)
            technologies = prep.get_technologies()

            # All technologies should be present, including those with CCS and CCU suffixes
            assert len(technologies) == 4
            assert "bfccs" in technologies
            assert "bfccu" in technologies
            assert technologies["bfccs"]["display_name"] == "Blast Furnace with CCS"
            assert technologies["bfccu"]["from_year"] == 2030
