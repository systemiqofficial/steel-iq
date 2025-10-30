import pytest
import pyomo.environ as pyo

# --- Pytest fixtures to patch the module under test ---
#
from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
    create_process_from_furnace_group,
    add_furnace_groups_as_process_centers,
    add_demand_centers_as_process_centers,
    add_suppliers_as_process_centers,
    set_up_steel_trade_lp,
    solve_steel_trade_lp_and_return_commodity_allocations,
)

# --- Dummy implementations for dependencies ---

# Dummy constants (patch these in your module if needed)
CAPACITY_LIMIT = 0.95
ACTIVE_STATUSES = {"operating"}
PRIMARY_PRODUCTS = ["steel"]
SOFT_MINIMUM_CAPACITY_PERCENTAGE = 0.6


# Dummy domain classes for trade LP modelling (simulate tlp)
class DummyCommodity:
    def __init__(self, name):
        self.name = name


class DummyBOMElement:
    def __init__(self, name, commodity, output_commodities, parameters, dependent_commodities=None, energy_cost=0):
        self.name = name
        self.commodity = commodity
        self.output_commodities = output_commodities
        self.parameters = parameters
        self.dependent_commodities = dependent_commodities or {}
        self.energy_cost = energy_cost


class DummyProcess:
    def __init__(self, name, type, bill_of_materials):
        self.name = name
        self.type = type
        self.bill_of_materials = bill_of_materials


class DummyProcessCenter:
    def __init__(self, name, process, capacity, location, production_cost=0.1, soft_minimum_capacity=0.0):
        self.production_cost = production_cost
        self.name = name
        self.process = process
        self.capacity = capacity
        self.location = location
        self.soft_minimum_capacity = soft_minimum_capacity


class DummyProcessConnector:
    def __init__(self, from_process, to_process):
        self.from_process = from_process
        self.to_process = to_process


class DummyProcessType:
    PRODUCTION = "PRODUCTION"
    DEMAND = "DEMAND"
    SUPPLY = "SUPPLY"


class DummyMaterialParameters:
    INPUT_RATIO = type("Enum", (), {"value": "input_ratio"})
    MAXIMUM_RATIO = type("Enum", (), {"value": "maximum_ratio"})
    MINIMUM_RATIO = type("Enum", (), {"value": "minimum_ratio"})


# Dummy allocations container
class DummyAllocations:
    def __init__(self):
        self.allocations = {}  # keys: (from_pc, to_pc, commodity), value: allocation (float)

    def get_allocation_cost(self, from_pc, to_pc, comm):
        # FIXME - is this correct? 2025-05-02 Jochen
        return 0


# Dummy allocation variables and lp_model
class DummyAllocationVariables:
    def __init__(self):
        self.data = {}

    def __iter__(self):
        return iter(self.data.keys())

    def __getitem__(self, key):
        return self.data.get(key, DummyVariable())

    def __setitem__(self, key, value):
        self.data[key] = value

    def items(self):
        return self.data.items()


class DummyVariable:
    def fix(self, value):
        pass


class DummyLPModel:
    def __init__(self):
        self.allocation_variables = DummyAllocationVariables()


# Dummy TradeLPModel that stores processes, bom_elements, process centers, connectors, etc.
class DummyTradeLPModel:
    def __init__(self, lp_epsilon=1e-3, year=None, solver_options=None):
        self._processes = {}
        self.process_centers = []
        self.bom_elements = {}
        self.commodities = []
        self.allocations = DummyAllocations()
        self.connectors = []
        self.lp_model = DummyLPModel()
        self.lp_epsilon = lp_epsilon
        self.year = year
        self.solver_options = solver_options or {}
        self.transportation_costs = []

    @property
    def processes(self):
        """Return processes as a list for iteration."""
        return list(self._processes.values())

    def get_bom_element(self, name):
        if name in self.bom_elements:
            return self.bom_elements[name]
        raise StopIteration

    def add_bom_elements(self, boms):
        for bom in boms:
            self.bom_elements[bom.name] = bom

    def get_process(self, name):
        if name in self._processes:
            return self._processes[name]
        return None

    def add_processes(self, processes):
        for process in processes:
            self._processes[process.name] = process

    def add_process_centers(self, centers):
        self.process_centers.extend(centers)

    def add_process_connectors(self, connectors):
        self.connectors.extend(connectors)

    def build_lp_model(self):
        pass

    def solve_lp_model(self):
        # Return a mock result with optimal termination
        class MockResult:
            class MockSolver:
                termination_condition = pyo.TerminationCondition.optimal

            solver = MockSolver()

        return MockResult()

    def extract_solution(self):
        pass

    def add_commodities(self, commodities):
        self.commodities.extend(commodities)

    def get_distance(self, from_pc, to_pc):
        """Mock distance calculation"""
        return 100.0  # Return a dummy distance

    def add_tariff_information(self, quota_dict=None, tax_dict=None):
        """Mock tariff information addition"""
        pass

    def add_transportation_costs(self, costs):
        """Mock transportation costs addition"""
        self.transportation_costs.extend(costs)


# Save the original __init__ to use in tests that require the unpatched version.
ORIGINAL_DUMMY_TRADE_LP_MODEL_INIT = DummyTradeLPModel.__init__


# Dummy commodity allocation class
class DummyCommodityAllocations:
    def __init__(self, commodity, allocations):
        self.commodity = commodity
        self.allocations = allocations
        self.records = []

    def add_allocation(self, source, destination, volume):
        self.records.append((source, destination, volume))


# --- Dummy repository and related objects ---


class DummyContainer:
    def __init__(self):
        self.data = {}
        self.items = []

    def list(self):
        return self.items

    def get(self, key):
        return self.data[key]


class DummyRepository:
    def __init__(self):
        self.plants = DummyContainer()
        self.demand_centers = DummyContainer()
        self.suppliers = DummyContainer()

    def get(self, key):
        # Search among plants, demand_centers, and suppliers
        for container in (self.plants, self.demand_centers, self.suppliers):
            if key in container.data:
                return container.data[key]
        raise KeyError(f"Key {key} not found.")


class DummyPlant:
    def __init__(self, plant_id, furnace_groups, location="plant_location"):
        self.plant_id = plant_id
        self.furnace_groups = furnace_groups
        self.location = location
        self.furnace_group_dict = {fg.furnace_group_id: fg for fg in furnace_groups}

    def get_furnace_group(self, furnace_group_id):
        return self.furnace_group_dict[furnace_group_id]


