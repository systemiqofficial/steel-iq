import pytest
import math
from geopy.distance import geodesic
import pyomo.environ as pyo

from steelo.domain.trade_modelling.trade_lp_modelling import (
    Commodity,
    BOMElement,
    MaterialParameters,
    ProcessType,
    Process,
    ProcessCenter,
    Allocations,
    ProcessConnector,
    TradeLPModel,
    TransportationCost,
)

from steelo.domain.constants import LP_EPSILON


@pytest.fixture
def sample_commodities():
    """Fixture to create some sample Commodity objects."""
    return [
        Commodity("iron"),
        Commodity("coal"),
        Commodity("steel"),
    ]


##############################################
# Basic Component Tests
##############################################


def test_commodity_init():
    """Test creating a Commodity with a valid string name."""
    c = Commodity("Coal")
    assert c.name == "coal"


def test_commodity_init_nonstring():
    """Test that creating a Commodity with a non-string raises ValueError."""
    with pytest.raises(ValueError) as exc:
        Commodity(123)
    assert "Commodity name must be a string" in str(exc.value)


def test_commodity_equality():
    """Test the equality operation of Commodity."""
    c1 = Commodity("steel")
    c2 = Commodity("Steel")
    assert c1 == c2  # Names are both lowered, so they match.


def test_commodity_inequality():
    """Test the inequality operation of Commodity with different names."""
    c1 = Commodity("iron")
    c2 = Commodity("steel")
    assert c1 != c2


def test_bom_element_init():
    """Test creating a BOMElement with valid data."""
    c_input = Commodity("iron_ore")
    c_output = Commodity("hot_metal")
    bom_params = {MaterialParameters.INPUT_RATIO: 1.6}
    bom_elem = BOMElement(
        name="BO_Iron2HotMetal",
        commodity=c_input,
        output_commodities=[c_output],
        parameters=bom_params,
    )
    assert bom_elem.commodity == c_input
    assert bom_elem.output_commodities == [c_output]
    assert bom_elem.parameters[MaterialParameters.INPUT_RATIO] == 1.6


def test_process_init():
    """Test creating a Process."""
    c_input = Commodity("iron_ore")
    c_output = Commodity("hot_metal")
    bom_elem = BOMElement(
        name="BO_Iron2HotMetal",
        commodity=c_input,
        output_commodities=[c_output],
        parameters={MaterialParameters.INPUT_RATIO: 1.6},
    )
    proc = Process(
        name="BlastFurnace",
        type=ProcessType.PRODUCTION,
        bill_of_materials=[bom_elem],
    )
    assert proc.name == "BlastFurnace"
    assert proc.type == ProcessType.PRODUCTION
    assert len(proc.bill_of_materials) == 1
    assert proc.products == [c_output]


def test_process_center_init(location_mock_factory):
    """Test creating a ProcessCenter."""
    # Suppose location_mock_factory returns a valid Location instance
    loc = location_mock_factory(lat=10, lon=20, iso3="ABC")

    c_input = Commodity("iron_ore")
    c_output = Commodity("hot_metal")
    bom_elem = BOMElement(
        name="BO_Iron2HotMetal",
        commodity=c_input,
        output_commodities=[c_output],
        parameters={MaterialParameters.INPUT_RATIO: 1.6},
    )
    proc = Process(name="BlastFurnace", type=ProcessType.PRODUCTION, bill_of_materials=[bom_elem])

    pc = ProcessCenter(
        name="PC1",
        process=proc,
        capacity=100.0,
        location=loc,
    )

    assert pc.name == "PC1"
    assert pc.process == proc
    assert pc.capacity == 100.0
    assert pc.location == loc


