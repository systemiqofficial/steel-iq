"""Tests for the post_process_datacollection module."""

import tempfile
import pandas as pd
import pickle
from pathlib import Path

from steelo.adapters.dataprocessing.postprocessing.post_process_datacollection import (
    extract_and_process_stored_dataCollection,
)
from steelo.domain.commands import (
    ChangeFurnaceGroupTechnology,
    CloseFurnaceGroup,
    RenovateFurnaceGroup,
)


def test_commands_column_included_in_output():
    """Test that commands are properly mapped to furnace groups in the output CSV."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)

        # Create sample datacollection for 2 years
        sample_data_2025 = {
            "plant_001": {
                "location": "USA",
                "balance": 1000,
                "furnace_groups": pd.DataFrame(
                    [
                        {
                            "furnace_group_id": "plant_001_fg_001",
                            "technology": "BFBOF",
                            "product": "steel",
                            "chosen_reductant": "coal",
                            "capacity": 1000,
                            "production": 900,
                            "unit_vopex": 100,
                            "unit_fopex": 50,
                            "unit_fixed_opex": 0.05,
                            "unit_production_cost": 150,
                            "debt_repayment_for_current_year": 10,
                            "historic_balance": 500,
                            "emissions_scope1": 2.0,
                            "materials": {"coal": {"demand": 500, "total_cost": 50000, "unit_cost": 100}},
                            "energy": {"electricity": {"demand": 200, "total_cost": 10000, "unit_cost": 50}},
                        }
                    ]
                ),
            },
            "plant_002": {
                "location": "Germany",
                "balance": 2000,
                "furnace_groups": pd.DataFrame(
                    [
                        {
                            "furnace_group_id": "plant_002_fg_001",
                            "technology": "EAF",
                            "product": "steel",
                            "chosen_reductant": "electricity",
                            "capacity": 800,
                            "production": 700,
                            "unit_vopex": 90,
                            "unit_fopex": 40,
                            "unit_fixed_opex": 0.05,
                            "unit_production_cost": 150,
                            "debt_repayment_for_current_year": 8,
                            "historic_balance": 400,
                            "emissions_scope1": 0.5,
                            "materials": {"scrap": {"demand": 400, "total_cost": 40000, "unit_cost": 100}},
                            "energy": {"electricity": {"demand": 300, "total_cost": 15000, "unit_cost": 50}},
                        }
                    ]
                ),
            },
        }

        sample_data_2026 = {
            "plant_001": {
                "location": "USA",
                "balance": 1200,
                "furnace_groups": pd.DataFrame(
                    [
                        {
                            "furnace_group_id": "plant_001_fg_001",
                            "technology": "DRI-EAF",  # Changed technology
                            "product": "steel",
                            "chosen_reductant": "natural_gas",
                            "capacity": 1000,
                            "production": 950,
                            "unit_vopex": 85,
                            "unit_fopex": 45,
                            "unit_fixed_opex": 0.05,
                            "unit_production_cost": 150,
                            "debt_repayment_for_current_year": 12,
                            "historic_balance": 600,
                            "emissions_scope1": 1.0,
                            "materials": {"natural_gas": {"demand": 300, "total_cost": 30000, "unit_cost": 100}},
                            "energy": {"electricity": {"demand": 250, "total_cost": 12500, "unit_cost": 50}},
                        }
                    ]
                ),
            },
            "plant_002": {
                "location": "Germany",
                "balance": 2100,
                "furnace_groups": pd.DataFrame(
                    [
                        {
                            "furnace_group_id": "plant_002_fg_001",
                            "technology": "EAF",
                            "product": "steel",
                            "chosen_reductant": "electricity",
                            "capacity": 800,
                            "production": 750,
                            "unit_vopex": 88,
                            "unit_fopex": 42,
                            "unit_fixed_opex": 0.05,
                            "unit_production_cost": 150,
                            "debt_repayment_for_current_year": 9,
                            "historic_balance": 450,
                            "emissions_scope1": 0.5,
                            "materials": {"scrap": {"demand": 420, "total_cost": 42000, "unit_cost": 100}},
                            "energy": {"electricity": {"demand": 320, "total_cost": 16000, "unit_cost": 50}},
                        }
                    ]
                ),
            },
        }

        # Save the sample data as pickles
        with open(data_dir / "datacollection_post_allocation_2025.pkl", "wb") as f:
            pickle.dump(sample_data_2025, f)
        with open(data_dir / "datacollection_post_allocation_2026.pkl", "wb") as f:
            pickle.dump(sample_data_2026, f)

        # Create commands reflecting the PAM decisions
        commands = {
            2025: {
                "plant_001_fg_001": ChangeFurnaceGroupTechnology(
                    plant_id="plant_001",
                    furnace_group_id="plant_001_fg_001",
                    technology_name="DRI-EAF",
                    old_technology_name="BFBOF",
                    npv=1000000,
                    cosa=500,
                    utilisation=0.9,
                    capex=2000000,
                    capex_no_subsidy=2000000,  # Same as capex if no subsidy
                    capacity=1000,
                    remaining_lifetime=20,
                    bom={},
                    cost_of_debt=0.05,
                    cost_of_debt_no_subsidy=0.05,  # Same as cost_of_debt if no subsidy
                    capex_subsidies=[],
                    debt_subsidies=[],
                ),
                "plant_002_fg_001": RenovateFurnaceGroup(
                    plant_id="plant_002",
                    furnace_group_id="plant_002_fg_001",
                    capex=1000000,
                    capex_no_subsidy=1000000,
                    cost_of_debt=0.04,
                    cost_of_debt_no_subsidy=0.04,
                    capex_subsidies=[],
                    debt_subsidies=[],
                ),
            },
            2026: {"plant_002_fg_001": CloseFurnaceGroup(plant_id="plant_002", furnace_group_id="plant_002_fg_001")},
        }

        # Process the data with commands
        output_file = data_dir / "test_output.csv"
        result_path = extract_and_process_stored_dataCollection(
            commands=commands, data_dir=data_dir, output_path=output_file, store=True
        )

        # Read the output CSV and verify commands column
        df = pd.read_csv(result_path)

        # Check that commands column exists
        assert "commands" in df.columns, "Commands column is missing from the output CSV!"

        # Check specific command mappings for 2025
        df_2025 = df[df["year"] == 2025]
        plant_001_command = df_2025[df_2025["furnace_group_id"] == "plant_001_fg_001"]["commands"].iloc[0]
        assert plant_001_command == "ChangeFurnaceGroupTechnology", (
            f"Expected ChangeFurnaceGroupTechnology for plant_001_fg_001 in 2025, got {plant_001_command}"
        )

        plant_002_command = df_2025[df_2025["furnace_group_id"] == "plant_002_fg_001"]["commands"].iloc[0]
        assert plant_002_command == "RenovateFurnaceGroup", (
            f"Expected RenovateFurnaceGroup for plant_002_fg_001 in 2025, got {plant_002_command}"
        )

        # Check specific command mappings for 2026
        df_2026 = df[df["year"] == 2026]
        plant_002_2026_command = df_2026[df_2026["furnace_group_id"] == "plant_002_fg_001"]["commands"].iloc[0]
        assert plant_002_2026_command == "CloseFurnaceGroup", (
            f"Expected CloseFurnaceGroup for plant_002_fg_001 in 2026, got {plant_002_2026_command}"
        )


def test_feedstock_rows_with_empty_materials_do_not_crash():
    """Ensure frames with entirely empty materials/cost data are preserved and handled."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)

        sample_data_2025 = {
            "plant_001": {
                "location": "USA",
                "balance": 100,
                "furnace_groups": pd.DataFrame(
                    [
                        {
                            "furnace_group_id": "plant_001_fg_001",
                            "technology": "BFBOF",
                            "product": "steel",
                            "chosen_reductant": "coal",
                            "capacity": 1000,
                            "production": 0,
                            "unit_vopex": 0,
                            "unit_fopex": 0,
                            "unit_production_cost": 0,
                            "debt_repayment_for_current_year": 0,
                            "historic_balance": 0,
                            # The following columns are intentionally all None to mimic empty feedstock data
                            "materials": None,
                            "energy": None,
                            "cost_breakdown": None,
                        }
                    ]
                ),
            }
        }

        with open(data_dir / "datacollection_post_allocation_2025.pkl", "wb") as f:
            pickle.dump(sample_data_2025, f)

        output_file = data_dir / "test_output.csv"
        result_path = extract_and_process_stored_dataCollection(
            commands={2025: {}}, data_dir=data_dir, output_path=output_file, store=True
        )

        df = pd.read_csv(result_path)

        # Basic sanity check that the furnace row is present and year assigned
        assert not df.empty
        assert (df["furnace_group_id"] == "plant_001_fg_001").any()