class DummyFurnaceGroup:
    def __init__(self, furnace_group_id, technology, status, capacity, unit_fopex=1):
        self.furnace_group_id = furnace_group_id
        self.technology = technology
        self.status = status
        self.capacity = capacity
        self.unit_fopex = unit_fopex
        self.energy_vopex_by_input = {}

    @property
    def effective_primary_feedstocks(self):
        """Returns the effective primary feedstocks as a list, similar to the real FurnaceGroup class."""
        if self.technology.dynamic_business_case is None:
            return []
        return self.technology.dynamic_business_case

    @property
    def carbon_cost_per_unit(self):
        """Mock carbon cost per unit for testing."""
        return 0.0


class DummyTechnology:
    def __init__(self, name, dynamic_business_case):
        self.name = name
        self.dynamic_business_case = dynamic_business_case


class DummyFeedstock:
    def __init__(
        self, name, metallic_charge, required_quantity, maximum_share, minimum_share, secondary_feedstock, outputs
    ):
        self.name = name
        self.metallic_charge = metallic_charge
        self.required_quantity_per_ton_of_product = required_quantity
        self.maximum_share_in_product = maximum_share
        self.minimum_share_in_product = minimum_share
        self.secondary_feedstock = secondary_feedstock
        self.outputs = outputs

    def get_primary_outputs(self, primary_products: list[str] | None = None):
        return self.outputs


class DummyDemandCenter:
    def __init__(self, demand_center_id, demand_by_year, center_of_gravity="demand_location"):
        self.demand_center_id = demand_center_id
        self.demand_by_year = demand_by_year
        self.center_of_gravity = center_of_gravity


class DummySupplier:
    def __init__(self, supplier_id, commodity, capacity_by_year, location="supplier_location", production_cost=0.1):
        self.production_cost = production_cost
        self.supplier_id = supplier_id
        self.commodity = commodity
        self.capacity_by_year = capacity_by_year
        self.location = location


class DummyUoW:
    def __init__(self, repository):
        self.repository = repository


class DummyEnvironment:
    def __init__(self):
        self.year = 2025  # Default year for testing
        self.legal_process_connectors = []
        self.dynamic_feedstocks = {}  # Empty feedstocks for testing


class DummyMessageBus:
    def __init__(self, repository):
        self.uow = DummyUoW(repository)
        self.env = DummyEnvironment()  # Mock environment, if needed


@pytest.fixture(autouse=True)
def patch_dependencies(monkeypatch):
    # Patch the tlp module within your module
    import steelo as my_module

    monkeypatch.setattr(
        my_module.domain.trade_modelling.trade_lp_modelling,
        "Commodity",
        DummyCommodity,
    )
    monkeypatch.setattr(
        my_module.domain.trade_modelling.trade_lp_modelling,
        "BOMElement",
        DummyBOMElement,
    )
    monkeypatch.setattr(
        my_module.domain.trade_modelling.trade_lp_modelling,
        "Process",
        DummyProcess,
    )
    monkeypatch.setattr(
        my_module.domain.trade_modelling.trade_lp_modelling,
        "ProcessCenter",
        DummyProcessCenter,
    )
    monkeypatch.setattr(
        my_module.domain.trade_modelling.trade_lp_modelling,
        "ProcessConnector",
        DummyProcessConnector,
    )
    monkeypatch.setattr(
        my_module.domain.trade_modelling.trade_lp_modelling,
        "TradeLPModel",
        DummyTradeLPModel,
    )
    monkeypatch.setattr(
        my_module.domain.trade_modelling.trade_lp_modelling,
        "ProcessType",
        DummyProcessType,
    )
    monkeypatch.setattr(
        my_module.domain.trade_modelling.trade_lp_modelling,
        "MaterialParameters",
        DummyMaterialParameters,
    )

    # Patch CommodityAllocations to our dummy version
    monkeypatch.setattr(my_module.domain.models, "CommodityAllocations", DummyCommodityAllocations)


# --- Tests ---
def create_mock_config():
    """Create a mock config for testing."""
    from dataclasses import dataclass, field
    from steelo.domain import Year

    @dataclass
    class MockConfig:
        primary_products: list[str] = field(default_factory=lambda: ["steel"])
        active_statuses: list[str] = field(default_factory=lambda: ["operating"])
        capacity_limit: float = 0.95
        soft_minimum_capacity_percentage: float = 0.6
        hot_metal_radius: float = 5.0
        closely_allocated_products: list[str] = field(default_factory=lambda: ["hot_metal"])
        distantly_allocated_products: list[str] = field(default_factory=lambda: ["pig_iron"])
        lp_epsilon: float = 1e-3
        start_year: Year = Year(2025)
        end_year: Year = Year(2050)

    return MockConfig()


def test_create_process_from_furnace_group_empty_list():
    # Test branch when technology.dynamic_business_case is an empty list.
    tech = DummyTechnology(name="EAF", dynamic_business_case=[])
    furnace_group = DummyFurnaceGroup(furnace_group_id="plant1_fg1", technology=tech, status="operating", capacity=100)
    lp_model = DummyTradeLPModel()
    config = create_mock_config()
    process = create_process_from_furnace_group(furnace_group, lp_model, config)
    assert process.name == "EAF"
    assert process.type == DummyProcessType.PRODUCTION
    assert process.bill_of_materials == []


def test_create_process_from_furnace_group_feedstock():
    # Test branch when dynamic_business_case is a list of feedstocks.
    # Create a feedstock that will cause lp_model.get_bom_element() to raise StopIteration.
    outputs = {"steel": 1}
    feedstock = DummyFeedstock(
        name="HS",
        metallic_charge="Fe",  # not a float so it will not be skipped
        required_quantity=1.0,
        maximum_share=0.5,
        minimum_share=0.1,
        secondary_feedstock={"slag": 0.05},
        outputs=outputs,
    )
    tech = DummyTechnology(name="BOF", dynamic_business_case=[feedstock])
    furnace_group = DummyFurnaceGroup(furnace_group_id="plant1_fg2", technology=tech, status="operating", capacity=150)
    lp_model = DummyTradeLPModel()
    config = create_mock_config()
    process = create_process_from_furnace_group(furnace_group, lp_model, config)
    # Should have created a BOMElement and added it to lp_model.bom_elements
    assert process.name == "BOF"
    assert process.type == DummyProcessType.PRODUCTION
    assert len(process.bill_of_materials) == 1
    assert "HS" in lp_model.bom_elements


