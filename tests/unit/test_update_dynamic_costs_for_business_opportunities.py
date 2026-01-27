"""Tests for PlantGroup.update_dynamic_costs_for_business_opportunities method."""

import pytest
from unittest.mock import MagicMock
from steelo.domain.models import PlantGroup, Subsidy, Location
from steelo.domain.commands import UpdateDynamicCosts
from steelo.devdata import get_furnace_group, get_plant, PointInTime, TimeFrame, Year


@pytest.fixture
def mock_custom_energy_costs():
    """Create mock custom energy costs with .sel() method."""
    mock_power = MagicMock()
    mock_power.sel.return_value.values = 50.0  # USD/MWh

    mock_lcoh = MagicMock()
    mock_lcoh.sel.return_value.values = 3.5  # $/kg

    return {
        "power_price": mock_power,
        "capped_lcoh": mock_lcoh,
    }


@pytest.fixture
def capex_dict_all_locs():
    """CAPEX by region and technology."""
    return {
        "Americas": {
            "EAF": 1000.0,
            "BOF": 1500.0,
            "DRI": 2000.0,
            "DRIH2": 2500.0,
        },
        "Europe": {
            "EAF": 1100.0,
            "BOF": 1600.0,
            "DRI": 2100.0,
            "DRIH2": 2600.0,
        },
    }


@pytest.fixture
def cost_debt_all_locs():
    """Cost of debt by ISO3 country code."""
    return {
        "USA": 0.05,
        "DEU": 0.04,
        "CHN": 0.06,
    }


@pytest.fixture
def iso3_to_region_map():
    """Map ISO3 codes to regions."""
    return {
        "USA": "Americas",
        "DEU": "Europe",
        "CHN": "Asia",
    }


def test_update_costs_for_considered_plant_no_subsidies(
    mock_custom_energy_costs, capex_dict_all_locs, cost_debt_all_locs, iso3_to_region_map
):
    """Test updating costs for a considered plant with no subsidies."""
    # Create a considered furnace group
    fg = get_furnace_group(
        fg_id="fg_test",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2027), end=Year(2047)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.08  # Old value
    fg.technology.capex = 1200.0  # Old value
    fg.energy_costs = {"electricity": 60.0, "hydrogen": 4.0}  # Old values
    fg.bill_of_materials = {
        "energy": {
            "electricity": {"unit_cost": 60.0, "demand": 0.5},
            "hydrogen": {"unit_cost": 4.0, "demand": 0.1},
        },
        "materials": {
            "scrap": {"unit_cost": 200.0, "demand": 1.0},
        },
    }

    plant = get_plant(
        plant_id="plant_test",
        furnace_groups=[fg],
    )
    plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # Execute
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
        capex_subsidies={},
        debt_subsidies={},
    )

    # Verify
    assert len(commands) == 1
    cmd = commands[0]
    assert isinstance(cmd, UpdateDynamicCosts)
    assert cmd.plant_id == "plant_test"
    assert cmd.furnace_group_id == "fg_test"
    assert cmd.new_cost_of_debt == 0.05  # From cost_debt_all_locs["USA"]
    assert cmd.new_cost_of_debt_no_subsidy == 0.05
    assert cmd.new_capex == 1000.0  # From capex_dict_all_locs["Americas"]["EAF"]
    assert cmd.new_capex_no_subsidy == 1000.0
    assert cmd.new_electricity_cost == pytest.approx(50.0)  # Geospatial layer already in USD/kWh-equivalent
    assert cmd.new_hydrogen_cost == 3.5  # From mock_custom_energy_costs
    assert cmd.new_bill_of_materials is not None
    assert cmd.new_bill_of_materials["energy"]["electricity"]["unit_cost"] == pytest.approx(50.0)
    assert cmd.new_bill_of_materials["energy"]["hydrogen"]["unit_cost"] == 3.5


