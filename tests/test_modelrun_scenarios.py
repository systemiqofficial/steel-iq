"""
Tests for ModelRun scenario system integration.
Testing the new scenario fields and factory methods.
"""
import pytest
from steeloweb.models import ModelRun, Scenario, ScenarioVariation, SensitivitySweep, MasterExcelFile
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def master_excel():
    """Create a MasterExcelFile for testing"""
    return MasterExcelFile.objects.create(
        name="Test Master Excel",
        description="Test file for scenarios"
    )


@pytest.fixture
def scenario(master_excel):
    """Create a Scenario object for testing"""
    return Scenario.objects.create(
        name="Test Scenario",
        description="Test scenario description",
        master_excel=master_excel,
        start_year=2025,
        end_year=2050
    )


@pytest.fixture
def variation(scenario):
    """Create a ScenarioVariation object for testing"""
    return ScenarioVariation.objects.create(
        scenario=scenario,
        name="High Growth",
        description="High growth variation",
        is_active=True
    )


@pytest.fixture
def sweep(scenario):
    """Create a SensitivitySweep object for testing"""
    return SensitivitySweep.objects.create(
        scenario=scenario,
        name="Carbon Price Sweep",
        parameter_path="policy.carbon_price",
        base_value=50.0,
        variation_type="range",
        variation_values=[25, 50, 75, 100]
    )


@pytest.mark.django_db
class TestModelRunScenarios:
    """Test ModelRun scenario integration"""

    def test_modelrun_from_scenario(self, scenario):
        """Test creating a ModelRun from a Scenario"""
        modelrun = ModelRun.from_scenario(scenario)

        assert modelrun.id is not None
        assert modelrun.name == "Test Scenario"
        assert modelrun.scenario == scenario
        assert modelrun.scenario_variation is None
        assert modelrun.config == {}

    def test_modelrun_from_variation(self, scenario, variation):
        """Test creating a ModelRun from a Scenario with variation"""
        modelrun = ModelRun.from_scenario(scenario, variation=variation)

        assert modelrun.id is not None
        assert modelrun.name == "Test Scenario - High Growth"
        assert modelrun.scenario == scenario
        assert modelrun.scenario_variation == variation
        assert modelrun.config == {}

    def test_modelrun_from_scenario_custom_name(self, scenario):
        """Test creating a ModelRun with a custom name"""
        modelrun = ModelRun.from_scenario(scenario, name="Custom Name")

        assert modelrun.name == "Custom Name"
        assert modelrun.scenario == scenario

    def test_modelrun_get_scenario_label(self, scenario, variation, sweep):
        """Test get_scenario_label method"""
        # Test with scenario only
        modelrun = ModelRun.from_scenario(scenario)
        assert modelrun.get_scenario_label() == "Test Scenario"

        # Test with scenario and variation
        modelrun2 = ModelRun.from_scenario(scenario, variation=variation)
        assert modelrun2.get_scenario_label() == "Test Scenario > High Growth"

        # Test with sensitivity sweep
        modelrun3 = ModelRun.from_scenario(scenario)
        modelrun3.sensitivity_sweep = sweep
        modelrun3.sweep_parameter_value = 50.0
        modelrun3.save()
        assert modelrun3.get_scenario_label() == "Test Scenario (50.0)"

    def test_modelrun_without_scenario(self):
        """Test backward compatibility - ModelRun without scenario"""
        modelrun = ModelRun.objects.create(
            name="Manual Run",
            config={},
        )

        assert modelrun.scenario is None
        assert modelrun.scenario_variation is None
        assert modelrun.sensitivity_sweep is None
        assert modelrun.sweep_parameter_value is None
        assert modelrun.get_scenario_label() == "Manual run"

    def test_modelrun_scenario_fields_nullable(self):
        """Test that all scenario fields are nullable (backward compatibility)"""
        # This should not raise any errors
        modelrun = ModelRun.objects.create(
            name="Test",
            config={},
        )

        assert modelrun.id is not None
        assert modelrun.scenario is None
        assert modelrun.scenario_variation is None
        assert modelrun.sensitivity_sweep is None
        assert modelrun.sweep_parameter_value is None