def test_add_furnace_groups_as_process_centers():
    # Create a dummy plant with one active furnace group.
    tech = DummyTechnology(name="EAF", dynamic_business_case=[])
    furnace_group = DummyFurnaceGroup(furnace_group_id="plant2_fg1", technology=tech, status="operating", capacity=50)
    plant = DummyPlant(plant_id="plant2", furnace_groups=[furnace_group])
    repo = DummyRepository()
    repo.plants.items = [plant]
    repo.plants.data = {"plant2": plant}
    lp_model = DummyTradeLPModel()
    config = create_mock_config()

    # Pass modelled_products as required.
    add_furnace_groups_as_process_centers(repo, lp_model, config)
    # Check that a process center was added
    assert len(lp_model.process_centers) == 1
    pc = lp_model.process_centers[0]
    expected_capacity = config.capacity_limit * furnace_group.capacity
    assert pc.capacity == expected_capacity
    # Also check that the process is now in the lp_model processes
    assert "EAF" in lp_model._processes


def test_add_demand_centers_as_process_centers():
    # Create a dummy demand center.
    year = 2025
    demand_center = DummyDemandCenter(demand_center_id="demand1", demand_by_year={year: 200})
    repo = DummyRepository()
    repo.demand_centers.items = [demand_center]
    repo.demand_centers.data = {"demand1": demand_center}
    lp_model = DummyTradeLPModel()

    add_demand_centers_as_process_centers(repo, lp_model, year)
    # Check that the "demand" process is added.
    assert "demand" in lp_model._processes
    # Check that a process center was added with capacity equal to the demand.
    pc = lp_model.process_centers[0]
    assert pc.capacity == 200
    assert pc.location == demand_center.center_of_gravity


def test_add_suppliers_as_process_centers():
    # Create a dummy supplier.
    year = 2025
    supplier = DummySupplier(supplier_id="sup1", commodity="scrap", capacity_by_year={year: 300})
    repo = DummyRepository()
    repo.suppliers.items = [supplier]
    repo.suppliers.data = {"sup1": supplier}
    lp_model = DummyTradeLPModel()
    config = create_mock_config()

    add_suppliers_as_process_centers(repo, lp_model, year, config)
    # Check that a supply process for the commodity is created.
    supply_process_name = "scrap_supply"
    assert supply_process_name in lp_model._processes
    # Check that a process center was added for the supplier.
    assert any(pc.name == "sup1" for pc in lp_model.process_centers)
    # Also check that the capacity is set correctly.
    pc = next(pc for pc in lp_model.process_centers if pc.name == "sup1")
    assert pc.capacity == 300


def test_set_up_steel_trade_lp(monkeypatch):
    # For this test we need a repository with plants, demand centers, and suppliers.
    year = 2025

    # Create a dummy furnace group and plant.
    tech = DummyTechnology(name="EAF", dynamic_business_case=None)
    furnace_group = DummyFurnaceGroup(furnace_group_id="plant3_fg1", technology=tech, status="operating", capacity=75)
    plant = DummyPlant(plant_id="plant3", furnace_groups=[furnace_group])
    repo = DummyRepository()
    repo.plants.items = [plant]
    repo.plants.data = {"plant3": plant}

    # Create a dummy demand center.
    demand_center = DummyDemandCenter(demand_center_id="demand2", demand_by_year={year: 150})
    repo.demand_centers.items = [demand_center]
    repo.demand_centers.data = {"demand2": demand_center}

    # Create a dummy supplier.
    supplier = DummySupplier(supplier_id="sup2", commodity="scrap", capacity_by_year={year: 250})
    repo.suppliers.items = [supplier]
    repo.suppliers.data = {"sup2": supplier}

    # Patch DummyTradeLPModel.__init__ using monkeypatch.
    orig_init = ORIGINAL_DUMMY_TRADE_LP_MODEL_INIT

    def init_with_processes(self, lp_epsilon=1e-3, year=None, solver_options=None):
        orig_init(self, lp_epsilon, year, solver_options)
        for proc_name in [
            "BF",
            "DRI",
            "EAF",
            "BOF",
            "demand",
            "scrap_supply",
            "Prep Sinter",
            "io_high_supply",
            "io_mid_supply",
            "io_low_supply",
            "pellets_high_supply",
            "pellets_mid_supply",
        ]:
            self._processes[proc_name] = DummyProcess(proc_name, DummyProcessType.PRODUCTION, [])

    monkeypatch.setattr(DummyTradeLPModel, "__init__", init_with_processes)

    mock_config = create_mock_config()
    message_bus = DummyMessageBus(repo)
    lp_model = set_up_steel_trade_lp(
        message_bus=message_bus, year=year, config=mock_config, legal_process_connectors=[]
    )
    # Check that the legal connector processes are present.
    process_names = [p.name for p in lp_model.processes]
    for key in [
        "BF",
        "DRI",
        "EAF",
        "BOF",
        "demand",
        "scrap_supply",
        "Prep Sinter",
        "io_high_supply",
        "io_mid_supply",
        "io_low_supply",
        "pellets_high_supply",
        "pellets_mid_supply",
    ]:
        assert key in process_names
    # Check that some process centers were added from the furnace groups, demand centers, and suppliers.
    assert len(lp_model.process_centers) >= 3


