import pytest
from django.contrib.auth import get_user_model
from steeloweb.models import Scenario, ScenarioVariation, SensitivitySweep, MasterExcelFile

User = get_user_model()


@pytest.fixture
def user(db):
    """Create a test user"""
    return User.objects.create_user(username='testuser', password='testpass123')


@pytest.fixture
def master_excel(db):
    """Create a test master Excel file"""
    return MasterExcelFile.objects.create(
        name='Test Master Excel',
        description='Test file for scenarios',
        validation_status='valid'
    )


@pytest.fixture
def scenario(db, user, master_excel):
    """Create a test scenario"""
    return Scenario.objects.create(
        name='Base Scenario',
        description='Test base scenario',
        created_by=user,
        master_excel=master_excel,
        start_year=2025,
        end_year=2050,
        technology_overrides={'tech1': {'enabled': True}},
        economic_overrides={'discount_rate': 0.05},
    )


@pytest.mark.django_db
def test_scenario_creation(user, master_excel):
    """Test creating a scenario"""
    scenario = Scenario.objects.create(
        name='Test Scenario',
        description='A test scenario',
        created_by=user,
        master_excel=master_excel,
        start_year=2025,
        end_year=2050,
        technology_overrides={'tech1': {'enabled': True}},
        economic_overrides={'discount_rate': 0.05},
        geospatial_overrides={'region': 'Europe'},
        policy_overrides={'carbon_tax': 50},
        agent_overrides={'agent_count': 100},
    )

    assert scenario.name == 'Test Scenario'
    assert scenario.created_by == user
    assert scenario.master_excel == master_excel
    assert scenario.start_year == 2025
    assert scenario.end_year == 2050
    assert scenario.technology_overrides == {'tech1': {'enabled': True}}
    assert scenario.economic_overrides == {'discount_rate': 0.05}
    assert scenario.geospatial_overrides == {'region': 'Europe'}
    assert scenario.policy_overrides == {'carbon_tax': 50}
    assert scenario.agent_overrides == {'agent_count': 100}
    assert scenario.created_at is not None
    assert scenario.updated_at is not None


@pytest.mark.django_db
def test_scenario_str(scenario):
    """Test scenario string representation"""
    assert str(scenario) == 'Base Scenario'


@pytest.mark.django_db
def test_scenario_get_all_overrides(scenario):
    """Test getting all overrides"""
    overrides = scenario.get_all_overrides()

    assert 'technology' in overrides
    assert 'economic' in overrides
    assert 'geospatial' in overrides
    assert 'policy' in overrides
    assert 'agent' in overrides
    assert overrides['technology'] == {'tech1': {'enabled': True}}
    assert overrides['economic'] == {'discount_rate': 0.05}


@pytest.mark.django_db
def test_scenario_relationships(user, master_excel):
    """Test scenario relationships"""
    # Create base scenario
    base = Scenario.objects.create(
        name='Base',
        master_excel=master_excel,
        created_by=user,
    )

    # Create derived scenario
    derived = Scenario.objects.create(
        name='Derived',
        master_excel=master_excel,
        created_by=user,
        base_scenario=base,
    )

    assert derived.base_scenario == base
    assert base.derived_scenarios.count() == 1
    assert base.derived_scenarios.first() == derived


@pytest.mark.django_db
def test_scenario_variation_creation(scenario):
    """Test creating a scenario variation"""
    variation = ScenarioVariation.objects.create(
        scenario=scenario,
        name='High Growth',
        description='High growth variant',
        additional_overrides={'economic': {'growth_rate': 0.1}},
        is_active=True,
    )

    assert variation.scenario == scenario
    assert variation.name == 'High Growth'
    assert variation.is_active is True
    assert variation.additional_overrides == {'economic': {'growth_rate': 0.1}}


@pytest.mark.django_db
def test_scenario_variation_str(scenario):
    """Test scenario variation string representation"""
    variation = ScenarioVariation.objects.create(
        scenario=scenario,
        name='High Growth',
    )

    assert str(variation) == 'Base Scenario - High Growth'


