"""
Tests for ``PlantGroupRepository.register_plant_in_group``.

Covers get-or-create group, append plant, reverse-map update, unconditional
``seen`` membership, reverse-lookup for runtime-born plants, and a smoke test
against the JSON-backed repository for protocol symmetry.
"""

import tempfile
from pathlib import Path

from steelo.adapters.repositories.in_memory_repository import (
    PlantGroupInMemoryRepository,
)
from steelo.adapters.repositories.json_repository import PlantGroupJsonRepository
from steelo.domain.models import Location, Plant, PlantGroup


def _make_plant(plant_id: str, iso3: str = "CHN") -> Plant:
    """Create a minimal Plant suitable for plant-group registration tests."""
    return Plant(
        plant_id=plant_id,
        location=Location(lat=0.0, lon=0.0, country=iso3, region="unknown", iso3=iso3),
        furnace_groups=[],
        power_source="grid",
        soe_status="private",
        parent_gem_id="indi",
        workforce_size=500,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={},
    )


def test_register_plant_in_existing_group_appends_and_updates_reverse_map():
    """
    Registering into an already-present group appends the plant, updates the
    reverse map, and adds the group to ``seen``.
    """
    repo = PlantGroupInMemoryRepository()
    group = PlantGroup(plant_group_id="indi_CHN", plants=[])
    repo.add(group)
    repo.seen.clear()
    plant = _make_plant("P1", iso3="CHN")

    repo.register_plant_in_group(plant, "indi_CHN")

    assert plant in repo.get("indi_CHN").plants
    assert repo.plant_id_to_plantgroup_id["P1"] == "indi_CHN"
    assert group in repo.seen


def test_register_plant_creates_group_on_demand():
    """
    Registering a plant into a group that does not yet exist creates the
    group, places the plant in it, and adds it to ``seen``.
    """
    repo = PlantGroupInMemoryRepository()
    plant = _make_plant("P1", iso3="AUS")

    repo.register_plant_in_group(plant, "indi_AUS")

    created = repo.get("indi_AUS")
    assert created.plant_group_id == "indi_AUS"
    assert created.plants == [plant]
    assert repo.plant_id_to_plantgroup_id["P1"] == "indi_AUS"
    assert created in repo.seen


def test_register_plant_re_register_still_adds_group_to_seen():
    """
    Dirty-set invariant: re-registering after ``seen`` has been cleared
    still marks the group as changed in the current UoW turn.
    """
    repo = PlantGroupInMemoryRepository()
    plant_a = _make_plant("P1", iso3="CHN")
    plant_b = _make_plant("P2", iso3="CHN")
    repo.register_plant_in_group(plant_a, "indi_CHN")
    repo.seen.clear()

    repo.register_plant_in_group(plant_b, "indi_CHN")

    group = repo.get("indi_CHN")
    assert plant_b in group.plants
    assert group in repo.seen


def test_reverse_lookup_works_for_runtime_born_plant():
    """
    Regression test for Issue 1 in indi_issue.md: ``get_by_plant_id`` must
    resolve for plants that were added via ``register_plant_in_group``
    (not via the bulk ``add`` path).
    """
    repo = PlantGroupInMemoryRepository()
    plant = _make_plant("P1", iso3="SAU")

    repo.register_plant_in_group(plant, "indi_SAU")

    resolved = repo.get_by_plant_id("P1")
    assert resolved.plant_group_id == "indi_SAU"


def test_json_repository_register_plant_round_trip():
    """
    Protocol symmetry smoke test: the JSON-backed repository persists a
    newly-registered plant and mirrors the in-memory ``seen`` semantics.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "plant_groups.json"
        repo = PlantGroupJsonRepository(path, plant_lifetime=20)
        plant = _make_plant("P1", iso3="AUS")

        repo.register_plant_in_group(plant, "indi_AUS")

        stored = repo.list()
        assert any(pg.plant_group_id == "indi_AUS" for pg in stored)
        stored_aus = next(pg for pg in stored if pg.plant_group_id == "indi_AUS")
        assert len(stored_aus.plants) == 1
        assert stored_aus.plants[0].plant_id == "P1"
        assert any(pg.plant_group_id == "indi_AUS" for pg in repo.seen)
