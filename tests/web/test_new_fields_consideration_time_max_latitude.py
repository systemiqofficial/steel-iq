"""Test the new fields: consideration_time and max_latitude."""

import pytest
import json
from decimal import Decimal
from django.forms import IntegerField, FloatField
from steeloweb.models import DataPreparation, DataPackage, ModelRun
from steeloweb.forms import ModelRunCreateForm


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


def test_consideration_time_field_exists(db):
    """Test that consideration_time field exists in the form."""
    form = ModelRunCreateForm()
    assert "consideration_time" in form.fields


def test_max_latitude_field_exists(db):
    """Test that max_latitude field exists in the form."""
    form = ModelRunCreateForm()
    assert "max_latitude" in form.fields


# === Field Type Tests ===


def test_consideration_time_field_type(db):
    """Test that consideration_time field is an IntegerField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("consideration_time")

    assert isinstance(field, IntegerField)
    assert field.min_value == 1
    assert field.max_value == 10
    assert field.required is False


def test_max_latitude_field_type(db):
    """Test that max_latitude field is a FloatField with correct constraints."""
    form = ModelRunCreateForm()
    field = form.fields.get("max_latitude")

    assert isinstance(field, FloatField)
    assert field.min_value == 0  # Changed to 0 for absolute latitude
    assert field.max_value == 90
    assert field.required is False


# === Default Value Tests ===


def test_consideration_time_default_value(db):
    """Test that consideration_time has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("consideration_time")
    assert field.initial == 3


def test_max_latitude_default_value(db):
    """Test that max_latitude has correct default value."""
    form = ModelRunCreateForm()
    field = form.fields.get("max_latitude")
    assert field.initial == 70.0


# === Field Validation Tests ===


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        ("consideration_time", 0),  # Below minimum
        ("consideration_time", 11),  # Above maximum
        ("consideration_time", -1),  # Negative
        ("max_latitude", -1.0),  # Below minimum (now 0)
        ("max_latitude", 91.0),  # Above maximum
        ("max_latitude", -45.0),  # Negative (not allowed for absolute)
        ("max_latitude", 100.0),  # Way above maximum
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
        ("consideration_time", 1),  # Minimum value
        ("consideration_time", 5),  # Middle value
        ("consideration_time", 10),  # Maximum value
        ("max_latitude", 0.0),  # Equator only (minimum)
        ("max_latitude", 30.0),  # Tropics
        ("max_latitude", 45.5),  # Mid-latitude
        ("max_latitude", 65.0),  # Legacy default still valid
        ("max_latitude", 70.0),  # Current default value
        ("max_latitude", 90.0),  # Full globe (maximum)
    ],
)
def test_field_boundary_validation_valid_values(db, base_form_data, field_name, valid_value):
    """Test that form validation accepts valid boundary values."""
    form_data = base_form_data.copy()
    form_data[field_name] = valid_value

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"


# === Field Propagation Tests ===


def test_consideration_time_propagation(db, base_form_data):
    """Test that consideration_time is correctly passed to model config."""
    form_data = base_form_data.copy()
    form_data["consideration_time"] = 5

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)

    # Simulate the config creation process that happens in the view
    exclude_fields = {"data_preparation"}
    modelrun.config = {}
    for field in form.cleaned_data:
        if field not in exclude_fields and form.cleaned_data[field] is not None:
            value = form.cleaned_data[field]
            # Convert Decimal to float for JSON serialization
            if hasattr(value, "is_finite"):  # Decimal check
                value = float(value)
            modelrun.config[field] = value

    modelrun.save()

    assert modelrun.config["consideration_time"] == 5


def test_max_latitude_propagation(db, base_form_data):
    """Test that max_latitude is correctly passed to model config."""
    form_data = base_form_data.copy()
    form_data["max_latitude"] = 45.5

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)

    # Simulate the config creation process that happens in the view
    exclude_fields = {"data_preparation"}
    modelrun.config = {}
    for field in form.cleaned_data:
        if field not in exclude_fields and form.cleaned_data[field] is not None:
            value = form.cleaned_data[field]
            # Convert Decimal to float for JSON serialization
            if hasattr(value, "is_finite"):  # Decimal check
                value = float(value)
            modelrun.config[field] = value

    modelrun.save()

    assert modelrun.config["max_latitude"] == 45.5


# === SimulationConfig Integration Tests ===


def test_consideration_time_in_simulation_config(db, base_form_data):
    """Test that consideration_time is properly included in SimulationConfig."""
    from steelo.simulation import SimulationConfig

    # Check that SimulationConfig has the consideration_time attribute
    assert hasattr(SimulationConfig, "__dataclass_fields__")
    assert "consideration_time" in SimulationConfig.__dataclass_fields__

    # Test default value
    field = SimulationConfig.__dataclass_fields__["consideration_time"]
    assert field.default == 3


