import pytest

from steelo.adapters.repositories import JsonRepository


@pytest.fixture
def repository(tmp_path):
    plants_path = tmp_path / "plants.json"
    demand_centers_path = tmp_path / "demand_centers.json"
    suppliers_path = tmp_path / "suppliers.json"
    plant_groups_path = tmp_path / "plant_groups.json"
    trade_tariffs_path = tmp_path / "trade_tariffs.json"
    subsidies_path = tmp_path / "subsidies.json"
    input_costs_path = tmp_path / "input_costs.json"
    primary_feedstocks_path = tmp_path / "primary_feedstocks.json"
    carbon_costs_path = tmp_path / "carbon_costs.json"
    region_emissivity_path = (
        tmp_path / "region_emissivity.json"
    )  # FIXME: Uncomment when grid emissivity when grid emissivity is included @Marcus - final model
    capex_path = tmp_path / "capex.json"
    cost_of_capital_path = tmp_path / "cost_of_capital.json"
    legal_process_connectors_path = tmp_path / "legal_process_connectors.json"
    country_mappings_path = tmp_path / "country_mappings.json"
    hydrogen_efficiency_path = tmp_path / "hydrogen_efficiency.json"
    hydrogen_capex_opex_path = tmp_path / "hydrogen_capex_opex.json"
    railway_costs_path = tmp_path / "railway_costs.json"
    biomass_availability_path = tmp_path / "biomass_availability.json"
    transport_emissions_path = tmp_path / "transport_emissions.json"

    return JsonRepository(
        plant_lifetime=20,
        plants_path=plants_path,
        demand_centers_path=demand_centers_path,
        suppliers_path=suppliers_path,
        plant_groups_path=plant_groups_path,
        trade_tariffs_path=trade_tariffs_path,
        subsidies_path=subsidies_path,
        input_costs_path=input_costs_path,
        primary_feedstocks_path=primary_feedstocks_path,
        carbon_costs_path=carbon_costs_path,
        region_emissivity_path=region_emissivity_path,
        capex_path=capex_path,
        cost_of_capital_path=cost_of_capital_path,
        legal_process_connectors_path=legal_process_connectors_path,
        country_mappings_path=country_mappings_path,
        hydrogen_efficiency_path=hydrogen_efficiency_path,
        hydrogen_capex_opex_path=hydrogen_capex_opex_path,
        railway_costs_path=railway_costs_path,
        transport_emissions_path=transport_emissions_path,
        biomass_availability_path=biomass_availability_path,
    )


def test_json_repository_plant(repository, plant):
    # Given a JSON repository in a temporary directory and a plant
    #
    # When a plant agent is added to the repository
    repository.plants.add(plant)

    # Then the plant is in the repository when we get it by giving the path to a new repository
    new_repository = JsonRepository(
        plant_lifetime=20,
        plants_path=repository.plants.path,
        demand_centers_path=repository.demand_centers.path,
        suppliers_path=repository.suppliers.path,
        plant_groups_path=repository.plant_groups.path,
        trade_tariffs_path=repository.trade_tariffs.path,
        subsidies_path=repository.subsidies.path,
        input_costs_path=repository.input_costs.path,
        primary_feedstocks_path=repository.primary_feedstocks.path,
        carbon_costs_path=repository.carbon_costs.path,
        region_emissivity_path=repository.region_emissivity.path,
        capex_path=repository.capex.path,
        cost_of_capital_path=repository.cost_of_capital.path,
        legal_process_connectors_path=repository.legal_process_connectors.path,
        country_mappings_path=repository.country_mappings.path,
        hydrogen_efficiency_path=repository.hydrogen_efficiency.path,
        hydrogen_capex_opex_path=repository.hydrogen_capex_opex.path,
        railway_costs_path=repository.railway_costs.path,
        transport_emissions_path=repository.transport_emissions.path,
        biomass_availability_path=repository.biomass_availability.path,
    )
    plant_from_repo = new_repository.plants.get(plant.plant_id)
    assert plant_from_repo.plant_id == plant.plant_id

    [plant_from_repo] = new_repository.plants.list()
    assert plant_from_repo.plant_id == plant.plant_id


