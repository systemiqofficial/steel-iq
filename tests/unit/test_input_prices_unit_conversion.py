"""
Unit tests for input prices unit conversion in read_regional_input_prices_from_master_excel.

Ensures that commodities whose BOM consumption is stored in tonnes per tonne
(bio-PCI, PCI, coke, coking coal, hydrogen) get their prices converted from USD/kg
to USD/t during ingestion.
"""

import pandas as pd
import pytest
from steelo.adapters.dataprocessing.excel_reader import read_regional_input_prices_from_master_excel
from steelo.domain.constants import T_TO_KG
from steelo.devdata import get_plant


@pytest.fixture
def temp_excel_with_mixed_units(tmp_path):
    """Create a temporary Excel file with mixed unit materials for testing."""
    excel_path = tmp_path / "test_input_costs.xlsx"

    # Create test data with various materials and units
    data = {
        "ISO-3 code": ["USA", "USA", "USA", "USA", "USA"],
        "Commodity": ["Bio-PCI", "PCI", "Coke", "Coking coal", "Hydrogen"],  # Production names (will be normalized)
        "Unit": ["USD/kg", "USD/kg", "USD/kg", "USD/kg", "USD/kg"],
        "2025": [0.66, 0.14, 0.30, 0.18, 5.00],  # Test prices in USD/kg
        "2026": [0.68, 0.15, 0.31, 0.19, 5.10],
    }

    df = pd.DataFrame(data)
    df.to_excel(excel_path, index=False)

    return excel_path


def test_bio_pci_conversion_from_usd_per_kg_to_usd_per_t(temp_excel_with_mixed_units):
    """Test that bio-PCI prices are converted from USD/kg to USD/t (multiply by 1000)."""
    # Read input costs
    result = read_regional_input_prices_from_master_excel(
        excel_path=temp_excel_with_mixed_units, input_costs_sheet="Sheet1"
    )

    # Find USA 2025 data
    usa_2025 = next((r for r in result if r.iso3 == "USA" and r.year == 2025), None)
    assert usa_2025 is not None, "USA 2025 data not found"

    # Bio-PCI price should be converted: 0.66 USD/kg × 1000 = 660 USD/t
    expected_bio_pci_price = 0.66 * T_TO_KG
    assert usa_2025.costs["bio_pci"] == pytest.approx(expected_bio_pci_price, rel=1e-6), (
        f"bio_pci price should be {expected_bio_pci_price} USD/t but got {usa_2025.costs['bio_pci']}"
    )


def test_pci_conversion_from_usd_per_kg_to_usd_per_t(temp_excel_with_mixed_units):
    """Test that PCI prices are converted from USD/kg to USD/t (multiply by 1000)."""
    result = read_regional_input_prices_from_master_excel(
        excel_path=temp_excel_with_mixed_units, input_costs_sheet="Sheet1"
    )

    usa_2025 = next((r for r in result if r.iso3 == "USA" and r.year == 2025), None)
    assert usa_2025 is not None

    # PCI price should be converted: 0.14 USD/kg × 1000 = 140 USD/t
    expected_pci_price = 0.14 * T_TO_KG
    assert usa_2025.costs["pci"] == pytest.approx(expected_pci_price, rel=1e-6), (
        f"pci price should be {expected_pci_price} USD/t but got {usa_2025.costs['pci']}"
    )


def test_coke_conversion_from_usd_per_kg_to_usd_per_t(temp_excel_with_mixed_units):
    """Test that coke prices are converted from USD/kg to USD/t (multiply by 1000)."""
    result = read_regional_input_prices_from_master_excel(
        excel_path=temp_excel_with_mixed_units, input_costs_sheet="Sheet1"
    )

    usa_2025 = next((r for r in result if r.iso3 == "USA" and r.year == 2025), None)
    assert usa_2025 is not None

    # Coke price should be converted: 0.30 USD/kg × 1000 = 300 USD/t
    expected_coke_price = 0.30 * T_TO_KG
    assert usa_2025.costs["coke"] == pytest.approx(expected_coke_price, rel=1e-6), (
        f"coke price should be {expected_coke_price} USD/t but got {usa_2025.costs['coke']}"
    )


def test_coking_coal_conversion_from_usd_per_kg_to_usd_per_t(temp_excel_with_mixed_units):
    """Test that coking coal prices are converted from USD/kg to USD/t (multiply by 1000)."""
    result = read_regional_input_prices_from_master_excel(
        excel_path=temp_excel_with_mixed_units, input_costs_sheet="Sheet1"
    )

    usa_2025 = next((r for r in result if r.iso3 == "USA" and r.year == 2025), None)
    assert usa_2025 is not None

    # Coking coal price should be converted: 0.18 USD/kg × 1000 = 180 USD/t
    expected_coking_coal_price = 0.18 * T_TO_KG
    assert usa_2025.costs["coking_coal"] == pytest.approx(expected_coking_coal_price, rel=1e-6), (
        f"coking_coal price should be {expected_coking_coal_price} USD/t but got {usa_2025.costs['coking_coal']}"
    )


