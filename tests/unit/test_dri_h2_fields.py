"""Test DRI H2 technology availability fields in Django UI with new dynamic system."""

import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from steeloweb.models import ModelRun, DataPackage, MasterExcelFile

User = get_user_model()


@pytest.fixture
def test_user(db):
    """Create a test user."""
    return User.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def sample_data_package(db):
    """Create a sample data package."""
    return DataPackage.objects.create(
        name="test-package",
        version="1.0.0",
        source_type=DataPackage.SourceType.S3,
        source_url="https://example.com/test.zip",
    )


@pytest.fixture
def sample_master_excel(db):
    """Create a sample master Excel file."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    # Create a dummy Excel file
    fake_excel = SimpleUploadedFile(
        "test.xlsx",
        b"fake excel content",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    return MasterExcelFile.objects.create(
        name="Test Excel",
        description="Test master input Excel",
        file=fake_excel,
        validation_status="valid",
    )


@pytest.mark.django_db
def test_dri_h2_fields_in_form(client, test_user, sample_master_excel, ready_data_preparation):
    """Test that DRI H2 fields are properly passed from form to simulation config."""
    # Login
    client.force_login(test_user)

    # Create form data with dynamic technology settings
    form_data = {
        "name": "Test DRI H2 Fields",
        "description": "Testing DRI H2 technology availability",
        "master_input_excel": sample_master_excel.id,
        "data_preparation": ready_data_preparation.id,
        "start_year": 2025,
        "end_year": 2050,
        # Technology settings in new format
        "tech_DRIH2_allowed": "on",  # Checkbox is "on" when checked
        "tech_DRIH2_from_year": 2030,
        "tech_DRIH2_to_year": 2050,
        "tech_DRIH2EAF_allowed": "on",
        "tech_DRIH2EAF_from_year": 2030,
        "tech_DRIH2EAF_to_year": 2050,
        # Other required fields with defaults
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "construction_time": 4,
        "probability_of_announcement": 0.7,
        "probability_of_construction": 0.9,
        "top_n_loctechs_as_business_op": 5,
        "chosen_grid_emissions_scenario": "Business As Usual",
        "scrap_generation_scenario": "business_as_usual",
        "chosen_emissions_boundary_for_carbon_costs": "cbam",
        "use_iron_ore_premiums": True,
        "include_tariffs": True,
        # Other technology settings (required for form)
        "tech_BF_allowed": "on",
        "tech_BF_from_year": 2025,
        "tech_BF_to_year": "",
        "tech_BFBOF_allowed": "on",
        "tech_BFBOF_from_year": 2025,
        "tech_BFBOF_to_year": "",
        "tech_BOF_allowed": "on",
        "tech_BOF_from_year": 2025,
        "tech_BOF_to_year": "",
        "tech_DRING_allowed": "on",
        "tech_DRING_from_year": 2025,
        "tech_DRING_to_year": "",
        "tech_DRINGEAF_allowed": "on",
        "tech_DRINGEAF_from_year": 2025,
        "tech_DRINGEAF_to_year": "",
        "tech_EAF_allowed": "on",
        "tech_EAF_from_year": 2025,
        "tech_EAF_to_year": "",
        # ESF, ESFEAF, MOE, and DRI - need from_year even if disabled
        "tech_ESF_from_year": 2025,
        "tech_ESF_to_year": "",
        "tech_ESFEAF_from_year": 2025,
        "tech_ESFEAF_to_year": "",
        "tech_MOE_from_year": 2025,
        "tech_MOE_to_year": "",
        "tech_DRI_allowed": "on",
        "tech_DRI_from_year": 2025,
        "tech_DRI_to_year": "",
    }

    # Submit form
    response = client.post(reverse("create-modelrun"), form_data)

    # Check redirect (successful creation) - if not, print form errors
    if response.status_code != 302:
        # Try to extract form errors from the response

        if hasattr(response, "context") and response.context:
            if "form" in response.context:
                form = response.context["form"]
                if hasattr(form, "errors"):
                    print(f"Form errors: {form.errors}")
        # Print content for debugging
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            # Look for error messages in content
            content = response.content.decode("utf-8")
            if "error" in content.lower():
                import re

                error_pattern = re.compile(r'<[^>]*class="[^"]*error[^"]*"[^>]*>(.*?)</[^>]*>', re.DOTALL)
                errors = error_pattern.findall(content)
                if errors:
                    print(f"Errors found in HTML: {errors[:5]}")  # Print first 5 errors

    assert response.status_code == 302

    # Verify ModelRun was created with correct params
    model_run = ModelRun.objects.latest("updated_at")
    assert model_run.name == "Test DRI H2 Fields"

    # Check that technology_settings are in the config
    assert "technology_settings" in model_run.config
    tech_settings = model_run.config["technology_settings"]

    # Check DRI H2 settings
    assert "DRIH2" in tech_settings
    assert tech_settings["DRIH2"]["allowed"] is True
    assert tech_settings["DRIH2"]["from_year"] == 2030
    assert tech_settings["DRIH2"]["to_year"] == 2050

    assert "DRIH2EAF" in tech_settings
    assert tech_settings["DRIH2EAF"]["allowed"] is True
    assert tech_settings["DRIH2EAF"]["from_year"] == 2030
    assert tech_settings["DRIH2EAF"]["to_year"] == 2050


@pytest.mark.django_db
def test_dri_h2_fields_disabled(client, test_user, sample_master_excel, ready_data_preparation):
    """Test DRI H2 fields when technology is disabled."""
    client.force_login(test_user)

    form_data = {
        "name": "Test DRI H2 Disabled",
        "description": "Testing DRI H2 disabled",
        "master_input_excel": sample_master_excel.id,
        "data_preparation": ready_data_preparation.id,
        "start_year": 2025,
        "end_year": 2050,
        # DRI H2 disabled - no checkbox means disabled
        # "tech_DRIH2_allowed": not included means unchecked/False
        "tech_DRIH2_from_year": 2025,  # Still need a value even when disabled
        "tech_DRIH2_to_year": "",
        # Other required fields
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "construction_time": 4,
        "probability_of_announcement": 0.7,
        "probability_of_construction": 0.9,
        "top_n_loctechs_as_business_op": 5,
        "chosen_grid_emissions_scenario": "Business As Usual",
        "scrap_generation_scenario": "business_as_usual",
        "chosen_emissions_boundary_for_carbon_costs": "cbam",
        "use_iron_ore_premiums": True,
        "include_tariffs": True,
        # Other technology settings (basic set enabled)
        "tech_BF_allowed": "on",
        "tech_BF_from_year": 2025,
        "tech_BF_to_year": "",
        "tech_BFBOF_allowed": "on",
        "tech_BFBOF_from_year": 2025,
        "tech_BFBOF_to_year": "",
        "tech_BOF_allowed": "on",
        "tech_BOF_from_year": 2025,
        "tech_BOF_to_year": "",
        "tech_DRING_allowed": "on",
        "tech_DRING_from_year": 2025,
        "tech_DRING_to_year": "",
        "tech_DRINGEAF_allowed": "on",
        "tech_DRINGEAF_from_year": 2025,
        "tech_DRINGEAF_to_year": "",
        "tech_EAF_allowed": "on",
        "tech_EAF_from_year": 2025,
        "tech_EAF_to_year": "",
        # ESF, ESFEAF, MOE, and DRI - need from_year even if disabled
        "tech_ESF_from_year": 2025,
        "tech_ESF_to_year": "",
        "tech_ESFEAF_from_year": 2025,
        "tech_ESFEAF_to_year": "",
        "tech_MOE_from_year": 2025,
        "tech_MOE_to_year": "",
        "tech_DRI_allowed": "on",
        "tech_DRI_from_year": 2025,
        "tech_DRI_to_year": "",
        "tech_DRIH2EAF_from_year": 2025,
        "tech_DRIH2EAF_to_year": "",
    }

    response = client.post(reverse("create-modelrun"), form_data)
    assert response.status_code == 302

    model_run = ModelRun.objects.latest("updated_at")
    assert "technology_settings" in model_run.config
    tech_settings = model_run.config["technology_settings"]

    # Check DRIH2 is disabled (either not in settings or allowed is False)
    if "DRIH2" in tech_settings:
        assert tech_settings["DRIH2"]["allowed"] is False
    if "DRIH2EAF" in tech_settings:
        assert tech_settings["DRIH2EAF"]["allowed"] is False


@pytest.mark.django_db
def test_dri_h2_partial_year_range(client, test_user, sample_master_excel, ready_data_preparation):
    """Test DRI H2 with only from_year specified (no to_year limit)."""
    client.force_login(test_user)

    form_data = {
        "name": "Test DRI H2 Partial Range",
        "description": "Testing DRI H2 with only from_year",
        "master_input_excel": sample_master_excel.id,
        "data_preparation": ready_data_preparation.id,
        "start_year": 2025,
        "end_year": 2050,
        # DRI H2 with only from_year
        "tech_DRIH2_allowed": "on",
        "tech_DRIH2_from_year": 2035,
        "tech_DRIH2_to_year": "",  # No end limit
        "tech_DRIH2EAF_allowed": "on",
        "tech_DRIH2EAF_from_year": 2035,
        "tech_DRIH2EAF_to_year": "",
        # Other required fields
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "construction_time": 4,
        "probability_of_announcement": 0.7,
        "probability_of_construction": 0.9,
        "top_n_loctechs_as_business_op": 5,
        "chosen_grid_emissions_scenario": "Business As Usual",
        "scrap_generation_scenario": "business_as_usual",
        "chosen_emissions_boundary_for_carbon_costs": "cbam",
        "use_iron_ore_premiums": True,
        "include_tariffs": True,
        # Other technology settings
        "tech_BF_allowed": "on",
        "tech_BF_from_year": 2025,
        "tech_BF_to_year": "",
        "tech_BFBOF_allowed": "on",
        "tech_BFBOF_from_year": 2025,
        "tech_BFBOF_to_year": "",
        "tech_BOF_allowed": "on",
        "tech_BOF_from_year": 2025,
        "tech_BOF_to_year": "",
        "tech_DRING_allowed": "on",
        "tech_DRING_from_year": 2025,
        "tech_DRING_to_year": "",
        "tech_DRINGEAF_allowed": "on",
        "tech_DRINGEAF_from_year": 2025,
        "tech_DRINGEAF_to_year": "",
        "tech_EAF_allowed": "on",
        "tech_EAF_from_year": 2025,
        "tech_EAF_to_year": "",
        # ESF, ESFEAF, MOE, and DRI - need from_year even if disabled
        "tech_ESF_from_year": 2025,
        "tech_ESF_to_year": "",
        "tech_ESFEAF_from_year": 2025,
        "tech_ESFEAF_to_year": "",
        "tech_MOE_from_year": 2025,
        "tech_MOE_to_year": "",
        "tech_DRI_allowed": "on",
        "tech_DRI_from_year": 2025,
        "tech_DRI_to_year": "",
    }

    response = client.post(reverse("create-modelrun"), form_data)
    assert response.status_code == 302

    model_run = ModelRun.objects.latest("updated_at")
    assert "technology_settings" in model_run.config
    tech_settings = model_run.config["technology_settings"]

    # Check DRI H2 settings
    assert "DRIH2" in tech_settings
    assert tech_settings["DRIH2"]["allowed"] is True
    assert tech_settings["DRIH2"]["from_year"] == 2035
    assert tech_settings["DRIH2"]["to_year"] is None  # Empty string converts to None

    assert "DRIH2EAF" in tech_settings
    assert tech_settings["DRIH2EAF"]["allowed"] is True
    assert tech_settings["DRIH2EAF"]["from_year"] == 2035
    assert tech_settings["DRIH2EAF"]["to_year"] is None
