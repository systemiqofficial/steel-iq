"""
Tests for CountryMapping functionality
"""

import pytest
import pandas as pd

from steelo.domain.models import CountryMapping
from steelo.adapters.dataprocessing.excel_reader import read_country_mappings
from steelo.adapters.repositories.json_repository import CountryMappingInDb


@pytest.fixture
def excel_with_country_mappings(tmp_path):
    """Create Excel file with country mappings sheet."""
    excel_path = tmp_path / "test_country_mappings.xlsx"

    data = {
        "Country": ["Germany", "United States", "China", "Brazil"],
        "ISO 2-letter code": ["DE", "US", "CN", "BR"],
        "ISO 3-letter code": ["DEU", "USA", "CHN", "BRA"],
        "irena_name": ["Germany", "United States", "China", "Brazil"],
        "irena_region": ["Europe", "North America", "Asia", "South America"],
        "region_for_outputs": ["Europe", "North America", "Asia", "South America"],
        "ssp_region": ["OECD90", "OECD90", "ASIA", "LAM"],
        "gem_country": ["Germany", "United States", "China", None],
        "ws_region": ["Europe", "North America", "Asia", None],
        "eu_or_non_eu": ["EU", "Non-EU", "Non-EU", "Non-EU"],
        "tiam-ucl_region": ["WEU", "USA", "CHI", "BRA"],
        "EU": [True, False, False, False],
        "EFTA/EUCU": [False, False, False, False],
        "OECD": [True, True, False, False],
        "NAFTA": [False, True, False, False],
        "Mercosur": [False, False, False, True],
        "ASEAN": [False, False, False, False],
        "RCEP": [False, False, True, False],
    }
    df = pd.DataFrame(data)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Country mapping", index=False)

    return excel_path


@pytest.fixture
def excel_with_missing_columns(tmp_path):
    """Create Excel file with missing required columns."""
    excel_path = tmp_path / "invalid_mappings.xlsx"

    data = {
        "Country": ["Germany", "United States"],
        "ISO 2-letter code": ["DE", "US"],
        # Missing ISO 3-letter code and other columns
    }
    df = pd.DataFrame(data)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Country mapping", index=False)

    return excel_path


def test_country_mapping_domain_model():
    """Test CountryMapping domain model creation and properties."""
    mapping = CountryMapping(
        country="Germany",
        iso2="DE",
        iso3="DEU",
        irena_name="Germany",
        irena_region="Europe",
        region_for_outputs="Europe",
        ssp_region="OECD90",
        gem_country="Germany",
        ws_region="Europe",
        eu_region="EU",
        tiam_ucl_region="WEU",
        EU=True,
        OECD=True,
    )

    assert mapping.country == "Germany"
    assert mapping.iso2 == "DE"
    assert mapping.iso3 == "DEU"
    assert mapping.id == "DEU"  # id property should return iso3
    assert mapping.irena_name == "Germany"
    assert mapping.region_for_outputs == "Europe"
    assert mapping.ssp_region == "OECD90"
    assert mapping.gem_country == "Germany"
    assert mapping.ws_region == "Europe"
    assert mapping.tiam_ucl_region == "WEU"


def test_country_mapping_equality_and_hash():
    """Test CountryMapping equality and hash methods."""
    mapping1 = CountryMapping(
        country="Germany",
        iso2="DE",
        iso3="DEU",
        irena_name="Germany",
        irena_region="Europe",
        region_for_outputs="Europe",
        ssp_region="OECD90",
        gem_country="Germany",
        ws_region="Europe",
        eu_region="EU",
        tiam_ucl_region="WEU",
        EU=True,
        OECD=True,
    )

    mapping2 = CountryMapping(
        country="Germany",
        iso2="DE",
        iso3="DEU",
        irena_name="Germany",
        irena_region="Europe",
        region_for_outputs="Europe",
        ssp_region="OECD90",
        gem_country="Germany",
        ws_region="Europe",
        eu_region="EU",
        tiam_ucl_region="WEU",
        EU=True,
        OECD=True,
    )

    mapping3 = CountryMapping(
        country="United States",
        iso2="US",
        iso3="USA",
        irena_name="United States",
        irena_region="North America",
        region_for_outputs="North America",
        ssp_region="OECD90",
        gem_country="United States",
        ws_region="North America",
        eu_region="Non-EU",
        tiam_ucl_region="USA",
        OECD=True,
        NAFTA=True,
    )

    assert mapping1 == mapping2
    assert mapping1 != mapping3
    assert hash(mapping1) == hash(mapping2)
    assert hash(mapping1) != hash(mapping3)


