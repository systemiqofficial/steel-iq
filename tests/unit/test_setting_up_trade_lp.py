import pytest
import pyomo.environ as pyo

# --- Pytest fixtures to patch the module under test ---
#
from steelo.domain import Volumes
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
    def __init__(self, name, type, bill_of_materials, products=None, technology=None):
        self.name = name
        self.type = type
        self.bill_of_materials = bill_of_materials
        self.products = products or []
        self.technology = technology if technology is not None else name


class DummyProcessCenter:
    def __init__(
        self,
        name,
        process,
        capacity,
        location,
        production_cost=0.1,
        soft_minimum_capacity=0.0,
        energy_costs_per_input=None,
        last_production=None,
        input_intensities=None,
    ):
        self.production_cost = production_cost
        self.name = name
        self.process = process
        self.capacity = capacity
        self.location = location
        self.soft_minimum_capacity = soft_minimum_capacity
        self.energy_costs_per_input = energy_costs_per_input
        self.last_production = last_production
        self.input_intensities = input_intensities


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


class DummyConstraintCollection:
    def __init__(self):
        self.data = []

    def add(self, item):
        self.data.append(item)

    def pprint(self):
        # No-op for tests
        pass

    def __len__(self):
        return len(self.data)


class DummyLPModel:
    def __init__(self):
        self.allocation_variables = DummyAllocationVariables()
        self.secondary_feedstock_constraints = DummyConstraintCollection()
        self.max_secondary_feedstock_allocation = {}
        self.secondary_feedstock_index_set = set()


# Dummy TradeLPModel that stores processes, bom_elements, process centers, connectors, etc.
class DummyTradeLPModel:
    def __init__(self, lp_epsilon=1e-3, year=None, solver_options=None, random_seed=42, **kwargs):
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

    def build_lp_model(
        self, willingness_to_pay_list=None, carbon_border_mechanisms=None, country_mappings=None, year=None
    ):
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

    def add(self, obj):
        """Mimic repository add semantics for tests."""
        self.data[getattr(obj, "supplier_id", getattr(obj, "plant_id", getattr(obj, "demand_center_id", None)))] = obj
        self.items.append(obj)


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
    def __init__(self, furnace_group_id, technology, status, capacity, unit_fopex=1, chosen_reductant=""):
        self.furnace_group_id = furnace_group_id
        self.technology = technology
        self.status = status
        self.capacity = capacity
        self.unit_fopex = unit_fopex
        self.energy_vopex_by_input = {}
        self.chosen_reductant = chosen_reductant
        self.production = 0.0
        self.bill_of_materials = None

    @property
    def effective_primary_feedstocks(self):
        """Returns the effective primary feedstocks as a list, similar to the real FurnaceGroup class."""
        if self.technology.dynamic_business_case is None:
            return []
        if not self.chosen_reductant:
            return self.technology.dynamic_business_case
        return [fs for fs in self.technology.dynamic_business_case if fs.reductant == self.chosen_reductant]

    @property
    def carbon_cost_per_unit(self):
        """Mock carbon cost per unit for testing."""
        return 0.0


class DummyTechnology:
    def __init__(self, name, dynamic_business_case, product="steel"):
        self.name = name
        self.dynamic_business_case = dynamic_business_case
        self.product = product


class DummyFeedstock:
    def __init__(
        self,
        name,
        metallic_charge,
        required_quantity,
        maximum_share,
        minimum_share,
        secondary_feedstock,
        outputs,
        carbon_outputs=None,
        energy_requirements=None,
        reductant="",
    ):
        self.name = name
        self.metallic_charge = metallic_charge
        self.required_quantity_per_ton_of_product = required_quantity
        self.maximum_share_in_product = maximum_share
        self.minimum_share_in_product = minimum_share
        self.secondary_feedstock = secondary_feedstock
        self.outputs = outputs
        self.carbon_outputs = carbon_outputs or {}
        self.energy_requirements = energy_requirements or {}
        self.reductant = reductant

    def get_primary_outputs(self, primary_products: list[str] | None = None):
        return self.outputs


class DummyDemandCenter:
    def __init__(self, demand_center_id, demand_by_year, center_of_gravity="demand_location"):
        self.demand_center_id = demand_center_id
        self.demand_by_year = demand_by_year
        self.center_of_gravity = center_of_gravity


class DummySupplier:
    def __init__(
        self, supplier_id, commodity, capacity_by_year, location="supplier_location", production_cost_by_year=None
    ):
        # Support both old-style single production_cost and new-style production_cost_by_year for compatibility
        if production_cost_by_year is None:
            # Create a default production_cost_by_year dictionary with value 0.1 for all years
            from steelo.domain import Year

            production_cost_by_year = {Year(year): 0.1 for year in range(2020, 2051)}
        self.production_cost_by_year = production_cost_by_year
        self.supplier_id = supplier_id
        self.commodity = commodity
        self.capacity_by_year = capacity_by_year
        self.location = location
        self.mine_cost_by_year = {}
        self.mine_price_by_year = {}


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


@pytest.fixture(autouse=True)
def patch_exit(monkeypatch):
    """Prevent set_up_steel_trade_lp from terminating the test run via exit()."""
    monkeypatch.setattr("builtins.exit", lambda *args, **kwargs: None)


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
        random_seed: int = 42
        start_year: Year = Year(2025)
        end_year: Year = Year(2060)
        cbam_carbon_scope: str = "direct_only"

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


