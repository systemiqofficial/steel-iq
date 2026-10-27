"""Test cases for the extended ModelRunCreateForm and CircularityDataForm."""

import json
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from steeloweb.forms import ModelRunCreateForm, CircularityDataForm


@pytest.fixture
def minimal_form_data(ready_data_preparation):
    """Minimal valid form data for ModelRunCreateForm."""
    return {
        "data_preparation": ready_data_preparation.id,
        "start_year": 2025,
        "plant_lifetime": 20,
        "end_year": 2060,
    }


@pytest.mark.django_db
def test_form_valid_with_minimal_data(minimal_form_data):
    """Test the form is valid with just the required fields."""
    form = ModelRunCreateForm(data=minimal_form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"


@pytest.mark.django_db
def test_form_invalid_with_end_year_before_start_year():
    """Test form is invalid when end_year is before start_year."""
    form_data = {
        "start_year": 2030,
        "end_year": 2025,
    }
    form = ModelRunCreateForm(data=form_data)
    assert not form.is_valid()
    assert "End year must be after start year" in form.errors.get("__all__")[0]


@pytest.mark.django_db
def test_form_valid_with_same_start_and_end_year(ready_data_preparation):
    """Form should allow single-year simulations."""
    form_data = {
        "data_preparation": ready_data_preparation.id,
        "start_year": 2025,
        "plant_lifetime": 20,
        "end_year": 2025,
    }
    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"


@pytest.mark.django_db
def test_form_render_defaults_to_clustering_by_plant(mock_technology_extraction, client):
    """The create page opens with clustering ticked and the scope dropdown on 'plant'."""
    response = client.get(reverse("create-modelrun"))
    assert response.status_code == 200

    form = response.context["form"]
    assert form["enable_furnace_group_clustering"].value() is True
    assert form["geographical_clustering_scope"].value() == "plant"

    assert b'name="geographical_clustering_scope"' in response.content
    assert b'<option value="plant" selected>' in response.content
    assert b"cluster_hot_metal_techs_by_plant_group" not in response.content


# CircularityDataForm tests


@pytest.fixture
def valid_circularity_file():
    """Create a valid JSON file for circularity data."""
    json_content = json.dumps({"circularity": {"test": "data"}})
    return SimpleUploadedFile("circularity.json", bytes(json_content, "utf-8"), content_type="application/json")


def test_circularity_form_valid_with_required_data(valid_circularity_file):
    """Test the form is valid with required fields."""
    form_data = {
        "name": "Test Circularity Data",
    }

    form = CircularityDataForm(data=form_data, files={"circularity_file": valid_circularity_file})
    assert form.is_valid(), f"Form errors: {form.errors}"


def test_circularity_form_valid_with_all_fields(valid_circularity_file):
    """Test the form is valid with all fields populated."""
    form_data = {
        "name": "Test Circularity Data",
        "description": "Test description for circularity data",
    }

    form = CircularityDataForm(data=form_data, files={"circularity_file": valid_circularity_file})
    assert form.is_valid(), f"Form errors: {form.errors}"


def test_circularity_form_invalid_with_non_json_file():
    """Test form is invalid when file is not JSON."""
    form_data = {
        "name": "Test Circularity Data",
    }

    text_content = "This is not a JSON file"
    text_file = SimpleUploadedFile("circularity.txt", bytes(text_content, "utf-8"), content_type="text/plain")

    form = CircularityDataForm(data=form_data, files={"circularity_file": text_file})
    assert not form.is_valid()
    assert "Only JSON files are allowed" in form.errors.get("circularity_file")[0]
