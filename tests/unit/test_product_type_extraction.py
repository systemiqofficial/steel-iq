"""Test product type extraction from Master Excel."""

import pandas as pd
import pytest
from pathlib import Path
from steelo.adapters.dataprocessing.technology_extractor import extract_technologies, Technology


def test_extract_technologies_with_product_type():
    """Test that product types are extracted correctly from Excel."""
    # Create test dataframe with Product column
    df = pd.DataFrame(
        {
            "Technology": ["BF", "EAF", "DRI", "BOF", "MOE"],
            "Product": ["iron", "steel", "iron", "steel", "steel"],
            "Name in the Dashboard": [
                "Blast Furnace",
                "Electric Arc Furnace",
                "Direct Reduced Iron",
                "Basic Oxygen Furnace",
                "Molten Ore Electrolysis",
            ],
        }
    )

    excel_path = Path("test.xlsx")
    result = extract_technologies(df, excel_path)

    # Verify structure
    assert "technologies" in result
    assert "schema_version" in result
    assert result["schema_version"] == 3  # New version with product_type

    # Verify each technology has product_type
    techs = result["technologies"]
    assert len(techs) == 5

    # Check specific technologies
    assert "bf" in techs
    assert techs["bf"]["product_type"] == "iron"
    assert techs["bf"]["display_name"] == "Blast Furnace"

    assert "eaf" in techs
    assert techs["eaf"]["product_type"] == "steel"

    assert "dri" in techs
    assert techs["dri"]["product_type"] == "iron"


def test_missing_product_column_raises_error():
    """Test that missing Product column raises clear error."""
    df = pd.DataFrame(
        {
            "Technology": ["BF", "EAF"],
            "Name in the Dashboard": ["Blast Furnace", "Electric Arc Furnace"],
            # No Product column
        }
    )

    excel_path = Path("test.xlsx")

    with pytest.raises(ValueError) as exc_info:
        extract_technologies(df, excel_path)

    assert "Product column not found" in str(exc_info.value)


def test_missing_product_value_raises_error():
    """Test that missing product value for a technology raises error."""
    df = pd.DataFrame(
        {
            "Technology": ["BF", "EAF"],
            "Product": ["iron", ""],  # EAF missing product (empty string)
            "Name in the Dashboard": ["Blast Furnace", "Electric Arc Furnace"],
        }
    )

    excel_path = Path("test.xlsx")

    with pytest.raises(ValueError) as exc_info:
        extract_technologies(df, excel_path)

    assert "missing required Product value" in str(exc_info.value)
    assert "EAF" in str(exc_info.value)


def test_invalid_product_value_raises_error():
    """Test that invalid product value raises error."""
    df = pd.DataFrame(
        {
            "Technology": ["BF", "EAF"],
            "Product": ["iron", "aluminum"],  # Invalid product type
            "Name in the Dashboard": ["Blast Furnace", "Electric Arc Furnace"],
        }
    )

    excel_path = Path("test.xlsx")

    with pytest.raises(ValueError) as exc_info:
        extract_technologies(df, excel_path)

    assert "Invalid product type 'aluminum'" in str(exc_info.value)
    assert "Must be 'iron' or 'steel'" in str(exc_info.value)


def test_technology_model_requires_product_type():
    """Test that Technology model requires product_type field."""
    # This should fail without product_type
    with pytest.raises(Exception):  # Pydantic ValidationError
        tech = Technology(
            code="BF",
            slug="bf",
            normalized_code="BF",
            display_name="Blast Furnace",
            # Missing product_type
            allowed=True,
            from_year=2025,
            to_year=None,
        )

    # This should succeed with product_type
    tech = Technology(
        code="BF",
        slug="bf",
        normalized_code="BF",
        display_name="Blast Furnace",
        product_type="iron",  # Required field
        allowed=True,
        from_year=2025,
        to_year=None,
    )
    assert tech.product_type == "iron"