def test_process_center_distance_calculation(location_mock_factory):
    """Test the distance calculation between two ProcessCenters."""
    loc1 = location_mock_factory(lat=10.0, lon=20.0, iso3="XYZ")
    loc2 = location_mock_factory(lat=10.5, lon=20.5, iso3="ABC")

    # Overriding distance to other iso3, for instance
    loc1.distance_to_other_iso3 = {"ABC": 55.0}

    c_input = Commodity("iron_ore")
    c_output = Commodity("hot_metal")
    bom_elem = BOMElement(
        name="BO_Iron2HotMetal",
        commodity=c_input,
        output_commodities=[c_output],
        parameters={MaterialParameters.INPUT_RATIO: 1.6},
    )
    proc = Process(name="BlastFurnace", type=ProcessType.PRODUCTION, bill_of_materials=[bom_elem])

    pc1 = ProcessCenter(name="PC1", process=proc, capacity=100.0, location=loc1)
    pc2 = ProcessCenter(name="PC2", process=proc, capacity=100.0, location=loc2)

    # Because loc1.distance_to_other_iso3 has an entry for ABC,
    # the function will use that distance.
    dist = pc1.distance_to_other_processcenter(pc2)
    assert dist == 55.0

    # If we remove the iso3 override, geodesic would be used:
    loc1.distance_to_other_iso3 = {}
    # Approx geodesic distance:
    dist_geodesic = geodesic((10.0, 20.0), (10.5, 20.5)).kilometers
    dist_recalc = pc1.distance_to_other_processcenter(pc2)
    assert math.isclose(dist_geodesic, dist_recalc, rel_tol=1e-2)


def test_process_center_set_optimal_production_ok(location_mock_factory):
    """Test set_optimal_production within capacity."""
    loc = location_mock_factory(lat=10, lon=20, iso3="ABC")
    proc = Process(name="Dummy", type=ProcessType.PRODUCTION, bill_of_materials=[])

    pc = ProcessCenter(name="PC1", process=proc, capacity=100.0, location=loc)
    pc.set_optimal_production(50.0)
    assert pc.optimal_production == 50.0


def test_process_center_set_optimal_production_exceeds(location_mock_factory):
    """Test that set_optimal_production raises ValueError if it exceeds capacity."""
    loc = location_mock_factory(lat=10, lon=20, iso3="ABC")
    proc = Process(name="Dummy", type=ProcessType.PRODUCTION, bill_of_materials=[])

    pc = ProcessCenter(name="PC1", process=proc, capacity=100.0, location=loc)
    with pytest.raises(ValueError):
        pc.set_optimal_production(150.0)


##############################################
# Allocations Tests
##############################################


def test_allocations_init_and_access():
    """Test basic Allocations creation, setting, and getting."""
    pc1 = ProcessCenter(name="PC1", process=None, capacity=10, location=None)
    pc2 = ProcessCenter(name="PC2", process=None, capacity=20, location=None)
    c_iron = Commodity("iron")

    allocations_dict = {(pc1, pc2, c_iron): 5.0}
    alloc = Allocations(allocations=allocations_dict)
    assert alloc.get_allocation(pc1, pc2, c_iron) == 5.0

    # Update the value
    alloc.set_allocation(pc1, pc2, c_iron, 7.0)
    assert alloc.get_allocation(pc1, pc2, c_iron) == 7.0


def test_allocations_validate_ok():
    """Test validate_allocations with correct usage."""
    pc1 = ProcessCenter(name="PC1", process=None, capacity=10, location=None)
    pc2 = ProcessCenter(name="PC2", process=None, capacity=20, location=None)
    c_iron = Commodity("iron")

    # The total from PC1 is 5.0, which is <= 10, so it should be fine.
    allocations_dict = {(pc1, pc2, c_iron): 5.0}
    alloc = Allocations(allocations=allocations_dict)
    alloc.validate_allocations()  # should not raise