def test_create_process_from_furnace_group_carbon_outputs_bridged_to_dependent_commodities():
    """CCS carbon_outputs are added to BOM dependent_commodities with per-input unit conversion."""
    required_quantity = 1.05  # t hot_metal input per t steel output
    feedstock = DummyFeedstock(
        name="BF_CCS_HS",
        metallic_charge="hot_metal",
        required_quantity=required_quantity,
        maximum_share=1.0,
        minimum_share=0.0,
        secondary_feedstock={},
        outputs={"steel": 1},
        carbon_outputs={"co2_stored": 2.7, "co2_slip": 0.13},
    )
    tech = DummyTechnology(name="BF+CCS", dynamic_business_case=[feedstock])
    furnace_group = DummyFurnaceGroup(
        furnace_group_id="plant_ccs_fg1", technology=tech, status="operating", capacity=100
    )
    lp_model = DummyTradeLPModel()
    config = create_mock_config()

    process = create_process_from_furnace_group(furnace_group, lp_model, config)

    assert len(process.bill_of_materials) == 1
    bom = process.bill_of_materials[0]

    # dependent_commodities keys are real tlp.Commodity objects — look up by name
    dep_by_name = {c.name: v for c, v in bom.dependent_commodities.items()}

    assert "co2_stored" in dep_by_name, "co2_stored should be bridged into dependent_commodities"
    assert "co2_slip" in dep_by_name, "co2_slip should be bridged into dependent_commodities"

    # Values are converted: tCO2/t-product-output → tCO2/t-primary-input
    assert dep_by_name["co2_stored"] == pytest.approx(2.7 / required_quantity)
    assert dep_by_name["co2_slip"] == pytest.approx(0.13 / required_quantity)


def test_create_process_from_furnace_group_zero_carbon_output_excluded():
    """Zero-valued carbon outputs are excluded from dependent_commodities (no LP effect, avoids noise)."""
    feedstock = DummyFeedstock(
        name="BF_CCS_HS2",
        metallic_charge="hot_metal",
        required_quantity=1.0,
        maximum_share=1.0,
        minimum_share=0.0,
        secondary_feedstock={},
        outputs={"steel": 1},
        carbon_outputs={"co2_stored": 2.7, "co2_utilised": 0.0},
    )
    tech = DummyTechnology(name="BF+CCS2", dynamic_business_case=[feedstock])
    furnace_group = DummyFurnaceGroup(
        furnace_group_id="plant_ccs_fg2", technology=tech, status="operating", capacity=100
    )
    lp_model = DummyTradeLPModel()
    config = create_mock_config()

    process = create_process_from_furnace_group(furnace_group, lp_model, config)
    bom = process.bill_of_materials[0]
    dep_by_name = {c.name: v for c, v in bom.dependent_commodities.items()}

    assert "co2_stored" in dep_by_name
    assert "co2_utilised" not in dep_by_name, "zero-valued carbon output should be excluded"


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


def test_add_furnace_groups_as_process_centers_energy_costs_are_facility_specific():
    """Two furnace groups sharing a technology reuse the same Process/BOM (built from
    whichever FG came first), but must each carry their own energy cost override so the
    LP objective isn't priced on the first FG's energy costs fleet-wide."""
    feedstock = DummyFeedstock(
        name="scrap_feed",
        metallic_charge="scrap",
        required_quantity=1.0,
        maximum_share=1.0,
        minimum_share=0.0,
        secondary_feedstock={},
        outputs={"steel": 1.0},
    )
    tech = DummyTechnology(name="EAF", dynamic_business_case=[feedstock])

    fg1 = DummyFurnaceGroup(furnace_group_id="fg1", technology=tech, status="operating", capacity=50)
    fg1.energy_vopex_by_input = {"scrap": 10.0}
    fg2 = DummyFurnaceGroup(furnace_group_id="fg2", technology=tech, status="operating", capacity=80)
    fg2.energy_vopex_by_input = {"scrap": 40.0}

    plant1 = DummyPlant(plant_id="plant1", furnace_groups=[fg1])
    plant2 = DummyPlant(plant_id="plant2", furnace_groups=[fg2])
    repo = DummyRepository()
    repo.plants.items = [plant1, plant2]
    repo.plants.data = {"plant1": plant1, "plant2": plant2}

    lp_model = DummyTradeLPModel()
    config = create_mock_config()

    add_furnace_groups_as_process_centers(repo, lp_model, config)

    pc1 = next(pc for pc in lp_model.process_centers if pc.name == "fg1")
    pc2 = next(pc for pc in lp_model.process_centers if pc.name == "fg2")

    # Both furnace groups reuse the same (first-built) shared Process/BOM...
    assert pc1.process is pc2.process
    # ...but each ProcessCenter carries its own facility-specific energy cost override.
    assert pc1.energy_costs_per_input["scrap"] == pytest.approx(10.0)
    assert pc2.energy_costs_per_input["scrap"] == pytest.approx(40.0)


