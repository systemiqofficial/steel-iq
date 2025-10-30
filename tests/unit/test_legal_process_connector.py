"""
Tests for LegalProcessConnector functionality
"""

import pytest
import pandas as pd
import json

from steelo.domain.models import LegalProcessConnector
from steelo.adapters.dataprocessing.excel_reader import read_legal_process_connectors
from steelo.adapters.repositories.json_repository import LegalProcessConnectorJsonRepository, LegalProcessConnectorInDb
from steelo.data.recreation_functions import recreate_legal_process_connectors_data


@pytest.fixture
def excel_with_legal_process_connectors(tmp_path):
    """Create Excel file with legal process connectors sheet."""
    excel_path = tmp_path / "test_legal_process.xlsx"

    data = {
        "from_process": ["iron_bf", "iron_dri", "steel_bof", "steel_eaf"],
        "to_process": ["steel_bof", "steel_eaf", "prep_coke", "prep_sinter"],
    }
    df = pd.DataFrame(data)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Legal Process connectors", index=False)

    return excel_path


@pytest.fixture
def excel_with_invalid_technology(tmp_path):
    """Create Excel file with invalid technology names."""
    excel_path = tmp_path / "invalid_tech.xlsx"

    data = {"from_process": ["invalid_tech", "iron_bf"], "to_process": ["steel_bof", "invalid_process"]}
    df = pd.DataFrame(data)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Legal Process connectors", index=False)

    return excel_path


def test_read_legal_process_connectors_success(excel_with_legal_process_connectors):
    """Test reading valid legal process connectors."""
    connectors = read_legal_process_connectors(excel_with_legal_process_connectors)

    assert len(connectors) == 4
    assert all(isinstance(c, LegalProcessConnector) for c in connectors)

    # Check that technology names were translated correctly
    assert connectors[0].from_technology_name == "BF"  # iron_bf -> BF
    assert connectors[0].to_technology_name == "BOF"  # steel_bof -> BOF
    assert connectors[1].from_technology_name == "DRI"  # iron_dri -> DRI
    assert connectors[1].to_technology_name == "EAF"  # steel_eaf -> EAF


def test_legal_process_connector_repository_roundtrip(tmp_path):
    """Test storing and retrieving legal process connectors in JSON repository."""
    json_path = tmp_path / "legal_process_connectors.json"

    # Create some test connectors
    connectors = [
        LegalProcessConnector(from_technology_name="BF", to_technology_name="BOF"),
        LegalProcessConnector(from_technology_name="DRI", to_technology_name="EAF"),
    ]

    # Save to repository
    repo = LegalProcessConnectorJsonRepository(json_path)
    repo.add_list(connectors)

    # Verify JSON file was created
    assert json_path.exists()

    # Load and verify data
    with open(json_path) as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0]["from_technology_name"] == "BF"
    assert data[0]["to_technology_name"] == "BOF"

    # Test loading from repository
    repo2 = LegalProcessConnectorJsonRepository(json_path)
    loaded_connectors = repo2.list()
    assert len(loaded_connectors) == 2
    assert loaded_connectors[0].from_technology_name == "BF"
    assert loaded_connectors[0].to_technology_name == "BOF"


def test_legal_process_connector_in_db_conversion():
    """Test conversion between domain and database models."""
    # Create domain object
    domain_obj = LegalProcessConnector(from_technology_name="DRI", to_technology_name="EAF")

    # Convert to DB model
    db_obj = LegalProcessConnectorInDb.from_domain(domain_obj)
    assert db_obj.from_technology_name == "DRI"
    assert db_obj.to_technology_name == "EAF"

    # Convert back to domain
    domain_obj2 = db_obj.to_domain()
    assert domain_obj2.from_technology_name == "DRI"
    assert domain_obj2.to_technology_name == "EAF"
    assert isinstance(domain_obj2, LegalProcessConnector)


def test_recreate_legal_process_connectors_data(excel_with_legal_process_connectors, tmp_path):
    """Test the CLI recreation function."""
    json_path = tmp_path / "legal_process_connectors.json"

    # Run recreation function
    repo = recreate_legal_process_connectors_data(
        legal_process_connectors_json_path=json_path,
        excel_path=excel_with_legal_process_connectors,
        sheet_name="Legal Process connectors",
    )

    # Verify repository was created and contains data
    assert isinstance(repo, LegalProcessConnectorJsonRepository)
    connectors = repo.list()
    assert len(connectors) == 4

    # Verify JSON file was created
    assert json_path.exists()
    with open(json_path) as f:
        data = json.load(f)
    assert len(data) == 4
