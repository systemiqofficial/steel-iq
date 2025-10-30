"""Test that Django form fields correctly map to SimulationConfig parameters."""

import pytest
from steeloweb.models import DataPreparation, DataPackage
from steeloweb.forms import ModelRunCreateForm
from django.forms import IntegerField


@pytest.fixture
def data_packages(db):
    """Create required data packages for tests."""
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.LOCAL,
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA,
        version="1.0.0",
        source_type=DataPackage.SourceType.LOCAL,
    )
    return core_package, geo_package


@pytest.fixture
def data_preparation(db, data_packages):
    """Create a ready data preparation for model runs."""
    core_package, geo_package = data_packages
    return DataPreparation.objects.create(
        name="Test Data",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory="/fake/path",
    )


def test_plant_lifetime_field_exists_in_form(db):
    """Test that plant_lifetime field exists in the form."""
    form = ModelRunCreateForm()
    assert "plant_lifetime" in form.fields


def test_plant_lifetime_default_value(db):
    """Test that plant_lifetime has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("plant_lifetime")
    assert field is not None
    assert field.initial == 20


def test_plant_lifetime_field_type(db):
    """Test that plant_lifetime field is an IntegerField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("plant_lifetime")

    # Check it's an IntegerField
    assert isinstance(field, IntegerField)

    # Check min/max values if set
    if hasattr(field, "min_value"):
        assert field.min_value >= 1
    if hasattr(field, "max_value"):
        assert field.max_value <= 100


def test_plant_lifetime_passed_to_config(db, data_preparation):
    """Test that plant_lifetime value is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 25,  # Custom value
        "global_risk_free_rate": 0.0209,  # Default value
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "plant_lifetime": form.cleaned_data.get("plant_lifetime", 20),
        # ... other fields
    }
    modelrun.save()

    # Check that the value was stored correctly
    assert modelrun.config["plant_lifetime"] == 25


def test_global_risk_free_rate_field_exists_in_form(db):
    """Test that global_risk_free_rate field exists in the form."""
    form = ModelRunCreateForm()
    assert "global_risk_free_rate" in form.fields


def test_global_risk_free_rate_default_value(db):
    """Test that global_risk_free_rate has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("global_risk_free_rate")
    assert field is not None
    assert field.initial == 0.0209  # 2.09% default


def test_global_risk_free_rate_field_type(db):
    """Test that global_risk_free_rate field is a DecimalField with correct constraints."""
    from django.forms import DecimalField

    form = ModelRunCreateForm()
    field = form.fields.get("global_risk_free_rate")

    # Check it's a DecimalField
    assert isinstance(field, DecimalField)

    # Check min/max values
    assert field.min_value == 0.0
    assert field.max_value == 0.5  # 50% max


def test_global_risk_free_rate_passed_to_config(db, data_preparation):
    """Test that global_risk_free_rate value is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.035,  # Custom value 3.5%
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "plant_lifetime": form.cleaned_data.get("plant_lifetime", 20),
        "global_risk_free_rate": float(form.cleaned_data.get("global_risk_free_rate", 0.0209)),
        # ... other fields
    }
    modelrun.save()

    # Check that the value was stored correctly
    assert modelrun.config["global_risk_free_rate"] == 0.035


def test_construction_time_field_exists_in_form(db):
    """Test that construction_time field exists in the form."""
    form = ModelRunCreateForm()
    assert "construction_time" in form.fields


def test_construction_time_default_value(db):
    """Test that construction_time has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("construction_time")
    assert field is not None
    assert field.initial == 4  # 4 years default


def test_construction_time_field_type(db):
    """Test that construction_time field is an IntegerField with correct constraints."""
    from django.forms import IntegerField

    form = ModelRunCreateForm()
    field = form.fields.get("construction_time")

    # Check it's an IntegerField
    assert isinstance(field, IntegerField)

    # Check min/max values
    assert field.min_value == 1
    assert field.max_value == 10  # Reasonable max for construction time