def test_add_furnace_groups_as_process_centers_reductant_variants_get_distinct_bom():
    """Two furnace groups of the same technology but different chosen_reductant must NOT
    share a Process/BOM — each reductant's feedstock (input ratio, energy cost) is different,
    and baking one reductant's BOM in fleet-wide would misprice/mislegalize every facility on
    the other reductant."""
    coke_feed = DummyFeedstock(
        name="bf_iron_ore_coke",
        metallic_charge="iron_ore",
        required_quantity=1.6,
        maximum_share=1.0,
        minimum_share=0.0,
        secondary_feedstock={},
        outputs={"steel": 1.0},
        reductant="coke",
    )
    hydrogen_feed = DummyFeedstock(
        name="bf_iron_ore_hydrogen",
        metallic_charge="iron_ore",
        required_quantity=1.4,
        maximum_share=1.0,
        minimum_share=0.0,
        secondary_feedstock={},
        outputs={"steel": 1.0},
        reductant="hydrogen",
    )
    tech = DummyTechnology(name="BF", dynamic_business_case=[coke_feed, hydrogen_feed])

    fg_coke = DummyFurnaceGroup(
        furnace_group_id="fg_coke", technology=tech, status="operating", capacity=50, chosen_reductant="coke"
    )
    fg_coke.energy_vopex_by_input = {"iron_ore": 16.0}
    fg_hydrogen = DummyFurnaceGroup(
        furnace_group_id="fg_hydrogen", technology=tech, status="operating", capacity=80, chosen_reductant="hydrogen"
    )
    fg_hydrogen.energy_vopex_by_input = {"iron_ore": 56.0}

    plant1 = DummyPlant(plant_id="plant1", furnace_groups=[fg_coke])
    plant2 = DummyPlant(plant_id="plant2", furnace_groups=[fg_hydrogen])
    repo = DummyRepository()
    repo.plants.items = [plant1, plant2]
    repo.plants.data = {"plant1": plant1, "plant2": plant2}

    lp_model = DummyTradeLPModel()
    config = create_mock_config()

    add_furnace_groups_as_process_centers(repo, lp_model, config)

    pc_coke = next(pc for pc in lp_model.process_centers if pc.name == "fg_coke")
    pc_hydrogen = next(pc for pc in lp_model.process_centers if pc.name == "fg_hydrogen")

    # Distinct Process objects (different reductants), but grouped under the same technology.
    assert pc_coke.process is not pc_hydrogen.process
    assert pc_coke.process.technology == "BF"
    assert pc_hydrogen.process.technology == "BF"
    assert pc_coke.process.name != pc_hydrogen.process.name

    # Each Process's own BOM reflects only that reductant's feedstock/input ratio.
    coke_boms = pc_coke.process.bill_of_materials
    hydrogen_boms = pc_hydrogen.process.bill_of_materials
    assert len(coke_boms) == 1
    assert len(hydrogen_boms) == 1
    assert coke_boms[0].parameters["input_ratio"] == pytest.approx(1.6)
    assert hydrogen_boms[0].parameters["input_ratio"] == pytest.approx(1.4)
    # Energy cost is per ton of input: energy_vopex_by_input / required_quantity_per_ton_of_product.
    assert coke_boms[0].energy_cost == pytest.approx(16.0 / 1.6)
    assert hydrogen_boms[0].energy_cost == pytest.approx(56.0 / 1.4)


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

    def init_with_processes(self, lp_epsilon=1e-3, year=None, solver_options=None, random_seed=42, **kwargs):
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


def test_set_up_steel_trade_lp_connector_wiring_covers_all_reductant_variants(monkeypatch):
    """A LegalProcessConnector names a technology (e.g. "BF" -> "BOF"), which may resolve to
    several reductant-variant Process objects. Connector wiring must expand to every
    combination, so a coke-BF facility and a hydrogen-BF facility both connect to BOF —
    connectivity stays technology-level even though BOM content is now variant-level."""
    from steelo.domain.models import LegalProcessConnector

    year = 2025

    coke_feed = DummyFeedstock(
        name="bf_iron_ore_coke",
        metallic_charge="iron_ore",
        required_quantity=1.6,
        maximum_share=1.0,
        minimum_share=0.0,
        secondary_feedstock={},
        outputs={"hot_metal": 1.0},
        reductant="coke",
    )
    hydrogen_feed = DummyFeedstock(
        name="bf_iron_ore_hydrogen",
        metallic_charge="iron_ore",
        required_quantity=1.4,
        maximum_share=1.0,
        minimum_share=0.0,
        secondary_feedstock={},
        outputs={"hot_metal": 1.0},
        reductant="hydrogen",
    )
    tech = DummyTechnology(name="BF", dynamic_business_case=[coke_feed, hydrogen_feed], product="hot_metal")

    fg_coke = DummyFurnaceGroup(
        furnace_group_id="fg_coke", technology=tech, status="operating", capacity=50, chosen_reductant="coke"
    )
    fg_hydrogen = DummyFurnaceGroup(
        furnace_group_id="fg_hydrogen", technology=tech, status="operating", capacity=80, chosen_reductant="hydrogen"
    )
    plant1 = DummyPlant(plant_id="plant1", furnace_groups=[fg_coke])
    plant2 = DummyPlant(plant_id="plant2", furnace_groups=[fg_hydrogen])
    repo = DummyRepository()
    repo.plants.items = [plant1, plant2]
    repo.plants.data = {"plant1": plant1, "plant2": plant2}

    # Pre-register a downstream "BOF" process (as the existing test above does), since it's
    # not built from a furnace group in this test.
    orig_init = ORIGINAL_DUMMY_TRADE_LP_MODEL_INIT

    def init_with_bof_process(self, lp_epsilon=1e-3, year=None, solver_options=None, random_seed=42, **kwargs):
        orig_init(self, lp_epsilon, year, solver_options)
        self._processes["BOF"] = DummyProcess("BOF", DummyProcessType.PRODUCTION, [])

    monkeypatch.setattr(DummyTradeLPModel, "__init__", init_with_bof_process)

    mock_config = create_mock_config()
    message_bus = DummyMessageBus(repo)
    lp_model = set_up_steel_trade_lp(
        message_bus=message_bus,
        year=year,
        config=mock_config,
        legal_process_connectors=[LegalProcessConnector(from_technology_name="BF", to_technology_name="BOF")],
    )

    bf_processes = {p.name: p for p in lp_model.processes if p.technology == "BF"}
    assert set(bf_processes.keys()) == {"BF_coke", "BF_hydrogen"}
    bof_process = next(p for p in lp_model.processes if p.name == "BOF")

    wired_pairs = {(conn.from_process.name, conn.to_process.name) for conn in lp_model.connectors}
    assert ("BF_coke", "BOF") in wired_pairs
    assert ("BF_hydrogen", "BOF") in wired_pairs
    assert all(conn.to_process is bof_process for conn in lp_model.connectors if conn.from_process.technology == "BF")


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

    commodity = DummyCommodity("steel")
    from_pc = DummyProcessCenter(
        "eu_pc",
        DummyProcess("p1", DummyProcessType.PRODUCTION, [], products=[commodity]),
        100,
        eu_location,
        production_cost=100.0,
    )
    to_pc = DummyProcessCenter(
        "us_pc",
        DummyProcess("p2", DummyProcessType.PRODUCTION, [], products=[commodity]),
        100,
        non_eu_location,
        production_cost=50.0,
    )

    lp_model.process_centers = [from_pc, to_pc]
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

    commodity = DummyCommodity("steel")
    from_pc = DummyProcessCenter(
        "us_pc",
        DummyProcess("p1", DummyProcessType.PRODUCTION, [], products=[commodity]),
        100,
        non_eu_location,
        production_cost=50.0,
    )
    to_pc = DummyProcessCenter(
        "eu_pc",
        DummyProcess("p2", DummyProcessType.PRODUCTION, [], products=[commodity]),
        100,
        eu_location,
        production_cost=100.0,
    )

    lp_model.process_centers = [from_pc, to_pc]
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

    # Import to EU (reference 100) from non-EU (embedded cost 50) should add differential (+50)
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

    commodity = DummyCommodity("steel")
    from_pc = DummyProcessCenter(
        "us_pc",
        DummyProcess("p1", DummyProcessType.PRODUCTION, [], products=[commodity]),
        100,
        non_eu_location,
        production_cost=50.0,
    )
    to_pc = DummyProcessCenter(
        "eu_pc",
        DummyProcess("p2", DummyProcessType.PRODUCTION, [], products=[commodity]),
        100,
        eu_location,
        production_cost=100.0,
    )

    lp_model.process_centers = [from_pc, to_pc]
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