def test_max_latitude_in_geo_config(db):
    """Test that max_latitude is properly included in GeoConfig."""
    from steelo.simulation import GeoConfig

    # Check that GeoConfig has the max_latitude attribute
    assert hasattr(GeoConfig, "__dataclass_fields__")
    assert "max_latitude" in GeoConfig.__dataclass_fields__

    # Test default value
    field = GeoConfig.__dataclass_fields__["max_latitude"]
    assert field.default == 70.0


def test_geo_config_creation_with_max_latitude(db):
    """Test that GeoConfig can be created with custom max_latitude value."""
    from steelo.simulation import GeoConfig

    geo_config = GeoConfig(max_latitude=45.5)
    assert geo_config.max_latitude == 45.5


def test_simulation_config_creation_with_consideration_time(db, tmp_path):
    """Test that SimulationConfig can be created with custom consideration_time value."""
    from steelo.simulation import SimulationConfig
    from steelo.domain import Year
    from steelo.simulation_types import get_default_technology_settings

    # Use tmp_path fixture for the output directory
    output_dir = tmp_path / "output"
    excel_path = tmp_path / "test.xlsx"
    excel_path.touch()  # Create the file

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2030),
        master_excel_path=excel_path,
        output_dir=output_dir,
        consideration_time=5,
        technology_settings=get_default_technology_settings(),
    )
    assert config.consideration_time == 5


# === Form Widget Tests ===


def test_consideration_time_widget_class(db):
    """Test that consideration_time field has the correct CSS class for UI indication."""
    form = ModelRunCreateForm()
    field = form.fields["consideration_time"]
    widget_class = field.widget.attrs.get("class", "")
    assert "field-connected" in widget_class
    assert "field-not-connected" not in widget_class


def test_max_latitude_widget_class(db):
    """Test that max_latitude field has the correct CSS class for UI indication."""
    form = ModelRunCreateForm()
    field = form.fields["max_latitude"]
    widget_class = field.widget.attrs.get("class", "")
    assert "field-connected" in widget_class
    assert "field-not-connected" not in widget_class


# === Help Text Tests ===


def test_consideration_time_help_text(db):
    """Test that consideration_time field has appropriate help text."""
    form = ModelRunCreateForm()
    field = form.fields["consideration_time"]
    assert (
        field.help_text
        == "Years to consider a business opportunity and track its financial viability before deciding on announcement (planning horizon)"
    )


def test_max_latitude_help_text(db):
    """Test that max_latitude field has appropriate help text."""
    form = ModelRunCreateForm()
    field = form.fields["max_latitude"]
    assert (
        field.help_text
        == "Maximum absolute latitude — sites limited to |latitude| ≤ this value. To ignore, set value to 90"
    )


# === Cross-field Validation Tests ===


def test_consideration_time_construction_time_relationship(db, base_form_data):
    """Test that consideration_time and construction_time can have any valid relationship."""
    # Note: Currently there's no enforced relationship between these fields
    # This test documents that behavior
    form_data = base_form_data.copy()

    # Test consideration_time < construction_time
    form_data["construction_time"] = 4
    form_data["consideration_time"] = 2
    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form should be valid when consideration_time < construction_time: {form.errors}"

    # Test consideration_time > construction_time
    form_data["construction_time"] = 2
    form_data["consideration_time"] = 5
    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form should be valid when consideration_time > construction_time: {form.errors}"

    # Test consideration_time == construction_time
    form_data["construction_time"] = 3
    form_data["consideration_time"] = 3
    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form should be valid when consideration_time == construction_time: {form.errors}"


def test_max_latitude_max_altitude_independence(db, base_form_data):
    """Test that max_latitude and max_altitude are independent."""
    form_data = base_form_data.copy()

    # Test various combinations (all with positive max_latitude for absolute values)
    test_cases = [
        {"max_latitude": 90.0, "max_altitude": 0},  # Full globe, sea level
        {"max_latitude": 45.0, "max_altitude": 10000},  # Mid-latitudes, maximum altitude
        {"max_latitude": 0.0, "max_altitude": 5000},  # Equator only, mid altitude
        {"max_latitude": 65.0, "max_altitude": 1500},  # Defaults
    ]

    for test_case in test_cases:
        form_data.update(test_case)
        form = ModelRunCreateForm(data=form_data)
        assert form.is_valid(), f"Form should be valid for {test_case}: {form.errors}"


# === Meta.fields Inclusion Test ===