def test_allocations_validate_exceeds_capacity():
    """Test validate_allocations raises ValueError when sum exceeds capacity."""
    pc1 = ProcessCenter(name="PC1", process=None, capacity=10, location=None)
    pc2 = ProcessCenter(name="PC2", process=None, capacity=20, location=None)
    c_iron = Commodity("iron")

    # The total from PC1 is 15.0, which is > 10 => should raise.
    allocations_dict = {(pc1, pc2, c_iron): 15.0}
    alloc = Allocations(allocations=allocations_dict)
    with pytest.raises(ValueError) as exc:
        alloc.validate_allocations()
    assert "Total allocation from PC1 is higher than capacity" in str(exc.value)


##############################################
# ProcessConnector Tests
##############################################


def test_process_connector_init():
    """Test creating a ProcessConnector."""
    proc1 = Process(name="Supply", type=ProcessType.SUPPLY, bill_of_materials=[])
    proc2 = Process(name="Production", type=ProcessType.PRODUCTION, bill_of_materials=[])
    connector = ProcessConnector(from_process=proc1, to_process=proc2)

    assert connector.from_process == proc1
    assert connector.to_process == proc2
    assert connector.name == "Supply_Production"
    assert "Supply -> Production" in repr(connector)


##############################################
# TradeLPModel Tests (build, solve, etc.)
##############################################


def test_trade_lp_model_basic_build_and_solve(location_mock_factory):
    """
    End-to-end test that builds a simple supply -> production -> demand scenario,
    solves it, and checks that the solution is feasible.
    """
    # Create model
    model = TradeLPModel()

    # Create Commodities
    iron_ore = Commodity("iron_ore")
    steel = Commodity("steel")

    # Create BOMElements
    bom_iron2steel = BOMElement(
        name="BOM_Iron2Steel",
        commodity=iron_ore,
        output_commodities=[steel],
        parameters={MaterialParameters.INPUT_RATIO: 1.5},
    )

    # Create Processes
    supply_process = Process(name="IronOreSupply", type=ProcessType.SUPPLY, bill_of_materials=[])
    production_process = Process(
        name="SteelProduction", type=ProcessType.PRODUCTION, bill_of_materials=[bom_iron2steel]
    )
    demand_process = Process(name="SteelDemand", type=ProcessType.DEMAND, bill_of_materials=[])

    # Create locations
    loc_supply = location_mock_factory(lat=0, lon=0, iso3="SUP")
    loc_prod = location_mock_factory(lat=1, lon=1, iso3="PRO")
    loc_demand = location_mock_factory(lat=2, lon=2, iso3="DEM")

    # Create ProcessCenters
    pc_supply = ProcessCenter(
        name="PC_Supply",
        process=supply_process,
        capacity=100.0,  # We can supply up to 100 units
        location=loc_supply,
    )
    pc_prod = ProcessCenter(
        name="PC_Prod",
        process=production_process,
        capacity=80.0,  # We can produce up to 80 steel
        location=loc_prod,
    )
    pc_demand = ProcessCenter(
        name="PC_Demand",
        process=demand_process,
        capacity=50.0,  # We want 50 steel demanded
        location=loc_demand,
    )

    # Add them to model
    model.add_commodities([iron_ore, steel])
    model.add_processes([supply_process, production_process, demand_process])
    model.add_process_centers([pc_supply, pc_prod, pc_demand])
    model.add_bom_elements([bom_iron2steel])

    # Create process connectors: supply -> production -> demand
    connector_sp = ProcessConnector(from_process=supply_process, to_process=production_process)
    connector_pd = ProcessConnector(from_process=production_process, to_process=demand_process)
    model.add_process_connectors([connector_sp, connector_pd])

    # Build and solve
    model.build_lp_model()
    result = model.solve_lp_model()

    assert result.solver.status == pyo.SolverStatus.ok, f"Solver did not return 'ok': {result.solver.status}"

    # Extract solution
    model.extract_solution()

    # Check that we do have some allocations
    allocations = model.allocations.allocations
    # Should allocate iron_ore from PC_Supply to PC_Prod, then steel from PC_Prod to PC_Demand
    # We'll do a very basic check that there's no over-production and no negative flows
    for (from_pc, to_pc, comm), vol in allocations.items():
        assert vol >= 0
        # capacity checks
        if from_pc == pc_supply:
            assert vol <= pc_supply.capacity + LP_EPSILON
        elif from_pc == pc_prod:
            assert vol <= pc_prod.capacity + LP_EPSILON

    # We can also check if the sum of steel going to PC_Demand is ~50 (the demand),
    # minus the small slack if any.
    total_steel_to_demand = sum(vol for (f, t, c), vol in allocations.items() if t == pc_demand and c == steel)
    # Because we have enough supply, we expect to meet the demand if feasible.
    # The BOM says 1.5 iron_ore -> 1 steel, we have capacity 80 for steel production,
    # but only demand 50. We can meet that. Let's check we haven't exceeded it:
    assert total_steel_to_demand <= 50 + LP_EPSILON


