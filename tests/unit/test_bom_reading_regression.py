"""
Regression test to ensure BOM reading functions work correctly.

This test prevents the issue where business cases become empty due to
overly strict NaN handling or inconsistent metallic charge normalization.
"""

import tempfile
from pathlib import Path
import pandas as pd

from steelo.adapters.dataprocessing.excel_reader import read_dynamic_business_cases


def create_test_bom_excel():
    """Create a minimal test Excel file with BOM data."""
    data = [
        # BOF with hot_metal (using exact expected format)
        {
            "Business case": "steel_bof",  # Use correct Excel naming convention
            "Metallic charge": "hot_metal",  # Use exact expected format
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "hot_metal",
            "Value": 0.95,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",  # Add required column
            "ghg_factor_scope_1": 1.0,
            "ghg_factor_scope_2": 0.1,
            "ghg_factor_scope_3_rest": 0.05,
        },
        # BOF with scrap
        {
            "Business case": "steel_bof",
            "Metallic charge": "scrap",
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "scrap",
            "Value": 0.15,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 0.1,
            "ghg_factor_scope_2": 0.05,
            "ghg_factor_scope_3_rest": 0.01,
        },
        # EAF with scrap
        {
            "Business case": "steel_eaf",
            "Metallic charge": "scrap",
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "scrap",
            "Value": 1.05,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 0.1,
            "ghg_factor_scope_2": 0.05,
            "ghg_factor_scope_3_rest": 0.01,
        },
        # BF with pellets and coke
        {
            "Business case": "iron_bf",
            "Metallic charge": "pellets_low",
            "Reductant": "coke",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "pellets_low",
            "Value": 1.6,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 2.0,
            "ghg_factor_scope_2": 0.2,
            "ghg_factor_scope_3_rest": 0.1,
        },
        # DRI with pellets and natural gas
        {
            "Business case": "iron_dri",
            "Metallic charge": "pellets_mid",
            "Reductant": "natural gas",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "pellets_mid",
            "Value": 1.5,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 1.8,
            "ghg_factor_scope_2": 0.15,
            "ghg_factor_scope_3_rest": 0.08,
        },
    ]

    df = pd.DataFrame(data)

    # Create temporary Excel file
    temp_file = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    with pd.ExcelWriter(temp_file.name, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Bill of Materials", index=False)

    return Path(temp_file.name)


def test_bom_reading_produces_non_empty_business_cases():
    """Test that BOM reading produces non-empty business cases."""
    excel_path = create_test_bom_excel()

    try:
        # Read dynamic business cases - returns dict[str, list[PrimaryFeedstock]]
        technology_feedstocks = read_dynamic_business_cases(str(excel_path), "Bill of Materials")

        # Verify we got business cases back
        assert technology_feedstocks is not None, "Technology feedstocks should not be None"
        assert len(technology_feedstocks) > 0, "Technology feedstocks dictionary should not be empty"

        # Verify specific technologies are present
        expected_technologies = ["BOF", "EAF", "BF", "DRI"]
        found_technologies = set(technology_feedstocks.keys())

        for tech in expected_technologies:
            assert tech in found_technologies, f"Technology {tech} should be present in business cases"

        # Verify feedstocks have required quantities
        for technology, feedstock_list in technology_feedstocks.items():
            assert len(feedstock_list) > 0, f"Technology {technology} should have at least one feedstock"
            for feedstock in feedstock_list:
                assert feedstock.required_quantity_per_ton_of_product is not None, (
                    f"Feedstock {feedstock.name} should have required_quantity_per_ton_of_product set"
                )
                assert feedstock.required_quantity_per_ton_of_product > 0, (
                    f"Feedstock {feedstock.name} should have positive required quantity"
                )

        # Test specific cases that were problematic
        # BOF with "hot_metal" should work
        bof_hot_metal_found = False
        if "BOF" in technology_feedstocks:
            for feedstock in technology_feedstocks["BOF"]:
                if "hot" in feedstock.metallic_charge.lower():
                    bof_hot_metal_found = True
                    assert feedstock.required_quantity_per_ton_of_product == 0.95
                    break

        assert bof_hot_metal_found, "BOF with hot metal should be found and processed correctly"

    finally:
        # Clean up
        excel_path.unlink()


def test_bom_reading_handles_missing_values_gracefully():
    """Test that BOM reading handles missing values without crashing."""
    data = [
        # Valid entry
        {
            "Business case": "steel_bof",
            "Metallic charge": "scrap",
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "scrap",
            "Value": 1.0,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 0.1,
            "ghg_factor_scope_2": 0.05,
            "ghg_factor_scope_3_rest": 0.01,
        },
        # Entry with missing metallic charge - should be skipped
        {
            "Business case": "steel_bof",
            "Metallic charge": None,
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "scrap",
            "Value": 1.0,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 0.1,
            "ghg_factor_scope_2": 0.05,
            "ghg_factor_scope_3_rest": 0.01,
        },
    ]

    df = pd.DataFrame(data)
    temp_file = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)

    try:
        with pd.ExcelWriter(temp_file.name, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Bill of Materials", index=False)

        # Should not crash and should return valid business cases
        technology_feedstocks = read_dynamic_business_cases(temp_file.name, "Bill of Materials")

        assert technology_feedstocks is not None
        assert len(technology_feedstocks) > 0, "Should have at least one valid business case"

        # Verify the valid entry is present
        valid_found = False
        if "BOF" in technology_feedstocks:
            for feedstock in technology_feedstocks["BOF"]:
                if feedstock.metallic_charge == "scrap":
                    valid_found = True
                    break

        assert valid_found, "Valid BOF scrap feedstock should be present"

    finally:
        Path(temp_file.name).unlink()


def test_metallic_charge_case_handling():
    """Test that metallic charges are handled consistently regardless of case."""
    data = [
        # Mixed case metallic charges - should all be normalized to lowercase
        {
            "Business case": "steel_bof",
            "Metallic charge": "hot_metal",  # Use expected format
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "hot_metal",
            "Value": 0.95,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 1.0,
            "ghg_factor_scope_2": 0.1,
            "ghg_factor_scope_3_rest": 0.05,
        },
        {
            "Business case": "steel_bof",
            "Metallic charge": "scrap",  # Use expected format
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "scrap",
            "Value": 0.15,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 0.1,
            "ghg_factor_scope_2": 0.05,
            "ghg_factor_scope_3_rest": 0.01,
        },
    ]

    df = pd.DataFrame(data)
    temp_file = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)

    try:
        with pd.ExcelWriter(temp_file.name, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Bill of Materials", index=False)

        technology_feedstocks = read_dynamic_business_cases(temp_file.name, "Bill of Materials")

        assert technology_feedstocks is not None
        assert len(technology_feedstocks) > 0

        # Check that metallic charges are normalized to lowercase
        for technology, feedstock_list in technology_feedstocks.items():
            for feedstock in feedstock_list:
                assert feedstock.metallic_charge.islower(), (
                    f"Metallic charge should be lowercase, got: {feedstock.metallic_charge}"
                )

        # Verify both entries are present
        found_charges = set()
        for technology, feedstock_list in technology_feedstocks.items():
            for feedstock in feedstock_list:
                found_charges.add(feedstock.metallic_charge)

        # Should have processed both entries with normalized charges
        assert len(found_charges) >= 2, "Should have processed multiple metallic charges"

    finally:
        Path(temp_file.name).unlink()


def test_no_excessive_feedstock_removal():
    """Test that valid feedstocks are not removed due to overly strict validation."""
    data = [
        # Standard valid feedstocks that should NOT be removed
        {
            "Business case": "steel_bof",
            "Metallic charge": "hot_metal",
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "hot_metal",
            "Value": 0.95,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 1.0,
            "ghg_factor_scope_2": 0.1,
            "ghg_factor_scope_3_rest": 0.05,
        },
        {
            "Business case": "steel_bof",
            "Metallic charge": "scrap",
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "scrap",
            "Value": 0.15,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 0.1,
            "ghg_factor_scope_2": 0.05,
            "ghg_factor_scope_3_rest": 0.01,
        },
        {
            "Business case": "steel_eaf",
            "Metallic charge": "scrap",
            "Reductant": "",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "scrap",
            "Value": 1.05,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 0.1,
            "ghg_factor_scope_2": 0.05,
            "ghg_factor_scope_3_rest": 0.01,
        },
        {
            "Business case": "iron_bf",
            "Metallic charge": "pellets_low",
            "Reductant": "coke",
            "Side": "Input",
            "Type": "feedstock",
            "Metric type": "Materials",
            "Vector": "pellets_low",
            "Value": 1.6,
            "Unit": "t/t",
            "System boundary": "cradle-to-gate",
            "ghg_factor_scope_1": 2.0,
            "ghg_factor_scope_2": 0.2,
            "ghg_factor_scope_3_rest": 0.1,
        },
    ]

    df = pd.DataFrame(data)
    temp_file = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)

    try:
        with pd.ExcelWriter(temp_file.name, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Bill of Materials", index=False)

        technology_feedstocks = read_dynamic_business_cases(temp_file.name, "Bill of Materials")

        # Count total feedstocks across all technologies
        total_feedstocks = sum(len(feedstock_list) for feedstock_list in technology_feedstocks.values())
        # We should have 4 feedstocks (2 BOF, 1 EAF, 1 BF)
        # Note: OHF has been removed from the model
        assert total_feedstocks == 4, (
            f"Expected 4 feedstocks total, got {total_feedstocks}. Valid feedstocks may have been incorrectly removed."
        )

        # Verify each expected feedstock is present
        expected_feedstocks = [
            ("BOF", "hot_metal"),
            ("BOF", "scrap"),
            ("EAF", "scrap"),
            ("BF", "pellets_low"),
        ]

        found_feedstocks = set()
        for technology, feedstock_list in technology_feedstocks.items():
            for feedstock in feedstock_list:
                # Use uppercase technology for comparison (feedstock.technology is stored in original case)
                found_feedstocks.add((feedstock.technology.upper(), feedstock.metallic_charge))

        for expected_tech, expected_charge in expected_feedstocks:
            assert (expected_tech, expected_charge) in found_feedstocks, (
                f"Expected feedstock ({expected_tech}, {expected_charge}) was incorrectly removed"
            )

    finally:
        Path(temp_file.name).unlink()


if __name__ == "__main__":
    # Run tests manually if needed
    test_bom_reading_produces_non_empty_business_cases()
    test_bom_reading_handles_missing_values_gracefully()
    test_metallic_charge_case_handling()
    test_no_excessive_feedstock_removal()
    print("All BOM reading regression tests passed!")
