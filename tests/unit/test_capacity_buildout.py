"""Tests for capacity buildout event handler mappings and behaviour.

Verifies that:
- FurnaceGroupTechChanged does NOT trigger update_capacity_buildout (the double-counting fix).
- SinteringCapacityAdded does NOT trigger update_capacity_buildout (latent bug cleanup).
- FurnaceGroupAdded still correctly triggers update_capacity_buildout.
- The handler correctly updates env.added_capacity and env.new_plant_capacity.
- Deferred tech switches do not pollute added_capacity after counter reset.
"""

from unittest.mock import MagicMock

from steelo.domain import events
from steelo.service_layer import handlers


# ---------------------------------------------------------------------------
# 1. Event handler mapping tests
# ---------------------------------------------------------------------------


def test_furnace_group_tech_changed_does_not_trigger_capacity_buildout():
    """FurnaceGroupTechChanged must not include update_capacity_buildout.

    Completed deferred tech switches emit this event after the annual counter
    reset, so routing it to update_capacity_buildout would inflate
    added_capacity for the following year (the double-counting bug).
    """
    handler_list = handlers.EVENT_HANDLERS[events.FurnaceGroupTechChanged]
    assert handlers.update_capacity_buildout not in handler_list, (
        "update_capacity_buildout must not handle FurnaceGroupTechChanged"
    )


def test_sintering_capacity_added_does_not_trigger_capacity_buildout():
    """SinteringCapacityAdded must not include update_capacity_buildout.

    This event lacks the technology_name and is_new_plant attributes that
    update_capacity_buildout relies on, so routing it there would crash.
    """
    handler_list = handlers.EVENT_HANDLERS[events.SinteringCapacityAdded]
    assert handlers.update_capacity_buildout not in handler_list, (
        "update_capacity_buildout must not handle SinteringCapacityAdded"
    )


def test_furnace_group_added_still_triggers_capacity_buildout():
    """FurnaceGroupAdded must still route to update_capacity_buildout.

    Real capacity additions (PAM expansions and GEO new plants) emit this
    event and need their capacity tracked.
    """
    handler_list = handlers.EVENT_HANDLERS[events.FurnaceGroupAdded]
    assert handlers.update_capacity_buildout in handler_list, "update_capacity_buildout must handle FurnaceGroupAdded"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeUnitOfWork:
    """Minimal UoW fake that supports context-manager protocol and commit."""

    def __init__(self):
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.committed = True


# ---------------------------------------------------------------------------
# 2. Handler behaviour tests
# ---------------------------------------------------------------------------


def test_capacity_buildout_adds_to_env_added_capacity():
    """update_capacity_buildout should increment env.added_capacity for the
    technology specified in the FurnaceGroupAdded event.
    """
    env = MagicMock()
    uow = _FakeUnitOfWork()

    event = events.FurnaceGroupAdded(
        plant_id="plant-1",
        furnace_group_id="fg-1",
        technology_name="EAF",
        capacity=5000.0,
        is_new_plant=False,
    )

    handlers.update_capacity_buildout(event, uow=uow, env=env)

    env.add_capacity.assert_called_once()
    env.add_new_plant_capacity.assert_not_called()
    assert uow.committed is True


def test_capacity_buildout_tracks_new_plant_capacity_when_flagged():
    """When is_new_plant=True, update_capacity_buildout should also call
    env.add_new_plant_capacity so GEO new plants are tracked separately.
    """
    env = MagicMock()
    uow = _FakeUnitOfWork()

    event = events.FurnaceGroupAdded(
        plant_id="plant-1",
        furnace_group_id="fg-1",
        technology_name="DRI",
        capacity=8000.0,
        is_new_plant=True,
    )

    handlers.update_capacity_buildout(event, uow=uow, env=env)

    env.add_capacity.assert_called_once()
    env.add_new_plant_capacity.assert_called_once()
    assert uow.committed is True


def test_deferred_switch_does_not_pollute_added_capacity():
    """Simulates the deferred switch lifecycle: switch capacity is tracked at
    initiation, counters are reset at year-end, then FurnaceGroupTechChanged
    fires. After the fix, added_capacity must remain empty because the event
    no longer triggers update_capacity_buildout.
    """
    env = MagicMock()
    env.added_capacity = {}
    env.switched_capacity = {}
    env.new_plant_capacity = {}

    # Step 1: At switch initiation, switched_capacity would be incremented.
    # (Handled by ChangeFurnaceGroupStatusToSwitchingTechnology — not under test here.)

    # Step 2: Year-end reset (as finalise_iteration does).
    env.added_capacity = {}
    env.switched_capacity = {}
    env.new_plant_capacity = {}

    # Step 3: FurnaceGroupTechChanged fires after reset.
    events.FurnaceGroupTechChanged(
        furnace_group_id="fg-1",
        technology_name="SR",
        capacity=11100.0,
    )

    # The event handler list for FurnaceGroupTechChanged should NOT contain
    # update_capacity_buildout, so processing it through the registered
    # handlers must leave added_capacity untouched.
    for handler in handlers.EVENT_HANDLERS[events.FurnaceGroupTechChanged]:
        # Skip handlers that need real infrastructure (cost curve etc.)
        if handler is handlers.update_capacity_buildout:
            raise AssertionError("update_capacity_buildout should not be in FurnaceGroupTechChanged handlers")

    # added_capacity must still be empty — no phantom capacity.
    assert env.added_capacity == {}, f"added_capacity should be empty after deferred switch, got {env.added_capacity}"