def test_update_costs_for_announced_plant_no_subsidies(
    mock_custom_energy_costs, capex_dict_all_locs, cost_debt_all_locs, iso3_to_region_map
):
    """Test updating costs for an announced plant with no subsidies."""
    fg = get_furnace_group(
        fg_id="fg_announced",
        tech_name="BOF",
        capacity=200,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2026), end=Year(2046)),
            plant_lifetime=20,
        ),
        utilization_rate=0.75,
    )
    fg.status = "announced"
    fg.cost_of_debt = 0.07
    fg.technology.capex = 1700.0
    fg.energy_costs = {"electricity": 55.0, "hydrogen": 3.8}
    fg.bill_of_materials = {
        "energy": {
            "electricity": {"unit_cost": 55.0, "demand": 0.3},
        },
        "materials": {
            "hot_metal": {"unit_cost": 300.0, "demand": 0.95},
        },
    }

    plant = get_plant(plant_id="plant_announced", furnace_groups=[fg])
    plant.location = Location(lat=51.0, lon=9.0, country="DEU", region="Europe", iso3="DEU")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # Execute
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
        capex_subsidies={},
        debt_subsidies={},
    )

    # Verify
    assert len(commands) == 1
    cmd = commands[0]
    assert isinstance(cmd, UpdateDynamicCosts)
    assert cmd.new_cost_of_debt == 0.04  # From cost_debt_all_locs["DEU"]
    assert cmd.new_capex == 1600.0  # From capex_dict_all_locs["Europe"]["BOF"]


def test_update_costs_with_capex_subsidies(
    mock_custom_energy_costs, capex_dict_all_locs, cost_debt_all_locs, iso3_to_region_map
):
    """Test updating costs with CAPEX subsidies."""
    fg = get_furnace_group(
        fg_id="fg_subsidized",
        tech_name="DRI",
        capacity=150,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2027), end=Year(2047)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.06
    fg.technology.capex = 3000.0
    fg.energy_costs = {"electricity": 60.0, "hydrogen": 5.0}
    fg.bill_of_materials = {
        "energy": {
            "electricity": {"unit_cost": 60.0, "demand": 0.4},
            "hydrogen": {"unit_cost": 5.0, "demand": 0.8},
        },
        "materials": {
            "iron_ore": {"unit_cost": 100.0, "demand": 1.5},
        },
    }

    plant = get_plant(plant_id="plant_subsidized", furnace_groups=[fg])
    plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # Create CAPEX subsidy: 30% reduction
    capex_subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2026),
        end_year=Year(2035),
        technology_name="DRI",
        cost_item="capex",
        subsidy_type="relative",
        subsidy_amount=0.3,  # 30% reduction (stored as decimal)
    )

    capex_subsidies = {
        "USA": {
            "DRI": [capex_subsidy],
        }
    }

    # Execute
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
        capex_subsidies=capex_subsidies,
        debt_subsidies={},
    )

    # Verify
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd.new_capex_no_subsidy == 2000.0  # Base CAPEX from capex_dict_all_locs for DRI
    assert cmd.new_capex == 2000.0 * 0.7  # 30% subsidy applied
    assert cmd.new_capex == 1400.0


def test_update_costs_with_debt_subsidies(
    mock_custom_energy_costs, capex_dict_all_locs, cost_debt_all_locs, iso3_to_region_map
):
    """Test updating costs with debt subsidies."""
    fg = get_furnace_group(
        fg_id="fg_debt_sub",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2027), end=Year(2047)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "announced"
    fg.cost_of_debt = 0.07
    fg.technology.capex = 1200.0
    fg.energy_costs = {"electricity": 60.0, "hydrogen": 4.0}
    fg.bill_of_materials = {
        "energy": {
            "electricity": {"unit_cost": 60.0, "demand": 0.5},
        },
        "materials": {
            "scrap": {"unit_cost": 200.0, "demand": 1.0},
        },
    }

    plant = get_plant(plant_id="plant_debt_sub", furnace_groups=[fg])
    plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # Create debt subsidy: 2% absolute reduction
    debt_subsidy = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2025),
        end_year=Year(2030),
        technology_name="EAF",
        cost_item="debt",
        subsidy_type="absolute",
        subsidy_amount=0.02,  # 2% absolute reduction
    )

    debt_subsidies = {
        "USA": {
            "EAF": [debt_subsidy],
        }
    }

    # Execute - announced plant uses year = current_year + 1 = 2026
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
        capex_subsidies={},
        debt_subsidies=debt_subsidies,
    )

    # Verify
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd.new_cost_of_debt_no_subsidy == 0.05  # Base from cost_debt_all_locs["USA"]
    assert abs(cmd.new_cost_of_debt - 0.03) < 1e-10  # 0.05 - 0.02 subsidy (with floating point tolerance)


def test_skip_operating_plants(mock_custom_energy_costs, capex_dict_all_locs, cost_debt_all_locs, iso3_to_region_map):
    """Test that operating plants are skipped."""
    fg = get_furnace_group(
        fg_id="fg_operating",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2020), end=Year(2040)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "operating"  # Not considered or announced
    fg.cost_of_debt = 0.06
    fg.technology.capex = 1000.0
    fg.energy_costs = {"electricity": 50.0, "hydrogen": 3.0}

    plant = get_plant(plant_id="plant_operating", furnace_groups=[fg])
    plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # Execute
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
    )

    # Verify - should return empty list
    assert len(commands) == 0