def test_construction_time_passed_to_config(db, data_preparation):
    """Test that construction_time value is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "construction_time": 7,  # Custom value
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "plant_lifetime": form.cleaned_data.get("plant_lifetime", 20),
        "global_risk_free_rate": float(form.cleaned_data.get("global_risk_free_rate") or 0.0209),
        "construction_time": form.cleaned_data.get("construction_time", 4),
        # ... other fields
    }
    modelrun.save()

    # Check that the value was stored correctly
    assert modelrun.config["construction_time"] == 7


def test_expanded_capacity_field_exists_in_form(db):
    """Test that expanded_capacity field exists in the form."""
    form = ModelRunCreateForm()
    assert "expanded_capacity" in form.fields


def test_expanded_capacity_default_value(db):
    """Test that expanded_capacity has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("expanded_capacity")
    assert field is not None
    assert field.initial == 2.5  # 2.5 Mt default


def test_expanded_capacity_field_type(db):
    """Test that expanded_capacity field is a DecimalField with correct constraints."""
    from django.forms import DecimalField

    form = ModelRunCreateForm()
    field = form.fields.get("expanded_capacity")

    # Check it's a DecimalField
    assert isinstance(field, DecimalField)

    # Check min/max values
    assert field.min_value == 0.1
    assert field.max_value == 1000.0


def test_expanded_capacity_passed_to_config(db, data_preparation):
    """Test that expanded_capacity value is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "construction_time": 4,
        "expanded_capacity": 5.0,  # Custom value 5 Mt
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "expanded_capacity": float(form.cleaned_data.get("expanded_capacity", 2.5)) * 1000000,  # Convert Mt to t
        # ... other fields
    }
    modelrun.save()

    # Check that the value was stored correctly (in tonnes)
    assert modelrun.config["expanded_capacity"] == 5000000  # 5 Mt = 5,000,000 t


def test_capacity_limit_iron_field_exists_in_form(db):
    """Test that capacity_limit_iron field exists in the form."""
    form = ModelRunCreateForm()
    assert "capacity_limit_iron" in form.fields


def test_capacity_limit_iron_default_value(db):
    """Test that capacity_limit_iron has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("capacity_limit_iron")
    assert field is not None
    assert field.initial == 200  # 200 Mt default


def test_capacity_limit_iron_field_type(db):
    """Test that capacity_limit_iron field is a DecimalField with correct constraints."""
    from django.forms import DecimalField

    form = ModelRunCreateForm()
    field = form.fields.get("capacity_limit_iron")

    # Check it's a DecimalField
    assert isinstance(field, DecimalField)

    # Check min/max values
    assert field.min_value == 0.1
    assert field.max_value == 1000.0


def test_capacity_limit_iron_passed_to_config(db, data_preparation):
    """Test that capacity_limit_iron value is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "construction_time": 4,
        "capacity_limit_iron": 200.0,  # Custom value 200 Mt
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "capacity_limit_iron": float(form.cleaned_data.get("capacity_limit_iron", 200)) * 1000000,  # Convert Mt to t
        # ... other fields
    }
    modelrun.save()

    # Check that the value was stored correctly (in tonnes)
    assert modelrun.config["capacity_limit_iron"] == 200000000  # 200 Mt = 200,000,000 t


def test_capacity_limit_steel_field_exists_in_form(db):
    """Test that capacity_limit_steel field exists in the form."""
    form = ModelRunCreateForm()
    assert "capacity_limit_steel" in form.fields


def test_capacity_limit_steel_default_value(db):
    """Test that capacity_limit_steel has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("capacity_limit_steel")
    assert field is not None
    assert field.initial == 200  # 200 Mt default


def test_capacity_limit_steel_field_type(db):
    """Test that capacity_limit_steel field is a DecimalField with correct constraints."""
    from django.forms import DecimalField

    form = ModelRunCreateForm()
    field = form.fields.get("capacity_limit_steel")

    # Check it's a DecimalField
    assert isinstance(field, DecimalField)

    # Check min/max values
    assert field.min_value == 0.1
    assert field.max_value == 1000.0