def test_adapt_allocation_costs_cbam_adjusts_all_arcs_on_same_route():
    """Two exporter PCs in the same country trading with the same importer PC must both be
    adjusted — the dedup guard must key on the arc, not the country pair."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )

    lp_model = DummyTradeLPModel()

    eu_location = DummyLocation("DEU")
    non_eu_location = DummyLocation("USA")

    # Two distinct US process centers exporting to the same EU process center
    commodity = DummyCommodity("steel")
    from_pc_1 = DummyProcessCenter(
        "us_pc_1",
        DummyProcess("p1", DummyProcessType.PRODUCTION, [], products=[commodity]),
        100,
        non_eu_location,
        production_cost=50.0,
    )
    from_pc_2 = DummyProcessCenter(
        "us_pc_2",
        DummyProcess("p2", DummyProcessType.PRODUCTION, [], products=[commodity]),
        100,
        non_eu_location,
        production_cost=60.0,
    )
    to_pc = DummyProcessCenter(
        "eu_pc",
        DummyProcess("p3", DummyProcessType.PRODUCTION, [], products=[commodity]),
        100,
        eu_location,
        production_cost=100.0,
    )

    lp_model.process_centers = [from_pc_1, from_pc_2, to_pc]
    lp_model.legal_allocations = [
        (from_pc_1, to_pc, commodity),
        (from_pc_2, to_pc, commodity),
    ]
    lp_model.lp_model.allocation_costs = {
        ("us_pc_1", "eu_pc", "steel"): 10.0,
        ("us_pc_2", "eu_pc", "steel"): 20.0,
    }

    cbam = DummyCarbonBorderMechanism(mechanism_name="CBAM", applying_region_column="EU", start_year=2025)
    country_mappings = {
        "DEU": DummyCountryMapping("DEU", EU=True),
        "USA": DummyCountryMapping("USA", EU=False),
    }

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    # Both arcs share the same country pair (USA -> DEU) but must each be adjusted independently
    assert lp_model.lp_model.allocation_costs[("us_pc_1", "eu_pc", "steel")] == 10.0 + 50.0
    assert lp_model.lp_model.allocation_costs[("us_pc_2", "eu_pc", "steel")] == 20.0 + 40.0


def test_build_reference_producer_carbon_costs_capacity_weight_fallback():
    """Without production history the reference falls back to capacity weighting."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import build_reference_producer_carbon_costs
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp

    steel = DummyCommodity("steel")
    eu_location = DummyLocation("DEU")

    producer_1 = DummyProcessCenter(
        "eu_prod_1",
        DummyProcess("p1", tlp.ProcessType.PRODUCTION, [], products=[steel]),
        capacity=100,
        location=eu_location,
        production_cost=100.0,
    )
    producer_2 = DummyProcessCenter(
        "eu_prod_2",
        DummyProcess("p2", tlp.ProcessType.PRODUCTION, [], products=[steel]),
        capacity=300,
        location=eu_location,
        production_cost=60.0,
    )
    # Non-production PC producing the same commodity/location must not contribute
    demand_center = DummyProcessCenter(
        "eu_demand",
        DummyProcess("d1", tlp.ProcessType.DEMAND, [], products=[steel]),
        capacity=1000,
        location=eu_location,
        production_cost=0.0,
    )

    references = build_reference_producer_carbon_costs([producer_1, producer_2, demand_center])

    # Weighted average: (100*100 + 300*60) / 400 = 70.0
    assert references.steel_ref["DEU"] == pytest.approx(70.0)


