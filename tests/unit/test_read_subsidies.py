"""Tests for read_subsidies function and its helper functions."""

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.excel_reader import (
    _expand_technology_pattern,
    _normalize_cost_item,
    _parse_subsidy_type,
    read_subsidies,
)


# =============================================================================
# Tests for _expand_technology_pattern
# =============================================================================

ALL_TECHNOLOGIES = ["BF", "BF+CCS", "BF+CCU", "DRI", "DRI+CCS", "DRI+CCU", "EAF", "SR+CCS"]


def test_expand_technology_pattern_empty_string_returns_all():
    """Test that empty string expands to all technologies."""
    # Arrange
    pattern = ""

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert result == ALL_TECHNOLOGIES


def test_expand_technology_pattern_none_returns_all():
    """Test that None expands to all technologies."""
    # Arrange
    pattern = None

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert result == ALL_TECHNOLOGIES


def test_expand_technology_pattern_nan_returns_all():
    """Test that NaN expands to all technologies."""
    # Arrange
    pattern = float("nan")

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert result == ALL_TECHNOLOGIES


def test_expand_technology_pattern_whitespace_returns_all():
    """Test that whitespace-only string expands to all technologies."""
    # Arrange
    pattern = "   "

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert result == ALL_TECHNOLOGIES


def test_expand_technology_pattern_wildcard_ccs():
    """Test that CCS* wildcard matches all technologies containing CCS."""
    # Arrange
    pattern = "CCS*"

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert set(result) == {"BF+CCS", "DRI+CCS", "SR+CCS"}


def test_expand_technology_pattern_wildcard_dri():
    """Test that DRI* wildcard matches all DRI technologies."""
    # Arrange
    pattern = "DRI*"

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert set(result) == {"DRI", "DRI+CCS", "DRI+CCU"}


def test_expand_technology_pattern_exact_match():
    """Test that exact technology name returns single match."""
    # Arrange
    pattern = "DRI"

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert result == ["DRI"]


def test_expand_technology_pattern_exact_match_with_plus():
    """Test that technology name with plus sign returns single match."""
    # Arrange
    pattern = "BF+CCS"

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert result == ["BF+CCS"]


def test_expand_technology_pattern_unknown_returns_empty(caplog):
    """Test that unknown technology returns empty list with warning."""
    # Arrange
    pattern = "UNKNOWN_TECH"

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert result == []
    assert "not found in available technologies" in caplog.text


def test_expand_technology_pattern_wildcard_no_match(caplog):
    """Test that wildcard with no matches returns empty list with warning."""
    # Arrange
    pattern = "NOMATCH*"

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert result == []
    assert "matched no technologies" in caplog.text


def test_expand_technology_pattern_trims_whitespace():
    """Test that pattern with whitespace is trimmed."""
    # Arrange
    pattern = "  DRI  "

    # Act
    result = _expand_technology_pattern(pattern, ALL_TECHNOLOGIES)

    # Assert
    assert result == ["DRI"]


# =============================================================================
# Tests for _normalize_cost_item
# =============================================================================


def test_normalize_cost_item_empty_defaults_to_opex():
    """Test that empty string defaults to opex."""
    # Arrange
    cost_item = ""

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "opex"


def test_normalize_cost_item_none_defaults_to_opex():
    """Test that None defaults to opex."""
    # Arrange
    cost_item = None

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "opex"


def test_normalize_cost_item_nan_defaults_to_opex():
    """Test that NaN defaults to opex."""
    # Arrange
    cost_item = float("nan")

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "opex"


def test_normalize_cost_item_opex_lowercase():
    """Test that opex lowercase is normalized."""
    # Arrange
    cost_item = "opex"

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "opex"


def test_normalize_cost_item_opex_uppercase():
    """Test that OPEX uppercase is normalized to opex."""
    # Arrange
    cost_item = "OPEX"

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "opex"


def test_normalize_cost_item_capex():
    """Test that capex is normalized."""
    # Arrange
    cost_item = "CAPEX"

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "capex"


def test_normalize_cost_item_cost_of_debt():
    """Test that 'cost of debt' is normalized."""
    # Arrange
    cost_item = "Cost of Debt"

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "cost of debt"


def test_normalize_cost_item_debt_shorthand():
    """Test that 'debt' shorthand is normalized to 'cost of debt'."""
    # Arrange
    cost_item = "debt"

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "cost of debt"