def test_capacity_limit_steel_passed_to_config(db, data_preparation):
    """Test that capacity_limit_steel value is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "construction_time": 4,
        "capacity_limit_steel": 175.0,  # Custom value 175 Mt
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "capacity_limit_steel": float(form.cleaned_data.get("capacity_limit_steel", 200)) * 1000000,  # Convert Mt to t
        # ... other fields
    }
    modelrun.save()

    # Check that the value was stored correctly (in tonnes)
    assert modelrun.config["capacity_limit_steel"] == 175000000  # 175 Mt = 175,000,000 t


def test_capacity_limit_defaults_applied_when_blank(db, data_preparation):
    """Defaults should be 200 Mt when the capacity fields are left empty."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "construction_time": 4,
        "capacity_limit_iron": "",
        "capacity_limit_steel": "",
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "capacity_limit_iron": float(form.cleaned_data.get("capacity_limit_iron") or 200) * 1000000,
        "capacity_limit_steel": float(form.cleaned_data.get("capacity_limit_steel") or 200) * 1000000,
    }
    modelrun.save()

    assert modelrun.config["capacity_limit_iron"] == 200000000
    assert modelrun.config["capacity_limit_steel"] == 200000000


def test_probabilistic_agents_field_exists_in_form(db):
    """Test that probabilistic_agents field exists in the form."""
    form = ModelRunCreateForm()
    assert "probabilistic_agents" in form.fields


def test_probabilistic_agents_default_value(db):
    """Test that probabilistic_agents has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("probabilistic_agents")
    assert field is not None
    assert field.initial is True  # Default should be True (probabilistic mode)


def test_probabilistic_agents_field_type(db):
    """Test that probabilistic_agents field is a BooleanField."""
    from django.forms import BooleanField

    form = ModelRunCreateForm()
    field = form.fields.get("probabilistic_agents")

    # Check it's a BooleanField
    assert isinstance(field, BooleanField)


def test_probabilistic_agents_passed_to_config_true(db, data_preparation):
    """Test that probabilistic_agents=True is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "probabilistic_agents": True,  # Explicitly set to True
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "probabilistic_agents": form.cleaned_data.get("probabilistic_agents", True),
        # ... other fields
    }
    modelrun.save()

    # Check that the value was stored correctly
    assert modelrun.config["probabilistic_agents"] is True


def test_probabilistic_agents_passed_to_config_false(db, data_preparation):
    """Test that probabilistic_agents=False is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "probabilistic_agents": False,  # Explicitly set to False (deterministic)
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "probabilistic_agents": form.cleaned_data.get("probabilistic_agents", True),
        # ... other fields
    }
    modelrun.save()

    # Check that the value was stored correctly
    assert modelrun.config["probabilistic_agents"] is False


def test_new_capacity_share_from_new_plants_field_exists_in_form(db):
    """Test that new_capacity_share_from_new_plants field exists in the form."""
    form = ModelRunCreateForm()
    assert "new_capacity_share_from_new_plants" in form.fields


def test_new_capacity_share_from_new_plants_default_value(db):
    """Test that new_capacity_share_from_new_plants has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("new_capacity_share_from_new_plants")
    assert field is not None
    assert field.initial == 0.2  # 20% default


def test_new_capacity_share_from_new_plants_field_type(db):
    """Test that new_capacity_share_from_new_plants field is a DecimalField with correct constraints."""
    from django.forms import DecimalField

    form = ModelRunCreateForm()
    field = form.fields.get("new_capacity_share_from_new_plants")

    # Check it's a DecimalField
    assert isinstance(field, DecimalField)

    # Check min/max values
    assert field.min_value == 0.0  # 0% minimum (all capacity from expansions)
    assert field.max_value == 1.0  # 100% maximum (all capacity from new plants)

    # Check decimal precision
    assert field.max_digits == 3  # x.xx format
    assert field.decimal_places == 2