def test_json_repository_plant_list(repository, plant):
    # Given a JSON repository in a temporary directory and a list of plants
    plants = [plant]

    # When a list of plants is added to the repository
    repository.plants.add_list(plants)

    # Then the plants are in the repository when we get it by giving the path to a new repository
    new_repository = JsonRepository(
        plant_lifetime=20,
        plants_path=repository.plants.path,
        demand_centers_path=repository.demand_centers.path,
        suppliers_path=repository.suppliers.path,
        plant_groups_path=repository.plant_groups.path,
        trade_tariffs_path=repository.trade_tariffs.path,
        subsidies_path=repository.subsidies.path,
        input_costs_path=repository.input_costs.path,
        primary_feedstocks_path=repository.primary_feedstocks.path,
        carbon_costs_path=repository.carbon_costs.path,
        capex_path=repository.capex.path,
        cost_of_capital_path=repository.cost_of_capital.path,
        region_emissivity_path=repository.region_emissivity.path,
        legal_process_connectors_path=repository.legal_process_connectors.path,
        country_mappings_path=repository.country_mappings.path,
        hydrogen_efficiency_path=repository.hydrogen_efficiency.path,
        hydrogen_capex_opex_path=repository.hydrogen_capex_opex.path,
        railway_costs_path=repository.railway_costs.path,
        transport_emissions_path=repository.transport_emissions.path,
        biomass_availability_path=repository.biomass_availability.path,
    )
    [plant_from_repo] = new_repository.plants.list()
    assert plant_from_repo.plant_id == plant.plant_id


# -------------------------------------------- Test TransportKPIJsonRepository --------------------------------------------
def test_transport_emissions_json_repository_creation(tmp_path):
    """Test creating a TransportKPIJsonRepository"""
    from steelo.adapters.repositories.json_repository import TransportKPIJsonRepository

    path = tmp_path / "transport_emissions.json"
    repo = TransportKPIJsonRepository(path)

    assert repo.path == path
    assert repo.list() == []  # Check behavior, not internal state
    assert not path.exists()  # File not created until save


def test_transport_emissions_add_and_list(tmp_path):
    """Test adding and listing transport emissions"""
    from steelo.adapters.repositories.json_repository import TransportKPIJsonRepository
    from steelo.domain.models import TransportKPI

    path = tmp_path / "transport_emissions.json"
    repo = TransportKPIJsonRepository(path)

    emissions = [
        TransportKPI(
            reporter_iso="USA",
            partner_iso="CHN",
            commodity="iron ore",
            ghg_factor=0.025,
            transportation_cost=28.0,
            updated_on="2024-01-01",
        ),
        TransportKPI(
            reporter_iso="DEU",
            partner_iso="FRA",
            commodity="steel",
            ghg_factor=0.015,
            transportation_cost=42.0,
            updated_on="2024-01-02",
        ),
    ]

    repo.add_list(emissions)

    assert len(repo.list()) == 2
    assert path.exists()

    # Check that data was saved
    retrieved = repo.list()
    assert retrieved[0].reporter_iso == "USA"
    assert retrieved[1].commodity == "steel"


def test_transport_emissions_get_as_dict(tmp_path):
    """Test converting to dictionary format"""
    from steelo.adapters.repositories.json_repository import TransportKPIJsonRepository
    from steelo.domain.models import TransportKPI

    path = tmp_path / "transport_emissions.json"
    repo = TransportKPIJsonRepository(path)

    emissions = [
        TransportKPI(
            reporter_iso="USA",
            partner_iso="CHN",
            commodity="iron ore",
            ghg_factor=0.025,
            transportation_cost=28.0,
            updated_on="2024-01-01",
        ),
        TransportKPI(
            reporter_iso="DEU",
            partner_iso="FRA",
            commodity="steel",
            ghg_factor=0.015,
            transportation_cost=42.0,
            updated_on="2024-01-02",
        ),
    ]

    repo.add_list(emissions)

    dict_format = repo.get_as_dict()

    assert ("USA", "CHN", "iron ore") in dict_format
    assert dict_format[("USA", "CHN", "iron ore")] == 0.025
    assert ("DEU", "FRA", "steel") in dict_format
    assert dict_format[("DEU", "FRA", "steel")] == 0.015


def test_transport_emissions_save_and_load(tmp_path):
    """Test saving and loading from JSON file"""
    from steelo.adapters.repositories.json_repository import TransportKPIJsonRepository
    from steelo.domain.models import TransportKPI

    path = tmp_path / "transport_emissions.json"
    repo1 = TransportKPIJsonRepository(path)

    emissions = [
        TransportKPI(
            reporter_iso="USA",
            partner_iso="CHN",
            commodity="iron ore",
            ghg_factor=0.025,
            transportation_cost=28.0,
            updated_on="2024-01-01",
        )
    ]

    repo1.add_list(emissions)

    # Create new repository instance and load from file
    repo2 = TransportKPIJsonRepository(path)

    loaded = repo2.list()
    assert len(loaded) == 1
    assert loaded[0].reporter_iso == "USA"
    assert loaded[0].ghg_factor == 0.025