def test_normalize_cost_item_hydrogen():
    """Test that hydrogen is normalized."""
    # Arrange
    cost_item = "hydrogen"

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "hydrogen"


def test_normalize_cost_item_h2_shorthand():
    """Test that h2 shorthand is normalized to hydrogen."""
    # Arrange
    cost_item = "h2"

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "hydrogen"


def test_normalize_cost_item_electricity():
    """Test that electricity is normalized."""
    # Arrange
    cost_item = "electricity"

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "electricity"


def test_normalize_cost_item_unknown_returns_none(caplog):
    """Test that unknown cost item returns None with warning."""
    # Arrange
    cost_item = "unknown_item"

    # Act
    result = _normalize_cost_item(cost_item, row_index=5)

    # Assert
    assert result is None
    assert "unknown cost item" in caplog.text
    assert "row 5" in caplog.text


def test_normalize_cost_item_trims_whitespace():
    """Test that cost item with whitespace is trimmed."""
    # Arrange
    cost_item = "  capex  "

    # Act
    result = _normalize_cost_item(cost_item, row_index=0)

    # Assert
    assert result == "capex"


# =============================================================================
# Tests for _parse_subsidy_type
# =============================================================================


def test_parse_subsidy_type_empty_defaults_to_absolute():
    """Test that empty string defaults to absolute."""
    # Arrange
    subsidy_type = ""

    # Act
    result = _parse_subsidy_type(subsidy_type, cost_item="opex", row_index=0)

    # Assert
    assert result == "absolute"


def test_parse_subsidy_type_none_defaults_to_absolute():
    """Test that None defaults to absolute."""
    # Arrange
    subsidy_type = None

    # Act
    result = _parse_subsidy_type(subsidy_type, cost_item="opex", row_index=0)

    # Assert
    assert result == "absolute"


def test_parse_subsidy_type_nan_defaults_to_absolute():
    """Test that NaN defaults to absolute."""
    # Arrange
    subsidy_type = float("nan")

    # Act
    result = _parse_subsidy_type(subsidy_type, cost_item="opex", row_index=0)

    # Assert
    assert result == "absolute"


def test_parse_subsidy_type_absolute():
    """Test that 'Absolute' is parsed correctly."""
    # Arrange
    subsidy_type = "Absolute"

    # Act
    result = _parse_subsidy_type(subsidy_type, cost_item="opex", row_index=0)

    # Assert
    assert result == "absolute"


def test_parse_subsidy_type_relative():
    """Test that 'Relative' is parsed correctly."""
    # Arrange
    subsidy_type = "Relative"

    # Act
    result = _parse_subsidy_type(subsidy_type, cost_item="opex", row_index=0)

    # Assert
    assert result == "relative"


def test_parse_subsidy_type_relative_for_capex_allowed():
    """Test that relative subsidy is allowed for capex."""
    # Arrange
    subsidy_type = "relative"

    # Act
    result = _parse_subsidy_type(subsidy_type, cost_item="capex", row_index=0)

    # Assert
    assert result == "relative"


def test_parse_subsidy_type_relative_for_cost_of_debt_returns_none(caplog):
    """Test that relative subsidy for cost of debt returns None with warning."""
    # Arrange
    subsidy_type = "relative"

    # Act
    result = _parse_subsidy_type(subsidy_type, cost_item="cost of debt", row_index=3)

    # Assert
    assert result is None
    assert "relative subsidies not supported for cost of debt" in caplog.text
    assert "row 3" in caplog.text


def test_parse_subsidy_type_trims_whitespace():
    """Test that subsidy type with whitespace is trimmed."""
    # Arrange
    subsidy_type = "  relative  "

    # Act
    result = _parse_subsidy_type(subsidy_type, cost_item="opex", row_index=0)

    # Assert
    assert result == "relative"


# =============================================================================
# Tests for read_subsidies
# =============================================================================


def _make_country_df():
    """Create mock country mapping DataFrame with trade bloc columns."""
    return pd.DataFrame(
        {
            "ISO 3-letter code": ["CAN", "MEX", "USA", "DEU", "FRA"],
            "Country": ["Canada", "Mexico", "United States", "Germany", "France"],
            "NAFTA": [True, True, True, False, False],
            "EU": [False, False, False, True, True],
        }
    )