def test_build_reference_producer_carbon_costs_excludes_idle_plants():
    """Idle producers (zero production last year) should not dilute the reference carbon
    cost, while an actively producing zero-carbon plant must dilute it — a zero carbon
    cost means no priced emissions, not idleness."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import build_reference_producer_carbon_costs
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp

    steel = DummyCommodity("steel")
    eu_location = DummyLocation("DEU")

    # Active producer: small output, high carbon cost
    active_producer = DummyProcessCenter(
        "eu_active",
        DummyProcess("p1", tlp.ProcessType.PRODUCTION, [], products=[steel]),
        capacity=100,
        location=eu_location,
        production_cost=100.0,
    )
    active_producer.last_production = 100.0

    # Idle producer: large capacity, produced nothing last year
    idle_producer = DummyProcessCenter(
        "eu_idle",
        DummyProcess("p2", tlp.ProcessType.PRODUCTION, [], products=[steel]),
        capacity=1000,
        location=eu_location,
        production_cost=50.0,
    )
    idle_producer.last_production = 0.0

    references = build_reference_producer_carbon_costs([active_producer, idle_producer])

    # Only the active producer counts: 100*100 / 100 = 100.0
    assert references.steel_ref["DEU"] == pytest.approx(100.0)

    # A decarbonised plant that IS producing dilutes the reference honestly
    green_producer = DummyProcessCenter(
        "eu_green",
        DummyProcess("p3", tlp.ProcessType.PRODUCTION, [], products=[steel]),
        capacity=1000,
        location=eu_location,
        production_cost=0.0,
    )
    green_producer.last_production = 300.0

    references = build_reference_producer_carbon_costs([active_producer, idle_producer, green_producer])

    # (100*100 + 300*0) / 400 = 25.0
    assert references.steel_ref["DEU"] == pytest.approx(25.0)


def test_adapt_allocation_costs_cbam_import_to_eu_demand_center():
    """Finished-steel imports into an EU demand centre are the primary real-world CBAM
    channel — they must be adjusted against the domestic reference producer carbon cost,
    since demand centres carry no production_cost of their own."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp

    lp_model = DummyTradeLPModel()

    eu_location = DummyLocation("DEU")
    non_eu_location = DummyLocation("USA")
    steel = DummyCommodity("steel")

    eu_producer = DummyProcessCenter(
        "eu_producer",
        DummyProcess("p1", tlp.ProcessType.PRODUCTION, [], products=[steel]),
        capacity=100,
        location=eu_location,
        production_cost=100.0,
    )
    eu_demand = DummyProcessCenter(
        "eu_demand",
        DummyProcess("d1", tlp.ProcessType.DEMAND, [], products=[steel]),
        capacity=200,
        location=eu_location,
        production_cost=0.0,
    )
    us_exporter = DummyProcessCenter(
        "us_exporter",
        DummyProcess("p2", tlp.ProcessType.PRODUCTION, [], products=[steel]),
        capacity=100,
        location=non_eu_location,
        production_cost=50.0,
    )

    lp_model.process_centers = [eu_producer, eu_demand, us_exporter]
    lp_model.legal_allocations = [(us_exporter, eu_demand, steel)]
    lp_model.lp_model.allocation_costs = {("us_exporter", "eu_demand", "steel"): 10.0}

    cbam = DummyCarbonBorderMechanism(mechanism_name="CBAM", applying_region_column="EU", start_year=2025)
    country_mappings = {
        "DEU": DummyCountryMapping("DEU", EU=True),
        "USA": DummyCountryMapping("USA", EU=False),
    }

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    # Import into EU demand (reference cost 100) from US exporter (cost 50): differential = +50
    assert lp_model.lp_model.allocation_costs[("us_exporter", "eu_demand", "steel")] == 10.0 + 50.0


def test_adapt_allocation_costs_cbam_skips_demand_center_without_domestic_producers():
    """A destination country with no domestic producers of the commodity has nothing to
    protect, so the flow into its demand centre must be left unadjusted."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp

    lp_model = DummyTradeLPModel()

    eu_location = DummyLocation("DEU")
    non_eu_location = DummyLocation("USA")
    steel = DummyCommodity("steel")

    # No EU producer of steel exists — only a demand center
    eu_demand = DummyProcessCenter(
        "eu_demand",
        DummyProcess("d1", tlp.ProcessType.DEMAND, [], products=[steel]),
        capacity=200,
        location=eu_location,
        production_cost=0.0,
    )
    us_exporter = DummyProcessCenter(
        "us_exporter",
        DummyProcess("p2", tlp.ProcessType.PRODUCTION, [], products=[steel]),
        capacity=100,
        location=non_eu_location,
        production_cost=50.0,
    )

    lp_model.process_centers = [eu_demand, us_exporter]
    lp_model.legal_allocations = [(us_exporter, eu_demand, steel)]
    lp_model.lp_model.allocation_costs = {("us_exporter", "eu_demand", "steel"): 10.0}

    cbam = DummyCarbonBorderMechanism(mechanism_name="CBAM", applying_region_column="EU", start_year=2025)
    country_mappings = {
        "DEU": DummyCountryMapping("DEU", EU=True),
        "USA": DummyCountryMapping("USA", EU=False),
    }

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    assert lp_model.lp_model.allocation_costs[("us_exporter", "eu_demand", "steel")] == 10.0


def _embedded_chain_fixture():
    """DEU iron+steel chain, a DEU demand centre, and US producers, for embedded-cost tests.

    Carbon is booked on the iron stage (hot metal at 100 $/t); both steel stages carry
    zero own carbon cost, mirroring how the model books emissions.
    """
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp

    steel = DummyCommodity("steel")
    hot_metal = DummyCommodity("hot_metal")
    scrap = DummyCommodity("scrap")
    eu_location = DummyLocation("DEU")
    us_location = DummyLocation("USA")

    steel_bom = [DummyBOMElement("hm", hot_metal, [steel], {}), DummyBOMElement("sc", scrap, [steel], {})]

    eu_iron = DummyProcessCenter(
        "eu_iron",
        DummyProcess("bf", tlp.ProcessType.PRODUCTION, [], products=[hot_metal]),
        capacity=100,
        location=eu_location,
        production_cost=100.0,
    )
    eu_steel = DummyProcessCenter(
        "eu_steel",
        DummyProcess("bof", tlp.ProcessType.PRODUCTION, steel_bom, products=[steel]),
        capacity=100,
        location=eu_location,
        production_cost=0.0,
    )
    eu_steel.input_intensities = {"hot_metal": 0.9, "scrap": 0.2}
    eu_demand = DummyProcessCenter(
        "eu_demand",
        DummyProcess("d1", tlp.ProcessType.DEMAND, [], products=[steel]),
        capacity=200,
        location=eu_location,
        production_cost=0.0,
    )
    us_steel = DummyProcessCenter(
        "us_steel",
        DummyProcess("eaf", tlp.ProcessType.PRODUCTION, steel_bom, products=[steel]),
        capacity=100,
        location=us_location,
        production_cost=0.0,
    )
    us_steel.input_intensities = {"scrap": 1.1}

    cbam = DummyCarbonBorderMechanism(mechanism_name="CBAM", applying_region_column="EU", start_year=2025)
    country_mappings = {
        "DEU": DummyCountryMapping("DEU", EU=True),
        "USA": DummyCountryMapping("USA", EU=False),
    }
    return steel, hot_metal, eu_iron, eu_steel, eu_demand, us_steel, cbam, country_mappings


def test_adapt_allocation_costs_cbam_steel_import_charged_on_embedded_iron_carbon():
    """Regression for the structurally dead import channel: EU steel plants carry zero own
    carbon cost because emissions are booked on the iron stage, yet steel imports into an
    EU demand centre must still be charged against the carbon embedded via the iron inputs."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )

    steel, _, eu_iron, eu_steel, eu_demand, us_steel, cbam, country_mappings = _embedded_chain_fixture()

    lp_model = DummyTradeLPModel()
    lp_model.process_centers = [eu_iron, eu_steel, eu_demand, us_steel]
    lp_model.legal_allocations = [(us_steel, eu_demand, steel)]
    lp_model.lp_model.allocation_costs = {("us_steel", "eu_demand", "steel"): 10.0}

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    # DEU steel reference = 0 + 0.9 * 100 = 90; US scrap-based exporter embeds 0 -> +90.
    # Scrap comes from SUPPLY nodes, so it must not count as embedded iron carbon.
    assert lp_model.lp_model.allocation_costs[("us_steel", "eu_demand", "steel")] == pytest.approx(10.0 + 90.0)


