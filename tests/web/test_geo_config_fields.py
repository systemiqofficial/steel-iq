"""Test that Django form GeoConfig fields correctly map to GeoConfig parameters."""

import pytest
from django.forms import DecimalField, BooleanField, ChoiceField, IntegerField
from steeloweb.models import DataPreparation, DataPackage
from steeloweb.forms import ModelRunCreateForm


TRANSPORT_COST_FIELDS = {
    "iron_mine_to_plant": 0.013,
    "iron_to_steel_plant": 0.015,
    "steel_to_demand": 0.019,
}


def build_config_from_form(form):
    """Replicate view logic for serialising form data into the ModelRun config."""
    exclude_fields = {"data_preparation"}
    config = {}

    for field_name, value in form.cleaned_data.items():
        if field_name in exclude_fields or field_name in TRANSPORT_COST_FIELDS:
            continue
        if value is None:
            continue
        if hasattr(value, "is_finite") and value.is_finite():  # Decimal check
            value = float(value)
        config[field_name] = value

    transport_costs = {}
    for field_name, default in TRANSPORT_COST_FIELDS.items():
        raw_value = form.cleaned_data.get(field_name)
        if raw_value is None:
            transport_costs[field_name] = default
        else:
            transport_costs[field_name] = float(raw_value) if hasattr(raw_value, "is_finite") else float(raw_value)

    config["transportation_cost_per_km_per_ton"] = transport_costs
    return config


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


@pytest.fixture
def base_form_data(data_preparation):
    """Base form data with all required fields for tests."""
    return {
        "name": "Test Run",
        "start_year": 2025,
        "end_year": 2030,
        "data_preparation": data_preparation.pk,
        # Required technology fields
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
        # Economic parameters
        "plant_lifetime": 20,
        "global_risk_free_rate": 0.0209,
    }


# === Field Existence Tests ===


def test_hydrogen_ceiling_percentile_field_exists(db):
    """Test that hydrogen_ceiling_percentile field exists in the form."""
    form = ModelRunCreateForm()
    assert "hydrogen_ceiling_percentile" in form.fields


def test_included_power_mix_field_exists(db):
    """Test that included_power_mix field exists in the form."""
    form = ModelRunCreateForm()
    assert "included_power_mix" in form.fields


def test_intraregional_trade_allowed_field_exists(db):
    """Test that intraregional_trade_allowed field exists in the form."""
    form = ModelRunCreateForm()
    assert "intraregional_trade_allowed" in form.fields


def test_long_dist_pipeline_transport_cost_field_exists(db):
    """Test that long_dist_pipeline_transport_cost field exists in the form."""
    form = ModelRunCreateForm()
    assert "long_dist_pipeline_transport_cost" in form.fields


def test_iron_mine_to_plant_field_exists(db):
    """Test that iron_mine_to_plant field exists in the form."""
    form = ModelRunCreateForm()
    assert "iron_mine_to_plant" in form.fields


def test_iron_to_steel_plant_field_exists(db):
    """Test that iron_to_steel_plant field exists in the form."""
    form = ModelRunCreateForm()
    assert "iron_to_steel_plant" in form.fields


def test_steel_to_demand_field_exists(db):
    """Test that steel_to_demand field exists in the form."""
    form = ModelRunCreateForm()
    assert "steel_to_demand" in form.fields


def test_include_infrastructure_cost_field_exists(db):
    """Test that include_infrastructure_cost field exists in the form."""
    form = ModelRunCreateForm()
    assert "include_infrastructure_cost" in form.fields


def test_include_transport_cost_field_exists(db):
    """Test that include_transport_cost field exists in the form."""
    form = ModelRunCreateForm()
    assert "include_transport_cost" in form.fields


def test_include_lulc_cost_field_exists(db):
    """Test that include_lulc_cost field exists in the form."""
    form = ModelRunCreateForm()
    assert "include_lulc_cost" in form.fields


def test_max_altitude_field_exists(db):
    """Test that max_altitude field exists in the form."""
    form = ModelRunCreateForm()
    assert "max_altitude" in form.fields


def test_max_slope_field_exists(db):
    """Test that max_slope field exists in the form."""
    form = ModelRunCreateForm()
    assert "max_slope" in form.fields