def test_trade_lp_model_infeasible_demand(location_mock_factory):
    """
    Test that if we want more steel than we can possibly produce or supply,
    the solver doesn't fail with infeasibility (because of the demand slack).
    """
    model = TradeLPModel()

    # Create Commodities
    iron_ore = Commodity("iron_ore")
    steel = Commodity("steel")

    # Create BOMElements
    bom_iron2steel = BOMElement(
        name="BOM_Iron2Steel",
        commodity=iron_ore,
        output_commodities=[steel],
        parameters={MaterialParameters.INPUT_RATIO: 1.5},
    )

    # Create Processes
    supply_process = Process(name="IronOreSupply", type=ProcessType.SUPPLY, bill_of_materials=[])
    production_process = Process(
        name="SteelProduction", type=ProcessType.PRODUCTION, bill_of_materials=[bom_iron2steel]
    )
    demand_process = Process(name="SteelDemand", type=ProcessType.DEMAND, bill_of_materials=[])

    # Create locations
    loc_supply = location_mock_factory(lat=0, lon=0, iso3="SUP")
    loc_prod = location_mock_factory(lat=1, lon=1, iso3="PRO")
    loc_demand = location_mock_factory(lat=2, lon=2, iso3="DEM")

    # Create ProcessCenters
    pc_supply = ProcessCenter(
        name="PC_Supply",
        process=supply_process,
        capacity=10.0,  # Very little supply
        location=loc_supply,
    )
    pc_prod = ProcessCenter(
        name="PC_Prod",
        process=production_process,
        capacity=5.0,  # Not enough to meet demand
        location=loc_prod,
    )
    pc_demand = ProcessCenter(
        name="PC_Demand",
        process=demand_process,
        capacity=50.0,  # Demand for 50 steel
        location=loc_demand,
    )

    # Add them to model
    model.add_commodities([iron_ore, steel])
    model.add_processes([supply_process, production_process, demand_process])
    model.add_process_centers([pc_supply, pc_prod, pc_demand])
    model.add_bom_elements([bom_iron2steel])

    # Create process connectors: supply -> production -> demand
    connector_sp = ProcessConnector(from_process=supply_process, to_process=production_process)
    connector_pd = ProcessConnector(from_process=production_process, to_process=demand_process)
    model.add_process_connectors([connector_sp, connector_pd])

    # Build and solve
    model.build_lp_model()
    result = model.solve_lp_model()

    # Should not be infeasible due to slack
    assert result.solver.status == pyo.SolverStatus.ok

    model.extract_solution()
    allocations = model.allocations.allocations

    # Check that we have flows but can't meet entire demand, so some slack might be used
    total_steel_to_demand = sum(vol for (f, t, c), vol in allocations.items() if t == pc_demand and c == steel)

    # We'll produce at most 5 steel because of capacity, so let's see if it's around that
    assert total_steel_to_demand <= 5 + LP_EPSILON


##############################################
# Example fixture for mocking a Location
##############################################