def test_commands_column_with_empty_commands_for_year():
    """Test that commands column contains NaN when commands exist but year has no commands."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)

        # Create minimal sample data
        sample_data = {
            "plant_001": {
                "location": "USA",
                "balance": 1000,
                "furnace_groups": pd.DataFrame(
                    [
                        {
                            "furnace_group_id": "plant_001_fg_001",
                            "technology": "BFBOF",
                            "product": "steel",
                            "chosen_reductant": "coal",
                            "capacity": 1000,
                            "production": 900,
                            "unit_vopex": 100,
                            "unit_fopex": 50,
                            "unit_fixed_opex": 0.05,
                            "unit_production_cost": 150,
                            "debt_repayment_for_current_year": 10,
                            "historic_balance": 500,
                            "emissions_scope1": 2.0,
                            "materials": {},
                            "energy": {},
                        }
                    ]
                ),
            }
        }

        # Save the sample data
        with open(data_dir / "datacollection_post_allocation_2025.pkl", "wb") as f:
            pickle.dump(sample_data, f)

        # Process with commands dict containing year but no actual commands
        output_file = data_dir / "test_output_no_commands.csv"
        result_path = extract_and_process_stored_dataCollection(
            commands={2025: {}},  # Year exists but no commands for any furnace group
            data_dir=data_dir,
            output_path=output_file,
            store=True,
        )

        # Read the output CSV and verify commands column has NaN values
        df = pd.read_csv(result_path)
        assert "commands" in df.columns, "Commands column should be present when commands dict has year keys!"

        # All commands should be NaN since no actual commands were provided
        assert df["commands"].isna().all(), (
            "All command values should be NaN when no commands are provided for the year!"
        )


def test_chosen_reductant_values_are_normalized():
    """Post-processed CSV should replace spaces in chosen_reductant with underscores."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)

        sample_data = {
            "plant_001": {
                "location": "ARE",
                "balance": 0,
                "furnace_groups": pd.DataFrame(
                    [
                        {
                            "furnace_group_id": "plant_001_fg_001",
                            "technology": "DRI",
                            "product": "iron",
                            "chosen_reductant": "natural gas",
                            "capacity": 1000,
                            "production": 900,
                            "unit_vopex": 0,
                            "unit_fopex": 0,
                            "unit_production_cost": 0,
                            "debt_repayment_for_current_year": 0,
                            "historic_balance": 0,
                            "materials": {"io_low": {"demand": 100, "total_cost": 1000, "unit_cost": 10}},
                            "energy": {},
                        }
                    ]
                ),
            }
        }

        with open(data_dir / "datacollection_post_allocation_2025.pkl", "wb") as f:
            pickle.dump(sample_data, f)

        output_file = data_dir / "normalized_output.csv"
        result_path = extract_and_process_stored_dataCollection(
            commands={2025: {}}, data_dir=data_dir, output_path=output_file, store=True
        )

        df = pd.read_csv(result_path)
        reductants = df.loc[df["furnace_group_id"] == "plant_001_fg_001", "chosen_reductant"].unique()
        assert len(reductants) == 1
        assert reductants[0] == "natural_gas"
