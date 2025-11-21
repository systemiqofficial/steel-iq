import pytest


from steelo.simulation_types import get_default_technology_settings

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import events, PointInTime, Year, TimeFrame, Volumes
from steelo.domain.models import CountryMappingService, CountryMapping
from steelo.simulation import Simulation
from steelo.economic_models import PlantAgentsModel


def get_furnace_group_with_negative_balance():
    """
    Create a furnace group with very negative historic_balance to trigger closure.
    Scaled to exceed MINIMUM_PRODUCTION_VOLUME_FOR_COST_CURVE (50k tpa).
    """
    # Use production=80000 to ensure it exceeds the 50k threshold
    # With utilization_rate=0.7, capacity = 80000/0.7 ≈ 114286t
    furnace_group = get_furnace_group(tech_name="EAF", fg_id="fg_to_close", production=80000)
    # Set a very negative historic balance to trigger closure
    # The threshold is: region_capex[tech_name] * capacity
    # With EAF capex ~400, capacity ~114286t, threshold = 400 * 114286 = 45,714,400
    # Set historic_balance much more negative to ensure closure
    furnace_group.historic_balance = -50000000.0  # -$50M to exceed the threshold
    return furnace_group


def get_plant_with_complementary_furnace_groups(tech_to_product, primary_furnace_group):
    """
    Create a plant with the primary furnace group and a complementary one to ensure
    both steel and iron products are available in the cost curve.

    The PlantAgentsModel expects both steel and iron prices to be extractable from
    the cost curve (see plant_agent.py:99-100), but a single furnace group only
    produces one product type. This function adds the missing product type to
    prevent IndexError when extracting prices from an empty cost curve list.
    """

    furnace_groups = [primary_furnace_group]

    # Add multiple complementary furnace groups to ensure both steel and iron remain
    # in cost curve even if some get closed. Make sure they have good financial health.
    current_product = tech_to_product.get(primary_furnace_group.technology.name, "steel")  # Default to steel
    if current_product == "steel":
        # Add many iron-producing furnace groups with allocated volumes and good balance
        for i in range(1, 6):  # Create 5 iron furnace groups
            iron_furnace = get_furnace_group(tech_name="BF", fg_id=f"iron_fg_{i}")
            iron_furnace.set_allocated_volumes(10.0)
            iron_furnace.historic_balance = 100000.0  # Good financial health
            furnace_groups.append(iron_furnace)
        # Add extra steel furnace groups too for balance
        for i in range(1, 4):  # Create 3 extra steel furnace groups
            steel_furnace = get_furnace_group(tech_name="EAF", fg_id=f"extra_steel_fg_{i}")
            steel_furnace.historic_balance = 100000.0  # Good financial health
            steel_furnace.status = "operating pre-retirement"  # Prevent economic evaluation
            furnace_groups.append(steel_furnace)
    elif current_product == "iron":
        # Add multiple steel-producing furnace groups with good balance
        steel_furnace1 = get_furnace_group(tech_name="EAF", fg_id="steel_fg_1")
        steel_furnace1.historic_balance = 100000.0  # Good financial health
        steel_furnace1.status = "operating pre-retirement"  # Prevent economic evaluation
        steel_furnace2 = get_furnace_group(tech_name="EAF", fg_id="steel_fg_2")
        steel_furnace2.historic_balance = 100000.0  # Good financial health
        steel_furnace2.status = "operating pre-retirement"  # Prevent economic evaluation
        steel_furnace3 = get_furnace_group(tech_name="EAF", fg_id="steel_fg_3")
        steel_furnace3.historic_balance = 100000.0  # Good financial health
        steel_furnace3.status = "operating pre-retirement"  # Prevent economic evaluation
        furnace_groups.extend([steel_furnace1, steel_furnace2, steel_furnace3])
        # Set allocated volumes for the iron-producing primary furnace group
        primary_furnace_group.set_allocated_volumes(10.0)

    return get_plant(furnace_groups=furnace_groups)


