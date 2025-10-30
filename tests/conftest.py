import pytest

from datetime import date

from steelo.bootstrap import bootstrap
from steelo.domain import Year
from steelo.domain.models import (
    Plant,
    Technology,
    FurnaceGroup,
    Location,
    ProductCategory,
    PointInTime,
    TimeFrame,
    DemandCenter,
)
from steelo.adapters.repositories import InMemoryRepository
from steelo.simulation_types import TechnologySettings, get_default_technology_settings, TechSettingsMap
from dataclasses import replace

# Global variables import removed - using models.py instead


# get_default_technology_settings is now imported from simulation_types


@pytest.fixture
def default_technology_settings() -> TechSettingsMap:
    """Get default technology settings using production loader."""
    return get_default_technology_settings()


@pytest.fixture
def make_technology_settings(default_technology_settings):
    """Factory fixture for creating technology settings with overrides.

    Returns a function that accepts overrides as dict[str, TechnologySettings | dict]
    and applies them using dataclasses.replace for clean partial updates.

    Usage:
        def test_something(make_technology_settings):
            # Override specific technologies with TechnologySettings objects
            tech_settings = make_technology_settings({
                'BF': TechnologySettings(allowed=False, from_year=2030, to_year=2040),
                'DRIH2': TechnologySettings(allowed=True, from_year=2028, to_year=None)
            })

            # Or use dict syntax for convenience
            tech_settings = make_technology_settings({
                'BF': {'allowed': False, 'from_year': 2030},
                'DRIH2': {'from_year': 2028}  # Only override specific fields
            })
    """

    def _make_settings(overrides: dict[str, TechnologySettings | dict] = None) -> TechSettingsMap:
        # Start with production defaults
        settings = default_technology_settings.copy()

        if overrides:
            for tech_code, override in overrides.items():
                if tech_code in settings:
                    current = settings[tech_code]
                    if isinstance(override, TechnologySettings):
                        # Direct replacement
                        settings[tech_code] = override
                    elif isinstance(override, dict):
                        # Partial update using dataclasses.replace
                        settings[tech_code] = replace(current, **override)
                    else:
                        raise ValueError(
                            f"Override for {tech_code} must be TechnologySettings or dict, got {type(override)}"
                        )
                else:
                    # Add new technology
                    if isinstance(override, TechnologySettings):
                        settings[tech_code] = override
                    elif isinstance(override, dict):
                        # Create new with defaults + overrides
                        defaults = {"allowed": True, "from_year": 2025, "to_year": None}
                        defaults.update(override)
                        settings[tech_code] = TechnologySettings(**defaults)
                    else:
                        raise ValueError(
                            f"Override for {tech_code} must be TechnologySettings or dict, got {type(override)}"
                        )

        return settings

    return _make_settings


@pytest.fixture
def technology_form_converter():
    """Factory for converting technology settings to Django form data.

    Returns a function that converts normalized technology codes and settings
    to the form field format expected by Django views.

    Usage:
        def test_form_submission(technology_form_converter, make_technology_settings):
            tech_settings = make_technology_settings({'BF': {'allowed': False}})
            form_data = technology_form_converter(tech_settings)
            # form_data contains: {'tech_BF_from_year': '2025', 'tech_BF_to_year': '', ...}
            # Note: no tech_BF_allowed field since BF is disabled
    """

    def _convert_to_form_data(tech_settings: TechSettingsMap) -> dict[str, str]:
        """Convert TechSettingsMap to Django form POST data format."""
        form_data = {}

        # Production mapping from normalized codes to form field slugs
        # This should ideally come from the production code, but for now inline it
        code_to_slug = {
            "BF": "BF",
            "BFBOF": "BFBOF",
            "BOF": "BOF",
            "DRING": "DRING",
            "DRINGEAF": "DRINGEAF",
            "DRIH2": "DRIH2",
            "DRIH2EAF": "DRIH2EAF",
            "EAF": "EAF",
            "ESF": "ESF",
            "ESFEAF": "ESFEAF",
            "MOE": "MOE",
            "DRI": "DRI",
        }

        for code, settings in tech_settings.items():
            slug = code_to_slug.get(code, code)

            # Add allowed checkbox if True (absence = disabled)
            if settings.allowed:
                form_data[f"tech_{slug}_allowed"] = "on"

            # Always add from_year (required field)
            form_data[f"tech_{slug}_from_year"] = str(settings.from_year)

            # Add to_year if present
            form_data[f"tech_{slug}_to_year"] = str(settings.to_year) if settings.to_year else ""

        return form_data

    return _convert_to_form_data