def _make_techno_df():
    """Create mock techno-economic details DataFrame with technology names."""
    return pd.DataFrame({"Technology": ["BF", "BF+CCS", "DRI", "DRI+CCS", "EAF"]})


def _make_subsidies_df(rows: list[dict]) -> pd.DataFrame:
    """Create subsidies DataFrame from row dicts."""
    columns = [
        "Scenario name",
        "Location",
        "Technology",
        "Cost item",
        "Subsidy type",
        "Subsidy amount",
        "Start year",
        "End year",
    ]
    data = {col: [] for col in columns}
    for row in rows:
        for col in columns:
            data[col].append(row.get(col))
    return pd.DataFrame(data)


def test_read_subsidies_basic_single_subsidy():
    """Test reading a single basic subsidy."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Test",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "capex",
                "Subsidy type": "absolute",
                "Subsidy amount": 200,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 1
    assert result[0].iso3 == "DEU"
    assert result[0].technology_name == "DRI"
    assert result[0].cost_item == "capex"
    assert result[0].subsidy_type == "absolute"
    assert result[0].subsidy_amount == 200.0


def test_read_subsidies_trade_bloc_expansion():
    """Test that trade bloc location expands to multiple countries."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "NAFTA Subsidy",
                "Location": "NAFTA",
                "Technology": "DRI",
                "Cost item": "capex",
                "Subsidy type": "absolute",
                "Subsidy amount": 100,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert - 3 NAFTA countries
    assert len(result) == 3
    iso3_codes = {s.iso3 for s in result}
    assert iso3_codes == {"CAN", "MEX", "USA"}


def test_read_subsidies_empty_technology_expands_to_all():
    """Test that empty technology field expands to all technologies."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "All Tech",
                "Location": "DEU",
                "Technology": None,
                "Cost item": "opex",
                "Subsidy type": "absolute",
                "Subsidy amount": 50,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert - 5 technologies
    assert len(result) == 5
    tech_names = {s.technology_name for s in result}
    assert tech_names == {"BF", "BF+CCS", "DRI", "DRI+CCS", "EAF"}


def test_read_subsidies_wildcard_technology_expansion():
    """Test that wildcard technology pattern expands to matching technologies."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "CCS Tech",
                "Location": "DEU",
                "Technology": "CCS*",
                "Cost item": "opex",
                "Subsidy type": "absolute",
                "Subsidy amount": 75,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert - 2 CCS technologies (BF+CCS, DRI+CCS)
    assert len(result) == 2
    tech_names = {s.technology_name for s in result}
    assert tech_names == {"BF+CCS", "DRI+CCS"}


def test_read_subsidies_empty_cost_item_defaults_to_opex():
    """Test that empty cost item defaults to opex."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Default Cost",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": None,
                "Subsidy type": "absolute",
                "Subsidy amount": 100,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 1
    assert result[0].cost_item == "opex"


def test_read_subsidies_empty_subsidy_type_defaults_to_absolute():
    """Test that empty subsidy type defaults to absolute."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Default Type",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "capex",
                "Subsidy type": None,
                "Subsidy amount": 100,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 1
    assert result[0].subsidy_type == "absolute"


def test_read_subsidies_relative_percentage_conversion():
    """Test that relative subsidy amounts are converted from percentage to decimal."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Relative",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "capex",
                "Subsidy type": "relative",
                "Subsidy amount": 10,  # 10%
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert - 10% -> 0.1
    assert len(result) == 1
    assert result[0].subsidy_amount == pytest.approx(0.1)


def test_read_subsidies_absolute_opex_no_conversion():
    """Test that absolute OPEX subsidies are not converted."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Absolute OPEX",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "opex",
                "Subsidy type": "absolute",
                "Subsidy amount": 200,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert - no conversion
    assert len(result) == 1
    assert result[0].subsidy_amount == 200.0


def test_read_subsidies_skip_empty_subsidy_amount(caplog):
    """Test that rows with empty subsidy amount are skipped."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Empty Amount",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "opex",
                "Subsidy type": "absolute",
                "Subsidy amount": None,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 0
    assert "empty subsidy amount" in caplog.text


def test_read_subsidies_skip_unknown_cost_item(caplog):
    """Test that rows with unknown cost item are skipped."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Unknown Cost",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "unknown_item",
                "Subsidy type": "absolute",
                "Subsidy amount": 100,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 0
    assert "unknown cost item" in caplog.text