@pytest.fixture
def logged_events(bus):
    """Add a logging event handler for all events."""
    from steelo.domain import events

    logged_events = []

    def log_events(evt):
        logged_events.append(evt)

    # Ensure all event types have handlers, even if empty initially
    all_event_types = [
        events.FurnaceGroupClosed,
        events.FurnaceGroupTechChanged,
        events.FurnaceGroupRenovated,
        events.FurnaceGroupAdded,
        events.SinteringCapacityAdded,
        events.SteelAllocationsCalculated,
        events.IterationOver,
    ]

    for event_type in all_event_types:
        if event_type not in bus.event_handlers:
            bus.event_handlers[event_type] = []
        bus.event_handlers[event_type].append(log_events)

    return logged_events


@pytest.fixture
def full_simulation(bus):
    # Given a plant with a furnace group
    plant = get_plant(furnace_groups=[get_furnace_group()])
    bus.uow.plants.add(plant)

    # Bus initiate environment variables
    bus.env.generate_cost_curve([plant.furnace_groups[0]], lag=0)
    # For test hardcode the demand first
    bus.env.current_demand = 50

    # When the simulation is run
    Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()


def get_furnace_group_for_tech_change():
    """
    Create a furnace group configured to trigger a technology change.
    Ensures proper economic conditions for NPV calculation to succeed.
    """
    furnace_group = get_furnace_group(tech_name="EAF", fg_id="fg_to_change")
    # Set positive historic balance to avoid closure
    furnace_group.historic_balance = 50000.0
    return furnace_group