@pytest.mark.django_db
def test_scenario_variation_merge(scenario):
    """Test merging scenario and variation overrides"""
    # Base scenario has technology and economic overrides
    scenario.technology_overrides = {'tech1': {'enabled': True, 'cost': 100}}
    scenario.economic_overrides = {'discount_rate': 0.05}
    scenario.save()

    # Variation adds geospatial and modifies economic
    variation = ScenarioVariation.objects.create(
        scenario=scenario,
        name='Modified',
        additional_overrides={
            'economic': {'discount_rate': 0.08, 'growth_rate': 0.1},
            'geospatial': {'region': 'Asia'},
        },
    )

    merged = variation.get_merged_overrides()

    # Check that base technology overrides are preserved
    assert merged['technology'] == {'tech1': {'enabled': True, 'cost': 100}}

    # Check that economic overrides are merged (variation takes precedence)
    assert merged['economic']['discount_rate'] == 0.08
    assert merged['economic']['growth_rate'] == 0.1

    # Check that new category is added
    assert merged['geospatial'] == {'region': 'Asia'}


@pytest.mark.django_db
def test_scenario_variation_deep_merge(scenario):
    """Test deep merging of nested dictionaries"""
    scenario.technology_overrides = {
        'tech1': {'enabled': True, 'cost': 100, 'params': {'a': 1, 'b': 2}}
    }
    scenario.save()

    variation = ScenarioVariation.objects.create(
        scenario=scenario,
        name='Modified',
        additional_overrides={
            'technology': {
                'tech1': {'cost': 150, 'params': {'b': 3, 'c': 4}},
                'tech2': {'enabled': True},
            }
        },
    )

    merged = variation.get_merged_overrides()

    # Check deep merge of nested params
    assert merged['technology']['tech1']['enabled'] is True  # From base
    assert merged['technology']['tech1']['cost'] == 150  # Overridden
    assert merged['technology']['tech1']['params']['a'] == 1  # From base
    assert merged['technology']['tech1']['params']['b'] == 3  # Overridden
    assert merged['technology']['tech1']['params']['c'] == 4  # New
    assert merged['technology']['tech2']['enabled'] is True  # New tech


@pytest.mark.django_db
def test_scenario_count_variations(scenario):
    """Test counting active variations"""
    assert scenario.count_variations() == 0

    # Create active variation
    ScenarioVariation.objects.create(
        scenario=scenario,
        name='Variation 1',
        is_active=True,
    )
    assert scenario.count_variations() == 1

    # Create inactive variation
    ScenarioVariation.objects.create(
        scenario=scenario,
        name='Variation 2',
        is_active=False,
    )
    assert scenario.count_variations() == 1  # Should still be 1

    # Create another active variation
    ScenarioVariation.objects.create(
        scenario=scenario,
        name='Variation 3',
        is_active=True,
    )
    assert scenario.count_variations() == 2


@pytest.mark.django_db
def test_sensitivity_sweep_creation(scenario):
    """Test creating a sensitivity sweep"""
    sweep = SensitivitySweep.objects.create(
        scenario=scenario,
        name='Discount Rate Sweep',
        parameter_path='economic.discount_rate',
        base_value=0.05,
        variation_type='percentage',
        variation_values=[0.9, 0.95, 1.0, 1.05, 1.1],
    )

    assert sweep.scenario == scenario
    assert sweep.name == 'Discount Rate Sweep'
    assert sweep.parameter_path == 'economic.discount_rate'
    assert sweep.base_value == 0.05
    assert sweep.variation_type == 'percentage'
    assert len(sweep.variation_values) == 5


@pytest.mark.django_db
def test_sensitivity_sweep_str(scenario):
    """Test sensitivity sweep string representation"""
    sweep = SensitivitySweep.objects.create(
        scenario=scenario,
        name='Cost Sweep',
        parameter_path='technology.cost',
        base_value=100,
        variation_type='absolute',
        variation_values=[80, 90, 100, 110, 120],
    )

    assert str(sweep) == 'Base Scenario - Cost Sweep'