def test_read_subsidies_skip_relative_cost_of_debt(caplog):
    """Test that relative subsidies for cost of debt are skipped."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Relative Debt",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "cost of debt",
                "Subsidy type": "relative",
                "Subsidy amount": 10,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 0
    assert "relative subsidies not supported for cost of debt" in caplog.text


def test_read_subsidies_column_normalization_with_newlines():
    """Test that column headers with newlines are normalized."""
    # Arrange
    subsidies_df = pd.DataFrame(
        {
            "Scenario name\n(name of policy)": ["Test"],
            "Location\n(country or trade bloc)": ["DEU"],
            "Technology\n(or empty for all)": ["DRI"],
            "Cost item\n(OPEX/CAPEX/etc)": ["capex"],
            "Subsidy type\n(absolute/relative)": ["absolute"],
            "Subsidy amount\n(USD or %)": [100],
            "Start year": [2025],
            "End year": [2050],
        }
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 1
    assert result[0].iso3 == "DEU"


def test_read_subsidies_missing_required_column_raises_error():
    """Test that missing required columns raise ValueError."""
    # Arrange
    subsidies_df = pd.DataFrame(
        {
            "Scenario name": ["Test"],
            "Location": ["DEU"],
            # Missing Technology column
            "Cost item": ["capex"],
            "Subsidy type": ["absolute"],
            "Subsidy amount": [100],
            "Start year": [2025],
            "End year": [2050],
        }
    )

    # Act & Assert
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        with pytest.raises(ValueError, match="Missing required columns.*Technology"):
            read_subsidies(Path("dummy.xlsx"))


def test_read_subsidies_combined_expansion_trade_bloc_and_all_tech():
    """Test combined trade bloc and all-technology expansion."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Big Expansion",
                "Location": "NAFTA",
                "Technology": None,  # All technologies
                "Cost item": "opex",
                "Subsidy type": "absolute",
                "Subsidy amount": 50,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert - 3 countries × 5 technologies = 15 subsidies
    assert len(result) == 15
    iso3_codes = {s.iso3 for s in result}
    tech_names = {s.technology_name for s in result}
    assert iso3_codes == {"CAN", "MEX", "USA"}
    assert tech_names == {"BF", "BF+CCS", "DRI", "DRI+CCS", "EAF"}


def test_read_subsidies_multiple_rows():
    """Test reading multiple subsidy rows."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Row 1",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "capex",
                "Subsidy type": "absolute",
                "Subsidy amount": 100,
                "Start year": 2025,
                "End year": 2040,
            },
            {
                "Scenario name": "Row 2",
                "Location": "FRA",
                "Technology": "EAF",
                "Cost item": "opex",
                "Subsidy type": "relative",
                "Subsidy amount": 15,
                "Start year": 2030,
                "End year": 2050,
            },
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 2
    deu_subsidy = next(s for s in result if s.iso3 == "DEU")
    fra_subsidy = next(s for s in result if s.iso3 == "FRA")

    assert deu_subsidy.technology_name == "DRI"
    assert deu_subsidy.subsidy_amount == 100.0

    assert fra_subsidy.technology_name == "EAF"
    assert fra_subsidy.subsidy_amount == pytest.approx(0.15)


def test_read_subsidies_hydrogen_cost_item():
    """Test that hydrogen cost item is recognized."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Hydrogen",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "hydrogen",
                "Subsidy type": "absolute",
                "Subsidy amount": 50,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 1
    assert result[0].cost_item == "hydrogen"


def test_read_subsidies_electricity_cost_item():
    """Test that electricity cost item is recognized."""
    # Arrange
    subsidies_df = _make_subsidies_df(
        [
            {
                "Scenario name": "Electricity",
                "Location": "DEU",
                "Technology": "DRI",
                "Cost item": "electricity",
                "Subsidy type": "relative",
                "Subsidy amount": 5,
                "Start year": 2025,
                "End year": 2050,
            }
        ]
    )

    # Act
    with patch("steelo.adapters.dataprocessing.excel_reader.pd.read_excel") as mock_read:
        mock_read.side_effect = [subsidies_df, _make_country_df(), _make_techno_df()]
        result = read_subsidies(Path("dummy.xlsx"))

    # Assert
    assert len(result) == 1
    assert result[0].cost_item == "electricity"
    assert result[0].subsidy_amount == pytest.approx(0.05)
