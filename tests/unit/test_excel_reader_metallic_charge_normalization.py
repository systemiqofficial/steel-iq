"""Tests for metallic charge normalization in MasterExcelReader's usage of read_dynamic_business_cases"""

import pytest
import pandas as pd
from unittest.mock import patch
from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader
from steelo.adapters.dataprocessing.excel_reader import read_dynamic_business_cases


class TestMetallicChargeNormalization:
    """Test normalization of metallic charge names from Excel."""

    def test_normalize_metallic_charge_function(self):
        """Test that a normalize_metallic_charge function exists and works correctly."""
        # First check if the function exists
        try:
            from steelo.adapters.dataprocessing.excel_reader import normalize_commodity_name
        except ImportError:
            pytest.fail("normalize_metallic_charge function should exist in excel_reader.py")

        # Test basic functionality
        assert normalize_commodity_name("Hot metal") == "hot_metal"
        assert normalize_commodity_name("Pig iron") == "pig_iron"
        assert normalize_commodity_name("iron ore") == "iron_ore"
        assert normalize_commodity_name("HOT METAL") == "hot_metal"
        assert normalize_commodity_name("hot_metal") == "hot_metal"


class TestDynamicBusinessCaseReadingWithNormalization:
    """Test that dynamic business case reading handles non-normalized metallic charges."""

    @pytest.fixture
    def mock_excel_data(self):
        """Create mock Excel data with non-normalized metallic charges."""
        data = pd.DataFrame(
            {
                "Business case": ["steel_bof", "steel_bof", "steel_bof", "steel_eaf", "steel_eaf"],
                "Metallic charge": ["Hot metal", "Pig iron", "Scrap", "Scrap", "DRI low"],
                "Reductant": [None, None, None, None, None],
                "Metric type": ["Materials", "Materials", "Materials", "Materials", "Materials"],
                "Side": ["Input", "Input", "Input", "Input", "Input"],  # Capital I
                "Type": ["material", "material", "material", "material", "material"],
                "Vector": ["hot_metal", "pig_iron", "scrap", "scrap", "dri_low"],
                "Value": [0.9, 0.05, 0.05, 1.0, 0.8],
                "Unit": ["t/t", "t/t", "t/t", "t/t", "t/t"],
                "System boundary": [
                    "cradle-to-gate",
                    "cradle-to-gate",
                    "cradle-to-gate",
                    "cradle-to-gate",
                    "cradle-to-gate",
                ],
                "ghg_factor_scope_1": [0.1, 0.1, 0.1, 0.1, 0.1],
                "ghg_factor_scope_2": [0.2, 0.2, 0.2, 0.2, 0.2],
                "ghg_factor_scope_3_rest": [0.3, 0.3, 0.3, 0.3, 0.3],
            }
        )
        return data

    @patch("pandas.read_excel")
    def test_read_dynamic_business_cases_with_normalization(self, mock_read_excel, mock_excel_data):
        """Test that non-normalized metallic charges are accepted after normalization."""
        mock_read_excel.return_value = mock_excel_data

        # This should not skip "Hot metal" and "Pig iron" entries
        result = read_dynamic_business_cases("dummy.xlsx", "Bill of Materials")

        # BOF should have business cases with hot metal and pig iron
        assert "BOF" in result
        bof_cases = result["BOF"]

        # Should have business cases for normalized charges
        assert any(case.metallic_charge == "hot_metal" for case in bof_cases)
        assert any(case.metallic_charge == "pig_iron" for case in bof_cases)
        assert any(case.metallic_charge == "scrap" for case in bof_cases)

    @patch("pandas.read_excel")
    def test_no_warnings_for_normalized_charges(self, mock_read_excel, mock_excel_data, caplog):
        """Test that no warnings are issued for properly normalized charges."""
        mock_read_excel.return_value = mock_excel_data

        with caplog.at_level("WARNING"):
            read_dynamic_business_cases("dummy.xlsx", "Bill of Materials")

        # Should not have warnings about invalid metallic charges
        warning_messages = [
            record.getMessage()
            for record in caplog.records
            if "Skipping invalid metallic charge" in record.getMessage()
        ]
        assert len(warning_messages) == 0, f"Unexpected warnings: {warning_messages}"

    @patch("pandas.read_excel")
    def test_business_case_creation_with_mixed_formats(self, mock_read_excel):
        """Test handling of mixed format metallic charges in same technology."""
        data = pd.DataFrame(
            {
                "Business case": ["steel_bof"] * 4,
                "Metallic charge": ["Hot metal", "hot_metal", "Pig iron", "pig_iron"],
                "Reductant": [None] * 4,
                "Metric type": ["Materials"] * 4,
                "Side": ["Input"] * 4,  # Capital I
                "Type": ["material"] * 4,
                "Vector": ["hot_metal", "hot_metal", "pig_iron", "pig_iron"],
                "Value": [0.9, 0.9, 0.05, 0.05],
                "Unit": ["t/t"] * 4,
                "System boundary": ["cradle-to-gate"] * 4,
                "ghg_factor_scope_1": [0.1] * 4,
                "ghg_factor_scope_2": [0.2] * 4,
                "ghg_factor_scope_3_rest": [0.3] * 4,
            }
        )
        mock_read_excel.return_value = data

        result = read_dynamic_business_cases("dummy.xlsx", "Bill of Materials")

        # Should handle both formats and create proper business cases
        assert "BOF" in result
        # Should not have duplicate business cases
        bof_cases = result["BOF"]
        assert len(set(bof_cases)) == len(bof_cases)  # No duplicates

    @patch("pandas.read_excel")
    def test_dynamic_business_case_order_is_deterministic(self, mock_read_excel):
        """Regression test ensuring feedstock ordering is deterministic across runs (spec 2025-10-30)."""

        def _make_frame():
            charges = ["io high", "IO mid", "Io Low"]
            reductants = ["hydrogen", "coal", "natural gas"]
            vectors = {"io high": "io_high", "IO mid": "io_mid", "Io Low": "io_low"}

            rows = []
            for mc in charges:
                for red in reductants:
                    rows.append(
                        {
                            "Business case": "case_dri",
                            "Metallic charge": mc,
                            "Reductant": red,
                            "Metric type": "Materials",
                            "Side": "Input",
                            "Type": "material",
                            "Vector": vectors[mc],
                            "Value": 1.0,
                            "Unit": "t/t",
                            "System boundary": "cradle-to-gate",
                            "ghg_factor_scope_1": 0.0,
                            "ghg_factor_scope_2": 0.0,
                            "ghg_factor_scope_3_rest": 0.0,
                        }
                    )
            return pd.DataFrame(rows)

        mock_read_excel.side_effect = lambda *args, **kwargs: _make_frame().copy()

        results = [read_dynamic_business_cases("dummy.xlsx", "Bill of Materials") for _ in range(5)]
        # Extract ordering tuples for deterministic comparison
        orderings = [tuple((fs.metallic_charge, fs.reductant) for fs in result["DRI"]) for result in results]

        first_ordering = orderings[0]
        assert all(order == first_ordering for order in orderings), "Feedstock order must be deterministic"


