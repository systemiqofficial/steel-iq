from types import SimpleNamespace

from steelo.adapters.repositories import InMemoryRepository
from steelo.domain import Location, Supplier, Volumes, Year
from steelo.domain.models import LegalProcessConnector
from steelo.domain.trade_modelling.process_network_validator import validate_process_network_connectivity
from steelo.simulation_types import get_default_technology_settings


def test_connector_to_disabled_technology_is_skipped():
    """Ensure validators ignore connectors targeting technologies disabled in the config."""
    repository = InMemoryRepository()
    supplier = Supplier(
        supplier_id="sup_io_mid",
        location=Location(lat=0.0, lon=0.0, country="Testland", region="Test", iso3="TST"),
        commodity="io_mid",
        capacity_by_year={Year(2025): Volumes(100.0)},
        production_cost=42.0,
    )
    repository.suppliers.add(supplier)

    connectors = [
        LegalProcessConnector(
            from_technology_name="io_mid_supply",
            to_technology_name="BF+CCU",
        )
    ]

    config = SimpleNamespace(
        primary_products=["steel", "iron"],
        technology_settings=get_default_technology_settings(),
        start_year=Year(2025),
    )

    results = validate_process_network_connectivity(
        repository=repository,
        legal_process_connectors=connectors,
        config=config,
        current_year=2025,
        verbose=False,
    )

    assert results["invalid_connectors"] == []