def test_transport_emissions_pydantic_model():
    """Test TransportKPIInDb Pydantic model"""
    from steelo.adapters.repositories.json_repository import TransportKPIInDb
    from steelo.domain.models import TransportKPI

    # Test from_domain
    emission = TransportKPI(
        reporter_iso="USA",
        partner_iso="CHN",
        commodity="iron ore",
        ghg_factor=0.025,
        transportation_cost=42.0,
        updated_on="2024-01-01",
    )

    db_model = TransportKPIInDb.from_domain(emission)
    assert db_model.reporter_iso == "USA"
    assert db_model.ghg_factor == 0.025

    # Test to_domain
    domain_obj = db_model.to_domain()
    assert isinstance(domain_obj, TransportKPI)
    assert domain_obj.reporter_iso == "USA"
    assert domain_obj.commodity == "iron ore"

    # Test model_dump
    data = db_model.model_dump()
    assert data["reporter_iso"] == "USA"
    assert data["ghg_factor"] == 0.025


# -------------------------------------------- Test SupplierJsonRepository --------------------------------------------
def test_supplier_repository_preserves_all_unique_suppliers(tmp_path):
    """Test that repository doesn't overwrite suppliers with different IDs."""
    from steelo.adapters.repositories.json_repository import SupplierJsonRepository
    from steelo.domain.models import Location, Supplier, Volumes, Year

    path = tmp_path / "suppliers.json"
    repo = SupplierJsonRepository(path)

    # Create multiple suppliers with unique IDs
    suppliers = []
    for i in range(5):
        location = Location(
            lat=-25.0 + i,
            lon=133.0 + i,
            country="Australia",
            region="Australia",
            iso3="AUS",
        )
        supplier = Supplier(
            supplier_id=f"supplier_{i}",
            location=location,
            commodity="io_low",
            capacity_by_year={Year(2024): Volumes(100_000_000)},
            production_cost_by_year={Year(2024): 50.0 + i},
            mine_cost_by_year={},
            mine_price_by_year={},
        )
        suppliers.append(supplier)

    # Add all suppliers to repository
    repo.add_list(suppliers)

    # Assert: All suppliers are preserved
    saved_suppliers = repo.list()
    assert len(saved_suppliers) == 5, f"Expected 5 suppliers, got {len(saved_suppliers)}"

    # Check all IDs are present
    saved_ids = {s.supplier_id for s in saved_suppliers}
    expected_ids = {f"supplier_{i}" for i in range(5)}
    assert saved_ids == expected_ids, f"Missing suppliers: {expected_ids - saved_ids}"


def test_supplier_repository_fails_on_duplicate_ids(tmp_path):
    """Test that repository fails when trying to add suppliers with duplicate IDs."""
    from steelo.adapters.repositories.json_repository import SupplierJsonRepository
    from steelo.domain.models import Location, Supplier, Volumes, Year

    path = tmp_path / "suppliers.json"
    repo = SupplierJsonRepository(path)

    location1 = Location(
        lat=-25.0,
        lon=133.0,
        country="Australia",
        region="Australia",
        iso3="AUS",
    )
    location2 = Location(
        lat=-26.0,
        lon=134.0,
        country="Australia",
        region="Australia",
        iso3="AUS",
    )

    # Create two suppliers with the SAME ID but different data
    supplier1 = Supplier(
        supplier_id="Australia_IO_low",  # Same ID
        location=location1,
        commodity="io_low",
        capacity_by_year={Year(2024): Volumes(100_000_000)},
        production_cost_by_year={Year(2024): 50.0},
        mine_cost_by_year={},
        mine_price_by_year={},
    )
    supplier2 = Supplier(
        supplier_id="Australia_IO_low",  # Same ID
        location=location2,
        commodity="io_low",
        capacity_by_year={Year(2024): Volumes(200_000_000)},
        production_cost_by_year={Year(2024): 45.0},
        mine_cost_by_year={},
        mine_price_by_year={},
    )

    # This should raise an error once we implement duplicate detection
    with pytest.raises(ValueError, match="Duplicate supplier_id"):
        repo.add_list([supplier1, supplier2])