class TestMasterExcelReaderWithNormalizedBusinessCases:
    """Test that MasterExcelReader creates BOF plants when Bill of Materials has non-normalized charges."""

    @pytest.fixture
    def mock_excel_file(self, tmp_path):
        """Create a mock Excel file with non-normalized metallic charges."""
        excel_path = tmp_path / "test_master.xlsx"

        # Create Bill of Materials sheet with non-normalized charges
        bom_data = pd.DataFrame(
            {
                "Business case": ["steel_bof", "steel_bof", "steel_bof", "iron_bf"],
                "Metallic charge": ["Hot metal", "Pig iron", "Scrap", "Sinter"],  # Non-normalized!
                "Reductant": [None, None, None, None],
                "Metric type": ["Materials", "Materials", "Materials", "Materials"],
                "Side": ["Input", "Input", "Input", "Input"],  # Must be capital I
                "Type": ["material", "material", "material", "material"],
                "Vector": ["hot_metal", "pig_iron", "scrap", "sinter"],
                "Value": [0.9, 0.05, 0.05, 1.6],
                "Unit": ["t/t", "t/t", "t/t", "t/t"],
                "System boundary": ["cradle-to-gate", "cradle-to-gate", "cradle-to-gate", "cradle-to-gate"],
                "ghg_factor_scope_1": [0.0, 0.0, 0.0, 0.0],
                "ghg_factor_scope_2": [0.0, 0.0, 0.0, 0.0],
                "ghg_factor_scope_3_rest": [0.0, 0.0, 0.0, 0.0],
            }
        )

        # Create Iron and steel plants sheet
        plants_data = pd.DataFrame(
            {
                "Plant ID": ["P100000120620"],
                "Coordinates": ["48.123, 11.456"],
                "Country": ["Germany"],
                "Main production equipment": ["BF; BOF"],
                "Nominal crude steel capacity (ttpa)": [7800],
                "Nominal BOF steel capacity (ttpa)": [7800],
                "Nominal iron capacity (ttpa)": [4000],
                "Nominal BF capacity (ttpa)": [4000],
                "Capacity operating status": ["operating"],
                "Start date": ["2010-01-01"],
            }
        )

        # Write to Excel
        with pd.ExcelWriter(excel_path) as writer:
            bom_data.to_excel(writer, sheet_name="Bill of Materials", index=False)
            plants_data.to_excel(writer, sheet_name="Iron and steel plants", index=False)

        return excel_path

    def test_bof_plants_created_with_normalized_charges(self, mock_excel_file):
        """Test that BOF furnace groups are created when charges are normalized."""
        with MasterExcelReader(mock_excel_file) as reader:
            plants, _ = reader.read_plants()  # Unpack tuple (plants, metadata)

        # Should have created at least one plant
        assert len(plants) > 0

        # Find the test plant
        test_plant = next((p for p in plants if p.plant_id == "P100000120620"), None)
        assert test_plant is not None, "Test plant should be created"

        # Check that BOF furnace group was created
        bof_groups = [fg for fg in test_plant.furnace_groups if fg.technology.name == "BOF"]
        assert len(bof_groups) > 0, "BOF furnace group should be created"

        # Check that BOF has dynamic business cases
        bof_group = bof_groups[0]
        assert len(bof_group.technology.dynamic_business_case) > 0, "BOF should have dynamic business cases"

        # Check that business cases include hot metal and pig iron
        business_case_strings = [str(bc) for bc in bof_group.technology.dynamic_business_case]
        assert any("hot_metal" in bc for bc in business_case_strings), "BOF should have hot_metal business case"
        assert any("pig_iron" in bc for bc in business_case_strings), "BOF should have pig_iron business case"