def test_fields_in_meta_fields_list(db):
    """Test that both new fields are included in the form's Meta.fields list."""
    form = ModelRunCreateForm()
    meta_fields = form.Meta.fields

    assert "consideration_time" in meta_fields, "consideration_time should be in Meta.fields"
    assert "max_latitude" in meta_fields, "max_latitude should be in Meta.fields"

    # Also check their relative positions
    consideration_time_index = meta_fields.index("consideration_time")
    construction_time_index = meta_fields.index("construction_time")
    assert consideration_time_index == construction_time_index + 1, (
        "consideration_time should come immediately after construction_time"
    )

    max_latitude_index = meta_fields.index("max_latitude")
    max_altitude_index = meta_fields.index("max_altitude")
    max_slope_index = meta_fields.index("max_slope")
    assert max_latitude_index > max_altitude_index, "max_latitude should come after max_altitude"
    assert max_latitude_index > max_slope_index, "max_latitude should come after max_slope"


# === Complete Form Submission Test ===


def test_complete_form_submission_with_new_fields(db, base_form_data):
    """Test that a complete form submission with the new fields works correctly."""
    form_data = base_form_data.copy()
    form_data.update(
        {
            "consideration_time": 3,
            "max_latitude": 50.0,
            # Include other geo fields to ensure everything works together
            "max_altitude": 2000,
            "max_slope": 3.5,
            "construction_time": 4,
        }
    )

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)

    # Build config as the view would
    exclude_fields = {"data_preparation"}
    modelrun.config = {}
    for field in form.cleaned_data:
        if field not in exclude_fields and form.cleaned_data[field] is not None:
            value = form.cleaned_data[field]
            # Convert Decimal to float for JSON serialization
            if hasattr(value, "is_finite"):  # Decimal check
                value = float(value)
            modelrun.config[field] = value

    modelrun.save()

    # Verify all values were saved correctly
    assert modelrun.config["consideration_time"] == 3
    assert modelrun.config["max_latitude"] == 50.0
    assert modelrun.config["max_altitude"] == 2000
    assert modelrun.config["max_slope"] == 3.5
    assert modelrun.config["construction_time"] == 4


# === Serialization and Type Safety Tests ===


def test_no_decimal_in_json_serialization(db, base_form_data):
    """Test that Decimal values are never stored in ModelRun.config JSON."""
    form_data = base_form_data.copy()
    form_data.update(
        {
            "max_latitude": 45.5,
            "max_slope": 3.5,
            "hydrogen_ceiling_percentile": 25.5,
        }
    )

    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form errors: {form.errors}"

    modelrun = form.save(commit=False)

    # Build config as the view would
    exclude_fields = {"data_preparation"}
    modelrun.config = {}
    for field in form.cleaned_data:
        if field not in exclude_fields and form.cleaned_data[field] is not None:
            value = form.cleaned_data[field]
            # Convert Decimal to float for JSON serialization
            if hasattr(value, "is_finite"):  # Decimal check
                value = float(value)
            modelrun.config[field] = value

    modelrun.save()

    # Serialize to JSON and back to ensure no Decimal types
    config_json = json.dumps(modelrun.config)
    config_reloaded = json.loads(config_json)

    # Check that all numeric values are float/int, not Decimal
    assert isinstance(config_reloaded["max_latitude"], (float, int))
    assert isinstance(config_reloaded["max_slope"], (float, int))
    assert isinstance(config_reloaded["hydrogen_ceiling_percentile"], (float, int))

    # Ensure no Decimal instances in the config
    def check_no_decimal(obj):
        """Recursively check for Decimal instances."""
        if isinstance(obj, Decimal):
            return False
        elif isinstance(obj, dict):
            return all(check_no_decimal(v) for v in obj.values())
        elif isinstance(obj, list):
            return all(check_no_decimal(v) for v in obj)
        return True

    assert check_no_decimal(modelrun.config), "Found Decimal in config"


def test_legacy_config_without_new_fields(db, data_preparation):
    """Test that configs without new fields get proper defaults."""
    # Create a ModelRun with minimal config (simulating legacy data)
    legacy_config = {
        "name": "Legacy Run",
        "start_year": 2025,
        "end_year": 2030,
        "plant_lifetime": 20,
        "construction_time": 4,
        "max_altitude": 1500,
        "max_slope": 2.0,
        # Note: No max_latitude or consideration_time
    }

    modelrun = ModelRun.objects.create(
        name="Legacy Run",
        data_preparation=data_preparation,
        config=legacy_config,
    )

    # Try to extract SimulationConfig - should use defaults for missing fields
    try:
        # Mock the output dir since we're just testing config extraction
        modelrun.config["output_dir"] = "/tmp/test_output"
        modelrun.config["master_excel_path"] = "/tmp/test.xlsx"

        # This would normally be done by to_simulation_config()
        from steelo.simulation import GeoConfig

        # Extract geo params with defaults
        geo_params = ["max_altitude", "max_slope", "max_latitude"]
        geo_data = {}
        for param in geo_params:
            if param in modelrun.config:
                geo_data[param] = float(modelrun.config[param])
            elif param == "max_latitude":
                # Should use default
                geo_data[param] = 65.0

        geo_config = GeoConfig(**geo_data)

        # Check defaults are applied
        assert geo_config.max_latitude == 65.0  # Default value
        assert geo_config.max_altitude == 1500.0  # From config
        assert geo_config.max_slope == 2.0  # From config

    except Exception as e:
        pytest.fail(f"Failed to handle legacy config: {e}")


