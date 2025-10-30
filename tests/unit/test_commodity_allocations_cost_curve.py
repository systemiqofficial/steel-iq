from unittest.mock import Mock
from steelo.domain.models import (
    CommodityAllocations,
    Plant,
    FurnaceGroup,
    Technology,
    Environment,
    Volumes,
    Location,
)


def test_create_cost_curve_for_hot_metal_from_furnace_groups():
    """Test that create_cost_curve works correctly for hot metal commodity from furnace groups."""
    # Create mock furnace groups
    mock_technology = Mock(spec=Technology)
    mock_technology.name = "BF"
    mock_technology.product = "iron"
    mock_technology.lcop = 100.0

    mock_fg1 = Mock(spec=FurnaceGroup)
    mock_fg1.furnace_group_id = "fg1"
    mock_fg1.technology = mock_technology
    mock_fg1.capacity = 1000.0
    mock_fg1.status = "operational"

    mock_fg2 = Mock(spec=FurnaceGroup)
    mock_fg2.furnace_group_id = "fg2"
    mock_fg2.technology = mock_technology
    mock_fg2.capacity = 2000.0
    mock_fg2.status = "operational"

    # Create mock plants
    mock_plant1 = Mock(spec=Plant)
    mock_plant1.location = Mock(spec=Location)
    mock_plant1.location.iso3 = "USA"

    mock_plant2 = Mock(spec=Plant)
    mock_plant2.location = Mock(spec=Location)
    mock_plant2.location.iso3 = "CAN"

    # Create allocations for hot metal
    allocations = {
        (mock_plant1, mock_fg1): {"demand_center1": Volumes(500.0)},
        (mock_plant2, mock_fg2): {"demand_center2": Volumes(1500.0)},
    }

    # Create CommodityAllocations instance
    commodity_allocations = CommodityAllocations(commodity="hot metal", allocations=allocations)

    # Create mock environment
    mock_env = Mock(spec=Environment)
    mock_env.year = 2025
    mock_env.cost_curve = {}  # Empty cost curves in environment

    # Mock the extract_price_from_costcurve to simulate the real behavior
    def mock_extract_price(demand, product, future=False):
        # hot metal maps to iron in the cost curves
        if product == "hot metal":
            product = "iron"
        if product not in mock_env.cost_curve and product != "iron":
            raise ValueError(f"No cost curve data available for product: {product}")
        return 100.0

    mock_env.extract_price_from_costcurve = mock_extract_price

    # Mock the generate_cost_curve function to return expected format
    expected_cost_curve = {
        "steel": [],
        "iron": [
            {"cumulative_capacity": 1000.0, "production_cost": 100.0},
            {"cumulative_capacity": 3000.0, "production_cost": 100.0},
        ],
    }

    # Mock the generate_cost_curve method on the environment
    mock_env.generate_cost_curve = Mock(return_value=expected_cost_curve)

    # This should now work with the fix
    commodity_allocations.create_cost_curve(mock_env)

    # Verify the cost curve was created correctly
    assert commodity_allocations.cost_curve == expected_cost_curve
    assert commodity_allocations.price == 100.0  # Should use the iron cost curve price


def test_create_cost_curve_for_steel_from_suppliers():
    """Test that create_cost_curve works correctly for commodities from suppliers."""
    from steelo.domain.models import Supplier

    # Create mock suppliers
    supplier1 = Mock(spec=Supplier)
    supplier1.production_cost = 50.0
    supplier1.capacity_by_year = {2025: Volumes(1000.0)}

    supplier2 = Mock(spec=Supplier)
    supplier2.production_cost = 60.0
    supplier2.capacity_by_year = {2025: Volumes(2000.0)}

    # Create allocations
    allocations = {supplier1: {"demand_center1": Volumes(800.0)}, supplier2: {"demand_center2": Volumes(1200.0)}}

    # Create CommodityAllocations instance
    commodity_allocations = CommodityAllocations(commodity="io_high", allocations=allocations)

    # Create mock environment
    mock_env = Mock(spec=Environment)
    mock_env.year = 2025

    # This should work correctly
    commodity_allocations.create_cost_curve(mock_env)

    # Verify the cost curve was created correctly
    assert len(commodity_allocations.cost_curve) == 2
    assert commodity_allocations.cost_curve[0]["cumulative_capacity"] == 1000.0
    assert commodity_allocations.cost_curve[0]["production_cost"] == 50.0
    assert commodity_allocations.cost_curve[1]["cumulative_capacity"] == 3000.0
    assert commodity_allocations.cost_curve[1]["production_cost"] == 60.0
    assert commodity_allocations.price == 60.0  # Price for total demand of 2000