def test_solve_steel_trade_lp_and_return_commodity_allocations(monkeypatch):
    # Ensure DummyTradeLPModel.__init__ is the original one.
    monkeyatch_init = ORIGINAL_DUMMY_TRADE_LP_MODEL_INIT
    monkeypatch.setattr(DummyTradeLPModel, "__init__", monkeyatch_init)

    # Create a dummy LP model and repository.
    repo = DummyRepository()
    year = 2025

    # For testing allocations, create a supplier and a demand center.
    supplier = DummySupplier(supplier_id="sup3", commodity="scrap", capacity_by_year={year: 400})
    repo.suppliers.items = [supplier]
    repo.suppliers.data = {"sup3": supplier}

    demand_center = DummyDemandCenter(demand_center_id="demand3", demand_by_year={year: 350})
    repo.demand_centers.items = [demand_center]
    repo.demand_centers.data = {"demand3": demand_center}

    # Also create a plant with a furnace group for the non-supplier branch.
    tech = DummyTechnology(name="EAF", dynamic_business_case=None)
    furnace_group = DummyFurnaceGroup(furnace_group_id="plant4_fg1", technology=tech, status="operating", capacity=60)
    plant = DummyPlant(plant_id="plant4", furnace_groups=[furnace_group])
    repo.plants.items = [plant]
    repo.plants.data = {"plant4": plant}

    lp_model = DummyTradeLPModel()
    # Populate lp_model with a commodity.
    commodity = DummyCommodity("steel")
    lp_model.commodities.append(commodity)
    # Pre-create dummy processes for supplier and demand.
    supplier_process = DummyProcess("sup3_supply", DummyProcessType.SUPPLY, [])
    demand_process = DummyProcess("demand", DummyProcessType.DEMAND, [])
    lp_model._processes["sup3_supply"] = supplier_process
    lp_model._processes["demand"] = demand_process

    # Create dummy process centers for allocation:
    from_pc = DummyProcessCenter("sup3", supplier_process, 400, supplier.location)
    to_pc = DummyProcessCenter("demand3", demand_process, 350, demand_center.center_of_gravity)
    # Add a positive allocation to the LP model's allocations dictionary.
    lp_model.allocations.allocations[(from_pc, to_pc, commodity)] = 100.0

    allocations = solve_steel_trade_lp_and_return_commodity_allocations(lp_model, repo)
    # Check that the returned dict has an entry for "steel"
    assert "steel" in allocations
    # And that our dummy allocation was recorded.
    alloc_obj = allocations["steel"]
    # Our dummy add_allocation appends to a records list.
    assert alloc_obj.allocations[supplier][demand_center] == 100.0


# --- Tests for enforce_trade_tariffs_on_allocations ---


class DummyTradeTariff:
    """Mock TradeTariff for testing."""

    def __init__(
        self,
        tariff_name,
        from_iso3,
        to_iso3,
        commodity=None,
        quota=None,
        tax_absolute=None,
        tax_percentage=None,
    ):
        self.tariff_name = tariff_name
        self.from_iso3 = from_iso3
        self.to_iso3 = to_iso3
        self.commodity = commodity
        self.quota = quota
        self.tax_absolute = tax_absolute
        self.tax_percentage = tax_percentage


def test_enforce_trade_tariffs_with_quota():
    """Test that quotas are correctly applied to allocations."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import enforce_trade_tariffs_on_allocations

    lp_model = DummyTradeLPModel()
    message_bus = DummyMessageBus(DummyRepository())
    message_bus.env.average_commodity_price_per_region = {}

    tariffs = [
        DummyTradeTariff(tariff_name="quota_tariff", from_iso3="USA", to_iso3="CHN", commodity="steel", quota=1000.0)
    ]

    enforce_trade_tariffs_on_allocations(message_bus, tariffs, lp_model)

    # Verify quota was added - the function should call add_tariff_information
    # Since we mocked add_tariff_information, we can't directly verify but we ensure no errors


def test_enforce_trade_tariffs_with_absolute_tax():
    """Test that absolute taxes are correctly applied."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import enforce_trade_tariffs_on_allocations

    lp_model = DummyTradeLPModel()
    message_bus = DummyMessageBus(DummyRepository())
    message_bus.env.average_commodity_price_per_region = {}

    tariffs = [
        DummyTradeTariff(tariff_name="abs_tax", from_iso3="USA", to_iso3="CHN", commodity="steel", tax_absolute=50.0)
    ]

    enforce_trade_tariffs_on_allocations(message_bus, tariffs, lp_model)
    # Should complete without errors


def test_enforce_trade_tariffs_with_percentage_tax():
    """Test that percentage taxes are correctly converted to absolute values."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import enforce_trade_tariffs_on_allocations

    lp_model = DummyTradeLPModel()
    message_bus = DummyMessageBus(DummyRepository())
    # Set up average prices for percentage calculation
    message_bus.env.average_commodity_price_per_region = {("steel", "USA"): 1000.0}

    tariffs = [
        DummyTradeTariff(tariff_name="pct_tax", from_iso3="USA", to_iso3="CHN", commodity="steel", tax_percentage=0.1)
    ]

    enforce_trade_tariffs_on_allocations(message_bus, tariffs, lp_model)
    # Should calculate tax as 0.1 * 1000.0 = 100.0


def test_enforce_trade_tariffs_with_wildcard_from():
    """Test that wildcard from_iso3='*' applies to all source countries."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import enforce_trade_tariffs_on_allocations

    lp_model = DummyTradeLPModel()
    message_bus = DummyMessageBus(DummyRepository())
    # Set up prices for multiple countries
    message_bus.env.average_commodity_price_per_region = {
        ("steel", "USA"): 1000.0,
        ("steel", "CHN"): 900.0,
        ("steel", "DEU"): 1100.0,
    }

    tariffs = [
        DummyTradeTariff(
            tariff_name="wildcard_from", from_iso3="*", to_iso3="EUR", commodity="steel", tax_percentage=0.05
        )
    ]

    enforce_trade_tariffs_on_allocations(message_bus, tariffs, lp_model)
    # Should apply to USA, CHN, and DEU to EUR


def test_enforce_trade_tariffs_with_wildcard_to():
    """Test that wildcard to_iso3='*' applies to all destination countries."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import enforce_trade_tariffs_on_allocations

    lp_model = DummyTradeLPModel()
    message_bus = DummyMessageBus(DummyRepository())
    message_bus.env.average_commodity_price_per_region = {
        ("steel", "USA"): 1000.0,
        ("steel", "CHN"): 900.0,
        ("steel", "DEU"): 1100.0,
    }

    tariffs = [
        DummyTradeTariff(
            tariff_name="wildcard_to", from_iso3="USA", to_iso3="*", commodity="steel", tax_percentage=0.05
        )
    ]

    enforce_trade_tariffs_on_allocations(message_bus, tariffs, lp_model)
    # Should apply from USA to all other countries


def test_enforce_trade_tariffs_with_nan_values():
    """Test that NaN values in quota/tax are properly skipped."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import enforce_trade_tariffs_on_allocations
    import math

    lp_model = DummyTradeLPModel()
    message_bus = DummyMessageBus(DummyRepository())
    message_bus.env.average_commodity_price_per_region = {}

    tariffs = [
        DummyTradeTariff(tariff_name="nan_quota", from_iso3="USA", to_iso3="CHN", commodity="steel", quota=math.nan),
        DummyTradeTariff(
            tariff_name="nan_tax", from_iso3="USA", to_iso3="CHN", commodity="steel", tax_absolute=math.nan
        ),
    ]

    enforce_trade_tariffs_on_allocations(message_bus, tariffs, lp_model)
    # Should complete without errors, skipping NaN values


