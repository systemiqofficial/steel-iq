import pytest
from django.urls import reverse
from steeloweb.models import ModelRun, DataPreparation, DataPackage


@pytest.mark.django_db
def test_modelrun_name_field_saved(mock_technology_extraction, client):
    """Test that the name field is properly saved when creating a ModelRun."""
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

    # Submit the form with a name
    response = client.post(
        reverse("create-modelrun"),
        {
            "name": "My Test Simulation",
            "start_year": 2025,
            "plant_lifetime": 20,
            "end_year": 2050,
            "data_preparation": data_prep.id,
            "scrap_generation_scenario": "business_as_usual",
            # Other required fields with defaults
            "bf_allowed": True,
            "bf_from_year": 2025,
            "bof_allowed": True,
            "bof_from_year": 2025,
            "dri_ng_allowed": True,
            "dri_ng_from_year": 2025,
            "dri_h2_allowed": False,
            "dri_h2_from_year": 2025,
            "eaf_allowed": True,
            "eaf_from_year": 2025,
            "esf_allowed": False,
            "esf_from_year": 2025,
            "moe_allowed": False,
            "moe_from_year": 2025,
        },
    )

    # Should redirect to detail view
    assert response.status_code == 302

    # Check that the ModelRun was created with the name
    modelrun = ModelRun.objects.last()
    assert modelrun.name == "My Test Simulation"

    # Check that the name appears in the detail view
    detail_response = client.get(reverse("modelrun-detail", kwargs={"pk": modelrun.pk}))
    assert detail_response.status_code == 200
    assert "My Test Simulation" in detail_response.content.decode()

    # Check that the name appears in the list view
    list_response = client.get(reverse("modelrun-list"))
    assert list_response.status_code == 200
    assert "My Test Simulation" in list_response.content.decode()


@pytest.mark.django_db
def test_modelrun_without_name(mock_technology_extraction, client):
    """Test that ModelRun works without a name (blank name)."""
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

    # Submit the form without a name
    response = client.post(
        reverse("create-modelrun"),
        {
            # No name field
            "start_year": 2025,
            "plant_lifetime": 20,
            "end_year": 2050,
            "data_preparation": data_prep.id,
            "scrap_generation_scenario": "business_as_usual",
            # Other required fields with defaults
            "bf_allowed": True,
            "bf_from_year": 2025,
            "bof_allowed": True,
            "bof_from_year": 2025,
            "dri_ng_allowed": True,
            "dri_ng_from_year": 2025,
            "dri_h2_allowed": False,
            "dri_h2_from_year": 2025,
            "eaf_allowed": True,
            "eaf_from_year": 2025,
            "esf_allowed": False,
            "esf_from_year": 2025,
            "moe_allowed": False,
            "moe_from_year": 2025,
        },
    )

    # Should redirect to detail view
    assert response.status_code == 302

    # Check that the ModelRun was created without a name
    modelrun = ModelRun.objects.last()
    assert modelrun.name == ""

    # Check that the detail view shows Model Run #ID
    detail_response = client.get(reverse("modelrun-detail", kwargs={"pk": modelrun.pk}))
    assert detail_response.status_code == 200
    content = detail_response.content.decode()
    assert f"Model Run #{modelrun.id}" in content

    # Check that the list view shows a dash
    list_response = client.get(reverse("modelrun-list"))
    assert list_response.status_code == 200
    assert '<span class="text-muted">-</span>' in list_response.content.decode()


@pytest.mark.django_db
def test_modelrun_str_method():
    """Test the __str__ method of ModelRun with and without name."""
    # Test with name
    modelrun_with_name = ModelRun(
        id=1,
        name="Named Simulation",
        state="created",
    )
    assert "Named Simulation" in str(modelrun_with_name)

    # Test without name
    modelrun_without_name = ModelRun(
        id=2,
        name="",
        state="created",
    )
    assert "ModelRun 2" in str(modelrun_without_name)
