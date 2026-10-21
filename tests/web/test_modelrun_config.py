"""Tests for the run configuration that the create form stores and ``ModelRun.run`` passes on."""

import json
import logging
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from steelo import simulation
from steelo.domain.constants import MT_TO_T
from steeloweb.models import DataPackage, DataPreparation, ModelRun

# One value per connected form field, each off its class default
OFF_DEFAULT_POST = {
    "random_seed": 7,
    "start_year": 2026,
    "end_year": 2030,
    "plant_lifetime": 25,
    "global_risk_free_rate": 0.03,
    "steel_price_buffer": 150.0,
    "iron_price_buffer": 120.0,
    "steel_market_clearing_share": 0.9,
    "iron_market_clearing_share": 0.85,
    "peg_iron_to_steel_price": True,
    "iron_to_steel_price_ratio": 0.6,
    "opening_balance_multiplier": 0.5,
    "construction_time": 3,
    "consideration_time": 2,
    "probability_of_announcement": 0.5,
    "probability_of_construction": 0.6,
    "top_n_loctechs_as_business_op": 10,
    "opportunity_pool_depth": 5,
    "pick_priority_sites_share": 0.1,
    "calculate_npv_sites_share": 0.2,
    "expanded_capacity": 3.0,
    "capacity_limit_iron": 50.0,
    "capacity_limit_steel": 60.0,
    "new_capacity_share_from_new_plants": 0.3,
    "use_iron_ore_premiums": False,
    "include_tariffs": False,
    "geographical_clustering_scope": "iso3",
    "chosen_grid_emissions_scenario": "Net Zero",
    "hydrogen_ceiling_percentile": 90.0,
    "intraregional_trade_allowed": True,
    "long_dist_pipeline_transport_cost": 2.0,
    "include_infrastructure_cost": False,
    "include_transport_cost": False,
    "iron_mine_to_plant": 0.02,
    "iron_to_steel_plant": 0.025,
    "steel_to_demand": 0.03,
    "include_lulc_cost": False,
    "included_power_mix": "Grid only",
    "max_slope": 5.0,
    "max_altitude": 2000,
    "max_latitude": 60.0,
}
# Switched off, these overwrite or reset other posted values, so they stay at their default (on)
KEPT_ON = ("probabilistic_agents", "enable_furnace_group_clustering")
MT_FIELDS = ("expanded_capacity", "capacity_limit_iron", "capacity_limit_steel")
TRANSPORT_ROUTES = ("iron_mine_to_plant", "iron_to_steel_plant", "steel_to_demand")


def ready_model_run(tmp_path, name, config):
    """A ``ModelRun`` with ``config`` on a ready data preparation, so ``run()`` gets as far as the simulation.

    Args:
        tmp_path: Folder for the preparation's data directory.
        name: The run's name.
        config: The run's stored config.

    Returns:
        The saved ``ModelRun``.
    """
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA,
        version="test",
        source_type=DataPackage.SourceType.LOCAL,
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA,
        version="test",
        source_type=DataPackage.SourceType.LOCAL,
    )
    data_prep = DataPreparation.objects.create(
        name="Test Preparation",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory=str(tmp_path / "prep"),
    )
    data_prep.master_excel_file.save("master.xlsx", ContentFile(b"fake excel content"), save=True)
    return ModelRun.objects.create(name=name, config=config, data_preparation=data_prep)


def held_value(config, name):
    """The value a ``SimulationConfig`` holds for one form field.

    Args:
        config: The run's ``SimulationConfig``.
        name: The form field's name.

    Returns:
        The transport cost of that route, else the ``SimulationConfig`` attribute, else the ``GeoConfig`` one.
    """
    if name in TRANSPORT_ROUTES:
        return config.geo_config.transportation_cost_per_km_per_ton[name]
    return getattr(config, name) if hasattr(config, name) else getattr(config.geo_config, name)


@pytest.mark.django_db
@patch("steelo.validation.validate_technology_settings")
@patch("steelo.bootstrap.bootstrap_simulation")
def test_run_passes_include_tariffs_to_the_simulation_config(mock_bootstrap_simulation, mock_validate, tmp_path):
    """An unticked "Include tariffs" box, stored as ``False``, reaches the ``SimulationConfig`` instead of its default."""
    model_run = ready_model_run(
        tmp_path,
        "Tariffs off",
        {
            "start_year": 2025,
            "end_year": 2026,
            "include_tariffs": False,
            "technology_settings": {"BF": {"allowed": True, "from_year": 2025, "to_year": None}},
        },
    )

    model_run.run()

    config = mock_bootstrap_simulation.call_args[0][0]
    assert config.include_tariffs is False


@pytest.mark.django_db
@patch("steelo.validation.validate_technology_settings")
@patch("steelo.bootstrap.bootstrap_simulation")
def test_run_drops_a_stored_scrap_generation_scenario(mock_bootstrap_simulation, mock_validate, tmp_path):
    """An older run's stored ``scrap_generation_scenario``, no longer a config field, does not stop the run."""
    model_run = ready_model_run(
        tmp_path,
        "Older run",
        {
            "start_year": 2025,
            "end_year": 2026,
            "scrap_generation_scenario": "circular_economy",
            "technology_settings": {"BF": {"allowed": True, "from_year": 2025, "to_year": None}},
        },
    )

    model_run.run()

    mock_bootstrap_simulation.assert_called_once()


