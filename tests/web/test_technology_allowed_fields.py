"""Test that technology allowed fields properly control technology availability."""

import pytest
from django.urls import reverse
from steeloweb.models import ModelRun, DataPackage, DataPreparation


@pytest.mark.django_db
def test_bf_allowed_field_controls_blast_furnace(mock_technology_extraction, client, ready_data_preparation):
    """Test that bf_allowed field properly controls blast furnace availability."""
    # Submit the form with BF disabled using new dynamic technology fields
    response = client.post(
        reverse("create-modelrun"),
        {
            "name": "Test BF Disabled Simulation",
            "start_year": 2025,
            "plant_lifetime": 20,
            "end_year": 2050,
            "data_preparation": ready_data_preparation.id,
            "scrap_generation_scenario": "business_as_usual",
            # Technology settings in new dynamic format
            # BF and BFBOF disabled (no checkbox = disabled)
            "tech_BF_from_year": 2025,
            "tech_BF_to_year": "",
            "tech_BFBOF_from_year": 2025,
            "tech_BFBOF_to_year": "",
            # Other technologies enabled
            "tech_BOF_allowed": "on",
            "tech_BOF_from_year": 2025,
            "tech_BOF_to_year": "",
            "tech_DRING_allowed": "on",
            "tech_DRING_from_year": 2025,
            "tech_DRING_to_year": "",
            "tech_DRINGEAF_allowed": "on",
            "tech_DRINGEAF_from_year": 2025,
            "tech_DRINGEAF_to_year": "",
            "tech_DRIH2_from_year": 2025,
            "tech_DRIH2_to_year": "",
            "tech_DRIH2EAF_from_year": 2025,
            "tech_DRIH2EAF_to_year": "",
            "tech_EAF_allowed": "on",
            "tech_EAF_from_year": 2025,
            "tech_EAF_to_year": "",
            "tech_ESF_from_year": 2025,
            "tech_ESF_to_year": "",
            "tech_ESFEAF_from_year": 2025,
            "tech_ESFEAF_to_year": "",
            "tech_MOE_from_year": 2025,
            "tech_MOE_to_year": "",
            "tech_DRI_allowed": "on",
            "tech_DRI_from_year": 2025,
            "tech_DRI_to_year": "",
            # Other required fields
            "global_risk_free_rate": 0.0209,
            "construction_time": 4,
            "probability_of_announcement": 0.7,
            "probability_of_construction": 0.9,
            "top_n_loctechs_as_business_op": 5,
            "chosen_grid_emissions_scenario": "Business As Usual",
            "chosen_emissions_boundary_for_carbon_costs": "cbam",
            "use_iron_ore_premiums": True,
            "include_tariffs": True,
        },
    )

    # Should redirect to detail view
    assert response.status_code == 302

    # Check that the ModelRun was created with BF disabled in technology_settings
    modelrun = ModelRun.objects.last()
    assert "technology_settings" in modelrun.config
    tech_settings = modelrun.config["technology_settings"]

    # BF should be disabled (either not present or allowed=False)
    if "BF" in tech_settings:
        assert tech_settings["BF"]["allowed"] is False
    if "BFBOF" in tech_settings:
        assert tech_settings["BFBOF"]["allowed"] is False