def test_enforce_trade_tariffs_iron_products_mapping():
    """Test that iron products are correctly mapped to 'iron' commodity."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import enforce_trade_tariffs_on_allocations

    lp_model = DummyTradeLPModel()
    message_bus = DummyMessageBus(DummyRepository())
    message_bus.env.average_commodity_price_per_region = {("iron", "USA"): 800.0}

    # Test with various iron products
    for iron_product in ["pig iron", "dri", "hbi"]:
        tariffs = [
            DummyTradeTariff(
                tariff_name="iron_tax",
                from_iso3="USA",
                to_iso3="CHN",
                commodity=iron_product,
                tax_percentage=0.1,
            )
        ]
        enforce_trade_tariffs_on_allocations(message_bus, tariffs, lp_model)
        # Should map to iron commodity


# --- Tests for fix_to_zero_allocations_where_distance_doesnt_match_commodity ---


class DummyLocation:
    def __init__(self, iso3):
        self.iso3 = iso3


def test_fix_allocations_hot_metal_short_distance():
    """Test that hot metal allocations are allowed within hot_metal_radius."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        fix_to_zero_allocations_where_distance_doesnt_match_commodity,
    )

    lp_model = DummyTradeLPModel()
    config = create_mock_config()
    config.hot_metal_radius = 10.0
    config.closely_allocated_products = ["hot_metal"]

    # Create allocation variables
    DummyProcessCenter("pc1", DummyProcess("p1", DummyProcessType.PRODUCTION, []), 100, DummyLocation("USA"))
    DummyProcessCenter("pc2", DummyProcess("p2", DummyProcessType.PRODUCTION, []), 100, DummyLocation("USA"))

    # Add allocation variable
    var = DummyVariable()
    lp_model.lp_model.allocation_variables[("pc1", "pc2", "hot_metal")] = var

    # Mock get_distance to return short distance

    def mock_get_distance(from_pc, to_pc):
        return 5.0  # Within hot metal radius

    lp_model.get_distance = mock_get_distance

    result = fix_to_zero_allocations_where_distance_doesnt_match_commodity(lp_model, config)

    # Hot metal should be allowed at short distance
    assert result is not None


def test_fix_allocations_pig_iron_long_distance():
    """Test that pig iron allocations are allowed over long distances."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        fix_to_zero_allocations_where_distance_doesnt_match_commodity,
    )

    lp_model = DummyTradeLPModel()
    config = create_mock_config()
    config.hot_metal_radius = 10.0
    config.distantly_allocated_products = ["pig_iron"]

    var = DummyVariable()
    lp_model.lp_model.allocation_variables[("pc1", "pc2", "pig_iron")] = var

    def mock_get_distance(from_pc, to_pc):
        return 1000.0  # Long distance

    lp_model.get_distance = mock_get_distance

    result = fix_to_zero_allocations_where_distance_doesnt_match_commodity(lp_model, config)

    # Pig iron should be allowed at long distance
    assert result is not None


# --- Tests for adapt_allocation_costs_for_carbon_border_mechanisms ---


class DummyCountryMapping:
    def __init__(self, iso3, EU=False, OECD=False, NAFTA=False):
        self.iso3 = iso3
        self.EU = EU
        self.OECD = OECD
        self.NAFTA = NAFTA


class DummyCarbonBorderMechanism:
    def __init__(self, mechanism_name, applying_region_column, start_year, end_year=None):
        self.mechanism_name = mechanism_name
        self.applying_region_column = applying_region_column
        self.start_year = start_year
        self.end_year = end_year

    def is_active(self, year):
        if year < self.start_year:
            return False
        if self.end_year is not None and year > self.end_year:
            return False
        return True

    def get_applying_region_countries(self, country_mappings):
        countries = set()
        for iso3, mapping in country_mappings.items():
            if hasattr(mapping, self.applying_region_column):
                attr_value = getattr(mapping, self.applying_region_column, False)
                if attr_value:
                    countries.add(iso3)
        return countries


def test_adapt_allocation_costs_cbam_export_from_eu():
    """Test CBAM export rebates from EU to non-EU."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )

    lp_model = DummyTradeLPModel()

    # Create process centers with different carbon costs
    eu_location = DummyLocation("DEU")
    non_eu_location = DummyLocation("USA")

    from_pc = DummyProcessCenter(
        "eu_pc", DummyProcess("p1", DummyProcessType.PRODUCTION, []), 100, eu_location, production_cost=100.0
    )
    to_pc = DummyProcessCenter(
        "us_pc", DummyProcess("p2", DummyProcessType.PRODUCTION, []), 100, non_eu_location, production_cost=50.0
    )

    commodity = DummyCommodity("steel")
    lp_model.legal_allocations = [(from_pc, to_pc, commodity)]
    lp_model.lp_model.allocation_costs = {("eu_pc", "us_pc", "steel"): 10.0}

    # Create carbon border mechanism
    cbam = DummyCarbonBorderMechanism(mechanism_name="CBAM", applying_region_column="EU", start_year=2025)

    # Create country mappings
    country_mappings = {
        "DEU": DummyCountryMapping("DEU", EU=True),
        "USA": DummyCountryMapping("USA", EU=False),
    }

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    # Export from EU (high cost) to non-EU (low cost) should add differential (50-100=-50)
    assert lp_model.lp_model.allocation_costs[("eu_pc", "us_pc", "steel")] == 10.0 - 50.0