@pytest.mark.parametrize(
    "furnace_group, expected_events, expected_status",
    [
        # historic_balance below threshold -> close furnace group
        (get_furnace_group_with_negative_balance(), [events.FurnaceGroupClosed], "closed"),
        # technology not optimal -> change technology (EAF might switch to BOF)
        # No event is raised immediately - the status changes and the event will be raised in the future
        (get_furnace_group_for_tech_change(), [], "operating switching technology"),
        # end of life reached at good utilization rate -> renovate furnace group
        (
            get_furnace_group(
                lifetime=PointInTime(
                    current=Year(2030),
                    time_frame=TimeFrame(start=Year(2010), end=Year(2030)),
                    plant_lifetime=20,
                ),
                utilization_rate=0.5,
            ),
            [events.FurnaceGroupRenovated],
            "operating",
        ),
        # already optimal technology and end of life not reached -> keep operating
        (get_furnace_group(), [], "operating"),
    ],
)
def test_simulation_service_with_plant_agent_events(
    bus, logged_events, furnace_group, expected_events, expected_status, mocker
):
    # Set random seed for deterministic behavior (unique per furnace group)
    import random

    random.seed(hash(furnace_group.furnace_group_id) % (2**32))

    # Clear any plants from previous test cases (bus fixture is shared across parameterized tests)
    bus.uow.plants.data = {}
    bus.uow.plants.furnace_id_to_plant_id = {}

    # Initialize technology_to_product if not present
    if not hasattr(bus.env, "technology_to_product"):
        bus.env.technology_to_product = {
            "EAF": "steel",
            "BOF": "steel",
            "DRI": "iron",
            "BF": "iron",
        }
    tech_to_product = bus.env.technology_to_product
    # Given a plant with a furnace group and complementary furnace group for cost curve
    plant = get_plant_with_complementary_furnace_groups(tech_to_product, furnace_group)

    # mocker.patch.object(plant, "evaluate_expansion", return_value=None)

    bus.uow.plants.add(plant)

    # Ensure config is set for this test
    if bus.env.config is None:
        from steelo.simulation import SimulationConfig
        from pathlib import Path
        import tempfile

        # Create a temporary directory for test outputs
        temp_dir = tempfile.mkdtemp()
        bus.env.config = SimulationConfig(
            start_year=Year(
                2025,
                technology_settings=get_default_technology_settings(),
            ),
            end_year=Year(2030),
            master_excel_path=Path(temp_dir) / "master.xlsx",
            output_dir=Path(temp_dir) / "output",
            probabilistic_agents=False,  # Disable probabilistic decision making for deterministic test
        )
    else:
        # Override probabilistic_agents even if config already exists
        bus.env.config.probabilistic_agents = False

    # Bus initiate environment variables
    # Set up country mappings for test
    if not hasattr(bus.env, "country_mappings") or bus.env.country_mappings is None:
        mappings = [
            CountryMapping(
                country="Germany",
                iso2="DE",
                iso3="DEU",
                irena_name="Germany",
                region_for_outputs="Europe",
                ssp_region="EUR",
                gem_country="Germany",
                ws_region="Europe",
                tiam_ucl_region="Western Europe",
                eu_region="EU",
            ),
        ]
        bus.env.country_mappings = CountryMappingService(mappings)

    # Ensure 'default' key exists in capex to prevent KeyError and set favorable capex for BOF
    if not hasattr(bus.env, "name_to_capex") or not bus.env.name_to_capex:
        bus.env.name_to_capex = {
            "greenfield": {"Europe": {"EAF": 400.0, "BOF": 300.0, "BF": 500.0, "DRI": 450.0, "BF-BOF": 700.0}}
        }

    if not hasattr(bus.env, "capex_renovation_share"):
        bus.env.capex_renovation_share = {"EAF": 0.4, "BOF": 0.4}  # Set renovation capex share for EAF and BOF

    if "default" not in bus.env.name_to_capex:
        bus.env.name_to_capex["default"] = bus.env.name_to_capex["greenfield"].copy()

    # Make BOF much cheaper to build than EAF
    if "greenfield" in bus.env.name_to_capex:
        bus.env.name_to_capex["greenfield"]["Europe"]["BOF"] = 150.0  # Very low capex for BOF
        bus.env.name_to_capex["greenfield"]["Europe"]["EAF"] = 400.0  # High capex for EAF
        bus.env.name_to_capex["greenfield"]["Europe"]["BF"] = 500.0  # BF capex
        bus.env.name_to_capex["greenfield"]["Europe"]["DRI"] = 450.0  # DRI capex
        bus.env.name_to_capex["greenfield"]["Europe"]["BF-BOF"] = 700.0  # BF-BOF capex
        bus.env.name_to_capex["default"]["Europe"]["BOF"] = 150.0  # Also update default
        bus.env.name_to_capex["default"]["Europe"]["EAF"] = 400.0
        bus.env.name_to_capex["default"]["Europe"]["BF"] = 500.0
        bus.env.name_to_capex["default"]["Europe"]["DRI"] = 450.0
        bus.env.name_to_capex["default"]["Europe"]["BF-BOF"] = 700.0

    # Initialize missing dynamic_feedstocks to prevent KeyError
    technologies = ["BF", "BOF", "EAF", "DRI", "SR", "MoE", "ESF", "CCUS", "Prep Sinter", "BFBOF"]
    if not hasattr(bus.env, "dynamic_feedstocks") or not bus.env.dynamic_feedstocks:
        bus.env.dynamic_feedstocks = {tech: [] for tech in technologies}

    # Initialize cost of equity and debt for DEU
    if not hasattr(bus.env, "industrial_cost_of_equity") or not bus.env.industrial_cost_of_equity:
        bus.env.industrial_cost_of_equity = {"DEU": 0.08}
    if not hasattr(bus.env, "industrial_cost_of_debt") or not bus.env.industrial_cost_of_debt:
        bus.env.industrial_cost_of_debt = {"DEU": 0.04}

    # Set allowed transitions for all technologies (overwrite fixture defaults)
    bus.env.allowed_furnace_transitions["BFBOF"] = ["EAF", "BOF", "BFBOF"]  # Can switch to other steel techs
    bus.env.allowed_furnace_transitions["EAF"] = ["BOF", "EAF"]  # EAF can switch to BOF

    # Ensure allowed_techs is accessible (trigger property evaluation)
    allowed_techs_dict = bus.env.allowed_techs  # Access property to populate cache
    # Verify year 2025 has allowed technologies
    assert Year(2025) in allowed_techs_dict, f"Year 2025 not in allowed_techs: {list(allowed_techs_dict.keys())}"
    assert allowed_techs_dict[Year(2025)], f"No allowed techs for 2025: {allowed_techs_dict}"

    # Ensure positive plant balance for technology switches and renovations
    # With 64kt capacity and $400/t capex, renovation cost is about $10M (64000 * 400 * 0.4)
    plant.balance = 50000000.0  # $50M balance to afford technology switches and renovations

    # Initialize test environment with proper economic data for NPV calculations
    from steelo.domain.models import PrimaryFeedstock

    # Set up basic environment data using fallback material costs
    if not hasattr(bus.env, "fallback_material_costs") or not bus.env.fallback_material_costs:
        from steelo.domain.models import FallbackMaterialCost

        # Create fallback material costs for test
        bus.env.fallback_material_costs = [
            # EAF materials - expensive
            FallbackMaterialCost(
                iso3="DEU",
                technology="EAF",
                metric="material_cost",
                unit="$/t",
                costs_by_year={Year(2025): 400.0, Year(2030): 420.0},
            ),
            # BOF materials - cheaper
            FallbackMaterialCost(
                iso3="DEU",
                technology="BOF",
                metric="material_cost",
                unit="$/t",
                costs_by_year={Year(2025): 250.0, Year(2030): 260.0},
            ),
            # Other technologies
            FallbackMaterialCost(
                iso3="DEU",
                technology="BF",
                metric="material_cost",
                unit="$/t",
                costs_by_year={Year(2025): 300.0, Year(2030): 310.0},
            ),
            FallbackMaterialCost(
                iso3="DEU",
                technology="DRI",
                metric="material_cost",
                unit="$/t",
                costs_by_year={Year(2025): 350.0, Year(2030): 360.0},
            ),
        ]

    # Initialize default metallic charge per technology if not present
    if (
        not hasattr(bus.env, "default_metallic_charge_per_technology")
        or not bus.env.default_metallic_charge_per_technology
    ):
        bus.env.default_metallic_charge_per_technology = {
            "EAF": {"scrap": 0.8, "hbi_mid": 0.2},
            "BOF": {"hot_metal": 0.7, "scrap": 0.3},
            "BF": {"pellets_high": 0.5, "sinter_mid": 0.3, "io_mid": 0.2},
            "DRI": {"pellets_high": 0.6, "hbi_low": 0.4},
        }

    # Set up average material cost for backward compatibility
    if not hasattr(bus.env, "average_material_cost"):
        bus.env.average_material_cost = {
            "scrap": {"average_cost": 400.0},  # Expensive scrap (EAF uses lots of this)
            "hot_metal": {"average_cost": 200.0},  # Very cheap hot metal (BOF uses this)
            "hbi_mid": {"average_cost": 450.0},  # Very expensive hbi_mid (EAF uses this)
            "pellets_high": {"average_cost": 80.0},
            "sinter_mid": {"average_cost": 70.0},
            "io_mid": {"average_cost": 60.0},
            "hbi_low": {"average_cost": 320.0},
        }

    # Convert fallback material costs to avg_boms format for backward compatibility
    bus.env.avg_boms = {}
    for tech in ["EAF", "BOF", "BF", "DRI"]:
        if tech in bus.env.default_metallic_charge_per_technology:
            bus.env.avg_boms[tech] = {}
            for material, share in bus.env.default_metallic_charge_per_technology[tech].items():
                bus.env.avg_boms[tech][material] = {
                    "demand_share_pct": share * 100,  # Convert to percentage
                    "unit_cost": bus.env.average_material_cost.get(material.lower(), {}).get("average_cost", 100.0),
                }

    bus.env.avg_utilization = {
        "EAF": {"utilization_rate": 0.8},
        "BOF": {"utilization_rate": 0.7},
    }  # Make BOF less utilized

    # Create dynamic feedstocks that match the hardcoded BOM materials
    # This ensures get_bom_from_avg_boms finds input_effectiveness and builds proper BOM structure
    if not hasattr(bus.env, "dynamic_feedstocks") or bus.env.dynamic_feedstocks.get("EAF") == []:
        # EAF feedstock - make it expensive and energy-intensive
        eaf_scrap = PrimaryFeedstock(metallic_charge="scrap", reductant="electricity", technology="EAF")
        eaf_scrap.energy_requirements = {"Electricity": 600.0}  # Very high electricity usage
        eaf_scrap.required_quantity_per_ton_of_product = 1.2  # Less efficient material usage
        eaf_scrap.emissions = {eaf_scrap.name: {"scope1": {"scrap": {"scope1": 0.5}, "electricity": {"scope1": 2.5}}}}

        eaf_hbi = PrimaryFeedstock(metallic_charge="hbi_mid", reductant="electricity", technology="EAF")
        eaf_hbi.energy_requirements = {"Electricity": 500.0}  # High electricity usage
        eaf_hbi.required_quantity_per_ton_of_product = 1.15  # Less efficient
        eaf_hbi.emissions = {eaf_hbi.name: {"scope1": {"hbi_mid": {"scope1": 0.4}, "electricity": {"scope1": 2.0}}}}

        # BOF feedstock - make it very cost-effective and efficient
        bof_hot_metal = PrimaryFeedstock(metallic_charge="hot_metal", reductant="oxygen", technology="BOF")
        bof_hot_metal.energy_requirements = {"Electricity": 25.0, "Natural Gas": 10.0}  # Very low energy requirements
        bof_hot_metal.required_quantity_per_ton_of_product = 0.9  # Very efficient
        bof_hot_metal.emissions = {
            bof_hot_metal.name: {
                "scope1": {
                    "hot_metal": {"scope1": 0.4},
                    "electricity": {"scope1": 0.1},
                    "natural_gas": {"scope1": 0.05},
                }
            }
        }

        bof_scrap = PrimaryFeedstock(metallic_charge="scrap", reductant="oxygen", technology="BOF")
        bof_scrap.energy_requirements = {"Electricity": 15.0}  # Very low electricity usage
        bof_scrap.required_quantity_per_ton_of_product = 0.95  # Efficient
        bof_scrap.emissions = {bof_scrap.name: {"scope1": {"scrap": {"scope1": 0.1}, "electricity": {"scope1": 0.05}}}}

        # Update dynamic_feedstocks to match materials in avg_boms
        bus.env.dynamic_feedstocks.update(
            {
                "EAF": [eaf_scrap, eaf_hbi],
                "BOF": [bof_hot_metal, bof_scrap],
            }
        )

    # Initialize virgin_iron_demand for PlantAgentsModel
    from steelo.domain.models import VirginIronDemand

    bus.env.virgin_iron_demand = VirginIronDemand(
        world_suppliers=[], steel_demand_dict={}, dynamic_feedstocks=bus.env.dynamic_feedstocks
    )

    bus.env.generate_cost_curve(plant.furnace_groups, lag=0)
    # For test hardcode the demand first
    bus.env.current_demand = 50

    # For technology change test, ensure the NPV calculation favors BOF over EAF
    # This needs to happen both for FurnaceGroupTechChanged event AND for "operating switching technology" status
    if expected_events == [events.FurnaceGroupTechChanged] or expected_status == "operating switching technology":
        # Mock return value that clearly favors switching to BOF
        # CRITICAL: EAF must have NEGATIVE NPV to ensure it's never selected by random.choices()
        # If EAF has positive NPV, random.choices() might select it, causing renovation path instead of switch
        mock_return_value = (
            {"EAF": -50000.0, "BOF": 500000.0},  # EAF negative (non-viable), BOF positive (viable)
            {"EAF": 400, "BOF": 150},  # BOF has lower capex
            1000.0,  # Cost of stranding asset
            {
                "EAF": {
                    "materials": {"scrap": {"demand": 1.0, "total_cost": 1.0, "unit_cost": 1.0, "product_volume": 1.0}},
                    "energy": {},
                },
                "BOF": {
                    "materials": {
                        "hot_metal": {"demand": 1.0, "total_cost": 1.0, "unit_cost": 1.0, "product_volume": 1.0}
                    },
                    "energy": {},
                },
            },  # BOM dictionaries
        )

        # Mock ALL EAF furnaces to ensure complete test isolation
        # The target furnace gets the real mock, others get a dummy return to prevent interference
        target_found = False
        for fg in plant.furnace_groups:
            if fg.technology.name == "EAF":
                if fg.furnace_group_id == furnace_group.furnace_group_id:
                    # Target furnace: use the real mock that favors BOF
                    mocker.patch.object(fg, "optimal_technology_name", return_value=mock_return_value)
                    target_found = True
                else:
                    # Extra EAF furnaces: mock to return empty/invalid result
                    # (belt & suspenders with pre-retirement status)
                    dummy_return = ({}, {}, None, {})
                    mocker.patch.object(fg, "optimal_technology_name", return_value=dummy_return)
        assert target_found, (
            f"Target furnace {furnace_group.furnace_group_id} not found in plant! "
            f"IDs: {[fg.furnace_group_id for fg in plant.furnace_groups]}"
        )

    # For renovation test, ensure the NPV calculation makes the current technology optimal
    elif expected_events == [events.FurnaceGroupRenovated]:
        # Mock return value that favors keeping the current EAF technology
        mock_return_value_renovation = (
            {"EAF": 300000.0},  # Positive NPV for current tech only
            {"EAF": 400},  # Capex for renovation
            0.0,  # No cost of stranding asset (same tech)
            {
                "EAF": {
                    "materials": {"scrap": {"demand": 1.0, "total_cost": 1.0, "unit_cost": 1.0, "product_volume": 1.0}},
                    "energy": {},
                }
            },  # BOM dictionaries
        )

        mocker.patch.object(furnace_group, "optimal_technology_name", return_value=mock_return_value_renovation)

    # For the "already optimal" test case (empty expected_events and status="operating")
    # Mock to ensure current technology (EAF) remains optimal
    elif expected_events == [] and expected_status == "operating":
        # Mock return value that keeps current EAF technology optimal
        mock_return_value_stay = (
            {"EAF": 300000.0},  # Positive NPV for current tech
            {"EAF": 400},  # Capex
            0.0,  # No cost of stranding asset
            {
                "EAF": {
                    "materials": {"scrap": {"demand": 1.0, "total_cost": 1.0, "unit_cost": 1.0, "product_volume": 1.0}},
                    "energy": {},
                }
            },  # BOM dictionaries
        )

        mocker.patch.object(furnace_group, "optimal_technology_name", return_value=mock_return_value_stay)

    # When the simulation is run
    Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()

    # Then the furnace group should have the expected status
    assert furnace_group.status == expected_status
    # And the expected events should have been raised
    raised_event_classes = [type(evt) for evt in logged_events]
    assert raised_event_classes == expected_events