@pytest.fixture
def location_mock_factory():
    """
    Return a function that can produce a mock or simple data-class-like object
    resembling your `steelo.domain.models.Location`.
    """

    class MockLocation:
        def __init__(self, lat, lon, iso3=None):
            self.lat = lat
            self.lon = lon
            self.iso3 = iso3
            self.distance_to_other_iso3 = {}

        def distance_to_other_processcenter(self, other):
            """If you needed a simpler location method, you could place it here."""
            pass

    def _factory(lat, lon, iso3=None):
        return MockLocation(lat, lon, iso3)

    return _factory


# TESTING TARIFF INFO:
def test_tariff_info_injection(location_mock_factory):
    c = Commodity("steel")
    pc1 = ProcessCenter("PC1", process=None, capacity=10, location=location_mock_factory(1, 2, iso3="AAA"))
    pc2 = ProcessCenter("PC2", process=None, capacity=10, location=location_mock_factory(3, 4, iso3="BBB"))

    model = TradeLPModel()
    model.add_commodities([c])
    model.add_process_centers([pc1, pc2])
    model.process_connectors = []
    model.legal_allocations = [(pc1, pc2, c)]
    model.lp_model = pyo.ConcreteModel()
    model.lp_model.allocation_variables = pyo.Var([("PC1", "PC2", "steel")], domain=pyo.NonNegativeReals)

    # Inject tariff data
    quota = {("AAA", "BBB", "steel"): 50}
    tax = {("AAA", "BBB", "steel"): 7.5}
    model.add_tariff_information(quota, tax)
    model.add_tariff_quotas_and_tax_as_parameters()

    assert model.lp_model.tariff_tax[("PC1", "PC2", "steel")] == 7.5
    assert model.lp_model.quota_allocations[("AAA", "BBB", "steel")] == [("PC1", "PC2", "steel")]


# TEST FOR BOM PARAMETER PARSING:
def test_bom_parameters_are_added():
    com_input = Commodity("iron")
    com_output = Commodity("steel")
    bom = BOMElement(
        "IronToSteel",
        commodity=com_input,
        output_commodities=[com_output],
        parameters={MaterialParameters.INPUT_RATIO.value: 1.5},
    )
    proc = Process(name="Converter", type=ProcessType.PRODUCTION, bill_of_materials=[bom])
    pc = ProcessCenter(name="PC1", process=proc, capacity=100.0, location=None)

    model = TradeLPModel()
    model.add_commodities([com_input, com_output])
    model.add_bom_elements([bom])
    model.add_process_centers([pc])
    model.lp_model = pyo.ConcreteModel()
    model.add_bom_parameters_as_parameters_to_lp()
    assert model.lp_model.bom_parameters["PC1", "iron", "input_ratio"] == 1.5


# TEST FOR FEEDSTOCK PRIMARY OUTPUT MAPPING:
def test_primary_output_of_feedstock_mapping():
    com_input = Commodity("scrap")
    com_output = Commodity("billet")
    bom = BOMElement("ScrapToBillet", com_input, [com_output], parameters={MaterialParameters.INPUT_RATIO: 1.2})
    proc = Process("Melter", ProcessType.PRODUCTION, [bom])
    pc = ProcessCenter("PC1", proc, 100, location=None)

    model = TradeLPModel()
    model.add_commodities([com_input, com_output])
    model.add_process_centers([pc])
    model.add_bom_elements([bom])
    model.lp_model = pyo.ConcreteModel()
    model.add_primary_outputs_of_feedstock_as_parameter_to_lp()

    assert model.lp_model.primary_outputs_of_feedstock[("PC1", "scrap")] == ["billet"]


# TEST SLACK VARIABLE CREATION:
def test_demand_slack_variable_creation():
    demand_proc = Process("Consumer", ProcessType.DEMAND, [])
    pc = ProcessCenter("PC_Demand", demand_proc, 60.0, location=None)

    model = TradeLPModel()
    model.add_process_centers([pc])
    model.lp_model = pyo.ConcreteModel()
    model.add_demand_slack_variables_to_lp()

    assert "PC_Demand" in model.lp_model.demand_slack_variable