def pytest_addoption(parser):
    parser.addoption(
        "--run-wind-and-pv-tests",
        action="store_true",
        default=False,
        help="Run tests for the wind_and_pv package only if this flag is set",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "wind_and_pv: wind_and_pv: Marks special tests that are only executed when --run-wind-and-pv-tests is set",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-wind-and-pv-tests"):
        skip_marker = pytest.mark.skip(reason="--run-wind-and-pv-tests not set")
        for item in items:
            if "wind_and_pv" in item.keywords:
                item.add_marker(skip_marker)


# Removed preserve_iso3_to_region fixture as we're migrating to dynamic country mappings


@pytest.fixture
def location_data():
    return {
        "iso3": "DEU",
        "country": "Germany",
        "region": "Europe",
        "lat": 49.40768,
        "lon": 8.69079,
        "distance_to_other_iso3": {"DEU": 0, "FRA": 500, "RUS": 2000},
    }


@pytest.fixture
def second_location_data():
    return {
        "iso3": "FRA",
        "country": "France",
        "region": "Europe",
        "lat": 46.603354,
        "lon": 1.888334,
        "distance_to_other_iso3": {"FRA": 0, "DEU": 500, "RUS": 2500},
    }


@pytest.fixture
def third_location_data():
    return {
        "iso3": "RUS",
        "country": "Russia",
        "region": "Europe",
        "lat": 61.52401,
        "lon": 105.318756,
        "distance_to_other_iso3": {"RUS": 0, "DEU": 2000, "FRA": 2500},
    }


@pytest.fixture
def technology_data():
    return {
        "name": "BF",
        "energy_consumption": 1.0,
        "bill_of_materials": {},
        "product": "steel",
    }


@pytest.fixture
def lifetime_data():
    return {
        "current": Year(2026),
        "time_frame": {"start": Year(2025), "end": Year(2050)},
    }


@pytest.fixture
def furnace_group_data(technology_data, lifetime_data):
    return {
        "furnace_group_id": "1",
        "capacity": 1,
        "status": "operating",
        "last_renovation_date": date(2015, 5, 4),
        "technology": technology_data,
        "historical_production": {2023: 1},
        "utilization_rate": 0.7,
        "lifetime": lifetime_data,
    }


@pytest.fixture
def furnace_group(furnace_group_data):
    return FurnaceGroup(**furnace_group_data)


@pytest.fixture
def second_furnace_group(second_furnace_group_data):
    return FurnaceGroup(**second_furnace_group_data)


@pytest.fixture
def plant_data(location_data, furnace_group_data):
    return {
        "plant_id": "1",
        "location": location_data,
        "furnace_groups": [furnace_group_data],
        "power_source": "coal",
        "soe_status": "yes",
        "parent_gem_id": "2",
        "workforce_size": 200,
        "certified": True,
        "category_steel_product": {ProductCategory("Flat")},
        "technology_unit_fopex": {"BF": 80.0, "BOF": 65.0, "EAF": 100.0, "Other": 70.0},  # Sample fopex data
    }


@pytest.fixture
def second_furnace_group_data(technology_data, lifetime_data):
    return {
        "furnace_group_id": "2",
        "capacity": 3,
        "status": "operating",
        "last_renovation_date": date(2015, 5, 4),
        "technology": technology_data,
        "historical_production": {2023: 1},
        "utilization_rate": 0.7,
        "lifetime": lifetime_data,
    }


@pytest.fixture
def second_plant_data(third_location_data, second_furnace_group_data):
    return {
        "plant_id": "2",
        "location": third_location_data,
        "furnace_groups": [second_furnace_group_data],
        "power_source": "coal",
        "soe_status": "yes",
        "parent_gem_id": "2",
        "workforce_size": 200,
        "certified": True,
        "category_steel_product": {ProductCategory("Flat")},
        "technology_unit_fopex": {"BF": 80.0, "BOF": 65.0, "EAF": 100.0, "Other": 70.0},  # Sample fopex data
    }


@pytest.fixture
def second_plant(second_plant_data):
    second_plant_data["location"] = Location(**second_plant_data["location"])

    furnace_groups = []
    furnace_group_data = second_plant_data["furnace_groups"]
    for fg in furnace_group_data:
        fg["technology"] = Technology(**fg["technology"])
        fg["lifetime"] = PointInTime(
            current=fg["lifetime"]["current"],
            time_frame=TimeFrame(**fg["lifetime"]["time_frame"]),
            plant_lifetime=20,
        )
        furnace_groups.append(FurnaceGroup(**fg))

    second_plant_data["furnace_groups"] = furnace_groups
    return Plant(**second_plant_data)


@pytest.fixture
def demand_center_data(second_location_data):
    return {
        "demand_center_id": "dc_1",
        "center_of_gravity": second_location_data,
        "demand_by_year": {2023: 2},
    }


@pytest.fixture
def demand_center(demand_center_data):
    demand_center_data["center_of_gravity"] = Location(**demand_center_data["center_of_gravity"])
    return DemandCenter(**demand_center_data)


@pytest.fixture
def repository_data(plant, second_plant, demand_center):
    return {
        "plants": [plant, second_plant],
        "demand_centers": [demand_center],
    }


@pytest.fixture
def repository_for_trade(repository_data):
    test_repo = InMemoryRepository()
    test_repo.plants.add_list(repository_data["plants"])
    test_repo.furnace_groups.add_list([fg for plant in repository_data["plants"] for fg in plant.furnace_groups])
    test_repo.demand_centers.add_list(repository_data["demand_centers"])
    return test_repo


@pytest.fixture
def plant(plant_data):
    plant_data["location"] = Location(**plant_data["location"])

    furnace_groups = []
    furnace_group_data = plant_data["furnace_groups"]
    for fg in furnace_group_data:
        fg["technology"] = Technology(**fg["technology"])
        fg["lifetime"] = PointInTime(
            current=fg["lifetime"]["current"],
            time_frame=TimeFrame(**fg["lifetime"]["time_frame"]),
            plant_lifetime=20,
        )
        furnace_groups.append(FurnaceGroup(**fg))

    plant_data["furnace_groups"] = furnace_groups
    return Plant(**plant_data)


@pytest.fixture
def bus(tmp_path):
    from steelo.domain.models import Environment
    from steelo.domain import Year
    from steelo.simulation import SimulationConfig

    # Create a minimal simulation config with required parameters
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path=tmp_path / "master.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),  # Use production defaults
    )

    # Create a minimal environment with simulation config for testing
    env = Environment(config=config)

    # Initialize technology_to_product mapping for tests
    env.technology_to_product = {
        "EAF": "steel",
        "BOF": "steel",
        "DRI": "iron",
        "BF": "iron",
    }

    # Bootstrap with the environment that has a simulation config
    return bootstrap(env=env)