def test_simulation_service_more_demand_then_production(bus):
    # Initialize technology_to_product if not present
    if not hasattr(bus.env, "technology_to_product"):
        bus.env.technology_to_product = {
            "EAF": "steel",
            "BOF": "steel",
            "DRI": "iron",
            "BF": "iron",
        }
    # Given a plant with a furnace group and demand higher than production
    production, demand = 100, 150
    furnace_group = get_furnace_group(capacity=Volumes(production))
    # Add minimal iron furnace group to prevent IndexError, but keep it small
    iron_furnace = get_furnace_group(tech_name="BF", fg_id="iron_fg", capacity=Volumes(10))
    plant = get_plant(furnace_groups=[furnace_group, iron_furnace])
    bus.uow.plants.add(plant)

    # Ensure config is set for this test
    if bus.env.config is None:
        from steelo.simulation import SimulationConfig
        from pathlib import Path
        import tempfile

        # Create a temporary directory for test outputs
        temp_dir = tempfile.mkdtemp()
        bus.env.config = SimulationConfig(
            start_year=Year(
                2025,
                technology_settings=get_default_technology_settings(),
            ),
            end_year=Year(2030),
            master_excel_path=Path(temp_dir) / "master.xlsx",
            output_dir=Path(temp_dir) / "output",
            probabilistic_agents=False,  # Disable probabilistic decision making for deterministic test
        )
    else:
        # Override probabilistic_agents even if config already exists
        bus.env.config.probabilistic_agents = False

    # Bus initiate environment variables - add missing environment data
    # Set up country mappings for test
    if not hasattr(bus.env, "country_mappings") or bus.env.country_mappings is None:
        mappings = [
            CountryMapping(
                country="Germany",
                iso2="DE",
                iso3="DEU",
                irena_name="Germany",
                region_for_outputs="Europe",
                ssp_region="EUR",
                gem_country="Germany",
                ws_region="Europe",
                tiam_ucl_region="Western Europe",
                eu_region="EU",
            ),
        ]
        bus.env.country_mappings = CountryMappingService(mappings)

    # Ensure 'default' key exists in capex to prevent KeyError
    if not hasattr(bus.env, "name_to_capex") or not bus.env.name_to_capex:
        bus.env.name_to_capex = {
            "greenfield": {"Europe": {"BF": 200.0, "EAF": 400.0, "BOF": 300.0, "DRI": 450.0, "BF-BOF": 700.0}}
        }
    if "default" not in bus.env.name_to_capex:
        bus.env.name_to_capex["default"] = bus.env.name_to_capex["greenfield"].copy()

    # Initialize capex_renovation_share if not present
    if not hasattr(bus.env, "capex_renovation_share"):
        bus.env.capex_renovation_share = {
            "EAF": 0.4,
            "BOF": 0.4,
            "DRI": 0.4,
            "BF": 0.4,
        }

    # Initialize missing dynamic_feedstocks to prevent KeyError
    technologies = ["BF", "BOF", "EAF", "DRI", "SR", "MoE", "ESF", "CCUS", "Prep Sinter", "BFBOF"]
    if not hasattr(bus.env, "dynamic_feedstocks") or not bus.env.dynamic_feedstocks:
        bus.env.dynamic_feedstocks = {tech: [] for tech in technologies}

    # Initialize cost of equity and debt for DEU
    if not hasattr(bus.env, "industrial_cost_of_equity") or not bus.env.industrial_cost_of_equity:
        bus.env.industrial_cost_of_equity = {"DEU": 0.08}
    if not hasattr(bus.env, "industrial_cost_of_debt") or not bus.env.industrial_cost_of_debt:
        bus.env.industrial_cost_of_debt = {"DEU": 0.04}

    # Initialize virgin_iron_demand for PlantAgentsModel
    from steelo.domain.models import VirginIronDemand

    bus.env.virgin_iron_demand = VirginIronDemand(
        world_suppliers=[], steel_demand_dict={}, dynamic_feedstocks=bus.env.dynamic_feedstocks
    )

    bus.env.generate_cost_curve(plant.furnace_groups, lag=0)  # Generate cost curve for all furnace groups
    bus.env.current_demand = demand

    # When the simulation is run it should complete successfully
    # The method under test here is: Environment.extract_price_from_costcurve
    # Current behavior: logs warning but doesn't raise exception when demand exceeds production
    Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()
    # Test passes if no exception is raised