def test_skip_plant_with_missing_cost_of_debt(mock_custom_energy_costs, capex_dict_all_locs, iso3_to_region_map):
    """Test that plants with missing cost of debt data are skipped."""
    fg = get_furnace_group(
        fg_id="fg_no_debt",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2027), end=Year(2047)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.06
    fg.technology.capex = 1000.0
    fg.energy_costs = {"electricity": 50.0, "hydrogen": 3.0}

    plant = get_plant(plant_id="plant_no_debt", furnace_groups=[fg])
    plant.location = Location(lat=40.0, lon=-100.0, country="ZZZ", region="Unknown", iso3="ZZZ")  # Unknown ISO3

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # Cost of debt missing for ZZZ
    cost_debt_all_locs = {"USA": 0.05, "DEU": 0.04}

    # Execute
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
    )

    # Verify - should skip this plant
    assert len(commands) == 0


def test_skip_furnace_group_with_missing_capex(mock_custom_energy_costs, cost_debt_all_locs, iso3_to_region_map):
    """Test that furnace groups with missing CAPEX data are skipped."""
    fg = get_furnace_group(
        fg_id="fg_no_capex",
        tech_name="DRI",  # Use valid tech but exclude from capex dict
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2027), end=Year(2047)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.06
    fg.technology.capex = 1000.0
    fg.energy_costs = {"electricity": 50.0, "hydrogen": 3.0}

    plant = get_plant(plant_id="plant_no_capex", furnace_groups=[fg])
    plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # CAPEX missing for DRI
    capex_dict_all_locs = {
        "Americas": {
            "EAF": 1000.0,
            "BOF": 1500.0,
            # DRI intentionally missing
        }
    }

    # Execute
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
    )

    # Verify - should skip this furnace group
    assert len(commands) == 0


def test_skip_furnace_group_with_no_cost_changes(
    mock_custom_energy_costs, capex_dict_all_locs, cost_debt_all_locs, iso3_to_region_map
):
    """Test that furnace groups with no cost changes are skipped.

    NOTE: This test expects a command because even if scalar costs match,
    the BOM will be updated with new energy prices.
    """
    fg = get_furnace_group(
        fg_id="fg_no_change",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2027), end=Year(2047)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    # Set costs to match what will be calculated
    fg.cost_of_debt = 0.05  # Matches cost_debt_all_locs["USA"]
    fg.technology.capex = 1000.0  # Matches capex_dict_all_locs["Americas"]["EAF"]
    fg.energy_costs = {"electricity": 50.0, "hydrogen": 3.5}  # Matches mock values

    plant = get_plant(plant_id="plant_no_change", furnace_groups=[fg])
    plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # Execute
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
    )

    # Verify - should create command because BOM is included in new_costs
    assert len(commands) == 1
    cmd = commands[0]
    # Scalar values should match
    assert cmd.new_cost_of_debt == 0.05
    assert cmd.new_capex == 1000.0
    assert cmd.new_electricity_cost == pytest.approx(50.0)
    assert cmd.new_hydrogen_cost == 3.5


def test_multiple_plants_mixed_statuses(
    mock_custom_energy_costs, capex_dict_all_locs, cost_debt_all_locs, iso3_to_region_map
):
    """Test processing multiple plants with different statuses."""
    # Plant 1: Considered
    fg1 = get_furnace_group(
        fg_id="fg_1",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2027), end=Year(2047)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg1.status = "considered"
    fg1.cost_of_debt = 0.08
    fg1.technology.capex = 1200.0
    fg1.energy_costs = {"electricity": 60.0, "hydrogen": 4.0}
    fg1.bill_of_materials = {
        "energy": {"electricity": {"unit_cost": 60.0, "demand": 0.5}},
    }

    plant1 = get_plant(plant_id="plant_1", furnace_groups=[fg1])
    plant1.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    # Plant 2: Announced
    fg2 = get_furnace_group(
        fg_id="fg_2",
        tech_name="BOF",
        capacity=200,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2026), end=Year(2046)),
            plant_lifetime=20,
        ),
        utilization_rate=0.75,
    )
    fg2.status = "announced"
    fg2.cost_of_debt = 0.07
    fg2.technology.capex = 1700.0
    fg2.energy_costs = {"electricity": 55.0, "hydrogen": 3.8}
    fg2.bill_of_materials = {
        "energy": {"electricity": {"unit_cost": 55.0, "demand": 0.3}},
    }

    plant2 = get_plant(plant_id="plant_2", furnace_groups=[fg2])
    plant2.location = Location(lat=51.0, lon=9.0, country="DEU", region="Europe", iso3="DEU")

    # Plant 3: Operating (should be skipped)
    fg3 = get_furnace_group(
        fg_id="fg_3",
        tech_name="DRI",
        capacity=150,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2020), end=Year(2040)),
            plant_lifetime=20,
        ),
        utilization_rate=0.8,
    )
    fg3.status = "operating"
    fg3.cost_of_debt = 0.06
    fg3.technology.capex = 2000.0
    fg3.energy_costs = {"electricity": 50.0, "hydrogen": 3.5}

    plant3 = get_plant(plant_id="plant_3", furnace_groups=[fg3])
    plant3.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant1, plant2, plant3])

    # Execute
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
    )

    # Verify - should only process plants 1 and 2
    assert len(commands) == 2
    assert {cmd.plant_id for cmd in commands} == {"plant_1", "plant_2"}


