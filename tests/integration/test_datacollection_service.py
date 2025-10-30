import pytest

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import events, PointInTime, Year, TimeFrame, PlantGroup
from steelo.domain.datacollector import DataCollector


@pytest.fixture
def multi_furnace_groups():
    return [
        # utilization_rate below threshold -> close furnace group
        get_furnace_group(utilization_rate=0.5, fg_id="fg_group_1"),
        # technology not optimal -> change technology
        get_furnace_group(tech_name="BF", fg_id="fg_group_2", production=80),
        # end of life reached at good utilization rate -> renovate furnace group
        get_furnace_group(
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2010), end=Year(2025)),
                plant_lifetime=20,
            ),
            fg_id="fg_group_3",
        ),
        get_furnace_group(fg_id="fg_group_4"),
    ]


def test_simulation_service_with_multiple_plant_furnaces(bus, multi_furnace_groups):
    import tempfile
    from pathlib import Path

    # Given a plant with multiple furnace groups
    plant = get_plant(furnace_groups=multi_furnace_groups)
    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant])

    bus.uow.plants.add(plant)
    bus.uow.plant_groups.add(plant_group)

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        data_collector = DataCollector(
            world_plant_groups=bus.uow.plant_groups.list(), env=bus.env, output_dir=output_dir
        )
        data_collector.attach_to_bus(bus)

        # Test the DataCollector's event logging functionality by directly sending events
        # This tests the integration between the message bus and DataCollector without
        # needing the complex PlantAgentsModel setup

        # When events are sent through the bus
        bus.handle(
            events.FurnaceGroupTechChanged(furnace_group_id="fg_group_2", technology_name="DRI-EAF", capacity=100)
        )
        bus.handle(events.FurnaceGroupRenovated(furnace_group_id="fg_group_3"))

        # Then the DataCollector should have logged these events
        assert len(data_collector.logged_events) == 2
        assert [type(evt) for evt in data_collector.logged_events] == [
            events.FurnaceGroupTechChanged,
            events.FurnaceGroupRenovated,
        ]

        # When we collect the events into trace_decisions
        data_collector.trace_decisions[0] = data_collector.collect_events()

        # Then logged_events should be cleared and trace_decisions should be populated
        assert data_collector.logged_events == []
        assert data_collector.trace_decisions == {
            0: {"fg_group_2": events.FurnaceGroupTechChanged, "fg_group_3": events.FurnaceGroupRenovated}
        }

        # When we send another event for the next time step
        bus.handle(events.FurnaceGroupClosed(furnace_group_id="fg_group_1"))

        # Then it should be logged
        assert len(data_collector.logged_events) == 1
        assert [type(evt) for evt in data_collector.logged_events] == [events.FurnaceGroupClosed]

        # When we collect again
        data_collector.trace_decisions[1] = data_collector.collect_events()

        # Then we should have both time steps recorded
        assert data_collector.trace_decisions == {
            0: {"fg_group_2": events.FurnaceGroupTechChanged, "fg_group_3": events.FurnaceGroupRenovated},
            1: {"fg_group_1": events.FurnaceGroupClosed},
        }