@pytest.fixture
def multi_furnace_groups():
    """
    Create multiple furnace groups for integration testing.
    All production values scaled to exceed MINIMUM_PRODUCTION_VOLUME_FOR_COST_CURVE (50k tpa).
    """
    return [
        # utilization_rate below threshold -> close furnace group
        # Scale production to 90k to exceed threshold (default 45k is below 50k)
        get_furnace_group(utilization_rate=0.5, fg_id="fg_group_1", production=90000),
        # technology not optimal -> change technology
        # Scale production to exceed MINIMUM_PRODUCTION_VOLUME_FOR_COST_CURVE (50k tpa)
        get_furnace_group(tech_name="BF", fg_id="fg_group_2", production=80000),
        # end of life reached at good utilization rate -> renovate furnace group
        # Scale production to 64k to exceed threshold
        get_furnace_group(
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(20010), end=Year(2025)),
                plant_lifetime=20,
            ),
            fg_id="fg_group_3",
            production=64000,
        ),
        # Normal operating furnace group - scale to 70k
        get_furnace_group(fg_id="fg_group_4", production=70000),
        # Add iron-producing furnace groups to prevent IndexError in cost curve
        # Scale to 60k and 55k respectively
        get_furnace_group(tech_name="BF", fg_id="iron_fg_1", production=60000),
        get_furnace_group(tech_name="DRI", fg_id="iron_fg_2", production=55000),
    ]