def test_max_latitude_field_exists(db):
    """Test that max_latitude field exists in the form."""
    form = ModelRunCreateForm()
    assert "max_latitude" in form.fields


# === Field Type Tests ===


def test_hydrogen_ceiling_percentile_field_type(db):
    """Test that hydrogen_ceiling_percentile field is a DecimalField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("hydrogen_ceiling_percentile")

    assert isinstance(field, DecimalField)
    assert field.min_value == 0.0
    assert field.max_value == 100.0
    assert field.decimal_places == 1
    assert field.max_digits == 5


def test_included_power_mix_field_type(db):
    """Test that included_power_mix field is a ChoiceField with correct options."""
    form = ModelRunCreateForm()
    field = form.fields.get("included_power_mix")

    assert isinstance(field, ChoiceField)
    expected_choices = [
        ("85% baseload + 15% grid", "85% baseload + 15% grid"),
        ("95% baseload + 5% grid", "95% baseload + 5% grid"),
        ("Not included", "Not included"),
        ("Grid only", "Grid only"),
    ]
    assert field.choices == expected_choices


def test_intraregional_trade_allowed_field_type(db):
    """Test that intraregional_trade_allowed field is a BooleanField."""
    form = ModelRunCreateForm()
    field = form.fields.get("intraregional_trade_allowed")

    assert isinstance(field, BooleanField)


def test_long_dist_pipeline_transport_cost_field_type(db):
    """Test that long_dist_pipeline_transport_cost field is a DecimalField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("long_dist_pipeline_transport_cost")

    assert isinstance(field, DecimalField)
    assert field.min_value == 0.0
    assert field.max_value == 10.0
    assert field.decimal_places == 1
    assert field.max_digits == 3


def test_iron_mine_to_plant_field_type(db):
    """Test that iron_mine_to_plant field is a DecimalField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("iron_mine_to_plant")

    assert isinstance(field, DecimalField)
    assert field.min_value == 0.0
    assert field.max_value == 10.0
    assert field.decimal_places == 3
    assert field.max_digits == 6


def test_iron_to_steel_plant_field_type(db):
    """Test that iron_to_steel_plant field is a DecimalField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("iron_to_steel_plant")

    assert isinstance(field, DecimalField)
    assert field.min_value == 0.0
    assert field.max_value == 10.0
    assert field.decimal_places == 3
    assert field.max_digits == 6


def test_steel_to_demand_field_type(db):
    """Test that steel_to_demand field is a DecimalField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("steel_to_demand")

    assert isinstance(field, DecimalField)
    assert field.min_value == 0.0
    assert field.max_value == 10.0
    assert field.decimal_places == 3
    assert field.max_digits == 6


def test_include_infrastructure_cost_field_type(db):
    """Test that include_infrastructure_cost field is a BooleanField."""
    form = ModelRunCreateForm()
    field = form.fields.get("include_infrastructure_cost")

    assert isinstance(field, BooleanField)


def test_include_transport_cost_field_type(db):
    """Test that include_transport_cost field is a BooleanField."""
    form = ModelRunCreateForm()
    field = form.fields.get("include_transport_cost")

    assert isinstance(field, BooleanField)


def test_include_lulc_cost_field_type(db):
    """Test that include_lulc_cost field is a BooleanField."""
    form = ModelRunCreateForm()
    field = form.fields.get("include_lulc_cost")

    assert isinstance(field, BooleanField)


def test_max_altitude_field_type(db):
    """Test that max_altitude field is an IntegerField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("max_altitude")

    assert isinstance(field, IntegerField)
    assert field.min_value == 0
    assert field.max_value == 10000


def test_max_slope_field_type(db):
    """Test that max_slope field is a DecimalField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("max_slope")

    assert isinstance(field, DecimalField)
    assert field.min_value == 0.0
    assert field.max_value == 90.0  # Max slope in degrees is 90° (vertical)
    assert field.decimal_places == 1


# === Default Value Tests ===


def test_hydrogen_ceiling_percentile_default_value(db):
    """Test that hydrogen_ceiling_percentile has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("hydrogen_ceiling_percentile")
    assert field.initial == 20.0


def test_included_power_mix_default_value(db):
    """Test that included_power_mix has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("included_power_mix")
    assert field.initial == "85% baseload + 15% grid"


def test_intraregional_trade_allowed_default_value(db):
    """Test that intraregional_trade_allowed has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("intraregional_trade_allowed")
    assert field.initial is True