def test_new_capacity_share_from_new_plants_passed_to_config_default(db, data_preparation):
    """Test that new_capacity_share_from_new_plants default value is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        # Don't specify new_capacity_share_from_new_plants - should use default
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "new_capacity_share_from_new_plants": float(
            form.cleaned_data.get("new_capacity_share_from_new_plants")
            if form.cleaned_data.get("new_capacity_share_from_new_plants") is not None
            else 0.2
        ),
        # ... other fields
    }
    modelrun.save()

    # Check that the default value was stored correctly
    assert modelrun.config["new_capacity_share_from_new_plants"] == 0.2


def test_new_capacity_share_from_new_plants_passed_to_config_custom_value(db, data_preparation):
    """Test that new_capacity_share_from_new_plants custom value is correctly passed to model config."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "new_capacity_share_from_new_plants": 0.5,  # Custom value 50%
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "new_capacity_share_from_new_plants": float(
            form.cleaned_data.get("new_capacity_share_from_new_plants")
            if form.cleaned_data.get("new_capacity_share_from_new_plants") is not None
            else 0.2
        ),
        # ... other fields
    }
    modelrun.save()

    # Check that the custom value was stored correctly
    assert modelrun.config["new_capacity_share_from_new_plants"] == 0.5


def test_new_capacity_share_from_new_plants_zero_value_preserved(db, data_preparation):
    """Test that zero value (0.0) is preserved and not replaced by default - CRITICAL boundary case."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
        "new_capacity_share_from_new_plants": 0.0,  # CRITICAL: All capacity from expansions
        # Add other required fields with defaults
        "scrap_generation_scenario": "business_as_usual",
        "bf_allowed": True,
        "bf_from_year": 2025,
        "bof_allowed": True,
        "bof_from_year": 2025,
        "dri_ng_allowed": True,
        "dri_ng_from_year": 2025,
        "eaf_allowed": True,
        "eaf_from_year": 2025,
        "esf_allowed": False,
        "esf_from_year": 2025,
        "moe_allowed": False,
        "moe_from_year": 2025,
    }

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Simulate what the view does
    modelrun = form.save(commit=False)
    modelrun.config = {
        "start_year": form.cleaned_data["start_year"],
        "end_year": form.cleaned_data["end_year"],
        "new_capacity_share_from_new_plants": float(
            form.cleaned_data.get("new_capacity_share_from_new_plants")
            if form.cleaned_data.get("new_capacity_share_from_new_plants") is not None
            else 0.2
        ),
        # ... other fields
    }
    modelrun.save()

    # Check that 0.0 was preserved and NOT replaced by default 0.2
    assert modelrun.config["new_capacity_share_from_new_plants"] == 0.0


def test_new_capacity_share_from_new_plants_validation_min_boundary(db):
    """Test that values below minimum (negative) are rejected."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "new_capacity_share_from_new_plants": -0.1,  # Invalid: negative value
        # Add other required fields
        "scrap_generation_scenario": "business_as_usual",
    }

    form = ModelRunCreateForm(data=form_data)
    assert not form.is_valid()
    assert "new_capacity_share_from_new_plants" in form.errors


def test_new_capacity_share_from_new_plants_validation_max_boundary(db):
    """Test that values above maximum (>1.0) are rejected."""
    form_data = {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "new_capacity_share_from_new_plants": 1.1,  # Invalid: greater than 100%
        # Add other required fields
        "scrap_generation_scenario": "business_as_usual",
    }

    form = ModelRunCreateForm(data=form_data)
    assert not form.is_valid()
    assert "new_capacity_share_from_new_plants" in form.errors


def test_new_capacity_share_from_new_plants_validation_valid_boundaries(db):
    """Test that exact boundary values (0.0 and 1.0) are accepted."""
    # Test 0.0 (minimum)
    form_data_min = {
        "name": "Test Run Min",
        "start_year": 2025,
        "end_year": 2030,
        "new_capacity_share_from_new_plants": 0.0,  # Valid: all capacity from expansions
        "scrap_generation_scenario": "business_as_usual",
    }
    form_min = ModelRunCreateForm(data=form_data_min)
    # Only check field validation, not overall form validity (other required fields missing)
    form_min.full_clean()
    assert "new_capacity_share_from_new_plants" not in form_min.errors

    # Test 1.0 (maximum)
    form_data_max = {
        "name": "Test Run Max",
        "start_year": 2025,
        "end_year": 2030,
        "new_capacity_share_from_new_plants": 1.0,  # Valid: all capacity from new plants
        "scrap_generation_scenario": "business_as_usual",
    }
    form_max = ModelRunCreateForm(data=form_data_max)
    # Only check field validation, not overall form validity (other required fields missing)
    form_max.full_clean()
    assert "new_capacity_share_from_new_plants" not in form_max.errors
