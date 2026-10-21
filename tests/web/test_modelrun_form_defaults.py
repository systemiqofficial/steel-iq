"""Parity of the run form's initial values with the ``SimulationConfig`` class defaults."""

from dataclasses import fields

from steelo import simulation
from steelo.domain import constants
from steeloweb import forms

# Form fields that set no config field
NO_COUNTERPART = {
    "name",
    "randomise_seed",
    "data_preparation",
    "total_steel_demand_scenario",
    "green_steel_demand_scenario",
    "circularity_file",
    "hydrogen_subsidies",
    "esf_cost_scenario",
    "moe_cost_scenario",
    "electrowinning_cost_scenario",
    "ccs_cost_scenario",
    "capacity_policy_no_swap_cutoff",
}
# Entered in Mt, stored in tonnes
MT_FIELDS = {"expanded_capacity", "capacity_limit_iron", "capacity_limit_steel"}
# Entries of geo_config.transportation_cost_per_km_per_ton
TRANSPORT_ROUTES = {"iron_mine_to_plant", "iron_to_steel_plant", "steel_to_demand"}


def class_default_of_form_field(name):
    """The class default a form field's initial must show.

    Args:
        name: The form field's name.

    Returns:
        The year constant, the transport cost entry, the ``CapacityPolicyConfig`` lever of a ``capacity_policy_*``
        field, or the ``SimulationConfig`` (else ``GeoConfig``) field's default, divided by ``MT_TO_T`` for the
        three capacity fields.
    """
    if name.startswith("capacity_policy_"):
        return simulation.config_default(f"capacity_policy.{name.removeprefix('capacity_policy_')}")
    if name == "start_year":
        return constants.START_YEAR_DEFAULT
    if name == "end_year":
        return constants.END_YEAR_DEFAULT
    if name in TRANSPORT_ROUTES:
        return simulation.config_default("geo_config.transportation_cost_per_km_per_ton")[name]
    config_names = {config_field.name for config_field in fields(simulation.SimulationConfig)}
    default = simulation.config_default(name if name in config_names else f"geo_config.{name}")
    return default / constants.MT_TO_T if name in MT_FIELDS else default


def test_every_form_field_with_a_counterpart_starts_at_the_class_default():
    """Each field of the run form that sets a config field shows the class default as its initial (Mt scaled).

    A field with no config field must be listed in ``NO_COUNTERPART``; any other field without a class default
    fails the test with a ``KeyError``. A ``data-default`` widget attribute, read by the page's reset buttons,
    must show the same value.
    """
    base_fields = forms.ModelRunCreateForm.base_fields
    initial_mismatches = {
        name: (form_field.initial, class_default_of_form_field(name))
        for name, form_field in base_fields.items()
        if name not in NO_COUNTERPART and form_field.initial != class_default_of_form_field(name)
    }
    data_defaults = {
        name: (form_field.widget.attrs["data-default"], str(form_field.initial))
        for name, form_field in base_fields.items()
        if "data-default" in form_field.widget.attrs
    }

    assert initial_mismatches == {}
    assert set(data_defaults) == {"consideration_time", "max_latitude"}
    assert {name: pair for name, pair in data_defaults.items() if pair[0] != pair[1]} == {}