@pytest.mark.django_db
def test_create_form_keeps_an_entered_zero(mock_technology_extraction, client, valid_modelrun_form_data):
    """A 0 typed into a field whose form allows 0 is stored as 0, not replaced by the field's default."""
    form_data = {
        **valid_modelrun_form_data,
        "hydrogen_ceiling_percentile": 0,
        "probability_of_announcement": 0,
    }

    response = client.post(reverse("create-modelrun"), data=form_data)

    assert response.status_code == 302
    config = ModelRun.objects.get().config
    assert config["hydrogen_ceiling_percentile"] == 0
    assert config["probability_of_announcement"] == 0


@pytest.mark.django_db
def test_create_form_records_the_plant_scope_with_clustering_off(
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
):
    """With clustering off, the stored clustering scope is "plant", the value the model then uses."""
    # An unticked box and the select it disables send no field
    form_data = {
        key: value
        for key, value in valid_modelrun_form_data.items()
        if key not in ("enable_furnace_group_clustering", "geographical_clustering_scope")
    }

    response = client.post(reverse("create-modelrun"), data=form_data)

    assert response.status_code == 302
    config = ModelRun.objects.get().config
    assert config["enable_furnace_group_clustering"] is False
    assert config["geographical_clustering_scope"] == "plant"


@pytest.mark.django_db
def test_create_form_stores_no_output_file_key(mock_technology_extraction, client, valid_modelrun_form_data):
    """A run created through the form carries no ``output_file`` key, which nothing reads."""
    response = client.post(reverse("create-modelrun"), data=valid_modelrun_form_data)

    assert response.status_code == 302
    assert "output_file" not in ModelRun.objects.get().config


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("posted", "stored"),
    [
        ("", simulation.config_default("global_risk_free_rate")),
        ("0", 0),
    ],
)
def test_create_form_stores_the_class_default_for_a_blank_field_and_an_entered_zero_as_zero(
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
    posted,
    stored,
):
    """A numeric field left blank stores the ``SimulationConfig`` default; the same field entered as 0 stores 0."""
    form_data = {**valid_modelrun_form_data, "global_risk_free_rate": posted}

    response = client.post(reverse("create-modelrun"), data=form_data)

    assert response.status_code == 302
    assert ModelRun.objects.get().config["global_risk_free_rate"] == stored


@pytest.mark.django_db
@patch("steelo.validation.validate_technology_settings")
@patch("steelo.bootstrap.bootstrap_simulation")
def test_run_drops_stored_keys_that_are_no_run_setting_with_one_warning(
    mock_bootstrap_simulation,
    mock_validate,
    tmp_path,
    caplog,
):
    """A stored config holding inputs that are no run setting still builds a config, with one warning naming them."""
    model_run = ready_model_run(
        tmp_path,
        "Older run",
        {
            "start_year": 2025,
            "end_year": 2026,
            "hydrogen_subsidies": False,
            "total_steel_demand_scenario": "business_as_usual",
            "technology_settings": {"BF": {"allowed": True, "from_year": 2025, "to_year": None}},
        },
    )

    with caplog.at_level(logging.WARNING, logger="steeloweb.models"):
        model_run.run()

    mock_bootstrap_simulation.assert_called_once()
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "hydrogen_subsidies" in warnings[0]
    assert "total_steel_demand_scenario" in warnings[0]


@pytest.mark.django_db
@patch("steelo.validation.validate_technology_settings")
@patch("steelo.bootstrap.bootstrap_simulation")
def test_every_connected_form_field_reaches_the_simulation_config(
    mock_bootstrap_simulation,
    mock_validate,
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
    ready_data_preparation,
):
    """A form posted with every connected field off its default gives a ``SimulationConfig`` holding each value."""
    fixtures = ready_data_preparation.get_data_path() / "data" / "fixtures"
    emissivity = json.loads((fixtures / "region_emissivity.json").read_text())
    emissivity["root"].append({**emissivity["root"][0], "scenario": "Net Zero"})
    (fixtures / "region_emissivity.json").write_text(json.dumps(emissivity))
    (fixtures / "plants.json").write_text("[]")
    (fixtures / "demand_centers.json").write_text("[]")
    ready_data_preparation.master_excel_file.save("master.xlsx", ContentFile(b"fake excel content"), save=True)
    posted = {name: "on" if value is True else value for name, value in OFF_DEFAULT_POST.items() if value is not False}
    form_data = {**valid_modelrun_form_data, **posted, **{name: "on" for name in KEPT_ON}}

    response = client.post(reverse("create-modelrun"), data=form_data)
    ModelRun.objects.get().run()

    assert response.status_code == 302
    config = mock_bootstrap_simulation.call_args[0][0]
    held = {name: held_value(config, name) for name in OFF_DEFAULT_POST}
    expected = {name: value * MT_TO_T if name in MT_FIELDS else value for name, value in OFF_DEFAULT_POST.items()}
    assert held == expected