@pytest.fixture
def ready_data_preparation(db, tmp_path):
    """Create a ready DataPreparation for tests that need it."""
    from steeloweb.models import DataPreparation, DataPackage
    import json

    # Create a test data directory with correct structure for technologies.json
    data_dir = tmp_path / "test_data_prep"
    data_dir.mkdir()

    # Create the expected directory structure: data/fixtures/
    fixtures_dir = data_dir / "data" / "fixtures"
    fixtures_dir.mkdir(parents=True)

    # Create a minimal technologies.json with complete structure including product_type
    technologies = {
        "schema_version": 3,
        "source": {"type": "test", "location": "test_fixture"},
        "technologies": {
            "BF": {
                "code": "BF",
                "slug": "bf",
                "normalized_code": "BF",
                "display_name": "Blast Furnace",
                "product_type": "iron",
                "allowed": True,
                "from_year": 2025,
                "to_year": None,
            },
            "BFBOF": {
                "code": "BFBOF",
                "slug": "bfbof",
                "normalized_code": "BFBOF",
                "display_name": "BFBOF",
                "product_type": "steel",
                "allowed": True,
                "from_year": 2025,
                "to_year": None,
            },
            "BOF": {
                "code": "BOF",
                "slug": "bof",
                "normalized_code": "BOF",
                "display_name": "Basic Oxygen Furnace",
                "product_type": "steel",
                "allowed": True,
                "from_year": 2025,
                "to_year": None,
            },
            "DRING": {
                "code": "DRING",
                "slug": "dring",
                "normalized_code": "DRING",
                "display_name": "DRI Natural Gas",
                "product_type": "iron",
                "allowed": True,
                "from_year": 2025,
                "to_year": None,
            },
            "DRINGEAF": {
                "code": "DRINGEAF",
                "slug": "dringeaf",
                "normalized_code": "DRINGEAF",
                "display_name": "DRINGEAF",
                "product_type": "steel",
                "allowed": True,
                "from_year": 2025,
                "to_year": None,
            },
            "DRIH2": {
                "code": "DRIH2",
                "slug": "drih2",
                "normalized_code": "DRIH2",
                "display_name": "DRI Hydrogen",
                "product_type": "iron",
                "allowed": True,
                "from_year": 2025,
                "to_year": None,
            },
            "DRIH2EAF": {
                "code": "DRIH2EAF",
                "slug": "drih2eaf",
                "normalized_code": "DRIH2EAF",
                "display_name": "DRIH2EAF",
                "product_type": "steel",
                "allowed": True,
                "from_year": 2025,
                "to_year": None,
            },
            "EAF": {
                "code": "EAF",
                "slug": "eaf",
                "normalized_code": "EAF",
                "display_name": "Electric Arc Furnace",
                "product_type": "steel",
                "allowed": True,
                "from_year": 2025,
                "to_year": None,
            },
            "ESF": {
                "code": "ESF",
                "slug": "esf",
                "normalized_code": "ESF",
                "display_name": "ESF",
                "product_type": "iron",
                "allowed": False,
                "from_year": 2025,
                "to_year": None,
            },
            "ESFEAF": {
                "code": "ESFEAF",
                "slug": "esfeaf",
                "normalized_code": "ESFEAF",
                "display_name": "ESFEAF",
                "product_type": "steel",
                "allowed": False,
                "from_year": 2025,
                "to_year": None,
            },
            "MOE": {
                "code": "MOE",
                "slug": "moe",
                "normalized_code": "MOE",
                "display_name": "MOE",
                "product_type": "steel",
                "allowed": False,
                "from_year": 2025,
                "to_year": None,
            },
            "DRI": {
                "code": "DRI",
                "slug": "dri",
                "normalized_code": "DRI",
                "display_name": "Direct Reduced Iron",
                "product_type": "iron",
                "allowed": True,
                "from_year": 2025,
                "to_year": None,
            },
        },
    }

    tech_file = fixtures_dir / "technologies.json"
    tech_file.write_text(json.dumps(technologies, indent=2))

    # Create required data packages
    core_package, _ = DataPackage.objects.get_or_create(
        name=DataPackage.PackageType.CORE_DATA,
        defaults={
            "version": "1.0.0",
            "source_type": DataPackage.SourceType.LOCAL,
            "source_url": "",
        },
    )
    geo_package, _ = DataPackage.objects.get_or_create(
        name=DataPackage.PackageType.GEO_DATA,
        defaults={
            "version": "1.0.0",
            "source_type": DataPackage.SourceType.LOCAL,
            "source_url": "",
        },
    )

    return DataPreparation.objects.create(
        name="Test Data Preparation",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory=str(data_dir),
    )
