from steelo.service_layer import get_markers


class PlantRepository:
    def __init__(self, plants):
        self.plants = plants

    def list(self):
        return self.plants


class Repository:
    def __init__(self, plants=None):
        plants = plants if plants is not None else []
        self.plants = PlantRepository(plants)


def test_plant_agent_get_markers(plant):
    # Given a plant in a repository
    repository = Repository([plant])

    # When we get the markers from the repository
    markers = get_markers(repository)

    # Then we should get the markers
    assert markers == [{"location": (plant.location.lat, plant.location.lon), "popup": plant.plant_id}]