def test_simulation_service_with_multiple_plant_furnaces(bus, logged_events, multi_furnace_groups, mocker):
    # Initialize technology_to_product if not present
    if not hasattr(bus.env, "technology_to_product"):
        bus.env.technology_to_product = {
            "EAF": "steel",
            "BOF": "steel",
            "DRI": "iron",
            "BF": "iron",
        }
    # Given a plant with mutiple furnace groups
    plant = get_plant(furnace_groups=multi_furnace_groups)
    # Set positive balance to afford renovations and technology switches
    plant.balance = 50000000.0  # $50M balance
    bus.uow.plants.add(plant)

    # Ensure config is set for this test
    if bus.env.config is None:
        from steelo.simulation import SimulationConfig
        from pathlib import Path
        import tempfile

        # Create a temporary directory for test outputs
        temp_dir = tempfile.mkdtemp()
        bus.env.config = SimulationConfig(
            start_year=Year(
                2025,
                technology_settings=get_default_technology_settings(),
            ),
            end_year=Year(2030),
            master_excel_path=Path(temp_dir) / "master.xlsx",
            output_dir=Path(temp_dir) / "output",
            probabilistic_agents=False,  # Disable probabilistic decision making for deterministic test
        )
    else:
        # Override probabilistic_agents even if config already exists
        bus.env.config.probabilistic_agents = False

    # Bus initiate environment variables - add missing environment data
    # Set up country mappings for test
    if not hasattr(bus.env, "country_mappings") or bus.env.country_mappings is None:
        mappings = [
            CountryMapping(
                country="Germany",
                iso2="DE",
                iso3="DEU",
                irena_name="Germany",
                region_for_outputs="Europe",
                ssp_region="EUR",
                gem_country="Germany",
                ws_region="Europe",
                tiam_ucl_region="Western Europe",
                eu_region="EU",
            ),
        ]
        bus.env.country_mappings = CountryMappingService(mappings)

    # Ensure 'default' key exists in capex to prevent KeyError
    if not hasattr(bus.env, "name_to_capex") or not bus.env.name_to_capex:
        bus.env.name_to_capex = {"greenfield": {"Europe": {"EAF": 400.0, "BOF": 300.0, "BF": 500.0, "DRI": 450.0}}}

    if not hasattr(bus.env, "capex_renovation_share"):
        bus.env.capex_renovation_share = {
            "EAF": 0.4,
            "BOF": 0.4,
            "BF": 0.4,
            "DRI": 0.4,
        }  # Set renovation capex share for all technologies

    if "default" not in bus.env.name_to_capex:
        bus.env.name_to_capex["default"] = bus.env.name_to_capex["greenfield"].copy()

    # Initialize missing dynamic_feedstocks to prevent KeyError
    technologies = ["BF", "BOF", "EAF", "DRI", "SR", "MoE", "ESF", "CCUS", "Prep Sinter", "BFBOF"]
    if not hasattr(bus.env, "dynamic_feedstocks") or not bus.env.dynamic_feedstocks:
        bus.env.dynamic_feedstocks = {tech: [] for tech in technologies}

    # Set allowed transitions for all technologies (overwrite fixture defaults)
    bus.env.allowed_furnace_transitions["BFBOF"] = ["EAF", "BOF", "BFBOF"]  # Can switch to other steel techs
    bus.env.allowed_furnace_transitions["EAF"] = ["BOF", "EAF"]  # EAF can switch to BOF

    # Initialize industrial_cost_of_debt for DEU
    if not hasattr(bus.env, "industrial_cost_of_debt") or not bus.env.industrial_cost_of_debt:
        bus.env.industrial_cost_of_debt = {"DEU": 0.05}

    # Initialize industrial_cost_of_equity for DEU
    if not hasattr(bus.env, "industrial_cost_of_equity") or not bus.env.industrial_cost_of_equity:
        bus.env.industrial_cost_of_equity = {"DEU": 0.08}

    # Initialize virgin_iron_demand for PlantAgentsModel
    from steelo.domain.models import VirginIronDemand

    bus.env.virgin_iron_demand = VirginIronDemand(
        world_suppliers=[], steel_demand_dict={}, dynamic_feedstocks=bus.env.dynamic_feedstocks
    )

    bus.env.generate_cost_curve(multi_furnace_groups, lag=0)
    # For test hardcode the demand first
    bus.env.current_demand = 150

    # Mock optimal_technology_name for all EAF furnace groups to prevent COSA calculation issues
    # This test is focused on the simulation running successfully with multiple furnaces
    for fg in multi_furnace_groups:
        if fg.technology.name == "EAF":
            # Return a mock that keeps the current technology optimal
            mock_return = (
                {fg.technology.name: 300000.0},  # Positive NPV for current tech
                {fg.technology.name: 400},  # Capex
                0.0,  # No COSA
                {fg.technology.name: {}},  # BOM
            )
            mocker.patch.object(fg, "optimal_technology_name", return_value=mock_return)

    # When the simulation is run
    Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()

    # Test that all furnace groups remain operating - the simulation should run successfully
    # (The specific business logic for technology changes and renovations is tested in the parametrized test)
    for fg in multi_furnace_groups:
        assert fg.status == "operating"

    # Test passes if simulation completes without errors
