from steelo.domain.models import CountryMapping, CountryMappingService


def test_country_mapping_service():
    """
    Test that CountryMappingService provides proper lookups.
    """
    # Create test mappings
    mappings = [
        CountryMapping(
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
        ),
        CountryMapping(
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
        ),
        CountryMapping(
            country="Brazil",
            iso2="BR",
            iso3="BRA",
            irena_name="Brazil",
            irena_region="South America",
            region_for_outputs="South America",
            ssp_region="LAM",
            gem_country=None,  # Test None value
            ws_region=None,
            eu_region="Non-EU",
            tiam_ucl_region="BRA",
            Mercosur=True,
        ),
    ]

    # Create service
    service = CountryMappingService(mappings)

    # Test lookup methods (note: these methods expect ISO3 codes, not country names)
    assert service.get_code("DEU") == "Germany"  # get_code maps ISO3 to country name
    assert service.get_code("USA") == "United States"
    assert service.get_code("BRA") == "Brazil"
    assert service.get_code("Unknown") is None

    assert service.get_irena_name("DEU") == "Germany"  # methods expect ISO3 codes
    assert service.get_region("USA") == "North America"
    assert service.get_ssp_region("BRA") == "LAM"

    # Test GEM country to WS region mapping
    assert service.get_ws_region_for_gem("Germany") == "Europe"
    assert service.get_ws_region_for_gem("United States") == "North America"
    assert service.get_ws_region_for_gem("Brazil") is None  # Brazil has None for gem_country

    # Test direct map access (code_to_country_map maps ISO3 to country names)
    code_to_country = service.code_to_country_map
    assert isinstance(code_to_country, dict)
    assert code_to_country["DEU"] == "Germany"
    assert code_to_country["USA"] == "United States"
    assert code_to_country["BRA"] == "Brazil"

    gem_ws_map = service.gem_country_ws_region_map
    assert isinstance(gem_ws_map, dict)
    assert gem_ws_map["Germany"] == "Europe"
    assert gem_ws_map["United States"] == "North America"
    assert "Brazil" not in gem_ws_map  # Should be excluded due to None value


def test_country_mapping_service_empty():
    """
    Test CountryMappingService with empty mappings.
    """
    service = CountryMappingService([])

    assert service.get_code("DEU") is None
    assert service.get_irena_name("DEU") is None
    assert service.get_region("DEU") is None
    assert service.get_ssp_region("DEU") is None
    assert service.get_ws_region_for_gem("Germany") is None

    assert len(service.code_to_country_map) == 0
    assert len(service.code_to_irena_map) == 0
    assert len(service.code_to_region_map) == 0
    assert len(service.code_to_ssp_region_map) == 0
    assert len(service.gem_country_ws_region_map) == 0