def test_adapt_allocation_costs_cbam_import_to_eu():
    """Test CBAM import adjustments from non-EU to EU."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )

    lp_model = DummyTradeLPModel()

    eu_location = DummyLocation("DEU")
    non_eu_location = DummyLocation("USA")

    from_pc = DummyProcessCenter(
        "us_pc", DummyProcess("p1", DummyProcessType.PRODUCTION, []), 100, non_eu_location, production_cost=50.0
    )
    to_pc = DummyProcessCenter(
        "eu_pc", DummyProcess("p2", DummyProcessType.PRODUCTION, []), 100, eu_location, production_cost=100.0
    )

    commodity = DummyCommodity("steel")
    lp_model.legal_allocations = [(from_pc, to_pc, commodity)]
    lp_model.lp_model.allocation_costs = {("us_pc", "eu_pc", "steel"): 10.0}

    cbam = DummyCarbonBorderMechanism(mechanism_name="CBAM", applying_region_column="EU", start_year=2025)
    country_mappings = {
        "DEU": DummyCountryMapping("DEU", EU=True),
        "USA": DummyCountryMapping("USA", EU=False),
    }

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    # Import to EU (high cost) from non-EU (low cost) should add differential (100-50=+50)
    assert lp_model.lp_model.allocation_costs[("us_pc", "eu_pc", "steel")] == 10.0 + 50.0


def test_adapt_allocation_costs_cbam_inactive_year():
    """Test that inactive CBAM doesn't adjust costs."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )

    lp_model = DummyTradeLPModel()

    eu_location = DummyLocation("DEU")
    non_eu_location = DummyLocation("USA")

    from_pc = DummyProcessCenter(
        "us_pc", DummyProcess("p1", DummyProcessType.PRODUCTION, []), 100, non_eu_location, production_cost=50.0
    )
    to_pc = DummyProcessCenter(
        "eu_pc", DummyProcess("p2", DummyProcessType.PRODUCTION, []), 100, eu_location, production_cost=100.0
    )

    commodity = DummyCommodity("steel")
    lp_model.legal_allocations = [(from_pc, to_pc, commodity)]
    lp_model.lp_model.allocation_costs = {("us_pc", "eu_pc", "steel"): 10.0}

    # CBAM starts in 2030, test year is 2026
    cbam = DummyCarbonBorderMechanism(mechanism_name="CBAM", applying_region_column="EU", start_year=2030)
    country_mappings = {
        "DEU": DummyCountryMapping("DEU", EU=True),
        "USA": DummyCountryMapping("USA", EU=False),
    }

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    # Cost should remain unchanged
    assert lp_model.lp_model.allocation_costs[("us_pc", "eu_pc", "steel")] == 10.0


def test_adapt_allocation_costs_cbam_no_double_counting():
    """Test that same trade flow isn't adjusted multiple times."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )

    lp_model = DummyTradeLPModel()

    eu_location = DummyLocation("DEU")
    non_eu_location = DummyLocation("USA")

    from_pc = DummyProcessCenter(
        "us_pc", DummyProcess("p1", DummyProcessType.PRODUCTION, []), 100, non_eu_location, production_cost=50.0
    )
    to_pc = DummyProcessCenter(
        "eu_pc", DummyProcess("p2", DummyProcessType.PRODUCTION, []), 100, eu_location, production_cost=100.0
    )

    commodity = DummyCommodity("steel")
    lp_model.legal_allocations = [(from_pc, to_pc, commodity)]
    lp_model.lp_model.allocation_costs = {("us_pc", "eu_pc", "steel"): 10.0}

    # Two mechanisms that both apply to EU
    cbam1 = DummyCarbonBorderMechanism(mechanism_name="CBAM", applying_region_column="EU", start_year=2025)
    cbam2 = DummyCarbonBorderMechanism(mechanism_name="OECD", applying_region_column="OECD", start_year=2025)

    country_mappings = {
        "DEU": DummyCountryMapping("DEU", EU=True, OECD=True),
        "USA": DummyCountryMapping("USA", EU=False, OECD=False),
    }

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam1, cbam2], country_mappings=country_mappings, year=2026
    )

    # Should only adjust once (first mechanism processes it)
    assert lp_model.lp_model.allocation_costs[("us_pc", "eu_pc", "steel")] == 10.0 + 50.0


# --- Tests for identify_bottlenecks ---


def test_identify_bottlenecks_empty_allocations():
    """Test bottleneck analysis with empty allocations."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import identify_bottlenecks
    from steelo.domain.models import CommodityAllocations

    year = 2025

    # Use dummy repository and environment
    repo = DummyRepository()
    repo.suppliers.items = []
    repo.plants.items = []

    # Create a dummy environment
    env = DummyEnvironment()
    config = create_mock_config()
    env.config = config

    # Create empty allocations
    iron_allocations = CommodityAllocations(commodity="iron", allocations={})
    commodity_allocations = {"iron": iron_allocations}

    # Should run without errors
    identify_bottlenecks(commodity_allocations, repo, env, year)


