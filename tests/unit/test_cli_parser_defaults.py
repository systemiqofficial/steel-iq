"""Parity of the ``run_simulation`` flag defaults with the ``SimulationConfig`` class defaults."""

from steelo import simulation
from steelo.domain import constants
from steelo.entrypoints import cli

# Parser dest -> the config field it sets
CONFIG_FIELD_OF_DEST = {
    "master_excel": "master_excel_path",
    "demand_scenario": "chosen_demand_scenario",
    "scrap_scenario": "chosen_scrap_scenario",
    "grid_emissions_scenario": "chosen_grid_emissions_scenario",
    "run_name": "run_name",
    "hydrogen_ceiling_percentile": "geo_config.hydrogen_ceiling_percentile",
    "intraregional_trade": "geo_config.intraregional_trade_allowed",
    "boa_run": "boa_run",
    "clustering": "enable_furnace_group_clustering",
    "clustering_scope": "geographical_clustering_scope",
    "plot_tm": "plot_tm",
    "plot_geo": "plot_geo",
    "peg_iron_to_steel_price": "peg_iron_to_steel_price",
    "iron_to_steel_price_ratio": "iron_to_steel_price_ratio",
    "random_seed": "random_seed",
    "enable_capacity_policy": "capacity_policy.enabled",
    "credit_validity_years": "capacity_policy.credit_validity_years",
    "cbam_rebate": "carbon_border_export_rebates",
    "optimization_solver": "optimization_solver",
}
# Flags that steer the CLI itself and set no config field
NOT_CONFIG_DESTS = {
    "help",
    "output_dir",
    "resume_from_year",
    "no_cache",
    "force_refresh",
    "steelo_home",
    "cache_stats",
    "clear_cache",
}
# A quiet console is intended; the viewer leaves log_level out of its comparison
DELIBERATE_DIFFERENCES = {"log_level"}


def test_every_cli_flag_default_is_none_or_the_class_default():
    """Each flag that sets a config field defaults to ``None`` (the class default applies) or to the class default.

    The years have no class default and must match the shared constants; ``--log-level`` is the one deliberate
    difference. A flag in none of the lists fails the test, so a new flag must be placed in one.
    """
    year_constants = {"start_year": constants.START_YEAR_DEFAULT, "end_year": constants.END_YEAR_DEFAULT}

    mismatches = {}
    for action in cli._build_parser()._actions:
        dest = action.dest
        if dest in NOT_CONFIG_DESTS or dest in DELIBERATE_DIFFERENCES:
            continue
        if dest in year_constants:
            expected = year_constants[dest]
        else:
            assert dest in CONFIG_FIELD_OF_DEST, f"--{dest} is in no list of this test"
            if action.default is None:
                continue
            expected = simulation.config_default(CONFIG_FIELD_OF_DEST[dest])
        if action.default != expected:
            mismatches[dest] = (action.default, expected)

    assert mismatches == {}