# TEST GETTER UTILITIES:
def test_getters_from_model():
    c = Commodity("copper")
    p = Process("Smelter", ProcessType.PRODUCTION, [])
    bom = BOMElement("CopperBOM", c, c, {MaterialParameters.INPUT_RATIO: 1.1})

    model = TradeLPModel()
    model.add_commodities([c])
    model.add_processes([p])
    model.add_bom_elements([bom])

    assert model.get_commodity("copper") == c
    assert model.get_process("Smelter") == p
    assert model.get_bom_element("CopperBOM") == bom


##############################################
# TransportationCost Tests
##############################################


def test_transportation_cost_init():
    """Test creating a TransportationCost."""
    tc = TransportationCost(from_iso3="USA", to_iso3="CHN", commodity="Steel", cost_per_ton=25.5)
    assert tc.from_iso3 == "USA"
    assert tc.to_iso3 == "CHN"
    assert tc.commodity == "steel"  # Normalized to lowercase
    assert tc.cost_per_ton == 25.5


def test_transportation_cost_equality():
    """Test equality of TransportationCost objects."""
    tc1 = TransportationCost(from_iso3="USA", to_iso3="CHN", commodity="steel", cost_per_ton=25.0)
    tc2 = TransportationCost(from_iso3="USA", to_iso3="CHN", commodity="Steel", cost_per_ton=30.0)
    # Equal because from_iso3, to_iso3, and commodity match (cost doesn't matter for equality)
    assert tc1 == tc2


def test_transportation_cost_inequality():
    """Test inequality of TransportationCost objects."""
    tc1 = TransportationCost(from_iso3="USA", to_iso3="CHN", commodity="steel", cost_per_ton=25.0)
    tc2 = TransportationCost(from_iso3="USA", to_iso3="DEU", commodity="steel", cost_per_ton=25.0)
    assert tc1 != tc2
    assert tc1 != "not a TransportationCost"


def test_transportation_cost_hash():
    """Test that TransportationCost can be used in sets/dicts."""
    tc1 = TransportationCost(from_iso3="USA", to_iso3="CHN", commodity="steel", cost_per_ton=25.0)
    tc2 = TransportationCost(from_iso3="USA", to_iso3="CHN", commodity="steel", cost_per_ton=30.0)
    tc3 = TransportationCost(from_iso3="DEU", to_iso3="FRA", commodity="iron", cost_per_ton=15.0)

    tc_set = {tc1, tc2, tc3}
    # tc1 and tc2 are equal, so set should have 2 elements
    assert len(tc_set) == 2


def test_transportation_cost_repr():
    """Test string representation of TransportationCost."""
    tc = TransportationCost(from_iso3="USA", to_iso3="CHN", commodity="steel", cost_per_ton=25.0)
    repr_str = repr(tc)
    assert "USA" in repr_str
    assert "CHN" in repr_str
    assert "steel" in repr_str
    assert "25.0" in repr_str


##############################################
# Transportation Cost Methods in TradeLPModel
##############################################


def test_add_transportation_costs(location_mock_factory):
    """Test adding transportation costs to the model."""
    model = TradeLPModel()

    tc1 = TransportationCost(from_iso3="USA", to_iso3="CHN", commodity="steel", cost_per_ton=25.0)
    tc2 = TransportationCost(from_iso3="DEU", to_iso3="FRA", commodity="iron", cost_per_ton=15.0)

    model.add_transportation_costs([tc1, tc2])

    assert len(model.transportation_costs) == 2
    assert tc1 in model.transportation_costs
    assert tc2 in model.transportation_costs


