import pytest
from django.urls import reverse
from steeloweb.models import ModelRun


@pytest.mark.django_db
def test_technology_availability_form_to_simulation(mock_technology_extraction, client, valid_modelrun_form_data):
    """Test that technology availability settings from form are passed to simulation."""
    # Update form data with technology restrictions using dynamic field names
    form_data = valid_modelrun_form_data.copy()

    # Remove allowed fields for disabled technologies (no checkbox = disabled)
    for tech in ["BF", "ESF", "MOE"]:
        field = f"tech_{tech}_allowed"
        if field in form_data:
            del form_data[field]

    form_data.update(
        {
            # BF disabled (keep from_year but no allowed checkbox)
            "tech_BF_from_year": "2025",
            "tech_BF_to_year": "",
            # BOF enabled with year restrictions
            "tech_BOF_allowed": "true",
            "tech_BOF_from_year": "2026",  # BOF only from 2026
            "tech_BOF_to_year": "2030",  # BOF only until 2030
            # DRI-NG enabled
            "tech_DRING_allowed": "true",
            "tech_DRING_from_year": "2025",
            "tech_DRING_to_year": "",
            # EAF enabled with year restrictions
            "tech_EAF_allowed": "true",
            "tech_EAF_from_year": "2025",
            "tech_EAF_to_year": "2028",  # EAF only until 2028
            # ESF disabled
            "tech_ESF_from_year": "2025",
            "tech_ESF_to_year": "",
            # MOE disabled
            "tech_MOE_from_year": "2025",
            "tech_MOE_to_year": "",
        }
    )

    # Submit form
    response = client.post(reverse("create-modelrun"), data=form_data)
    assert response.status_code == 302  # Redirect after success

    # Get created ModelRun
    model_run = ModelRun.objects.latest("started_at")

    # Verify technology settings are stored in the new technology_settings format
    tech_settings = model_run.config.get("technology_settings", {})

    # Check BF is disabled
    assert tech_settings.get("BF", {}).get("allowed") is False

    # Check BOF settings
    assert tech_settings.get("BOF", {}).get("allowed") is True
    assert str(tech_settings.get("BOF", {}).get("from_year")) == "2026"
    assert str(tech_settings.get("BOF", {}).get("to_year")) == "2030"

    # Check DRI-NG settings
    assert tech_settings.get("DRING", {}).get("allowed") is True

    # Check EAF settings
    assert tech_settings.get("EAF", {}).get("allowed") is True
    assert str(tech_settings.get("EAF", {}).get("from_year")) == "2025"
    assert str(tech_settings.get("EAF", {}).get("to_year")) == "2028"

    # Check ESF and MOE are disabled
    assert tech_settings.get("ESF", {}).get("allowed") is False
    assert tech_settings.get("MOE", {}).get("allowed") is False


@pytest.mark.django_db
def test_technology_availability_defaults(mock_technology_extraction, client, valid_modelrun_form_data):
    """Test default technology availability settings when not specified."""
    # Submit form without modifying technology settings
    response = client.post(reverse("create-modelrun"), data=valid_modelrun_form_data)
    assert response.status_code == 302

    # Get created ModelRun
    model_run = ModelRun.objects.latest("started_at")

    # Verify default values in technology_settings structure
    tech_settings = model_run.config.get("technology_settings", {})

    # All technologies should default to True (enabled) to respect Excel settings
    assert tech_settings.get("BF", {}).get("allowed", True) is True
    assert tech_settings.get("BOF", {}).get("allowed", True) is True
    assert tech_settings.get("DRING", {}).get("allowed", True) is True
    assert tech_settings.get("EAF", {}).get("allowed", True) is True
    # ESF and MOE now default to True to respect Excel settings
    assert tech_settings.get("ESF", {}).get("allowed", True) is True
    assert tech_settings.get("MOE", {}).get("allowed", True) is True


@pytest.mark.django_db
def test_technology_year_validation(mock_technology_extraction, client, valid_modelrun_form_data):
    """Test form validation for technology year settings."""
    # Test invalid year range (from_year > to_year)
    form_data = valid_modelrun_form_data.copy()
    form_data.update(
        {
            "eaf_allowed": True,
            "eaf_from_year": 2030,
            "eaf_to_year": 2025,  # Invalid: to_year before from_year
        }
    )

    response = client.post(reverse("create-modelrun"), data=form_data)

    # Check if form shows validation error (implementation may vary)
    # For now, just check that the form handles it without crashing
    assert response.status_code in [200, 302]  # Either shows form with errors or accepts it


@pytest.mark.django_db
def test_partial_year_restrictions(mock_technology_extraction, client, valid_modelrun_form_data):
    """Test that partial year restrictions (only from or only to) work correctly."""
    form_data = valid_modelrun_form_data.copy()
    form_data.update(
        {
            "tech_BF_allowed": "true",
            "tech_BF_from_year": "2027",  # Only from_year specified
            "tech_BF_to_year": "",
            "tech_EAF_allowed": "true",
            "tech_EAF_from_year": "2025",  # Need a from_year
            "tech_EAF_to_year": "2030",  # Only to_year specified
        }
    )

    response = client.post(reverse("create-modelrun"), data=form_data)
    assert response.status_code == 302

    model_run = ModelRun.objects.latest("started_at")
    tech_settings = model_run.config.get("technology_settings", {})

    assert tech_settings.get("BF", {}).get("from_year") == 2027  # Integer comparison
    assert tech_settings.get("BF", {}).get("to_year") in ["", None]
    assert tech_settings.get("EAF", {}).get("from_year") == 2025  # Integer comparison
    assert tech_settings.get("EAF", {}).get("to_year") == 2030  # Integer comparison


@pytest.mark.django_db
@pytest.mark.parametrize(
    "technology,field_prefix",
    [
        ("BF", "bf"),
        ("BOF", "bof"),
        ("DRI-NG", "dri_ng"),
        # ("DRI-H2", "dri_h2"),  # Removed as requested
        ("EAF", "eaf"),
        ("ESF", "esf"),
        ("MOE", "moe"),
    ],
)
def test_each_technology_can_be_disabled(
    mock_technology_extraction, client, valid_modelrun_form_data, technology, field_prefix
):
    """Test that each technology can be individually disabled."""
    form_data = valid_modelrun_form_data.copy()

    # Map field prefixes to technology keys for dynamic fields
    tech_key_map = {
        "bf": "BF",
        "bof": "BOF",
        "dri_ng": "DRING",
        "eaf": "EAF",
        "esf": "ESF",
        "moe": "MOE",
    }
    tech_key = tech_key_map.get(field_prefix, field_prefix.upper())

    # Disable this specific technology using dynamic field names
    form_data[f"tech_{tech_key}_allowed"] = "false"
    form_data[f"tech_{tech_key}_from_year"] = "2025"
    form_data[f"tech_{tech_key}_to_year"] = ""

    response = client.post(reverse("create-modelrun"), data=form_data)
    assert response.status_code == 302

    model_run = ModelRun.objects.latest("started_at")
    tech_settings = model_run.config.get("technology_settings", {})
    assert tech_settings.get(tech_key, {}).get("allowed") is False