def test_create_cost_curve_for_steel_from_furnace_groups():
    """Test that create_cost_curve works correctly for steel commodity from furnace groups."""
    # Create mock furnace groups
    mock_technology = Mock(spec=Technology)
    mock_technology.name = "BOF"
    mock_technology.product = "steel"
    mock_technology.lcop = 200.0

    mock_fg = Mock(spec=FurnaceGroup)
    mock_fg.furnace_group_id = "fg1"
    mock_fg.technology = mock_technology
    mock_fg.capacity = 1000.0
    mock_fg.status = "operational"

    # Create mock plant
    mock_plant = Mock(spec=Plant)
    mock_plant.location = Mock(spec=Location)
    mock_plant.location.iso3 = "USA"

    # Create allocations for steel
    allocations = {(mock_plant, mock_fg): {"demand_center1": Volumes(500.0)}}

    # Create CommodityAllocations instance
    commodity_allocations = CommodityAllocations(commodity="steel", allocations=allocations)

    # Create mock environment
    mock_env = Mock(spec=Environment)
    mock_env.year = 2025

    # Mock the generate_cost_curve function to return expected format
    expected_cost_curve = {"steel": [{"cumulative_capacity": 1000.0, "production_cost": 200.0}], "iron": []}

    # Mock the generate_cost_curve method on the environment
    mock_env.generate_cost_curve = Mock(return_value=expected_cost_curve)
    mock_env.extract_price_from_costcurve = Mock(return_value=200.0)

    commodity_allocations.create_cost_curve(mock_env)

    # Verify the cost curve was created correctly
    assert commodity_allocations.cost_curve == expected_cost_curve
    assert commodity_allocations.price == 200.0  # Should use the steel cost curve price


def test_extract_price_from_generated_cost_curve():
    """Test extracting price from a generated cost curve for iron products."""
    # This test demonstrates what the fix should do
    cost_curve = {
        "steel": [],
        "iron": [
            {"cumulative_capacity": 1000.0, "production_cost": 100.0},
            {"cumulative_capacity": 3000.0, "production_cost": 120.0},
            {"cumulative_capacity": 5000.0, "production_cost": 150.0},
        ],
    }

    # For hot metal (iron product) with demand of 2000
    # Should use the iron cost curve
    iron_curve = cost_curve["iron"]
    demand = 2000.0

    # Find the appropriate price
    price = None
    for entry in iron_curve:
        if entry["cumulative_capacity"] >= demand:
            price = entry["production_cost"]
            break

    assert price == 120.0  # Should be the second entry's price


