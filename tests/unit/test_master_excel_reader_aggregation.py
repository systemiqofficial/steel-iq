"""Unit tests for MasterExcelReader Plant ID aggregation functionality."""

import pytest
import pandas as pd
import tempfile
from pathlib import Path

from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader
from steelo.domain.models import Volumes, Year


@pytest.fixture
def master_excel_with_duplicates():
    """Create a test Excel file with duplicate Plant IDs."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
        # Create plant data with duplicate Plant IDs representing multiple furnace groups
        plants_data = pd.DataFrame(
            {
                "Plant ID": ["P100000120240", "P100000120240", "P100000120240", "P200000220240", "P300000320240"],
                "Company name": ["Rizhao Steel", "Rizhao Steel", "Rizhao Steel", "Another Steel", "Third Steel"],
                "Coordinates": ["35.416, 119.524", "35.416, 119.524", "35.416, 119.524", "40.0, 120.0", "30.0, 110.0"],
                "Country": ["China", "China", "China", "Japan", "Germany"],
                "Main production equipment": ["BF", "BOF", "EAF", "BF", "DRI"],
                "Nominal BF capacity (ttpa)": [5000, None, None, 3000, None],
                "Nominal BOF steel capacity (ttpa)": [None, 6000, None, None, None],
                "Nominal EAF steel capacity (ttpa)": [None, None, 2000, None, None],
                "Nominal DRI capacity (ttpa)": [None, None, None, None, 1500],
                "Nominal iron capacity (ttpa)": [5000, None, None, 3000, 1500],
                "Nominal crude steel capacity (ttpa)": [None, 6000, 2000, None, None],
                "Start date": ["2015", "2016", "2018", "2010", "2020"],
                "Capacity operating status": ["operating", "operating", "operating", "operating", "operating"],
                "Power source": ["grid", "grid", "grid", "grid", "renewable"],
                "SOE Status": ["SOE", "SOE", "SOE", "private", "private"],
                "Parent GEM ID": ["G123", "G123", "G123", "G456", "G789"],
                "Workforce size": [5000, 5000, 5000, 3000, 1000],
            }
        )

        # Create Bill of Materials sheet (required for MasterExcelReader)
        # Adding primary feedstock rows (Materials metric type with primary Vector)
        bom_data = pd.DataFrame(
            {
                "Business case": ["iron_bf", "iron_bf", "steel_bof", "steel_eaf", "iron_dri"],
                "Side": ["Input", "Input", "Input", "Input", "Input"],
                "Metallic charge": ["pellets_low", "pellets_low", "hot_metal", "scrap", "pellets_mid"],
                "Reductant": ["Coke+PCI", "Coke+PCI", None, None, "Natural gas"],
                "Metric type": ["Materials", "Energy", "Materials", "Materials", "Materials"],
                "Vector": ["pellets_low", "Coke+PCI", "hot_metal", "scrap", "pellets_mid"],
                "Type": ["Feedstock", "Reductant", "Feedstock", "Feedstock", "Feedstock"],
                "Value": [1.58, 0.45, 0.95, 1.09, 1.4],
                "Unit": ["t/t HM", "t/t HM", "t/t Liquid steel", "t/t Liquid steel", "t/t DRI"],
                "Date of update": pd.Timestamp("2025-08-21"),
            }
        )

        # Create historical production sheet (empty but needed)
        production_data = pd.DataFrame()

        # Write to Excel
        with pd.ExcelWriter(tf.name) as writer:
            plants_data.to_excel(writer, sheet_name="Iron and steel plants", index=False)
            bom_data.to_excel(writer, sheet_name="Bill of Materials", index=False)
            production_data.to_excel(writer, sheet_name="Historical production", index=False)

        yield Path(tf.name)


def test_duplicate_plant_ids_should_be_aggregated(master_excel_with_duplicates):
    """Test that duplicate Plant IDs are aggregated into single plants with multiple furnace groups."""
    reader = MasterExcelReader(master_excel_with_duplicates)
    plants, _ = reader.read_plants(simulation_start_year=Year(2025))  # Unpack tuple

    # Should have 3 unique plants (not 5 rows)
    assert len(plants) == 3, f"Expected 3 unique plants, got {len(plants)}"

    # Get plant IDs
    plant_ids = [p.plant_id for p in plants]
    assert len(set(plant_ids)) == 3, "All plant IDs should be unique"

    # Find the Rizhao Steel plant (with 3 duplicate rows)
    rizhao_plants = [p for p in plants if p.plant_id == "P100000120240"]
    assert len(rizhao_plants) == 1, "Should have exactly one plant with ID P100000120240"

    rizhao_plant = rizhao_plants[0]

    # Should have 3 furnace groups (BF, BOF, EAF)
    assert len(rizhao_plant.furnace_groups) == 3, (
        f"Rizhao Steel should have 3 furnace groups, got {len(rizhao_plant.furnace_groups)}"
    )

    # Verify furnace group technologies
    technologies = [fg.technology.name for fg in rizhao_plant.furnace_groups]
    assert set(technologies) == {"BF", "BOF", "EAF"}, f"Expected BF, BOF, EAF technologies, got {technologies}"

    # Verify capacities are preserved
    capacities = {fg.technology.name: fg.capacity for fg in rizhao_plant.furnace_groups}

    # Check BF capacity (5000 kt -> 5,000,000 tonnes)
    bf_capacity = next((c for tech, c in capacities.items() if tech == "BF"), None)
    assert bf_capacity is not None, "BF furnace group should exist"
    assert bf_capacity == Volumes(5_000_000), f"BF capacity should be 5,000,000 tonnes, got {bf_capacity}"

    # Check BOF capacity (6000 kt -> 6,000,000 tonnes)
    bof_capacity = next((c for tech, c in capacities.items() if tech == "BOF"), None)
    assert bof_capacity is not None, "BOF furnace group should exist"
    assert bof_capacity == Volumes(6_000_000), f"BOF capacity should be 6,000,000 tonnes, got {bof_capacity}"

    # Check EAF capacity (2000 kt -> 2,000,000 tonnes)
    eaf_capacity = next((c for tech, c in capacities.items() if tech == "EAF"), None)
    assert eaf_capacity is not None, "EAF furnace group should exist"
    assert eaf_capacity == Volumes(2_000_000), f"EAF capacity should be 2,000,000 tonnes, got {eaf_capacity}"


def test_plant_attributes_preserved_during_aggregation(master_excel_with_duplicates):
    """Test that plant-level attributes are preserved when aggregating duplicate IDs."""
    reader = MasterExcelReader(master_excel_with_duplicates)
    plants, _ = reader.read_plants(simulation_start_year=Year(2025))  # Unpack tuple

    # Find the Rizhao Steel plant
    rizhao_plant = next((p for p in plants if p.plant_id == "P100000120240"), None)
    assert rizhao_plant is not None, "Should find Rizhao Steel plant"

    # Check plant-level attributes (should be from first occurrence)
    assert rizhao_plant.location.country == "CHN", f"Country should be CHN, got {rizhao_plant.location.country}"
    assert rizhao_plant.power_source == "grid", f"Power source should be grid, got {rizhao_plant.power_source}"
    assert rizhao_plant.soe_status == "SOE", f"SOE status should be SOE, got {rizhao_plant.soe_status}"
    assert rizhao_plant.parent_gem_id == "G123", f"Parent GEM ID should be G123, got {rizhao_plant.parent_gem_id}"
    assert rizhao_plant.workforce_size == 5000, f"Workforce size should be 5000, got {rizhao_plant.workforce_size}"


def test_furnace_group_ids_remain_unique(master_excel_with_duplicates):
    """Test that furnace group IDs remain unique even when aggregating plants."""
    reader = MasterExcelReader(master_excel_with_duplicates)
    plants, _ = reader.read_plants(simulation_start_year=Year(2025))  # Unpack tuple

    # Collect all furnace group IDs
    all_furnace_group_ids = []
    for plant in plants:
        for fg in plant.furnace_groups:
            all_furnace_group_ids.append(fg.furnace_group_id)

    # All furnace group IDs should be unique
    assert len(all_furnace_group_ids) == len(set(all_furnace_group_ids)), "All furnace group IDs should be unique"

    # Check specific pattern for Rizhao Steel
    rizhao_plant = next((p for p in plants if p.plant_id == "P100000120240"), None)
    assert rizhao_plant is not None

    # Furnace group IDs should follow pattern: PlantID_index
    for idx, fg in enumerate(rizhao_plant.furnace_groups):
        expected_id = f"P100000120240_{idx}"
        assert fg.furnace_group_id == expected_id, f"Expected furnace group ID {expected_id}, got {fg.furnace_group_id}"


def test_single_plant_ids_not_affected(master_excel_with_duplicates):
    """Test that plants without duplicate IDs are not affected by aggregation."""
    reader = MasterExcelReader(master_excel_with_duplicates)
    plants, _ = reader.read_plants(simulation_start_year=Year(2025))  # Unpack tuple

    # Find plants with single occurrences
    single_occurrence_plants = [p for p in plants if p.plant_id in ["P200000220240", "P300000320240"]]
    assert len(single_occurrence_plants) == 2, "Should have 2 plants with single occurrences"

    # Check P200000220240 (single BF)
    plant2 = next((p for p in single_occurrence_plants if p.plant_id == "P200000220240"), None)
    assert plant2 is not None
    assert len(plant2.furnace_groups) == 1, "Plant P200000220240 should have 1 furnace group"
    assert plant2.furnace_groups[0].technology.name == "BF"

    # Check P300000320240 (single DRI)
    plant3 = next((p for p in single_occurrence_plants if p.plant_id == "P300000320240"), None)
    assert plant3 is not None
    assert len(plant3.furnace_groups) == 1, "Plant P300000320240 should have 1 furnace group"
    assert plant3.furnace_groups[0].technology.name == "DRI"


def test_aggregation_preserves_different_start_dates(master_excel_with_duplicates):
    """Test that different start dates for furnace groups are preserved during aggregation."""
    reader = MasterExcelReader(master_excel_with_duplicates)
    plants, _ = reader.read_plants(simulation_start_year=Year(2025))  # Unpack tuple

    # Find Rizhao Steel plant
    rizhao_plant = next((p for p in plants if p.plant_id == "P100000120240"), None)
    assert rizhao_plant is not None

    # Check that furnace groups have different last_renovation_dates based on their start dates
    renovation_dates = [fg.last_renovation_date for fg in rizhao_plant.furnace_groups]

    # Should have dates from 2015, 2016, and 2018
    expected_years = {2015, 2016, 2018}
    actual_years = {d.year for d in renovation_dates if d is not None}

    assert actual_years == expected_years, f"Expected renovation years {expected_years}, got {actual_years}"


def test_total_capacity_preserved_after_aggregation(master_excel_with_duplicates):
    """Test that total capacity across all plants is preserved after aggregation."""
    reader = MasterExcelReader(master_excel_with_duplicates)
    plants, _ = reader.read_plants(simulation_start_year=Year(2025))  # Unpack tuple

    # Calculate total capacity
    total_iron_capacity = 0
    total_steel_capacity = 0

    for plant in plants:
        for fg in plant.furnace_groups:
            if fg.technology.product == "iron":
                total_iron_capacity += fg.capacity
            else:  # steel
                total_steel_capacity += fg.capacity

    # Expected capacities (in tonnes, after conversion from kt)
    # Iron: 5000 + 3000 + 1500 = 9500 kt = 9,500,000 tonnes
    # Steel: 6000 + 2000 = 8000 kt = 8,000,000 tonnes
    expected_iron = Volumes(9_500_000)
    expected_steel = Volumes(8_000_000)

    assert total_iron_capacity == expected_iron, (
        f"Total iron capacity should be {expected_iron}, got {total_iron_capacity}"
    )
    assert total_steel_capacity == expected_steel, (
        f"Total steel capacity should be {expected_steel}, got {total_steel_capacity}"
    )