@pytest.mark.django_db
def test_all_technology_allowed_fields_saved_correctly(mock_technology_extraction, client):
    """Test that all technology allowed fields are saved correctly to config."""
    # Create DataPackages first
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.S3,
        source_url="https://example.com/core-data.zip",
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.S3,
        source_url="https://example.com/geo-data.zip",
    )

    # Create a DataPreparation for the form
    data_prep = DataPreparation.objects.create(
        name="Test Data Prep",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
    )

    # Submit the form with specific technology settings using dynamic fields
    form_data = {
        "name": "Test Technology Settings",
        "start_year": 2025,
        "plant_lifetime": 20,
        "end_year": 2037,
        "data_preparation": data_prep.id,
        "scrap_generation_scenario": "business_as_usual",
        # Technology settings using dynamic field names
        # BF disabled (no checkbox)
        "tech_BF_from_year": "2025",
        "tech_BF_to_year": "",
        # BOF enabled
        "tech_BOF_allowed": "true",
        "tech_BOF_from_year": "2025",
        "tech_BOF_to_year": "",
        # DRI-NG enabled
        "tech_DRING_allowed": "true",
        "tech_DRING_from_year": "2025",
        "tech_DRING_to_year": "",
        # DRI-H2 disabled (no checkbox)
        "tech_DRIH2_from_year": "2025",
        "tech_DRIH2_to_year": "",
        # EAF enabled
        "tech_EAF_allowed": "true",
        "tech_EAF_from_year": "2025",
        "tech_EAF_to_year": "",
        # ESF disabled (no checkbox)
        "tech_ESF_from_year": "2025",
        "tech_ESF_to_year": "",
        # MOE enabled
        "tech_MOE_allowed": "true",
        "tech_MOE_from_year": "2025",
        "tech_MOE_to_year": "",
    }

    response = client.post(reverse("create-modelrun"), form_data)

    # Should redirect to detail view
    assert response.status_code == 302

    # Check that all technology settings were saved correctly in the new structure
    modelrun = ModelRun.objects.last()
    tech_settings = modelrun.config.get("technology_settings", {})

    # Expected values based on form data
    expected_settings = {
        "BF": {"allowed": False},  # No checkbox = disabled
        "BOF": {"allowed": True},
        "DRING": {"allowed": True},
        "DRIH2": {"allowed": False},  # No checkbox = disabled
        "EAF": {"allowed": True},
        "ESF": {"allowed": False},  # No checkbox = disabled
        "MOE": {"allowed": True},
    }

    for tech_key, expected in expected_settings.items():
        actual_allowed = tech_settings.get(tech_key, {}).get("allowed")
        expected_allowed = expected["allowed"]
        assert actual_allowed == expected_allowed, (
            f"Technology {tech_key}.allowed not saved correctly: expected {expected_allowed}, got {actual_allowed}"
        )


@pytest.mark.django_db
def test_technology_allowed_defaults_when_not_specified(mock_technology_extraction, client):
    """Test that technology allowed fields have correct defaults when not specified."""
    # Create DataPackages first
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.S3,
        source_url="https://example.com/core-data.zip",
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.S3,
        source_url="https://example.com/geo-data.zip",
    )

    # Create a DataPreparation for the form
    data_prep = DataPreparation.objects.create(
        name="Test Data Prep",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
    )

    # Submit minimal form data without explicit allowed settings
    response = client.post(
        reverse("create-modelrun"),
        {
            "name": "Test Default Technology Settings",
            "start_year": 2025,
            "plant_lifetime": 20,
            "end_year": 2050,
            "data_preparation": data_prep.id,
            "scrap_generation_scenario": "business_as_usual",
            # Only include from_year fields (no allowed checkboxes)
            "tech_BF_from_year": "2025",
            "tech_BOF_from_year": "2025",
            "tech_DRING_from_year": "2025",
            "tech_DRIH2_from_year": "2025",
            "tech_EAF_from_year": "2025",
            "tech_ESF_from_year": "2025",
            "tech_MOE_from_year": "2025",
        },
    )

    # Should redirect to detail view
    assert response.status_code == 302

    # Check defaults - checkboxes not submitted default to False in technology_settings
    modelrun = ModelRun.objects.last()
    tech_settings = modelrun.config.get("technology_settings", {})

    # When checkboxes are not submitted, technologies should be disabled
    assert tech_settings.get("BF", {}).get("allowed") is False
    assert tech_settings.get("BOF", {}).get("allowed") is False
    assert tech_settings.get("DRING", {}).get("allowed") is False
    assert tech_settings.get("DRIH2", {}).get("allowed") is False
    assert tech_settings.get("EAF", {}).get("allowed") is False
    assert tech_settings.get("ESF", {}).get("allowed") is False
    assert tech_settings.get("MOE", {}).get("allowed") is False