@pytest.mark.django_db
def test_sensitivity_sweep_count_runs(scenario):
    """Test counting expected runs from sweep"""
    sweep = SensitivitySweep.objects.create(
        scenario=scenario,
        name='Test Sweep',
        parameter_path='economic.discount_rate',
        base_value=0.05,
        variation_type='percentage',
        variation_values=[0.8, 0.9, 1.0, 1.1, 1.2],
    )

    assert sweep.count_runs() == 5


@pytest.mark.django_db
def test_sensitivity_sweep_variation_types(scenario):
    """Test different variation types"""
    # Percentage variation
    percentage_sweep = SensitivitySweep.objects.create(
        scenario=scenario,
        name='Percentage Sweep',
        parameter_path='cost',
        base_value=100,
        variation_type='percentage',
        variation_values=[0.8, 0.9, 1.0, 1.1, 1.2],
    )
    assert percentage_sweep.variation_type == 'percentage'

    # Absolute variation
    absolute_sweep = SensitivitySweep.objects.create(
        scenario=scenario,
        name='Absolute Sweep',
        parameter_path='cost',
        base_value=100,
        variation_type='absolute',
        variation_values=[-20, -10, 0, 10, 20],
    )
    assert absolute_sweep.variation_type == 'absolute'

    # Range variation
    range_sweep = SensitivitySweep.objects.create(
        scenario=scenario,
        name='Range Sweep',
        parameter_path='cost',
        base_value=100,
        variation_type='range',
        variation_values=[50, 75, 100, 125, 150],
    )
    assert range_sweep.variation_type == 'range'


@pytest.mark.django_db
def test_scenario_cascade_delete(scenario):
    """Test that deleting a scenario deletes variations and sweeps"""
    # Create variations and sweeps
    variation = ScenarioVariation.objects.create(
        scenario=scenario,
        name='Test Variation',
    )
    sweep = SensitivitySweep.objects.create(
        scenario=scenario,
        name='Test Sweep',
        parameter_path='test',
        base_value=1.0,
        variation_type='percentage',
        variation_values=[1.0],
    )

    scenario_id = scenario.id
    variation_id = variation.id
    sweep_id = sweep.id

    # Delete scenario
    scenario.delete()

    # Check that variation and sweep are also deleted
    assert not ScenarioVariation.objects.filter(id=variation_id).exists()
    assert not SensitivitySweep.objects.filter(id=sweep_id).exists()


@pytest.mark.django_db
def test_scenario_protect_master_excel(scenario, master_excel):
    """Test that master excel cannot be deleted if scenarios exist"""
    # Attempting to delete master_excel should raise an error
    # because it's protected by the scenario
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        master_excel.delete()


@pytest.mark.django_db
def test_scenario_null_user(master_excel):
    """Test scenario with null user (user deleted)"""
    user = User.objects.create_user(username='temp_user', password='pass')

    scenario = Scenario.objects.create(
        name='Test Scenario',
        master_excel=master_excel,
        created_by=user,
    )

    # Delete user
    user.delete()

    # Scenario should still exist with null user
    scenario.refresh_from_db()
    assert scenario.created_by is None


@pytest.mark.django_db
def test_scenario_ordering(master_excel):
    """Test that scenarios are ordered by creation date (newest first)"""
    scenario1 = Scenario.objects.create(
        name='Scenario 1',
        master_excel=master_excel,
    )
    scenario2 = Scenario.objects.create(
        name='Scenario 2',
        master_excel=master_excel,
    )
    scenario3 = Scenario.objects.create(
        name='Scenario 3',
        master_excel=master_excel,
    )

    scenarios = list(Scenario.objects.all())
    assert scenarios[0] == scenario3
    assert scenarios[1] == scenario2
    assert scenarios[2] == scenario1


@pytest.mark.django_db
def test_scenario_default_values(master_excel):
    """Test default values for scenario fields"""
    scenario = Scenario.objects.create(
        name='Minimal Scenario',
        master_excel=master_excel,
    )

    assert scenario.description == ''
    assert scenario.start_year == 2025
    assert scenario.end_year == 2050
    assert scenario.technology_overrides == {}
    assert scenario.economic_overrides == {}
    assert scenario.geospatial_overrides == {}
    assert scenario.policy_overrides == {}
    assert scenario.agent_overrides == {}
    assert scenario.created_by is None
    assert scenario.base_scenario is None