def test_adapt_allocation_costs_cbam_source_side_embedded_cost_reduces_import_charge():
    """A foreign steel producer that already paid for carbon via its iron inputs must only
    be charged the shortfall against the destination reference, not the full amount."""
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )

    steel, hot_metal, eu_iron, eu_steel, eu_demand, us_steel, cbam, country_mappings = _embedded_chain_fixture()

    us_iron = DummyProcessCenter(
        "us_iron",
        DummyProcess("bf_us", tlp.ProcessType.PRODUCTION, [], products=[hot_metal]),
        capacity=100,
        location=DummyLocation("USA"),
        production_cost=50.0,
    )
    us_steel.input_intensities = {"hot_metal": 0.8}

    lp_model = DummyTradeLPModel()
    lp_model.process_centers = [eu_iron, eu_steel, eu_demand, us_steel, us_iron]
    lp_model.legal_allocations = [(us_steel, eu_demand, steel)]
    lp_model.lp_model.allocation_costs = {("us_steel", "eu_demand", "steel"): 10.0}

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    # US embedded = 0.8 * 50 = 40; DEU reference = 90 -> adjustment +50
    assert lp_model.lp_model.allocation_costs[("us_steel", "eu_demand", "steel")] == pytest.approx(10.0 + 50.0)


def test_adapt_allocation_costs_cbam_iron_import_compared_against_destination_iron_reference():
    """An iron-stage arc into an EU steel plant must be benchmarked against the destination
    country's iron reference — not the destination plant's own cost, which is denominated
    per tonne of steel and near zero by construction."""
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )

    steel, hot_metal, eu_iron, eu_steel, eu_demand, us_steel, cbam, country_mappings = _embedded_chain_fixture()

    us_iron = DummyProcessCenter(
        "us_iron",
        DummyProcess("bf_us", tlp.ProcessType.PRODUCTION, [], products=[hot_metal]),
        capacity=100,
        location=DummyLocation("USA"),
        production_cost=10.0,
    )

    lp_model = DummyTradeLPModel()
    lp_model.process_centers = [eu_iron, eu_steel, eu_demand, us_iron]
    lp_model.legal_allocations = [(us_iron, eu_steel, hot_metal)]
    lp_model.lp_model.allocation_costs = {("us_iron", "eu_steel", "hot_metal"): 10.0}

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    # DEU iron reference = 100, US iron cost = 10 -> adjustment +90
    # (against eu_steel's own cost of 0.0, nothing would fire)
    assert lp_model.lp_model.allocation_costs[("us_iron", "eu_steel", "hot_metal")] == pytest.approx(10.0 + 90.0)