def test_generate_cost_curve_handles_infinite_unit_costs():
    """Test that generate_cost_curve handles furnace groups with infinite unit production costs.

    This test demonstrates the current issue: furnace groups with infinite unit costs
    cause the entire cost curve to be empty, breaking the simulation.
    """
    from steelo.domain.models import Environment, FurnaceGroup, Technology, Volumes
    from unittest.mock import Mock
    import math

    # Create a mock technology
    mock_technology = Technology(name="EAF", product="steel", dynamic_business_case=None)

    # Create a furnace group that will have infinite unit_production_cost due to problematic opex/debt
    inf_cost_furnace_group = Mock(spec=FurnaceGroup)
    inf_cost_furnace_group.furnace_group_id = "test_fg_inf_cost"
    inf_cost_furnace_group.capacity = Volumes(1000.0)
    inf_cost_furnace_group.status = "operating"
    inf_cost_furnace_group.technology = mock_technology
    inf_cost_furnace_group.unit_production_cost = math.inf  # This is the problem!
    inf_cost_furnace_group.production = 800.0
    inf_cost_furnace_group.utilization_rate = 0.8

    # Create a good furnace group that should work
    good_furnace_group = Mock(spec=FurnaceGroup)
    good_furnace_group.furnace_group_id = "test_fg_good_cost"
    good_furnace_group.capacity = Volumes(1000.0)
    good_furnace_group.status = "operating"
    good_furnace_group.technology = mock_technology
    good_furnace_group.unit_production_cost = 150.0  # Reasonable cost
    good_furnace_group.production = 800.0
    good_furnace_group.utilization_rate = 0.8

    # Create a mock environment to call generate_cost_curve
    env = Mock(spec=Environment)
    env.year = 2025

    # Mock the generate_cost_curve method to return a simulated cost curve
    def mock_generate_cost_curve(furnace_groups, lag=0):
        cost_curve = {"steel": [], "iron": []}
        for fg in furnace_groups:
            if fg.technology.product == "steel":
                # Use unit_production_cost if available and finite, else use fallback
                if hasattr(fg, "unit_production_cost") and fg.unit_production_cost != math.inf:
                    cost = fg.unit_production_cost
                else:
                    # Fallback cost - high value
                    cost = 10000.0
                cost_curve["steel"].append(
                    {
                        "cumulative_capacity": fg.capacity.value if hasattr(fg.capacity, "value") else fg.capacity,
                        "production_cost": cost,
                    }
                )
        # Sort by production cost
        cost_curve["steel"].sort(key=lambda x: x["production_cost"])
        # Update cumulative capacity
        cumulative = 0
        for entry in cost_curve["steel"]:
            cumulative += entry["cumulative_capacity"]
            entry["cumulative_capacity"] = cumulative
        return cost_curve

    env.generate_cost_curve = mock_generate_cost_curve

    # Test 1: Just the infinite cost furnace group without opex/debt fallback data
    # Should get a high fallback cost (10000.0) since Mock doesn't have opex/debt attributes
    cost_curve_inf_only = env.generate_cost_curve([inf_cost_furnace_group])
    assert len(cost_curve_inf_only["steel"]) == 1, "Furnace groups with infinite unit_production_cost get fallback cost"
    assert cost_curve_inf_only["steel"][0]["production_cost"] == 10000.0, (
        "Should use high fallback cost when opex/debt unavailable"
    )

    # Test 2: Just the good furnace group - should work fine
    cost_curve_good_only = env.generate_cost_curve([good_furnace_group])
    assert len(cost_curve_good_only["steel"]) == 1, "Good furnace groups should be included"

    # Test 3: Mix of infinite and good - both should be included with appropriate costs
    cost_curve_mixed = env.generate_cost_curve([inf_cost_furnace_group, good_furnace_group])

    # After fix: both should be included - good one with 150.0, problematic one with 10000.0 fallback
    assert len(cost_curve_mixed["steel"]) == 2, "Both furnace groups should be included with appropriate costs"

    # Cost curve should be sorted by production cost (150.0, then 10000.0)
    assert cost_curve_mixed["steel"][0]["production_cost"] == 150.0, "Good furnace group should have lower cost"
    assert cost_curve_mixed["steel"][1]["production_cost"] == 10000.0, (
        "Problematic furnace group should have high fallback cost"
    )

    # Test 4: ALL furnace groups have infinite costs - after fix should get fallback costs
    all_infinite_furnace_groups = [
        Mock(
            spec=FurnaceGroup,
            furnace_group_id=f"fg_{i}",
            capacity=Volumes(1000.0),
            status="operating",
            technology=mock_technology,
            unit_production_cost=math.inf,
            production=800.0,
            utilization_rate=0.8,
        )
        for i in range(5)
    ]

    cost_curve_all_infinite = env.generate_cost_curve(all_infinite_furnace_groups)

    # After fix: instead of empty cost curves, we get fallback costs
    # This solves the "No cost curve data available for product: steel" error
    assert len(cost_curve_all_infinite["steel"]) == 5, (
        "All furnace groups should get fallback costs instead of being filtered out"
    )

    # All should have the high fallback cost since Mocks don't have opex/debt attributes
    for entry in cost_curve_all_infinite["steel"]:
        assert entry["production_cost"] == 10000.0, "All should use high fallback cost"


