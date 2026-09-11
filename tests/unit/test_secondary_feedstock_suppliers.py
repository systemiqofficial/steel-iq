from types import SimpleNamespace

from steelo.adapters.repositories.in_memory_repository import InMemoryRepository
from steelo.domain import Volumes
from steelo.domain.trade_modelling.set_up_steel_trade_lp import set_up_steel_trade_lp
from steelo.service_layer.message_bus import MessageBus
from steelo.service_layer.unit_of_work import UnitOfWork
from steelo.simulation import SimulationConfig


def _make_message_bus(repository: InMemoryRepository) -> MessageBus:
    env_stub = SimpleNamespace(
        average_commodity_price_per_region={},
        carbon_border_mechanisms=[],
        country_mappings=None,
    )
    return MessageBus(uow=UnitOfWork(repository), env=env_stub, event_handlers={}, command_handlers={})


def test_secondary_feedstock_constraints_create_virtual_supplier(tmp_path):
    repository = InMemoryRepository()
    config = SimulationConfig.for_testing(repository=repository, output_dir=tmp_path)
    message_bus = _make_message_bus(repository)

    constraints = {"bio_pci": {("USA",): 150.0}}

    set_up_steel_trade_lp(
        message_bus=message_bus,
        year=config.start_year,
        config=config,
        legal_process_connectors=[],
        active_trade_tariffs=None,
        secondary_feedstock_constraints=constraints,
        aggregated_metallic_charge_constraints=None,
        transport_kpis=None,
    )

    supplier = repository.suppliers.get("bio_pci_supply_process_center")
    assert supplier.commodity == "bio_pci"
    assert supplier.capacity_by_year[config.start_year] == Volumes(150.0)


def test_secondary_feedstock_supply_centre_registered_once(tmp_path):
    """The synthetic supply process and centre for a constrained feedstock appear once in the LP model."""
    repository = InMemoryRepository()
    config = SimulationConfig.for_testing(repository=repository, output_dir=tmp_path)
    message_bus = _make_message_bus(repository)

    lp_model = set_up_steel_trade_lp(
        message_bus=message_bus,
        year=config.start_year,
        config=config,
        legal_process_connectors=[],
        active_trade_tariffs=None,
        secondary_feedstock_constraints={"bio_pci": {("USA",): 150.0, ("AUS",): 50.0}},
        aggregated_metallic_charge_constraints=None,
        transport_kpis=None,
    )

    supply_processes = [process for process in lp_model.processes if process.name == "bio_pci_supply"]
    supply_centres = [centre for centre in lp_model.process_centers if centre.name == "bio_pci_supply_process_center"]
    assert len(supply_processes) == 1
    assert len(supply_centres) == 1
    assert supply_centres[0].capacity == Volumes(200.0)