def test_get_transportation_cost(location_mock_factory):
    """Test retrieving transportation costs from the model."""
    model = TradeLPModel()

    tc1 = TransportationCost(from_iso3="USA", to_iso3="CHN", commodity="steel", cost_per_ton=25.0)
    tc2 = TransportationCost(from_iso3="DEU", to_iso3="FRA", commodity="iron", cost_per_ton=15.0)

    model.add_transportation_costs([tc1, tc2])

    # Test successful lookup
    cost = model.get_transportation_cost("USA", "CHN", "steel")
    assert cost == 25.0

    cost2 = model.get_transportation_cost("DEU", "FRA", "iron")
    assert cost2 == 15.0

    # Test failed lookup returns 0
    cost3 = model.get_transportation_cost("USA", "DEU", "copper")
    assert cost3 == 0.0


def test_get_distance_haversine(location_mock_factory):
    """Test get_distance using haversine calculation."""
    model = TradeLPModel()

    loc1 = location_mock_factory(lat=10.0, lon=20.0, iso3="USA")
    loc2 = location_mock_factory(lat=10.5, lon=20.5, iso3="CHN")

    pc1 = ProcessCenter(name="PC1", process=None, capacity=100.0, location=loc1)
    pc2 = ProcessCenter(name="PC2", process=None, capacity=100.0, location=loc2)

    model.add_process_centers([pc1, pc2])

    # Default is haversine
    distance = model.get_distance("PC1", "PC2")
    assert distance > 0
    # Should be approximately 78 km for these coordinates
    assert 70 < distance < 90


##############################################
# Process Center Type Parameter Test
##############################################


def test_add_process_center_type_as_parameter():
    """Test adding process center type as parameter to LP model."""
    model = TradeLPModel()

    supply_proc = Process(name="Supply", type=ProcessType.SUPPLY, bill_of_materials=[])
    demand_proc = Process(name="Demand", type=ProcessType.DEMAND, bill_of_materials=[])
    prod_proc = Process(name="Production", type=ProcessType.PRODUCTION, bill_of_materials=[])

    pc_supply = ProcessCenter(name="PC_Supply", process=supply_proc, capacity=100, location=None)
    pc_demand = ProcessCenter(name="PC_Demand", process=demand_proc, capacity=100, location=None)
    pc_prod = ProcessCenter(name="PC_Prod", process=prod_proc, capacity=100, location=None)

    model.add_process_centers([pc_supply, pc_demand, pc_prod])

    model.lp_model = pyo.ConcreteModel()

    model.add_process_center_type_as_parameter_to_lp()

    assert model.lp_model.process_center_type["PC_Supply"] == "supply"
    assert model.lp_model.process_center_type["PC_Demand"] == "demand"
    assert model.lp_model.process_center_type["PC_Prod"] == "production"


##############################################
# Additional Coverage Tests
##############################################


def test_allocations_get_allocation_cost_with_costs():
    """Test get_allocation_cost when allocation_costs are defined."""
    pc1 = ProcessCenter(name="PC1", process=None, capacity=10, location=None)
    pc2 = ProcessCenter(name="PC2", process=None, capacity=20, location=None)
    c_iron = Commodity("iron")

    allocations_dict = {(pc1, pc2, c_iron): 5.0}
    allocation_costs = {(pc1, pc2, c_iron): 100.0}

    alloc = Allocations(allocations=allocations_dict, allocation_costs=allocation_costs)
    cost = alloc.get_allocation_cost(pc1, pc2, c_iron)
    assert cost == 100.0


def test_allocations_get_allocation_cost_without_costs():
    """Test get_allocation_cost returns 0.0 when allocation_costs is None."""
    pc1 = ProcessCenter(name="PC1", process=None, capacity=10, location=None)
    pc2 = ProcessCenter(name="PC2", process=None, capacity=20, location=None)
    c_iron = Commodity("iron")

    allocations_dict = {(pc1, pc2, c_iron): 5.0}

    alloc = Allocations(allocations=allocations_dict)
    cost = alloc.get_allocation_cost(pc1, pc2, c_iron)
    assert cost == 0.0