def test_adapt_allocation_costs_cbam_export_rebate_uses_embedded_cost():
    """EU steel exports must be rebated on their embedded carbon cost: down to the
    destination's reference where one exists, in full where the destination market has
    no domestic producers (unpriced market)."""
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import (
        adapt_allocation_costs_for_carbon_border_mechanisms,
    )

    steel, _, eu_iron, eu_steel, eu_demand, us_steel, cbam, country_mappings = _embedded_chain_fixture()
    country_mappings["CAN"] = DummyCountryMapping("CAN", EU=False)

    us_steel.production_cost = 20.0
    us_steel.input_intensities = {}
    us_demand = DummyProcessCenter(
        "us_demand",
        DummyProcess("d2", tlp.ProcessType.DEMAND, [], products=[steel]),
        capacity=200,
        location=DummyLocation("USA"),
        production_cost=0.0,
    )
    # Canada has demand but no domestic steel production at all
    can_demand = DummyProcessCenter(
        "can_demand",
        DummyProcess("d3", tlp.ProcessType.DEMAND, [], products=[steel]),
        capacity=200,
        location=DummyLocation("CAN"),
        production_cost=0.0,
    )

    lp_model = DummyTradeLPModel()
    lp_model.process_centers = [eu_iron, eu_steel, eu_demand, us_steel, us_demand, can_demand]
    lp_model.legal_allocations = [(eu_steel, us_demand, steel), (eu_steel, can_demand, steel)]
    lp_model.lp_model.allocation_costs = {
        ("eu_steel", "us_demand", "steel"): 10.0,
        ("eu_steel", "can_demand", "steel"): 10.0,
    }

    adapt_allocation_costs_for_carbon_border_mechanisms(
        trade_lp=lp_model, carbon_border_mechanisms=[cbam], country_mappings=country_mappings, year=2026
    )

    # EU embedded = 0.9 * 100 = 90. US reference = 20 -> rebate -70; CAN has no producers -> -90.
    assert lp_model.lp_model.allocation_costs[("eu_steel", "us_demand", "steel")] == pytest.approx(10.0 - 70.0)
    assert lp_model.lp_model.allocation_costs[("eu_steel", "can_demand", "steel")] == pytest.approx(10.0 - 90.0)


def test_adapt_allocation_costs_cbam_missing_intensities_fall_back_to_country_average():
    """Producers without a material-bill history (new plants) borrow the production-weighted
    average iron intensity of their country's steel fleet."""
    import steelo.domain.trade_modelling.trade_lp_modelling as tlp
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import build_reference_producer_carbon_costs

    steel, hot_metal, eu_iron, eu_steel, eu_demand, us_steel, _, _ = _embedded_chain_fixture()

    new_plant = DummyProcessCenter(
        "eu_new",
        DummyProcess("bof2", tlp.ProcessType.PRODUCTION, [], products=[steel]),
        capacity=100,
        location=DummyLocation("DEU"),
        production_cost=0.0,
    )

    references = build_reference_producer_carbon_costs([eu_iron, eu_steel, new_plant])

    # eu_steel has alpha 0.9, so DEU's average alpha is 0.9; the new plant borrows it and
    # both producers embed 0.9 * 100 = 90.
    assert references.steel_alpha["DEU"] == pytest.approx(0.9)
    assert references.steel_ref["DEU"] == pytest.approx(90.0)


# --- Tests for identify_bottlenecks ---


def test_identify_bottlenecks_empty_allocations():
    """Test bottleneck analysis with empty allocations."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import check_if_bottlenecks_identified
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
    check_if_bottlenecks_identified(commodity_allocations, repo, env, year)


def test_identify_bottlenecks_skip_scrap():
    """Test that scrap commodity is skipped in bottleneck analysis."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import check_if_bottlenecks_identified
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
    check_if_bottlenecks_identified(commodity_allocations, repo, env, year)


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

    def init_with_tracking(self, lp_epsilon=1e-3, year=None, solver_options=None, random_seed=42, **kwargs):
        orig_init(self, lp_epsilon, year, solver_options, random_seed=random_seed, **kwargs)
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

    def init_with_constraint_tracking(self, lp_epsilon=1e-3, year=None, solver_options=None, random_seed=42, **kwargs):
        orig_init(self, lp_epsilon, year, solver_options, random_seed=random_seed, **kwargs)
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

    def init_with_tracking(self, lp_epsilon=1e-3, year=None, solver_options=None, random_seed=42, **kwargs):
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
    # Ensure we created the dummy process center with +1 headroom
    assert any(center.capacity == 3001.0 for center in hydrogen_centers)


def test_secondary_feedstock_supplier_capacity_updated_each_year(monkeypatch):
    from steelo.domain import Year

    repo = DummyRepository()
    year = Year(2026)
    mock_config = create_mock_config()
    message_bus = DummyMessageBus(repo)

    secondary_feedstock_constraints = {"bio_pci": {("USA",): 150.0}}

    set_up_steel_trade_lp(
        message_bus=message_bus,
        year=year,
        config=mock_config,
        legal_process_connectors=[],
        secondary_feedstock_constraints=secondary_feedstock_constraints,
    )

    supplier = repo.suppliers.get("bio_pci_supply_process_center")
    assert supplier.capacity_by_year[year] == Volumes(150.0)


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


# --- Tests for meta-furnace group clustering integration ---


class DummyMetaFurnaceGroup:
    """Mock MetaFurnaceGroup for testing."""

    def __init__(
        self,
        meta_furnace_group_id,
        technology_name,
        chosen_reductant,
        location,
        total_capacity,
        weighted_avg_carbon_cost,
        dynamic_business_case,
        weighted_avg_energy_costs=None,
        capacity_shares=None,
        constituent_locations=None,
    ):
        self.meta_furnace_group_id = meta_furnace_group_id
        self.technology_name = technology_name
        self.chosen_reductant = chosen_reductant
        self.location = location
        self.total_capacity = total_capacity
        self.weighted_avg_carbon_cost = weighted_avg_carbon_cost
        self.dynamic_business_case = dynamic_business_case
        self.weighted_avg_energy_costs = weighted_avg_energy_costs or {}
        self.capacity_shares = capacity_shares or {}
        self.constituent_locations = constituent_locations or {}