def test_considered_plant_with_historical_npv(
    mock_custom_energy_costs, capex_dict_all_locs, cost_debt_all_locs, iso3_to_region_map
):
    """Test subsidy year calculation for considered plant with historical NPV data."""
    fg = get_furnace_group(
        fg_id="fg_historical",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2029), end=Year(2049)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.08
    fg.technology.capex = 1200.0
    fg.energy_costs = {"electricity": 60.0, "hydrogen": 4.0}
    fg.bill_of_materials = {
        "energy": {"electricity": {"unit_cost": 60.0, "demand": 0.5}},
    }
    # Simulate that this has been considered for 2 years already
    fg.historical_npv_business_opportunities = [100.0, 120.0]

    plant = get_plant(plant_id="plant_historical", furnace_groups=[fg])
    plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # Create a subsidy that only applies in specific years
    capex_subsidy_early = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2026),
        end_year=Year(2027),
        technology_name="EAF",
        cost_item="capex",
        subsidy_type="relative",
        subsidy_amount=0.5,  # 50% reduction (stored as decimal)
    )

    capex_subsidy_late = Subsidy(
        scenario_name="test",
        iso3="USA",
        start_year=Year(2028),
        end_year=Year(2035),
        technology_name="EAF",
        cost_item="capex",
        subsidy_type="relative",
        subsidy_amount=0.3,  # 30% reduction (stored as decimal)
    )

    capex_subsidies = {
        "USA": {
            "EAF": [capex_subsidy_early, capex_subsidy_late],
        }
    }

    # Execute with consideration_time=3
    # For considered plant with 2 years already considered:
    # year = current_year + consideration_time + 1 - years_already_considered
    # year = 2025 + 3 + 1 - 2 = 2027
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
        capex_subsidies=capex_subsidies,
        debt_subsidies={},
    )

    # Verify - should use subsidy from year 2027 (50% reduction)
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd.new_capex_no_subsidy == 1000.0
    assert cmd.new_capex == 500.0  # 1000 * 0.5 (50% subsidy)


def test_furnace_group_without_bill_of_materials(
    mock_custom_energy_costs, capex_dict_all_locs, cost_debt_all_locs, iso3_to_region_map
):
    """Test handling furnace group without bill_of_materials."""
    fg = get_furnace_group(
        fg_id="fg_no_bom",
        tech_name="EAF",
        capacity=100,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2027), end=Year(2047)),
            plant_lifetime=20,
        ),
        utilization_rate=0.7,
    )
    fg.status = "considered"
    fg.cost_of_debt = 0.08
    fg.technology.capex = 1200.0
    fg.energy_costs = {"electricity": 60.0, "hydrogen": 4.0}
    fg.bill_of_materials = None  # No BOM

    plant = get_plant(plant_id="plant_no_bom", furnace_groups=[fg])
    plant.location = Location(lat=40.0, lon=-100.0, country="USA", region="Americas", iso3="USA")

    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    # Execute
    commands = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=3,
        custom_energy_costs=mock_custom_energy_costs,
        capex_dict_all_locs=capex_dict_all_locs,
        cost_debt_all_locs=cost_debt_all_locs,
        iso3_to_region_map=iso3_to_region_map,
        global_risk_free_rate=0.02,
    )

    # Verify - should still create command but with None BOM
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd.new_bill_of_materials is None