def test_long_dist_pipeline_transport_cost_default_value(db):
    """Test that long_dist_pipeline_transport_cost has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("long_dist_pipeline_transport_cost")
    assert field.initial == 1.0


def test_include_infrastructure_cost_default_value(db):
    """Test that include_infrastructure_cost has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("include_infrastructure_cost")
    assert field.initial is True


def test_include_transport_cost_default_value(db):
    """Test that include_transport_cost has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("include_transport_cost")
    assert field.initial is True


def test_include_lulc_cost_default_value(db):
    """Test that include_lulc_cost has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("include_lulc_cost")
    assert field.initial is True


def test_iron_mine_to_plant_default_value(db):
    """Test that iron_mine_to_plant has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("iron_mine_to_plant")
    assert field.initial == 0.013


def test_iron_to_steel_plant_default_value(db):
    """Test that iron_to_steel_plant has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("iron_to_steel_plant")
    assert field.initial == 0.015


def test_steel_to_demand_default_value(db):
    """Test that steel_to_demand has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("steel_to_demand")
    assert field.initial == 0.019


def test_max_altitude_default_value(db):
    """Test that max_altitude has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("max_altitude")
    assert field.initial == 1500


def test_max_slope_default_value(db):
    """Test that max_slope has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("max_slope")
    assert field.initial == 2.0


# === Field Boundary Validation Tests ===


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        ("hydrogen_ceiling_percentile", -1.0),
        ("hydrogen_ceiling_percentile", 101.0),
        ("long_dist_pipeline_transport_cost", -0.1),
        ("long_dist_pipeline_transport_cost", 10.1),
        ("iron_mine_to_plant", -0.001),
        ("iron_mine_to_plant", 10.001),
        ("iron_to_steel_plant", -0.001),
        ("iron_to_steel_plant", 10.001),
        ("steel_to_demand", -0.001),
        ("steel_to_demand", 10.001),
        ("max_altitude", -1),
        ("max_altitude", 10001),
        ("max_slope", -0.1),
        ("max_slope", 100.1),
    ],
)
def test_field_boundary_validation_invalid_values(db, base_form_data, field_name, invalid_value):
    """Test that form validation catches invalid boundary values."""
    form_data = base_form_data.copy()
    form_data[field_name] = invalid_value

    form = ModelRunCreateForm(data=form_data)
    assert not form.is_valid()
    assert field_name in form.errors


@pytest.mark.parametrize(
    "field_name,valid_value",
    [
        ("hydrogen_ceiling_percentile", 0.0),
        ("hydrogen_ceiling_percentile", 50.5),
        ("hydrogen_ceiling_percentile", 100.0),
        ("long_dist_pipeline_transport_cost", 0.0),
        ("long_dist_pipeline_transport_cost", 5.5),
        ("long_dist_pipeline_transport_cost", 10.0),
        ("iron_mine_to_plant", 0.0),
        ("iron_mine_to_plant", 0.5),
        ("iron_mine_to_plant", 10.0),
        ("iron_to_steel_plant", 0.0),
        ("iron_to_steel_plant", 0.5),
        ("iron_to_steel_plant", 10.0),
        ("steel_to_demand", 0.0),
        ("steel_to_demand", 0.5),
        ("steel_to_demand", 10.0),
        ("max_altitude", 0),
        ("max_altitude", 5000),
        ("max_altitude", 10000),
        ("max_slope", 0.0),
        ("max_slope", 50.5),
        ("max_slope", 90.0),  # Max slope in degrees is 90° (vertical)
    ],
)
def test_field_boundary_validation_valid_values(db, base_form_data, field_name, valid_value):
    """Test that form validation accepts valid boundary values."""
    form_data = base_form_data.copy()
    form_data[field_name] = valid_value

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"


def test_included_power_mix_choice_validation_valid(db, base_form_data):
    """Test that included_power_mix accepts valid choices."""
    valid_choices = ["85% baseload + 15% grid", "95% baseload + 5% grid", "Not included", "Grid only"]

    for choice in valid_choices:
        form_data = base_form_data.copy()
        form_data["included_power_mix"] = choice

        form = ModelRunCreateForm(data=form_data)
        assert form.is_valid(), f"Form errors for choice '{choice}': {form.errors}"


def test_included_power_mix_choice_validation_invalid(db, base_form_data):
    """Test that included_power_mix rejects invalid choices."""
    form_data = base_form_data.copy()
    form_data["included_power_mix"] = "invalid_choice"

    form = ModelRunCreateForm(data=form_data)
    assert not form.is_valid()
    assert "included_power_mix" in form.errors


# === Field Propagation Tests ===


def test_hydrogen_ceiling_percentile_propagation(db, base_form_data):
    """Test that hydrogen_ceiling_percentile is correctly passed to model config."""
    form_data = base_form_data.copy()
    form_data["hydrogen_ceiling_percentile"] = 35.5

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = build_config_from_form(form)
    modelrun.save()

    assert modelrun.config["hydrogen_ceiling_percentile"] == 35.5


def test_included_power_mix_propagation(db, base_form_data):
    """Test that included_power_mix is correctly passed to model config."""
    form_data = base_form_data.copy()
    form_data["included_power_mix"] = "95% baseload + 5% grid"

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = build_config_from_form(form)
    modelrun.save()

    assert modelrun.config["included_power_mix"] == "95% baseload + 5% grid"


def test_boolean_fields_propagation(db, base_form_data):
    """Test that boolean GeoConfig fields are correctly passed to model config."""
    boolean_fields = [
        "intraregional_trade_allowed",
        "include_infrastructure_cost",
        "include_transport_cost",
        "include_lulc_cost",
    ]

    for field_name in boolean_fields:
        # Test True value
        form_data = base_form_data.copy()
        form_data[field_name] = True

        form = ModelRunCreateForm(data=form_data)
        assert form.is_valid(), f"Form errors for {field_name}=True: {form.errors}"

        modelrun = form.save(commit=False)
        modelrun.config = build_config_from_form(form)
        modelrun.save()

        assert modelrun.config[field_name] is True, f"Failed for {field_name}=True"

        # Test False value
        form_data[field_name] = False

        form = ModelRunCreateForm(data=form_data)
        assert form.is_valid(), f"Form errors for {field_name}=False: {form.errors}"

        modelrun = form.save(commit=False)
        modelrun.config = build_config_from_form(form)
        modelrun.save()

        assert modelrun.config[field_name] is False, f"Failed for {field_name}=False"


def test_long_dist_pipeline_transport_cost_propagation(db, base_form_data):
    """Test that long_dist_pipeline_transport_cost is correctly passed to model config."""
    form_data = base_form_data.copy()
    form_data["long_dist_pipeline_transport_cost"] = 2.5

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = build_config_from_form(form)
    modelrun.save()

    assert modelrun.config["long_dist_pipeline_transport_cost"] == 2.5


def test_max_altitude_propagation(db, base_form_data):
    """Test that max_altitude is correctly passed to model config."""
    form_data = base_form_data.copy()
    form_data["max_altitude"] = 2500

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = build_config_from_form(form)
    modelrun.save()

    assert modelrun.config["max_altitude"] == 2500


def test_max_slope_propagation(db, base_form_data):
    """Test that max_slope is correctly passed to model config."""
    form_data = base_form_data.copy()
    form_data["max_slope"] = 5.5

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = build_config_from_form(form)
    modelrun.save()

    assert modelrun.config["max_slope"] == 5.5


def test_priority_pct_propagation(db, base_form_data):
    """Test that priority_pct is correctly passed to model config."""
    form_data = base_form_data.copy()
    form_data["priority_pct"] = 15

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = build_config_from_form(form)
    modelrun.save()

    assert modelrun.config["priority_pct"] == 15


def test_priority_pct_default_propagation(db, base_form_data):
    """Test that priority_pct defaults to 5 when not specified."""
    form_data = base_form_data.copy()
    # Don't set priority_pct

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = build_config_from_form(form)
    modelrun.save()

    # Default value should be 5
    assert modelrun.config.get("priority_pct", 5) == 5


# === Default Value Propagation Tests ===


def test_default_values_propagation(db, base_form_data):
    """Test that default values are correctly used when fields are not explicitly set."""
    # Set explicit GeoConfig field values using their defaults
    form_data = base_form_data.copy()
    form_data.update(
        {
            "hydrogen_ceiling_percentile": 20.0,
            "included_power_mix": "85% baseload + 15% grid",
            "intraregional_trade_allowed": True,
            "long_dist_pipeline_transport_cost": 1.0,
            "iron_mine_to_plant": 0.013,
            "iron_to_steel_plant": 0.015,
            "steel_to_demand": 0.019,
            "include_infrastructure_cost": True,
            "include_transport_cost": True,
            "include_lulc_cost": True,
            "max_altitude": 1500,
            "max_slope": 2.0,
        }
    )

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = build_config_from_form(form)
    modelrun.save()

    # Check that default values are present
    expected_defaults = {
        "hydrogen_ceiling_percentile": 20.0,
        "included_power_mix": "85% baseload + 15% grid",
        "intraregional_trade_allowed": True,
        "long_dist_pipeline_transport_cost": 1.0,
        "include_infrastructure_cost": True,
        "include_transport_cost": True,
        "include_lulc_cost": True,
        "max_altitude": 1500,
        "max_slope": 2.0,
    }

    for field_name, expected_value in expected_defaults.items():
        assert field_name in modelrun.config, f"Field {field_name} missing from config"
        assert modelrun.config[field_name] == expected_value, (
            f"Wrong default for {field_name}: got {modelrun.config[field_name]}, expected {expected_value}"
        )

    assert modelrun.config["transportation_cost_per_km_per_ton"] == {
        "iron_mine_to_plant": 0.013,
        "iron_to_steel_plant": 0.015,
        "steel_to_demand": 0.019,
    }


# === GeoConfig Integration Tests ===


def test_geo_params_list_completeness(db):
    """Test that all GeoConfig fields are included in the geo_params list."""
    # Import here to avoid circular imports
    from steelo.simulation import GeoConfig

    # Get all GeoConfig dataclass fields
    geo_config_fields = {field.name for field in GeoConfig.__dataclass_fields__.values()}

    # Get fields that should be included from the form (excluding complex fields like dicts)
    form = ModelRunCreateForm()

    # These are the fields we expect to be propagated from form to GeoConfig
    expected_form_geo_fields = {
        "hydrogen_ceiling_percentile",
        "included_power_mix",
        "intraregional_trade_allowed",
        "long_dist_pipeline_transport_cost",
        "include_infrastructure_cost",
        "include_transport_cost",
        "include_lulc_cost",
        "max_altitude",
        "max_slope",
        "max_latitude",
    }

    # Check that all expected fields exist in the form
    for field_name in expected_form_geo_fields:
        assert field_name in form.fields, f"Field {field_name} missing from form"
        assert field_name in geo_config_fields, f"Field {field_name} not found in GeoConfig dataclass"


def test_geo_config_creation_with_form_data(db, base_form_data):
    """Test that GeoConfig can be created with form data."""
    from steelo.simulation import GeoConfig

    form_data = base_form_data.copy()
    # Set custom values for all GeoConfig fields
    form_data.update(
        {
            "hydrogen_ceiling_percentile": 30.0,
            "included_power_mix": "95% baseload + 5% grid",
            "intraregional_trade_allowed": False,
            "long_dist_pipeline_transport_cost": 1.5,
            "iron_mine_to_plant": 0.0,
            "iron_to_steel_plant": 0.025,
            "steel_to_demand": 0.031,
            "include_infrastructure_cost": False,
            "include_transport_cost": False,
            "include_lulc_cost": False,
            "max_altitude": 2000,
            "max_slope": 3.0,
        }
    )

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # Extract geo parameters as they would be in the ModelRun.to_simulation_config method
    geo_params = [
        "max_altitude",
        "max_slope",
        "max_latitude",
        "hydrogen_ceiling_percentile",
        "included_power_mix",
        "intraregional_trade_allowed",
        "long_dist_pipeline_transport_cost",
        "include_infrastructure_cost",
        "include_transport_cost",
        "include_lulc_cost",
    ]

    geo_config_data = {}
    for param in geo_params:
        if param in form.cleaned_data and form.cleaned_data[param] is not None:
            value = form.cleaned_data[param]
            # Convert Decimal to float for compatibility
            if hasattr(value, "is_finite") and value.is_finite():  # Decimal check
                value = float(value)
            geo_config_data[param] = value

    transport_fields = {
        "iron_mine_to_plant": "iron_mine_to_plant",
        "iron_to_steel_plant": "iron_to_steel_plant",
        "steel_to_demand": "steel_to_demand",
    }
    transport_costs = {}
    for form_field, dict_key in transport_fields.items():
        value = form.cleaned_data.get(form_field)
        if value is not None:
            transport_costs[dict_key] = float(value)

    if transport_costs:
        geo_config_data["transportation_cost_per_km_per_ton"] = transport_costs

    # Test that GeoConfig can be created with this data
    geo_config = GeoConfig(**geo_config_data)

    # Verify the values were set correctly
    assert geo_config.hydrogen_ceiling_percentile == 30.0
    assert geo_config.included_power_mix == "95% baseload + 5% grid"
    assert geo_config.intraregional_trade_allowed is False
    assert geo_config.long_dist_pipeline_transport_cost == 1.5
    assert geo_config.include_infrastructure_cost is False
    assert geo_config.include_transport_cost is False
    assert geo_config.include_lulc_cost is False
    assert geo_config.max_altitude == 2000.0
    assert geo_config.max_slope == 3.0
    assert geo_config.transportation_cost_per_km_per_ton["iron_mine_to_plant"] == 0.0
    assert geo_config.transportation_cost_per_km_per_ton["iron_to_steel_plant"] == 0.025
    assert geo_config.transportation_cost_per_km_per_ton["steel_to_demand"] == 0.031


def test_geo_params_list_matches_implementation(db):
    """Test that geo_params list in models.py includes all necessary fields."""
    # This test ensures the geo_params list in models.py is complete
    expected_geo_params = {
        "hydrogen_ceiling_percentile",
        "included_power_mix",
        "intraregional_trade_allowed",
        "long_dist_pipeline_transport_cost",
        "include_infrastructure_cost",
        "include_transport_cost",
        "include_lulc_cost",
        "max_altitude",
        "max_slope",
        "max_latitude",
    }

    # Check that the form has the expected fields in its Meta.fields
    form = ModelRunCreateForm()
    form_fields = set(form.Meta.fields)

    # All expected geo params should be in the form's Meta.fields
    for field in expected_geo_params:
        assert field in form_fields, f"Field {field} missing from ModelRunCreateForm.Meta.fields"


def test_form_field_widget_classes(db):
    """Test that form fields have the correct CSS classes for UI indication."""
    form = ModelRunCreateForm()

    # All GeoConfig fields should be marked as "connected" (working)
    connected_fields = [
        "hydrogen_ceiling_percentile",
        "included_power_mix",
        "intraregional_trade_allowed",
        "long_dist_pipeline_transport_cost",
        "iron_mine_to_plant",
        "iron_to_steel_plant",
        "steel_to_demand",
        "include_infrastructure_cost",
        "include_transport_cost",
        "include_lulc_cost",
        "max_altitude",
        "max_slope",
        "max_latitude",
    ]

    for field_name in connected_fields:
        field = form.fields[field_name]
        widget_class = field.widget.attrs.get("class", "")
        assert "field-connected" in widget_class, (
            f"Field {field_name} should have 'field-connected' class, got: {widget_class}"
        )
        assert "field-not-connected" not in widget_class, (
            f"Field {field_name} should not have 'field-not-connected' class"
        )


def test_transportation_costs_propagate_to_geo_config(db, base_form_data):
    """Test that transport cost inputs populate the GeoConfig dictionary, preserving zero values."""
    from steelo.simulation import GeoConfig

    form_data = base_form_data.copy()
    form_data.update(
        {
            "iron_mine_to_plant": 0.0,
            "iron_to_steel_plant": 0.024,
            "steel_to_demand": 0.029,
        }
    )

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)
    modelrun.config = build_config_from_form(form)
    modelrun.save()

    transport_costs = modelrun.config["transportation_cost_per_km_per_ton"]
    assert transport_costs == {
        "iron_mine_to_plant": 0.0,
        "iron_to_steel_plant": 0.024,
        "steel_to_demand": 0.029,
    }

    geo_config = GeoConfig(transportation_cost_per_km_per_ton=transport_costs)
    assert geo_config.transportation_cost_per_km_per_ton["iron_mine_to_plant"] == 0.0
    assert geo_config.transportation_cost_per_km_per_ton["iron_to_steel_plant"] == 0.024
    assert geo_config.transportation_cost_per_km_per_ton["steel_to_demand"] == 0.029