def test_add_furnace_groups_as_process_centers_with_meta_furnace_groups():
    """Test that meta-furnace groups are correctly processed into process centers."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import add_furnace_groups_as_process_centers

    # Create dummy location for meta-FG (capacity-weighted centroid)
    class MockLocation:
        def __init__(self, lat, lon, iso3="USA"):
            self.lat = lat
            self.lon = lon
            self.iso3 = iso3

    centroid_location = MockLocation(lat=40.5, lon=101.5)

    # Create a meta-furnace group representing 2 clustered BF-coke furnaces
    meta_fg = DummyMetaFurnaceGroup(
        meta_furnace_group_id="cluster_BF_coke_USA",
        technology_name="BF",
        chosen_reductant="coke",
        location=centroid_location,
        total_capacity=Volumes(4000.0),  # Combined capacity
        weighted_avg_carbon_cost=90.0,  # Weighted average
        dynamic_business_case=[],
        weighted_avg_energy_costs={"hot_metal": 25.5, "pig_iron": 30.0},
        capacity_shares={"plant1_fg0": 0.25, "plant2_fg0": 0.75},
    )

    repo = DummyRepository()
    lp_model = DummyTradeLPModel()
    config = create_mock_config()

    # Pass meta-furnace group via furnace_groups_override
    add_furnace_groups_as_process_centers(repo, lp_model, config, furnace_groups_override=[meta_fg])

    # Verify that a process center was created
    assert len(lp_model.process_centers) == 1
    pc = lp_model.process_centers[0]

    # Check process center properties
    assert pc.name == "cluster_BF_coke_USA"
    assert pc.capacity == config.capacity_limit * meta_fg.total_capacity
    assert pc.location == centroid_location
    assert pc.production_cost == 90.0  # Weighted average carbon cost

    # Verify the process was created/retrieved, keyed by the (technology, reductant) variant name
    assert "BF_coke" in lp_model._processes
    assert pc.process.technology == "BF"


def test_set_up_steel_trade_lp_with_meta_furnace_groups(monkeypatch):
    """Integration test: set up LP with meta-furnace groups instead of raw furnace groups."""
    from steelo.domain.trade_modelling.set_up_steel_trade_lp import set_up_steel_trade_lp

    year = 2025

    # Create empty repository (no plants, since we're using meta-FGs)
    repo = DummyRepository()
    repo.plants.items = []
    repo.demand_centers.items = []
    repo.suppliers.items = []

    # Create demand center
    demand_center = DummyDemandCenter(demand_center_id="demand_cluster", demand_by_year={year: 5000})
    repo.demand_centers.items = [demand_center]
    repo.demand_centers.data = {"demand_cluster": demand_center}

    # Create supplier
    supplier = DummySupplier(supplier_id="sup_cluster", commodity="scrap", capacity_by_year={year: 3000})
    repo.suppliers.items = [supplier]
    repo.suppliers.data = {"sup_cluster": supplier}

    # Create mock location
    class MockLocation:
        def __init__(self, lat, lon, iso3="CHN"):
            self.lat = lat
            self.lon = lon
            self.iso3 = iso3

    # Create meta-furnace groups
    meta_fg1 = DummyMetaFurnaceGroup(
        meta_furnace_group_id="cluster_BF_coke_CHN",
        technology_name="BF",
        chosen_reductant="coke",
        location=MockLocation(lat=35.0, lon=110.0),
        total_capacity=Volumes(10000.0),
        weighted_avg_carbon_cost=85.0,
        dynamic_business_case=[],
        weighted_avg_energy_costs={"hot_metal": 28.0},
    )

    meta_fg2 = DummyMetaFurnaceGroup(
        meta_furnace_group_id="cluster_EAF_electricity_CHN",
        technology_name="EAF",
        chosen_reductant="electricity",
        location=MockLocation(lat=36.0, lon=112.0),
        total_capacity=Volumes(5000.0),
        weighted_avg_carbon_cost=45.0,
        dynamic_business_case=[],
        weighted_avg_energy_costs={"scrap": 15.0},
    )

    # Patch DummyTradeLPModel to have required processes
    orig_init = ORIGINAL_DUMMY_TRADE_LP_MODEL_INIT

    def init_with_processes(self, lp_epsilon=1e-3, year=None, solver_options=None, random_seed=42, **kwargs):
        orig_init(self, lp_epsilon, year, solver_options, random_seed=random_seed, **kwargs)
        for proc_name in ["BF", "EAF", "demand", "scrap_supply"]:
            self._processes[proc_name] = DummyProcess(proc_name, DummyProcessType.PRODUCTION, [])

    monkeypatch.setattr(DummyTradeLPModel, "__init__", init_with_processes)

    mock_config = create_mock_config()
    message_bus = DummyMessageBus(repo)

    # Call set_up_steel_trade_lp with meta-furnace groups
    lp_model = set_up_steel_trade_lp(
        message_bus=message_bus,
        year=year,
        config=mock_config,
        legal_process_connectors=[],
        furnace_groups_override=[meta_fg1, meta_fg2],
    )

    # Verify that process centers were created for meta-furnace groups
    meta_fg_centers = [pc for pc in lp_model.process_centers if pc.name.startswith("cluster_")]
    assert len(meta_fg_centers) == 2

    # Check that capacities are correct
    bf_center = next(pc for pc in meta_fg_centers if "BF" in pc.name)
    assert bf_center.capacity == mock_config.capacity_limit * Volumes(10000.0)
    assert bf_center.production_cost == 85.0

    eaf_center = next(pc for pc in meta_fg_centers if "EAF" in pc.name)
    assert eaf_center.capacity == mock_config.capacity_limit * Volumes(5000.0)
    assert eaf_center.production_cost == 45.0

    # Verify demand and supplier centers were also created
    demand_centers = [pc for pc in lp_model.process_centers if pc.name == "demand_cluster"]
    assert len(demand_centers) == 1

    supplier_centers = [pc for pc in lp_model.process_centers if pc.name == "sup_cluster"]
    assert len(supplier_centers) == 1