@pytest.mark.django_db
def test_disable_all_iron_technologies_scenario(mock_technology_extraction, client):
    """Test scenario where all iron-making technologies are disabled except DRI."""
    # Create DataPackages first
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.S3,
        source_url="https://example.com/core-data.zip",
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.S3,
        source_url="https://example.com/geo-data.zip",
    )

    # Create a DataPreparation for the form
    data_prep = DataPreparation.objects.create(
        name="Test Data Prep",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
    )

    # Submit form with only DRI and EAF enabled (hydrogen-based steel scenario)
    response = client.post(
        reverse("create-modelrun"),
        {
            "name": "Hydrogen-Only Steel Scenario",
            "start_year": 2025,
            "plant_lifetime": 20,
            "end_year": 2050,
            "data_preparation": data_prep.id,
            "scrap_generation_scenario": "business_as_usual",
            # Technology settings using dynamic field names
            # BF disabled (no checkbox)
            "tech_BF_from_year": "2025",
            # BOF disabled (no checkbox)
            "tech_BOF_from_year": "2025",
            # DRI-NG disabled (no checkbox)
            "tech_DRING_from_year": "2025",
            # DRI-H2 enabled
            "tech_DRIH2_allowed": "true",
            "tech_DRIH2_from_year": "2025",
            # EAF enabled
            "tech_EAF_allowed": "true",
            "tech_EAF_from_year": "2025",
            # ESF disabled (no checkbox)
            "tech_ESF_from_year": "2025",
            # MOE disabled (no checkbox)
            "tech_MOE_from_year": "2025",
        },
    )

    # Should redirect to detail view
    assert response.status_code == 302

    # Verify the hydrogen-only scenario configuration in technology_settings
    modelrun = ModelRun.objects.last()
    tech_settings = modelrun.config.get("technology_settings", {})

    assert tech_settings.get("BF", {}).get("allowed") is False
    assert tech_settings.get("BOF", {}).get("allowed") is False
    assert tech_settings.get("DRING", {}).get("allowed") is False
    assert tech_settings.get("DRIH2", {}).get("allowed") is True
    assert tech_settings.get("EAF", {}).get("allowed") is True
    assert tech_settings.get("ESF", {}).get("allowed") is False
    assert tech_settings.get("MOE", {}).get("allowed") is False


@pytest.mark.django_db
def test_technology_year_ranges_with_allowed_fields(mock_technology_extraction, client):
    """Test that from_year and to_year fields work together with allowed fields."""
    # Create DataPackages first
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.S3,
        source_url="https://example.com/core-data.zip",
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.S3,
        source_url="https://example.com/geo-data.zip",
    )

    # Create a DataPreparation for the form
    data_prep = DataPreparation.objects.create(
        name="Test Data Prep",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
    )

    # Submit form with specific year ranges
    response = client.post(
        reverse("create-modelrun"),
        {
            "name": "Test Technology Year Ranges",
            "start_year": 2025,
            "plant_lifetime": 20,
            "end_year": 2050,
            "data_preparation": data_prep.id,
            "scrap_generation_scenario": "business_as_usual",
            # Technology settings using dynamic field names
            "tech_BF_allowed": "true",
            "tech_BF_from_year": "2025",
            "tech_BF_to_year": "2030",  # Phase out by 2030
            "tech_BOF_allowed": "true",
            "tech_BOF_from_year": "2025",
            "tech_BOF_to_year": "",  # No limit
            "tech_DRING_allowed": "true",
            "tech_DRING_from_year": "2025",
            "tech_DRING_to_year": "2035",
            "tech_DRIH2_allowed": "true",
            "tech_DRIH2_from_year": "2028",  # Available from 2028
            "tech_DRIH2_to_year": "",
            "tech_EAF_allowed": "true",
            "tech_EAF_from_year": "2025",
            "tech_EAF_to_year": "",
            # ESF disabled (no checkbox)
            "tech_ESF_from_year": "2025",
            # MOE disabled (no checkbox)
            "tech_MOE_from_year": "2025",
        },
    )

    # Should redirect to detail view
    assert response.status_code == 302

    # Verify the year ranges are saved correctly in technology_settings
    modelrun = ModelRun.objects.last()
    tech_settings = modelrun.config.get("technology_settings", {})

    assert tech_settings.get("BF", {}).get("allowed") is True
    assert tech_settings.get("BF", {}).get("from_year") == 2025
    assert tech_settings.get("BF", {}).get("to_year") == 2030  # Should be int, not string
    assert tech_settings.get("DRIH2", {}).get("from_year") == 2028
    assert tech_settings.get("DRING", {}).get("to_year") == 2035  # Should be int, not string