def test_hydrogen_converts_from_usd_per_kg_to_usd_per_t(temp_excel_with_mixed_units):
    """Test that hydrogen prices are converted from USD/kg to USD/t (multiply by 1000)."""
    result = read_regional_input_prices_from_master_excel(
        excel_path=temp_excel_with_mixed_units, input_costs_sheet="Sheet1"
    )

    usa_2025 = next((r for r in result if r.iso3 == "USA" and r.year == 2025), None)
    assert usa_2025 is not None

    # Hydrogen price should be converted: 5.00 USD/kg × 1000 = 5000 USD/t
    expected_hydrogen_price = 5.00 * T_TO_KG
    assert usa_2025.costs["hydrogen"] == pytest.approx(expected_hydrogen_price, rel=1e-6), (
        f"hydrogen price should be {expected_hydrogen_price} USD/t but got {usa_2025.costs['hydrogen']}"
    )


def test_conversion_across_multiple_years(temp_excel_with_mixed_units):
    """Test that conversion is applied consistently across all years."""
    result = read_regional_input_prices_from_master_excel(
        excel_path=temp_excel_with_mixed_units, input_costs_sheet="Sheet1"
    )

    # Check 2026 data
    usa_2026 = next((r for r in result if r.iso3 == "USA" and r.year == 2026), None)
    assert usa_2026 is not None

    # Bio-PCI 2026: 0.68 USD/kg × 1000 = 680 USD/t
    assert usa_2026.costs["bio_pci"] == pytest.approx(0.68 * T_TO_KG, rel=1e-6)
    # Coking coal 2026: 0.19 USD/kg × 1000 = 190 USD/t
    assert usa_2026.costs["coking_coal"] == pytest.approx(0.19 * T_TO_KG, rel=1e-6)
    # Hydrogen 2026: 5.10 USD/kg × 1000 = 5100 USD/t
    assert usa_2026.costs["hydrogen"] == pytest.approx(5.10 * T_TO_KG, rel=1e-6)


def test_bio_pci_cost_calculation_example():
    """
    Test the full cost calculation example from the issue report.

    Given:
    - Bio-PCI price: 0.66 USD/kg (AFG)
    - Bio-PCI usage: 0.233 t PCI / t iron

    Expected cost: 0.66 USD/kg × 1000 kg/t × 0.233 t PCI/t iron = 153.78 USD/t iron

    Without conversion, we would get: 0.66 × 0.233 = 0.154 USD/t iron (1000x too low)
    """
    # After conversion, price should be in USD/t
    bio_pci_price_per_kg = 0.66
    bio_pci_price_per_t = bio_pci_price_per_kg * T_TO_KG  # 660 USD/t

    # Usage in BOM
    bio_pci_usage_t_per_t = 0.233

    # Calculate cost per tonne of iron
    expected_cost_per_t_iron = bio_pci_price_per_t * bio_pci_usage_t_per_t

    # This should be approximately 153.78 USD/t iron
    assert expected_cost_per_t_iron == pytest.approx(153.78, rel=1e-2), (
        f"Bio-PCI cost should be 153.78 USD/t iron, but got {expected_cost_per_t_iron}"
    )

    # Verify the conversion fixes the 1000x underestimation
    # Without conversion: 0.66 × 0.233 = 0.154 USD/t iron (wrong)
    wrong_cost_without_conversion = bio_pci_price_per_kg * bio_pci_usage_t_per_t
    assert wrong_cost_without_conversion == pytest.approx(0.154, rel=1e-2)

    # With conversion, we get the correct value (1000x higher)
    assert expected_cost_per_t_iron / wrong_cost_without_conversion == pytest.approx(1000, rel=1e-1)