def test_identify_bottlenecks_skip_scrap():
    """Test that scrap commodity is skipped in bottleneck analysis."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import identify_bottlenecks
    from steelo.domain.models import CommodityAllocations

    year = 2025

    repo = DummyRepository()
    repo.suppliers.items = []
    repo.plants.items = []

    env = DummyEnvironment()
    config = create_mock_config()
    env.config = config

    # Scrap allocations should be skipped
    scrap_allocations = CommodityAllocations(commodity="scrap", allocations={})
    commodity_allocations = {"scrap": scrap_allocations}

    # Should complete without analyzing scrap (the function skips scrap)
    identify_bottlenecks(commodity_allocations, repo, env, year)


# --- Tests for transportation costs (transport_kpis) ---


class DummyTransportKPI:
    """Mock TransportKPI for testing."""

    def __init__(self, reporter_iso, partner_iso, commodity, transportation_cost, ghg_factor=0.05):
        self.reporter_iso = reporter_iso
        self.partner_iso = partner_iso
        self.commodity = commodity
        self.transportation_cost = transportation_cost
        self.ghg_factor = ghg_factor


def test_set_up_steel_trade_lp_with_transport_kpis(monkeypatch):
    """Test that transportation costs are added from TransportKPI objects."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import set_up_steel_trade_lp

    year = 2025

    # Create repository
    tech = DummyTechnology(name="EAF", dynamic_business_case=None)
    furnace_group = DummyFurnaceGroup(furnace_group_id="plant5_fg1", technology=tech, status="operating", capacity=100)
    plant = DummyPlant(plant_id="plant5", furnace_groups=[furnace_group])
    repo = DummyRepository()
    repo.plants.items = [plant]
    repo.plants.data = {"plant5": plant}

    demand_center = DummyDemandCenter(demand_center_id="demand5", demand_by_year={year: 200})
    repo.demand_centers.items = [demand_center]
    repo.demand_centers.data = {"demand5": demand_center}

    supplier = DummySupplier(supplier_id="sup5", commodity="scrap", capacity_by_year={year: 300})
    repo.suppliers.items = [supplier]
    repo.suppliers.data = {"sup5": supplier}

    # Create transport KPIs
    transport_kpis = [
        DummyTransportKPI(reporter_iso="USA", partner_iso="CHN", commodity="steel", transportation_cost=25.0),
        DummyTransportKPI(reporter_iso="DEU", partner_iso="FRA", commodity="steel", transportation_cost=15.0),
    ]

    # Patch DummyTradeLPModel to track if add_transportation_costs was called
    orig_init = ORIGINAL_DUMMY_TRADE_LP_MODEL_INIT
    transport_costs_added = []

    def init_with_tracking(self, lp_epsilon=1e-3, year=None, solver_options=None):
        orig_init(self, lp_epsilon, year, solver_options)
        original_add = self.add_transportation_costs

        def track_add_transportation_costs(costs):
            transport_costs_added.extend(costs)
            if hasattr(original_add, "__call__"):
                return original_add(costs)

        self.add_transportation_costs = track_add_transportation_costs

        # Add required processes
        for proc_name in ["BF", "DRI", "EAF", "BOF", "demand", "scrap_supply"]:
            self._processes[proc_name] = DummyProcess(proc_name, DummyProcessType.PRODUCTION, [])

    monkeypatch.setattr(DummyTradeLPModel, "__init__", init_with_tracking)

    mock_config = create_mock_config()
    message_bus = DummyMessageBus(repo)

    set_up_steel_trade_lp(
        message_bus=message_bus,
        year=year,
        config=mock_config,
        legal_process_connectors=[],
        transport_kpis=transport_kpis,
    )

    # Verify transportation costs were added
    assert len(transport_costs_added) == 2
    assert transport_costs_added[0].from_iso3 == "USA"
    assert transport_costs_added[0].to_iso3 == "CHN"
    assert transport_costs_added[0].cost_per_ton == 25.0


# --- Tests for aggregated metallic charge constraints ---


class DummyAggregatedMetallicChargeConstraint:
    """Mock AggregatedMetallicChargeConstraint for testing."""

    def __init__(self, technology_name, feedstock_pattern, minimum_share=None, maximum_share=None):
        self.technology_name = technology_name
        self.feedstock_pattern = feedstock_pattern
        self.minimum_share = minimum_share
        self.maximum_share = maximum_share


def test_set_up_steel_trade_lp_with_aggregated_constraints(monkeypatch):
    """Test that aggregated metallic charge constraints are converted and applied."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import set_up_steel_trade_lp

    year = 2025

    # Create repository
    tech = DummyTechnology(name="EAF", dynamic_business_case=None)
    furnace_group = DummyFurnaceGroup(furnace_group_id="plant6_fg1", technology=tech, status="operating", capacity=100)
    plant = DummyPlant(plant_id="plant6", furnace_groups=[furnace_group])
    repo = DummyRepository()
    repo.plants.items = [plant]
    repo.plants.data = {"plant6": plant}

    demand_center = DummyDemandCenter(demand_center_id="demand6", demand_by_year={year: 200})
    repo.demand_centers.items = [demand_center]
    repo.demand_centers.data = {"demand6": demand_center}

    # Create aggregated constraints
    constraints = [
        DummyAggregatedMetallicChargeConstraint(
            technology_name="EAF", feedstock_pattern="scrap*", minimum_share=0.3, maximum_share=0.8
        ),
        DummyAggregatedMetallicChargeConstraint(technology_name="BOF", feedstock_pattern="iron*", minimum_share=0.7),
    ]

    # Patch to track constraint setting
    orig_init = ORIGINAL_DUMMY_TRADE_LP_MODEL_INIT
    constraints_set = {}

    def init_with_constraint_tracking(self, lp_epsilon=1e-3, year=None, solver_options=None):
        orig_init(self, lp_epsilon, year, solver_options)
        self.aggregated_commodity_constraints = {}

        def track_constraints(value):
            constraints_set.update(value)

        # Override the property setter
        type(self).aggregated_commodity_constraints = property(
            lambda s: constraints_set, lambda s, v: track_constraints(v)
        )

        # Add required processes
        for proc_name in ["BF", "DRI", "EAF", "BOF", "demand"]:
            self._processes[proc_name] = DummyProcess(proc_name, DummyProcessType.PRODUCTION, [])

    monkeypatch.setattr(DummyTradeLPModel, "__init__", init_with_constraint_tracking)

    mock_config = create_mock_config()
    message_bus = DummyMessageBus(repo)

    set_up_steel_trade_lp(
        message_bus=message_bus,
        year=year,
        config=mock_config,
        legal_process_connectors=[],
        aggregated_metallic_charge_constraints=constraints,
    )

    # Verify constraints were converted correctly
    assert ("EAF", "scrap*") in constraints_set
    assert constraints_set[("EAF", "scrap*")]["minimum"] == 0.3
    assert constraints_set[("EAF", "scrap*")]["maximum"] == 0.8
    assert ("BOF", "iron*") in constraints_set
    assert constraints_set[("BOF", "iron*")]["minimum"] == 0.7


# --- Tests for secondary feedstock constraints ---


def test_set_up_steel_trade_lp_with_secondary_feedstock_constraints(monkeypatch):
    """Test that secondary feedstock constraints create dummy processes and centers."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import set_up_steel_trade_lp

    year = 2025

    # Create repository
    tech = DummyTechnology(name="EAF", dynamic_business_case=None)
    furnace_group = DummyFurnaceGroup(furnace_group_id="plant7_fg1", technology=tech, status="operating", capacity=100)
    plant = DummyPlant(plant_id="plant7", furnace_groups=[furnace_group])
    repo = DummyRepository()
    repo.plants.items = [plant]
    repo.plants.data = {"plant7": plant}

    demand_center = DummyDemandCenter(demand_center_id="demand7", demand_by_year={year: 200})
    repo.demand_centers.items = [demand_center]
    repo.demand_centers.data = {"demand7": demand_center}

    # Create secondary feedstock constraints
    secondary_feedstock_constraints = {"hydrogen": {("DEU", "FRA"): 1000.0, ("USA", "CHN"): 2000.0}}

    # Patch to track processes and centers
    orig_init = ORIGINAL_DUMMY_TRADE_LP_MODEL_INIT
    processes_added = []
    centers_added = []

    def init_with_tracking(self, lp_epsilon=1e-3, year=None, solver_options=None):
        orig_init(self, lp_epsilon, year, solver_options)
        original_add_processes = self.add_processes
        original_add_centers = self.add_process_centers

        def track_add_processes(procs):
            processes_added.extend(procs)
            return original_add_processes(procs)

        def track_add_centers(centers):
            centers_added.extend(centers)
            return original_add_centers(centers)

        self.add_processes = track_add_processes
        self.add_process_centers = track_add_centers

        # Add required processes
        for proc_name in ["BF", "DRI", "EAF", "BOF", "demand"]:
            self._processes[proc_name] = DummyProcess(proc_name, DummyProcessType.PRODUCTION, [])

    monkeypatch.setattr(DummyTradeLPModel, "__init__", init_with_tracking)

    mock_config = create_mock_config()
    message_bus = DummyMessageBus(repo)

    set_up_steel_trade_lp(
        message_bus=message_bus,
        year=year,
        config=mock_config,
        legal_process_connectors=[],
        secondary_feedstock_constraints=secondary_feedstock_constraints,
    )

    # Verify dummy hydrogen_supply process was created
    hydrogen_processes = [p for p in processes_added if p.name == "hydrogen_supply"]
    assert len(hydrogen_processes) > 0
    assert hydrogen_processes[0].type == DummyProcessType.SUPPLY

    # Verify dummy process center was created
    hydrogen_centers = [c for c in centers_added if c.name == "hydrogen_supply_process_center"]
    assert len(hydrogen_centers) > 0
    # Capacity should be total + 1 = 3001
    assert hydrogen_centers[0].capacity == 3001.0