def test_allocations_validate_with_none_processcenter():
    """Test validate_allocations skips None processcenters."""
    pc2 = ProcessCenter(name="PC2", process=None, capacity=20, location=None)
    c_iron = Commodity("iron")

    allocations_dict = {(None, pc2, c_iron): 5.0}
    alloc = Allocations(allocations=allocations_dict)
    alloc.validate_allocations()  # Should not raise


def test_allocations_validate_with_none_capacity():
    """Test validate_allocations skips processcenters with None capacity."""
    pc1 = ProcessCenter(name="PC1", process=None, capacity=None, location=None)
    pc2 = ProcessCenter(name="PC2", process=None, capacity=20, location=None)
    c_iron = Commodity("iron")

    allocations_dict = {(pc1, pc2, c_iron): 5.0}
    alloc = Allocations(allocations=allocations_dict)
    alloc.validate_allocations()  # Should not raise


def test_tradelp_add_tariff_information():
    """Test adding tariff information to TradeLPModel."""
    model = TradeLPModel()

    quota_dict = {("USA", "CHN", "steel"): 1000.0}
    tax_dict = {("USA", "CHN", "steel"): 0.25}

    model.add_tariff_information(quota_dict, tax_dict)

    assert model.tariff_quotas_by_iso3 == quota_dict
    assert model.tariff_taxes_by_iso3 == tax_dict


def test_tradelp_add_processes_adds_implicit_commodities():
    """Test that add_processes adds products as commodities if not already present."""
    model = TradeLPModel()
    c_output = Commodity("steel")

    bom_elem = BOMElement(name="BOM1", commodity=Commodity("iron_ore"), output_commodities=[c_output], parameters={})

    proc = Process(name="BF", type=ProcessType.PRODUCTION, bill_of_materials=[bom_elem])

    # Initially no commodities
    assert len(model.commodities) == 0

    # Add process
    model.add_processes([proc])

    # Now steel should be implicitly added
    assert c_output in model.commodities


def test_tradelp_add_bom_elements_adds_implicit_commodities():
    """Test that add_bom_elements adds commodities if not already present."""
    model = TradeLPModel()
    c_input = Commodity("iron_ore")

    bom_elem = BOMElement(name="BOM1", commodity=c_input, output_commodities=[Commodity("steel")], parameters={})

    # Initially no commodities
    assert len(model.commodities) == 0

    # Add BOM element
    model.add_bom_elements([bom_elem])

    # Now iron_ore should be implicitly added
    assert c_input in model.commodities


def test_get_distance_pref_economic(location_mock_factory):
    """Test get_distance with pref_economic type."""
    model = TradeLPModel()

    loc1 = location_mock_factory(lat=10.0, lon=20.0, iso3="USA")
    loc2 = location_mock_factory(lat=10.5, lon=20.5, iso3="CHN")

    pc1 = ProcessCenter(name="PC1", process=None, capacity=100.0, location=loc1)
    pc2 = ProcessCenter(name="PC2", process=None, capacity=100.0, location=loc2)

    model.add_process_centers([pc1, pc2])

    # Use pref_economic type
    distance = model.get_distance("PC1", "PC2", type="pref_economic")
    assert distance > 0


def test_get_distance_invalid_type_raises_error(location_mock_factory):
    """Test that get_distance raises ValueError for unknown distance type."""
    model = TradeLPModel()

    loc1 = location_mock_factory(lat=10.0, lon=20.0, iso3="USA")
    loc2 = location_mock_factory(lat=10.5, lon=20.5, iso3="CHN")

    pc1 = ProcessCenter(name="PC1", process=None, capacity=100.0, location=loc1)
    pc2 = ProcessCenter(name="PC2", process=None, capacity=100.0, location=loc2)

    model.add_process_centers([pc1, pc2])

    with pytest.raises(ValueError, match="Unknown distance type"):
        model.get_distance("PC1", "PC2", type="unknown_type")
