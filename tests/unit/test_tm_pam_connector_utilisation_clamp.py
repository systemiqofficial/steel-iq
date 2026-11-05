"""Tests for the utilisation clamp in TM_PAM_connector.update_furnace_group_utilisation.

The LP should never allocate above capacity, but if it does (solver anomaly), the
resulting utilisation must be clamped to 1.0 with a warning rather than propagating
a >1 expectation into avg_utilization and greenfield NPVs.
"""

import logging
from contextlib import contextmanager

from steelo.adapters.repositories.in_memory_repository import PlantInMemoryRepository
from steelo.devdata import get_furnace_group, get_plant
from steelo.domain.trade_modelling import TM_PAM_connector as connector_module
from steelo.domain.trade_modelling.TM_PAM_connector import TM_PAM_connector


def _connector_with(fg):
    plants_repo = PlantInMemoryRepository()
    plants_repo.add(get_plant(plant_id="plant_clamp", furnace_groups=[fg]))
    connector = TM_PAM_connector(dynamic_feedstocks_classes={}, plants=plants_repo)
    # The allocation is set directly on the FG; skip the graph-edge extraction
    connector.update_exported_volumes = lambda furnace_groups, volume_attribute="volume": None
    return connector


@contextmanager
def _capture_warnings():
    """Capture records on the function logger itself.

    A handler attached directly to the logger is immune to the propagate/level
    reconfiguration other tests apply to the steelo logger tree, which defeats caplog.
    """
    logger = logging.getLogger(f"{connector_module.__name__}.update_furnace_group_utilisation")
    records: list[logging.LogRecord] = []
    handler = logging.Handler(level=logging.WARNING)
    handler.emit = records.append  # type: ignore[method-assign]
    old_level, old_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def test_over_capacity_allocation_is_clamped_to_one():
    """An allocation above capacity yields utilisation 1.0 and logs a warning."""
    fg = get_furnace_group(fg_id="fg_clamp", tech_name="EAF", capacity=100)
    fg.allocated_volumes = 130.0

    connector = _connector_with(fg)
    with _capture_warnings() as records:
        connector.update_furnace_group_utilisation([fg])

    assert fg.utilization_rate == 1.0
    assert any("clamping to 1.0" in record.getMessage() for record in records)


def test_within_capacity_allocation_is_not_clamped():
    """A normal allocation keeps its exact ratio and logs no warning."""
    fg = get_furnace_group(fg_id="fg_no_clamp", tech_name="EAF", capacity=100)
    fg.allocated_volumes = 80.0

    connector = _connector_with(fg)
    with _capture_warnings() as records:
        connector.update_furnace_group_utilisation([fg])

    assert fg.utilization_rate == 0.8
    assert not any("clamping" in record.getMessage() for record in records)