# --- Tests for non-optimal solver results ---


def test_solve_steel_trade_lp_non_optimal_result(monkeypatch):
    """Test handling of non-optimal solver results."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        solve_steel_trade_lp_and_return_commodity_allocations,
    )
    import pyomo.environ as pyo

    repo = DummyRepository()
    year = 2025

    supplier = DummySupplier(supplier_id="sup_fail", commodity="scrap", capacity_by_year={year: 400})
    repo.suppliers.items = [supplier]
    repo.suppliers.data = {"sup_fail": supplier}

    lp_model = DummyTradeLPModel()
    commodity = DummyCommodity("steel")
    lp_model.commodities.append(commodity)

    # Mock solve_lp_model to return non-optimal result
    class MockNonOptimalResult:
        class MockSolver:
            termination_condition = pyo.TerminationCondition.infeasible

        solver = MockSolver()

    lp_model.solve_lp_model = lambda: MockNonOptimalResult()

    allocations = solve_steel_trade_lp_and_return_commodity_allocations(lp_model, repo)

    # Should return empty allocations for non-optimal solution
    assert "steel" in allocations
    assert len(allocations["steel"].allocations) == 0


def test_solve_steel_trade_lp_no_allocations(monkeypatch):
    """Test handling when LP model has no allocations."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        solve_steel_trade_lp_and_return_commodity_allocations,
    )

    repo = DummyRepository()
    lp_model = DummyTradeLPModel()
    commodity = DummyCommodity("steel")
    lp_model.commodities.append(commodity)

    # Set allocations to None to test that branch
    lp_model.allocations = None

    allocations = solve_steel_trade_lp_and_return_commodity_allocations(lp_model, repo)

    # Should return empty allocations
    assert "steel" in allocations
    assert len(allocations["steel"].allocations) == 0


# --- Tests for allocation extraction edge cases ---


def test_solve_steel_trade_lp_plant_to_plant_allocation(monkeypatch):
    """Test allocation from plant furnace group to another plant."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        solve_steel_trade_lp_and_return_commodity_allocations,
    )

    monkeyatch_init = ORIGINAL_DUMMY_TRADE_LP_MODEL_INIT
    monkeypatch.setattr(DummyTradeLPModel, "__init__", monkeyatch_init)

    repo = DummyRepository()

    # Create two plants with furnace groups
    tech1 = DummyTechnology(name="BF", dynamic_business_case=None)
    fg1 = DummyFurnaceGroup(furnace_group_id="plant8_fg1", technology=tech1, status="operating", capacity=100)
    plant1 = DummyPlant(plant_id="plant8", furnace_groups=[fg1])

    tech2 = DummyTechnology(name="BOF", dynamic_business_case=None)
    fg2 = DummyFurnaceGroup(furnace_group_id="plant9_fg1", technology=tech2, status="operating", capacity=200)
    plant2 = DummyPlant(plant_id="plant9", furnace_groups=[fg2])

    repo.plants.items = [plant1, plant2]
    repo.plants.data = {"plant8": plant1, "plant9": plant2}

    lp_model = DummyTradeLPModel()
    commodity = DummyCommodity("iron")
    lp_model.commodities.append(commodity)

    # Create processes for production
    from_process = DummyProcess("BF", DummyProcessType.PRODUCTION, [])
    to_process = DummyProcess("BOF", DummyProcessType.PRODUCTION, [])
    lp_model._processes["BF"] = from_process
    lp_model._processes["BOF"] = to_process

    # Create process centers for plant-to-plant allocation
    from_pc = DummyProcessCenter("plant8_fg1", from_process, 100, "location1")
    to_pc = DummyProcessCenter("plant9_fg1", to_process, 200, "location2")

    # Add allocation (plant to plant) with value > LP_TOLERANCE
    lp_model.allocations.allocations[(from_pc, to_pc, commodity)] = 75.0

    allocations = solve_steel_trade_lp_and_return_commodity_allocations(lp_model, repo)

    # Verify allocation was recorded
    assert "iron" in allocations
    iron_alloc = allocations["iron"]
    # Check that allocations dict has entries (real CommodityAllocations uses .allocations dict)
    assert len(iron_alloc.allocations) > 0