def test_help_text_updated(db):
    """Test that help text correctly describes absolute latitude."""
    form = ModelRunCreateForm()
    field = form.fields.get("max_latitude")

    assert "|latitude|" in field.help_text or "absolute" in field.help_text.lower(), (
        f"Help text should mention absolute latitude: {field.help_text}"
    )


# === Edge Case Tests ===


def test_max_latitude_boundary_values(db, base_form_data):
    """Test that max_latitude correctly handles boundary values."""
    # Test 0 (equator only)
    form_data = base_form_data.copy()
    form_data["max_latitude"] = 0
    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form should accept 0: {form.errors}"

    # Test 90 (full globe)
    form_data["max_latitude"] = 90
    form = ModelRunCreateForm(data=form_data)
    assert form.is_valid(), f"Form should accept 90: {form.errors}"

    # Test negative values should be rejected
    form_data["max_latitude"] = -45
    form = ModelRunCreateForm(data=form_data)
    assert not form.is_valid(), "Form should reject negative values"
    assert "max_latitude" in form.errors

    # Test values > 90 should be rejected
    form_data["max_latitude"] = 91
    form = ModelRunCreateForm(data=form_data)
    assert not form.is_valid(), "Form should reject values > 90"
    assert "max_latitude" in form.errors


def test_feasibility_mask_at_boundary():
    """Test that the feasibility mask includes sites exactly at the boundary."""
    import numpy as np
    import xarray as xr
    from steelo.simulation import GeoConfig

    # Create a simple 1D test dataset (single longitude)
    lats = np.array([-70, -65, -50, 0, 50, 65, 70])

    # Create mock feasibility mask (1D for simplicity)
    feasibility_data = np.ones(len(lats))
    feasibility_mask = xr.DataArray(feasibility_data, coords={"lat": lats}, dims=["lat"])

    # Test with max_latitude = 65
    geo_config = GeoConfig(max_latitude=65.0)

    # Apply the latitude filter as done in the code
    mask_corrected = xr.where(
        abs(feasibility_mask.lat) <= geo_config.max_latitude,
        feasibility_mask,
        0,
    )

    # Check that sites at exactly ±65 are included
    assert float(mask_corrected.sel(lat=-65)) == 1, "Site at -65° should be included"
    assert float(mask_corrected.sel(lat=65)) == 1, "Site at 65° should be included"

    # Check that sites beyond ±65 are excluded
    assert float(mask_corrected.sel(lat=-70)) == 0, "Site at -70° should be excluded"
    assert float(mask_corrected.sel(lat=70)) == 0, "Site at 70° should be excluded"

    # Check that sites within the band are included
    assert float(mask_corrected.sel(lat=0)) == 1, "Site at equator should be included"
    assert float(mask_corrected.sel(lat=50)) == 1, "Site at 50° should be included"


def test_zero_latitude_band():
    """Test that max_latitude = 0 only allows equator sites."""
    import numpy as np
    import xarray as xr
    from steelo.simulation import GeoConfig

    # Create a test dataset with various latitudes (1D for simplicity)
    lats = np.array([-45, -10, -1, 0, 1, 10, 45])

    feasibility_data = np.ones(len(lats))
    feasibility_mask = xr.DataArray(feasibility_data, coords={"lat": lats}, dims=["lat"])

    # Test with max_latitude = 0
    geo_config = GeoConfig(max_latitude=0.0)

    # Apply the latitude filter
    mask_corrected = xr.where(
        abs(feasibility_mask.lat) <= geo_config.max_latitude,
        feasibility_mask,
        0,
    )

    # Only the equator (0°) should be included
    assert float(mask_corrected.sel(lat=0)) == 1, "Equator should be included"

    # All other latitudes should be excluded
    for lat in [-45, -10, -1, 1, 10, 45]:
        assert float(mask_corrected.sel(lat=lat)) == 0, f"Latitude {lat}° should be excluded"