def test_coking_coal_cost_calculation_example():
    """
    Test the full cost calculation example for coking coal.

    Given:
    - Coking coal price: 0.18 USD/kg (AUS)
    - Coking coal usage: 0.443 t coking coal / t iron (estimated for IO_mid)

    Expected cost: 0.18 USD/kg × 1000 kg/t × 0.443 t coal/t iron = 79.74 USD/t iron

    Without conversion, we would get: 0.18 × 0.443 = 0.080 USD/t iron (1000x too low)
    """
    # After conversion, price should be in USD/t
    coking_coal_price_per_kg = 0.18
    coking_coal_price_per_t = coking_coal_price_per_kg * T_TO_KG  # 180 USD/t

    # Usage in BOM (typical value for IO_mid from investigation)
    coking_coal_usage_t_per_t = 0.443

    # Calculate cost per tonne of iron
    expected_cost_per_t_iron = coking_coal_price_per_t * coking_coal_usage_t_per_t

    # This should be approximately 79.74 USD/t iron
    assert expected_cost_per_t_iron == pytest.approx(79.74, rel=1e-2), (
        f"Coking coal cost should be 79.74 USD/t iron, but got {expected_cost_per_t_iron}"
    )

    # Verify the conversion fixes the 1000x underestimation
    # Without conversion: 0.18 × 0.443 = 0.080 USD/t iron (wrong)
    wrong_cost_without_conversion = coking_coal_price_per_kg * coking_coal_usage_t_per_t
    assert wrong_cost_without_conversion == pytest.approx(0.080, rel=1e-2)

    # With conversion, we get the correct value (1000x higher)
    assert expected_cost_per_t_iron / wrong_cost_without_conversion == pytest.approx(1000, rel=1e-1)


@pytest.fixture
def temp_excel_with_usd_per_t_materials(tmp_path):
    """Create Excel file with materials already in USD/t (should not be converted)."""
    excel_path = tmp_path / "test_input_costs_per_t.xlsx"

    data = {
        "ISO-3 code": ["USA", "USA"],
        "Commodity": ["Scrap", "Iron ore"],  # Use production-style names
        "Unit": ["USD/t", "USD/t"],
        "2025": [300.0, 120.0],  # Already in USD/t
    }

    df = pd.DataFrame(data)
    df.to_excel(excel_path, index=False)

    return excel_path


def test_usd_per_t_materials_not_double_converted(temp_excel_with_usd_per_t_materials):
    """Test that materials already in USD/t are not converted (no double conversion)."""
    result = read_regional_input_prices_from_master_excel(
        excel_path=temp_excel_with_usd_per_t_materials, input_costs_sheet="Sheet1"
    )

    usa_2025 = next((r for r in result if r.iso3 == "USA" and r.year == 2025), None)
    assert usa_2025 is not None

    # Prices should remain unchanged (no conversion for USD/t)
    assert usa_2025.costs["scrap"] == pytest.approx(300.0, rel=1e-6)
    assert usa_2025.costs["iron_ore"] == pytest.approx(120.0, rel=1e-6)


def test_bio_pci_with_hyphen_normalization(tmp_path):
    """
    Test that "Bio-PCI" with a hyphen is correctly normalized and converted.

    Production data uses "Bio-PCI" (with hyphen), which normalizes to "bio_pci" (underscore).
    """
    excel_path = tmp_path / "test_bio_pci_hyphen.xlsx"

    data = {
        "ISO-3 code": ["USA"],
        "Commodity": ["Bio-PCI"],
        "Unit": ["USD/kg"],
        "2025": [0.66],
    }

    df = pd.DataFrame(data)
    df.to_excel(excel_path, index=False)

    result = read_regional_input_prices_from_master_excel(excel_path=excel_path, input_costs_sheet="Sheet1")

    usa_2025 = next((r for r in result if r.iso3 == "USA" and r.year == 2025), None)
    assert usa_2025 is not None

    assert "bio_pci" in usa_2025.costs, f"Expected 'bio_pci' in costs dict, but got keys: {list(usa_2025.costs.keys())}"

    # The price should be converted: 0.66 USD/kg × 1000 = 660 USD/t
    expected_price = 0.66 * T_TO_KG
    assert usa_2025.costs["bio_pci"] == pytest.approx(expected_price, rel=1e-6), (
        f"Bio-PCI price should be {expected_price} USD/t (converted from USD/kg), "
        f"but got {usa_2025.costs['bio_pci']} USD/t. "
        f"If this is close to 0.66, the conversion is not being applied!"
    )


def test_update_furnace_hydrogen_costs_scales_prices_to_usd_per_t():
    """Hydrogen prices supplied in USD/kg should be converted to USD/t when applied to furnace groups."""
    plant = get_plant()

    for fg in plant.furnace_groups:
        fg.energy_costs = {"hydrogen": 0.0}

    plant.update_furnace_hydrogen_costs({"DEU": 4.2})

    expected_price = 4.2 * T_TO_KG  # Convert USD/kg to USD/t
    for fg in plant.furnace_groups:
        assert fg.energy_costs["hydrogen"] == pytest.approx(expected_price, rel=1e-6)