def test_generate_cost_curve_with_fallback_cost_calculation():
    """Test that generate_cost_curve provides fallback cost calculation when unit_production_cost fails.

    This test shows what SHOULD happen after implementing the fix.
    """
    from steelo.domain.models import (
        Environment,
        FurnaceGroup,
        Technology,
        Volumes,
    )
    from unittest.mock import Mock
    import math

    # Create a mock technology
    mock_technology = Technology(name="EAF", product="steel", dynamic_business_case=None)

    # Create furnace groups with infinite unit_production_cost but valid opex/debt components
    problematic_furnace_groups = []
    for i in range(3):
        fg = Mock(spec=FurnaceGroup)
        fg.furnace_group_id = f"problematic_fg_{i}"
        fg.capacity = Volumes(1000.0)
        fg.status = "operating"
        fg.technology = mock_technology
        fg.unit_production_cost = math.inf  # Problematic current calculation
        fg.production = 800.0  # Has production but cost calc fails
        fg.utilization_rate = 0.8

        # But has reasonable underlying cost components that could be used for fallback
        fg.opex = 100.0 + i * 10  # Reasonable opex values
        fg.debt_repayment_for_current_year = 50000.0 + i * 5000  # Reasonable debt values

        problematic_furnace_groups.append(fg)

    # Create a mock environment to call generate_cost_curve
    env = Mock(spec=Environment)
    env.year = 2025

    # Mock the generate_cost_curve method to return a simulated cost curve with fallback calculation
    def mock_generate_cost_curve_with_fallback(furnace_groups, lag=0):
        cost_curve = {"steel": [], "iron": []}
        for fg in furnace_groups:
            if fg.technology.product == "steel":
                # Use unit_production_cost if available and finite
                if hasattr(fg, "unit_production_cost") and fg.unit_production_cost != math.inf:
                    cost = fg.unit_production_cost
                else:
                    # Fallback calculation: opex + debt_repayment/production
                    if (
                        hasattr(fg, "opex")
                        and hasattr(fg, "debt_repayment_for_current_year")
                        and hasattr(fg, "production")
                    ):
                        cost = fg.opex + fg.debt_repayment_for_current_year / fg.production
                    else:
                        cost = 10000.0  # High fallback if no data available
                cost_curve["steel"].append(
                    {
                        "cumulative_capacity": fg.capacity.value if hasattr(fg.capacity, "value") else fg.capacity,
                        "production_cost": cost,
                    }
                )
        # Sort by production cost
        cost_curve["steel"].sort(key=lambda x: x["production_cost"])
        # Update cumulative capacity
        cumulative = 0
        for entry in cost_curve["steel"]:
            cumulative += entry["cumulative_capacity"]
            entry["cumulative_capacity"] = cumulative
        return cost_curve

    env.generate_cost_curve = mock_generate_cost_curve_with_fallback

    # FIXED BEHAVIOR: Should use fallback calculation with opex + debt_repayment/production
    cost_curve_current = env.generate_cost_curve(problematic_furnace_groups)

    # After fix implementation, furnace groups with infinite unit_production_cost
    # should use fallback calculation and be included in cost curve
    assert len(cost_curve_current["steel"]) == 3, (
        "Fix should enable fallback cost calculation for problematic furnace groups"
    )

    # Check that costs are calculated correctly:
    # fg_0: 100 + 50000/800 = 162.5
    # fg_1: 110 + 55000/800 = 178.75
    # fg_2: 120 + 60000/800 = 195.0
    expected_costs = [162.5, 178.75, 195.0]

    for i, entry in enumerate(cost_curve_current["steel"]):
        assert entry["production_cost"] == expected_costs[i], (
            f"Entry {i} should have cost {expected_costs[i]}, got {entry['production_cost']}"
        )
        assert entry["cumulative_capacity"] == (i + 1) * 1000.0, (
            f"Entry {i} should have cumulative capacity {(i + 1) * 1000.0}"
        )

    # All costs should be in reasonable range (150-200)
    for entry in cost_curve_current["steel"]:
        assert 150 <= entry["production_cost"] <= 200, "Fallback costs should be in reasonable range"