def test_country_mapping_optional_fields():
    """Test CountryMapping with optional fields as None."""
    mapping = CountryMapping(
        country="Brazil",
        iso2="BR",
        iso3="BRA",
        irena_name="Brazil",
        irena_region="South America",
        region_for_outputs="South America",
        ssp_region="LAM",
        gem_country=None,
        ws_region=None,
        eu_region="Non-EU",
        tiam_ucl_region="BRA",
        Mercosur=True,
    )

    assert mapping.gem_country is None
    assert mapping.ws_region is None
    assert mapping.id == "BRA"


def test_read_country_mappings_success(excel_with_country_mappings):
    """Test reading valid country mappings."""
    mappings = read_country_mappings(excel_with_country_mappings)

    assert len(mappings) == 4
    assert all(isinstance(m, CountryMapping) for m in mappings)

    # Check first mapping
    germany = mappings[0]
    assert germany.country == "Germany"
    assert germany.iso2 == "DE"
    assert germany.iso3 == "DEU"
    assert germany.irena_name == "Germany"
    assert germany.region_for_outputs == "Europe"
    assert germany.ssp_region == "OECD90"
    assert germany.gem_country == "Germany"
    assert germany.ws_region == "Europe"
    assert germany.tiam_ucl_region == "WEU"

    # Check mapping with None values
    brazil = mappings[3]
    assert brazil.country == "Brazil"
    assert brazil.gem_country is None
    assert brazil.ws_region is None


def test_read_country_mappings_missing_sheet(tmp_path):
    """Test handling of missing sheet."""
    excel_path = tmp_path / "no_sheet.xlsx"

    # Create Excel file without the expected sheet
    data = {"dummy": ["data"]}
    df = pd.DataFrame(data)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="wrong_sheet", index=False)

    mappings = read_country_mappings(excel_path, "Country mapping")
    assert len(mappings) == 0


def test_read_country_mappings_invalid_data(excel_with_missing_columns, caplog):
    """Test handling of invalid data."""
    mappings = read_country_mappings(excel_with_missing_columns)

    # Should return empty list due to missing columns
    assert len(mappings) == 0

    # Check that warnings were logged
    assert "Skipping row" in caplog.text


def test_country_mapping_in_db_conversion():
    """Test conversion between domain and database models."""
    # Create domain object
    domain_obj = CountryMapping(
        country="Germany",
        iso2="DE",
        iso3="DEU",
        irena_name="Germany",
        irena_region="Europe",
        region_for_outputs="Europe",
        ssp_region="OECD90",
        gem_country="Germany",
        ws_region="Europe",
        eu_region="EU",
        tiam_ucl_region="WEU",
        EU=True,
        OECD=True,
    )

    # Convert to DB model
    db_obj = CountryMappingInDb.from_domain(domain_obj)
    assert db_obj.country == "Germany"
    assert db_obj.iso2 == "DE"
    assert db_obj.iso3 == "DEU"
    assert db_obj.irena_name == "Germany"
    assert db_obj.region_for_outputs == "Europe"
    assert db_obj.ssp_region == "OECD90"
    assert db_obj.gem_country == "Germany"
    assert db_obj.ws_region == "Europe"
    assert db_obj.tiam_ucl_region == "WEU"

    # Convert back to domain
    domain_obj2 = db_obj.to_domain()
    assert domain_obj2.country == "Germany"
    assert domain_obj2.iso2 == "DE"
    assert domain_obj2.iso3 == "DEU"
    assert domain_obj2.irena_name == "Germany"
    assert domain_obj2.region_for_outputs == "Europe"
    assert domain_obj2.ssp_region == "OECD90"
    assert domain_obj2.gem_country == "Germany"
    assert domain_obj2.ws_region == "Europe"
    assert domain_obj2.tiam_ucl_region == "WEU"
    assert isinstance(domain_obj2, CountryMapping)


def test_country_mapping_in_db_sorting():
    """Test sorting of CountryMappingInDb objects."""
    mapping1 = CountryMappingInDb(
        country="Germany",
        iso2="DE",
        iso3="DEU",
        irena_name="Germany",
        irena_region="Europe",
        region_for_outputs="Europe",
        ssp_region="OECD90",
        gem_country="Germany",
        ws_region="Europe",
        eu_region="EU",
        tiam_ucl_region="WEU",
        EU=True,
        EFTA_EUCJ=False,
        OECD=True,
        NAFTA=False,
        Mercosur=False,
        ASEAN=False,
        RCEP=False,
    )

    mapping2 = CountryMappingInDb(
        country="United States",
        iso2="US",
        iso3="USA",
        irena_name="United States",
        irena_region="North America",
        region_for_outputs="North America",
        ssp_region="OECD90",
        gem_country="United States",
        ws_region="North America",
        eu_region="Non-EU",
        tiam_ucl_region="USA",
        EU=False,
        EFTA_EUCJ=False,
        OECD=True,
        NAFTA=True,
        Mercosur=False,
        ASEAN=False,
        RCEP=False,
    )

    # Test sorting by iso3
    assert mapping1 < mapping2  # DEU < USA
