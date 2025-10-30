import json
import tempfile
from pathlib import Path
import pytest

from steelo.bootstrap import bootstrap
from steelo.service_layer import UnitOfWork
from steelo.adapters.repositories import InMemoryRepository


@pytest.fixture
def mock_cost_of_x_file():
    """Create a temporary cost_of_x.json file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        data = {
            "Country code": {"0": "USA", "1": "CHN", "2": "DEU"},
            "Cost of equity - industrial assets": {"0": 0.25, "1": 0.30, "2": 0.20},
        }
        json.dump(data, f)
        temp_path = Path(f.name)
    yield temp_path
    temp_path.unlink()  # Clean up


@pytest.fixture
def mock_tech_switches_file():
    """Create a temporary tech_switches_allowed.csv file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write("Origin,BF,BOF,DRI,EAF\n")
        f.write("BF,NO,NO,NO,NO\n")
        f.write("BOF,NO,NO,YES,YES\n")
        f.write("DRI,NO,NO,NO,NO\n")
        f.write("EAF,NO,NO,NO,NO\n")
        temp_path = Path(f.name)
    yield temp_path
    temp_path.unlink()  # Clean up


@pytest.fixture
def bus(mock_cost_of_x_file, mock_tech_switches_file):
    """Create a message bus with all dependencies for integration tests."""
    from steelo.simulation import SimulationConfig
    from steelo.domain.constants import Year
    from steelo.simulation_types import get_default_technology_settings

    repository = InMemoryRepository()
    uow = UnitOfWork(repository=repository)

    # Create a basic config for tests
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=get_default_technology_settings(),
    )

    return bootstrap(uow=uow, config=config, tech_switches_csv=mock_tech_switches_file)
