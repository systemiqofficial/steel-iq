import json
import pytest
from pathlib import Path
from unittest.mock import patch
from steelo.adapters.dataprocessing.technology_extractor import (
    Technology,
    TechnologyConfig,
    extract_technologies,
    write_json_atomic,
    _slug_for,
    _sort_key,
)
from steelo.core.parse import normalize_code_for_dedup


class TestTechnologyExtraction:
    """Test technology extraction from Excel."""

    def test_deduplication_by_normalized_code(self):
        """Test that duplicate codes (BF, bf, B.F.) result in only one technology."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "Technology": ["BF", "bf", "B.F.", "EAF"],  # Three variations of BF
                "Name in the Dashboard": ["Blast Furnace 1", "Blast Furnace 2", "Blast Furnace 3", "Electric Arc"],
                "Product": ["iron", "iron", "iron", "steel"],
            }
        )

        result = extract_technologies(df, Path("test.xlsx"))

        # Should only have 2 technologies (one BF, one EAF)
        assert len(result["technologies"]) == 2
        assert "bf" in result["technologies"]
        assert "eaf" in result["technologies"]

        # Check that duplicates are tracked (order-agnostic)
        duplicates = result["source"]["duplicates"]
        assert len(duplicates) == 2
        assert set(map(str.lower, duplicates)) == {"bf", "b.f."}

    def test_ccs_ccu_as_technology_suffixes(self):
        """Test that technologies with CCS and CCU suffixes are properly handled."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "Technology": ["BF", "BF+CCS", "EAF", "EAF+CCU"],
                "Name in the Dashboard": [
                    "Blast Furnace",
                    "BF with Carbon Capture",
                    "Electric Arc",
                    "EAF with Carbon Util",
                ],
                "Product": ["iron", "iron", "steel", "steel"],
            }
        )

        result = extract_technologies(df, Path("test.xlsx"))

        # Should have all 4 technologies (including those with CCS and CCU suffixes)
        assert len(result["technologies"]) == 4
        assert "bfccs" in result["technologies"]
        assert "eafccu" in result["technologies"]

    def test_missing_display_name_column(self):
        """Test fallback when display name column is missing."""
        import pandas as pd
        from steelo.adapters.dataprocessing import technology_extractor

        df = pd.DataFrame(
            {
                "Technology": ["BF", "EAF"],
                "Product": ["iron", "steel"],
                # No 'Name in the Dashboard' column
            }
        )

        with patch.object(technology_extractor, "logger") as mock_logger:
            result = extract_technologies(df, Path("test.xlsx"))

            # Should use technology codes as display names
            assert result["technologies"]["bf"]["display_name"] == "BF"
            assert result["technologies"]["eaf"]["display_name"] == "EAF"

            # Should have logged warning
            mock_logger.warning.assert_any_call(
                "Display name column not found; using Technology codes as display names."
            )

    def test_atomic_write_with_utf8(self):
        """Test atomic file writing with UTF-8 encoding."""
        import tempfile

        test_data = {"test": "data", "unicode": "café ☕", "emoji": "🎉"}

        with tempfile.TemporaryDirectory() as tmpdir:
            dest_path = Path(tmpdir) / "test.json"
            write_json_atomic(test_data, dest_path)

            assert dest_path.exists()
            with open(dest_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                assert loaded == test_data
                assert loaded["unicode"] == "café ☕"
                assert loaded["emoji"] == "🎉"

    def test_timezone_aware_timestamps(self):
        """Test that timestamps include timezone information."""
        import pandas as pd
        import re

        df = pd.DataFrame({"Technology": ["BF"], "Name in the Dashboard": ["Blast Furnace"], "Product": ["iron"]})
        result = extract_technologies(df, Path("x.xlsx"))

        # Check ISO format with timezone
        assert isinstance(result["generated_at"], str)
        # Should match either +00:00 or Z format
        assert re.match(r"^\d{4}-\d{2}-\d{2}T.*(\+00:00|Z)$", result["generated_at"])
        assert re.match(r"^\d{4}-\d{2}-\d{2}T.*(\+00:00|Z)$", result["source"]["extraction_date"])

    def test_deterministic_ordering(self):
        """Test that technologies are extracted in deterministic order."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "Technology": ["ZZZ", "AAA", "MMM"],
                "Name in the Dashboard": ["Last Tech", "First Tech", "Middle Tech"],
                "Product": ["steel", "iron", "steel"],
            }
        )

        result = extract_technologies(df, Path("test.xlsx"))
        techs = list(result["technologies"].keys())

        # Should be ordered alphabetically by normalized slug
        assert techs == ["aaa", "mmm", "zzz"]

    def test_slug_normalization_preference(self):
        """Test that simpler codes are preferred and slug is normalized."""
        import pandas as pd

        # Create DataFrame with BF appearing in different forms
        # BF should be preferred over B.F. due to lower non-alphanumeric count
        df = pd.DataFrame(
            {
                "Technology": ["B.F.", "BF", "b-f"],  # Different forms of same tech
                "Name in the Dashboard": ["Blast Furnace Dot", "Blast Furnace Simple", "Blast Furnace Dash"],
                "Product": ["iron", "iron", "iron"],
            }
        )

        result = extract_technologies(df, Path("test.xlsx"))

        # Should have only one technology with normalized slug 'bf'
        assert len(result["technologies"]) == 1
        assert "bf" in result["technologies"]

        # Should prefer the simpler 'BF' code
        tech = result["technologies"]["bf"]
        assert tech["code"] == "BF"  # Simple form preferred
        assert tech["display_name"] == "Blast Furnace Simple"

        # Check that duplicates are tracked
        duplicates = result["source"]["duplicates"]
        assert len(duplicates) == 2
        # B.F. and b-f were discarded as duplicates
        assert set(d.lower() for d in duplicates) == {"b.f.", "b-f"}

    def test_year_validation(self):
        """Test that year range validation works."""
        from pydantic import ValidationError

        # Invalid: to_year before from_year
        with pytest.raises(ValidationError):
            Technology(
                code="BF",
                slug="bf",
                normalized_code="BF",
                display_name="Blast Furnace",
                product_type="iron",
                from_year=2030,
                to_year=2025,  # Invalid
            )

    def test_normalize_code_for_dedup(self):
        """Test code normalization for deduplication."""
        assert normalize_code_for_dedup("BF") == "BF"
        assert normalize_code_for_dedup("bf") == "BF"
        assert normalize_code_for_dedup("B.F.") == "BF"
        assert normalize_code_for_dedup("B-F") == "BF"
        assert normalize_code_for_dedup("b_f") == "BF"
        assert normalize_code_for_dedup(" B F ") == "BF"

    def test_slug_for(self):
        """Test slug generation from code."""
        assert _slug_for("BF") == "bf"
        assert _slug_for("B.F.") == "bf"
        assert _slug_for("DRI-EAF") == "drieaf"

    def test_sort_key(self):
        """Test sort key preference for simple codes."""
        # Simple code should sort before complex code
        assert _sort_key("BF") < _sort_key("B.F.")
        assert _sort_key("DRI") < _sort_key("D.R.I.")
        # Alphabetical ordering when complexity is same
        assert _sort_key("AAA") < _sort_key("ZZZ")

    def test_technology_config_schema(self):
        """Test TechnologyConfig schema validation."""
        from datetime import datetime, timezone

        config = TechnologyConfig(
            schema_version=3,
            generated_at=datetime.now(timezone.utc),
            source={"excel_path": "/path/to/file.xlsx", "sheet": "Techno-economic details"},
            technologies={
                "bf": Technology(
                    code="BF",
                    slug="bf",
                    normalized_code="BF",
                    display_name="Blast Furnace",
                    product_type="iron",
                    allowed=True,
                    from_year=2025,
                    to_year=None,
                )
            },
        )

        # Should serialize to JSON properly
        json_data = config.model_dump(mode="json")
        assert json_data["schema_version"] == 3
        assert "generated_at" in json_data
        assert json_data["technologies"]["bf"]["code"] == "BF"

    def test_whitespace_normalization_in_display_name(self):
        """Test that display names have normalized whitespace."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "Technology": ["BF"],
                "Name in the Dashboard": ["  Blast   Furnace   "],  # Multiple spaces
                "Product": ["iron"],
            }
        )

        result = extract_technologies(df, Path("test.xlsx"))

        # Should normalize whitespace
        assert result["technologies"]["bf"]["display_name"] == "Blast Furnace"

    def test_empty_dataframe(self):
        """Test extraction with empty dataframe."""
        import pandas as pd

        df = pd.DataFrame({"Technology": [], "Name in the Dashboard": [], "Product": []})
        result = extract_technologies(df, Path("test.xlsx"))

        assert len(result["technologies"]) == 0
        assert result["source"]["tech_count"] == 0

    def test_null_values_in_technology_column(self):
        """Test handling of null values in technology column."""
        import pandas as pd
        import numpy as np

        df = pd.DataFrame(
            {
                "Technology": ["BF", np.nan, "EAF", None],
                "Name in the Dashboard": ["Blast Furnace", "Invalid", "Electric Arc", "Invalid2"],
                "Product": ["iron", np.nan, "steel", None],
            }
        )

        result = extract_technologies(df, Path("test.xlsx"))

        # Should only have 2 technologies (nulls excluded)
        assert len(result["technologies"]) == 2
        assert "bf" in result["technologies"]
        assert "eaf" in result["technologies"]
