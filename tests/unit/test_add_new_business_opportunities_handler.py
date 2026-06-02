"""
Tests for ``add_new_business_opportunities_to_repository``.

Verifies that runtime-born plants are routed into per-country ``indi_<ISO3>``
groups at birth (not into the master ``"indi"`` group), that
``plant.parent_gem_id`` is updated to match the chosen group, and that
``PlantGroup.generate_new_plant`` no longer appends the plant to its own
``plants`` list (sole registration happens via the handler).
"""

from steelo.adapters.repositories.in_memory_repository import InMemoryRepository
from steelo.domain import commands
from steelo.domain.models import Location, Plant, PlantGroup
from steelo.service_layer.handlers import add_new_business_opportunities_to_repository
from steelo.service_layer.unit_of_work import UnitOfWork


def _make_plant(plant_id: str, iso3: str) -> Plant:
    """Minimal Plant carrying the placeholder ``parent_gem_id="indi"`` set by generate_new_plant."""
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


def _make_uow_with_master_indi() -> UnitOfWork:
    """UoW seeded with an empty master ``indi`` plant group (matches boot-time state)."""
    uow = UnitOfWork(InMemoryRepository())
    uow.plant_groups.add(PlantGroup(plant_group_id="indi", plants=[]))
    return uow


def test_new_plant_lands_in_per_country_indi_group():
    """
    Handler routes a runtime-born plant into ``indi_<ISO3>`` at birth and
    leaves the master ``"indi"`` group empty.
    """
    uow = _make_uow_with_master_indi()
    plant = _make_plant("P1", iso3="CHN")
    cmd = commands.AddNewBusinessOpportunities(new_plants=[plant])

    add_new_business_opportunities_to_repository(cmd, uow)

    assert plant in uow.plant_groups.get("indi_CHN").plants
    assert plant not in uow.plant_groups.get("indi").plants
    assert uow.plant_groups.get("indi").plants == []


def test_parent_gem_id_matches_repo_reverse_lookup():
    """
    After routing, ``plant.parent_gem_id`` is overwritten to ``indi_<ISO3>``
    and the repository reverse-lookup resolves to the same group.
    """
    uow = _make_uow_with_master_indi()
    plant = _make_plant("P1", iso3="AUS")
    cmd = commands.AddNewBusinessOpportunities(new_plants=[plant])

    add_new_business_opportunities_to_repository(cmd, uow)

    assert plant.parent_gem_id == "indi_AUS"
    assert uow.plant_groups.get_by_plant_id("P1").plant_group_id == "indi_AUS"


def test_multiple_iso3_each_routes_to_own_group():
    """
    Plants across different ISO3 countries each land in the correct
    per-country group in a single handler invocation.
    """
    uow = _make_uow_with_master_indi()
    plant_chn = _make_plant("P1", iso3="CHN")
    plant_aus = _make_plant("P2", iso3="AUS")
    plant_sau = _make_plant("P3", iso3="SAU")
    cmd = commands.AddNewBusinessOpportunities(new_plants=[plant_chn, plant_aus, plant_sau])

    add_new_business_opportunities_to_repository(cmd, uow)

    assert uow.plant_groups.get("indi_CHN").plants == [plant_chn]
    assert uow.plant_groups.get("indi_AUS").plants == [plant_aus]
    assert uow.plant_groups.get("indi_SAU").plants == [plant_sau]
    assert uow.plant_groups.get("indi").plants == []


def test_second_plant_in_same_iso3_reuses_group_and_stays_in_seen():
    """
    A second plant sharing an ISO3 with an earlier registration goes into
    the existing per-country group; ``seen`` still contains the group so
    the UoW sees it as changed for this turn.
    """
    uow = _make_uow_with_master_indi()
    plant_a = _make_plant("P1", iso3="CHN")
    plant_b = _make_plant("P2", iso3="CHN")
    cmd = commands.AddNewBusinessOpportunities(new_plants=[plant_a, plant_b])

    add_new_business_opportunities_to_repository(cmd, uow)

    chn_group = uow.plant_groups.get("indi_CHN")
    assert chn_group.plants == [plant_a, plant_b]
    assert chn_group in uow.plant_groups.seen


def test_generate_new_plant_does_not_append_to_group_plants():
    """
    ``PlantGroup.generate_new_plant`` no longer mutates the group's
    ``plants`` list — registration is the handler's responsibility.
    Source-level regression check: the body must not contain
    ``self.plants.append``, otherwise runtime plants would be double-
    registered (once inside the factory, once via the handler).
    """
    import inspect

    source = inspect.getsource(PlantGroup.generate_new_plant)
    assert "self.plants.append" not in source
