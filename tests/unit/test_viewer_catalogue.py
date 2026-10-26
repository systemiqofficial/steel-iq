"""Catalogue of local runs for the scenario viewer."""

import dataclasses
import hashlib
import json

from steelo import simulation
from steelo.adapters.repositories.in_memory_repository import InMemoryRepository
from steelo.capacity_policy.config import CapacityPolicyConfig
from steelo.simulation import GeoConfig, SimulationConfig
from steelo.viewer import catalogue


def write_run(
    run_dir,
    name,
    started,
    master="master_input.xlsx",
    master_source="default",
    made_in="cli",
    master_name=None,
    preparation_name=None,
    master_sha256=None,
    **overrides,
):
    """
    Write a fake run folder holding simulation_config.json and a hand-written viewer/run.json; without
    master_sha256 the key is left out, as in a run written before it.
    """
    config = SimulationConfig.for_testing(InMemoryRepository(), output_dir=run_dir, run_name=name, **overrides)
    (run_dir / "simulation_config.json").write_text(json.dumps(simulation.config_as_json_dict(config)))
    (run_dir / "viewer").mkdir()
    run = {
        "schema": 1,
        "name": name,
        "folder": run_dir.name,
        "made_in": made_in,
        "started": started,
        "ended": started,
        "steelo_version": "2.0.0",
        "input_data": {
            "master": master,
            "master_source": master_source,
            "master_name": master_name,
            "preparation_name": preparation_name,
        },
        "pages": ["emissions.html"],
        "files": [],
    }
    if master_sha256 is not None:
        run["input_data"]["master_sha256"] = master_sha256
    (run_dir / "viewer" / "run.json").write_text(json.dumps(run))
    return run_dir


def test_catalogue_dimensions_are_the_differing_settings_input_data_seed_and_run(tmp_path):
    """
    Tests that four runs (two differing in one setting, one on a custom master, one a copy of another under the
    same name) give exactly the filters input data, that setting, seed and run, with a distinct run value for the
    copy; that a run differing only in an excluded field adds none; and that the run detail flags the changed
    setting as changed from the default and varying.
    """
    runs = [
        write_run(tmp_path / "sim_1", "base", "2026-10-20T10:00:00"),
        write_run(
            tmp_path / "sim_2",
            "h20",
            "2026-10-20T10:01:00",
            geo_config=GeoConfig(hydrogen_ceiling_percentile=20.0),
        ),
        write_run(
            tmp_path / "sim_3",
            "custom",
            "2026-10-20T10:02:00",
            master="my_master.xlsx",
            master_source="custom",
        ),
        write_run(tmp_path / "sim_4", "base", "2026-10-20T10:03:00"),
    ]
    plots_only = write_run(
        tmp_path / "sim_5",
        "base_3",
        "2026-10-20T10:04:00",
        plots_dir=tmp_path / "elsewhere",
    )

    local = catalogue.build_manifest(runs, with_made_in=False)["sets"][0]
    with_plots_only = catalogue.build_manifest(runs + [plots_only], with_made_in=False)["sets"][0]

    keys = [dimension["key"] for dimension in local["dimensions"]]
    assert keys == ["input_data", "geo_config.hydrogen_ceiling_percentile", "seed", "run"]
    assert [dimension["key"] for dimension in with_plots_only["dimensions"]] == keys
    run_values = {run["dir"]: run["dims"]["run"] for run in local["runs"]}
    assert run_values["sim_1"] != run_values["sim_4"]
    settings = {run["dir"]: {s["key"]: s for s in run["settings"]} for run in local["runs"]}
    percentile = "geo_config.hydrogen_ceiling_percentile"
    assert settings["sim_2"][percentile]["changed"] is True
    assert settings["sim_2"][percentile]["varies"] is True
    assert settings["sim_2"][percentile]["default"] == "no cap"
    assert settings["sim_1"][percentile]["changed"] is False
    assert "default" not in settings["sim_1"][percentile]


def test_input_data_is_one_value_per_master_content(tmp_path):
    """
    Tests that an app run and a CLI run of one master's bytes under different names give one input data value, the
    CLI run's file name, and no input data filter; that a run without master_sha256 keeps its old label; that two
    masters under one file name each get the first eight characters of their hash appended; and that a master only
    app runs used takes its name in the app, not its file name.
    """
    one_sha256 = hashlib.sha256(b"one master").hexdigest()
    other_sha256 = hashlib.sha256(b"another master").hexdigest()
    app_sha256 = hashlib.sha256(b"an uploaded master").hexdigest()
    one_master = [
        write_run(
            tmp_path / "app_1",
            "app",
            "2026-10-20T10:00:00",
            master="master_input_template_master_input.xlsx",
            made_in="app",
            master_name="Master Input Template",
            preparation_name="Initial Setup Data",
            master_sha256=one_sha256,
        ),
        write_run(
            tmp_path / "sim_1",
            "cli",
            "2026-10-20T10:01:00",
            master="master_input_v3.0.0.xlsx",
            master_source="custom",
            master_sha256=one_sha256,
        ),
    ]
    one_file_name = [
        write_run(tmp_path / "sim_2", "old", "2026-10-20T10:02:00"),
        write_run(tmp_path / "sim_3", "one", "2026-10-20T10:03:00", master_sha256=one_sha256),
        write_run(tmp_path / "sim_4", "other", "2026-10-20T10:04:00", master_sha256=other_sha256),
        write_run(
            tmp_path / "app_2",
            "check",
            "2026-10-20T10:05:00",
            master="check_master_master_input.xlsx",
            made_in="app",
            master_name="Check master",
            preparation_name="Data with check master",
            master_sha256=app_sha256,
        ),
    ]

    grouped = catalogue.build_manifest(one_master, with_made_in=True)["sets"][0]
    clashing = catalogue.build_manifest(one_file_name, with_made_in=False)["sets"][0]

    assert [run["input_data"] for run in grouped["runs"]] == ["master_input_v3.0.0.xlsx"] * 2
    assert "input_data" not in [dimension["key"] for dimension in grouped["dimensions"]]
    assert {run["dir"]: run["input_data"] for run in clashing["runs"]} == {
        "sim_2": "master_input.xlsx (default)",
        "sim_3": f"master_input.xlsx {one_sha256[:8]}",
        "sim_4": f"master_input.xlsx {other_sha256[:8]}",
        "app_2": "Check master",
    }


def test_run_details_carry_list_items_and_switch_states(tmp_path):
    """
    Tests that a list setting carries its items for the run details' chips and a boolean setting its state for the
    on and off dot, beside the formatted value the filters use.
    """
    run = write_run(tmp_path / "sim_1", "base", "2026-10-20T10:00:00")

    local = catalogue.build_manifest([run], with_made_in=False)["sets"][0]

    settings = {s["key"]: s for s in local["runs"][0]["settings"]}
    statuses = settings["active_statuses"]
    assert statuses["items"] == SimulationConfig.for_testing(InMemoryRepository()).active_statuses
    assert statuses["value"] == ", ".join(statuses["items"])
    assert settings["include_tariffs"]["bool"] is True
    assert settings["include_tariffs"]["value"] == "on"
    assert "items" not in settings["include_tariffs"] and "bool" not in statuses


def test_settings_layout_places_every_counted_setting_once():
    """
    Tests that the run details' layout names every counted setting of a config exactly once, by key or by a group's
    prefix, and no key that is not one, so a new, renamed or removed setting cannot fall out of the run details
    unnoticed.
    """
    config = SimulationConfig.for_testing(InMemoryRepository())
    keys = set(catalogue.flatten_settings(simulation.config_as_json_dict(config)))
    named = []
    prefixes = []
    for section in catalogue.SETTINGS_LAYOUT:
        if "tiles" in section:
            named += [key for _, tile_keys in section["tiles"] for key in tile_keys]
        for group in section["groups"]:
            named += group["rows"] + [group[field] for field in ("switch", "greenfield") if field in group]
            if "prefix" in group:
                prefixes.append(group["prefix"])

    under_prefix = {key for key in keys if any(key.startswith(prefix) for prefix in prefixes)}
    assert len(named) == len(set(named))
    assert not set(named) & under_prefix
    assert set(named) | under_prefix == keys


def test_format_value_shows_shares_units_and_words():
    """
    Tests that a share reads in per cent, a quantity with its unit, the hydrogen ceiling as a percentile or as no
    cap, and None and booleans in the words their settings use.
    """
    cases = {
        ("equity_share", 0.2): "20%",
        ("global_risk_free_rate", 0.0209): "2.09%",
        ("iron_to_steel_price_ratio", 0.8): "80% of steel price",
        ("expanded_capacity", 2_500_000.0): "2.5 Mt",
        ("steel_price_buffer", 200.0): "200 USD/t",
        ("plant_lifetime", 20): "20 years",
        ("geo_config.max_slope", 2.0): "2°",
        ("geo_config.land_cover_factor.Cropland", 1.1): "1.1×",
        ("capacity_policy.replacement_ratio", 1.5): "1.5 : 1",
        ("geo_config.hydrogen_ceiling_percentile", 100.0): "no cap",
        ("geo_config.hydrogen_ceiling_percentile", 20.0): "20th percentile",
        ("geo_config.hydrogen_ceiling_percentile", 22.0): "22nd percentile",
        ("technology_settings.BF.to_year", None): "open",
        ("capacity_policy.credit_validity_years", None): "never expires",
        ("technology_settings.BF.allowed", True): "yes",
        ("include_tariffs", False): "off",
        ("seed", 42): "42",
    }

    assert {case: catalogue.format_value(*case) for case in cases} == cases


def test_ordered_values_puts_numbers_with_units_in_numeric_order():
    """
    Tests that filter values starting with a number sort by that number, units and per cent signs after it
    included, and that values not all numeric keep the natural order.
    """
    assert catalogue.ordered_values({"10 Mt", "2.5 Mt", "2.25 Mt"}) == ["2.25 Mt", "2.5 Mt", "10 Mt"]
    assert catalogue.ordered_values({"2.5%", "2.09%", "100%"}) == ["2.09%", "2.5%", "100%"]
    assert catalogue.ordered_values({"1e-05", "0.001"}) == ["1e-05", "0.001"]
    assert catalogue.ordered_values({"no cap", "20th percentile"}) == ["20th percentile", "no cap"]


def test_excluded_settings_name_only_real_config_fields():
    """
    Tests that every entry of the exclude list is a SimulationConfig, GeoConfig or CapacityPolicyConfig field,
    so a renamed or removed field cannot leave a stale entry behind. The exceptions are the removed
    scrap_generation_scenario and baseload_power_sim_dir, which older runs' configs still carry.
    """
    real = (
        {f.name for f in dataclasses.fields(SimulationConfig)}
        | {f"geo_config.{f.name}" for f in dataclasses.fields(GeoConfig)}
        | {f"capacity_policy.{f.name}" for f in dataclasses.fields(CapacityPolicyConfig)}
    )

    assert catalogue.EXCLUDED_SETTINGS - {"scrap_generation_scenario", "baseload_power_sim_dir"} <= real


def test_discover_runs_names_each_skipped_folder_in_one_warning_only(tmp_path, caplog):
    """
    Tests that a run in progress (simulation_config.json, no viewer/run.json) is named in a warning the first time
    the runs are found and not again, that a run started later is named once in its own warning, and that a
    run is named again once it has been listed and has lost its run.json.
    """
    write_run(tmp_path / "sim_1", "base", "2026-10-20T10:00:00")
    for folder in ("sim_2", "sim_3"):
        (tmp_path / folder).mkdir()
    (tmp_path / "sim_2" / "simulation_config.json").write_text("{}")

    with caplog.at_level("WARNING", logger=catalogue.__name__):
        catalogue.discover_runs(tmp_path, [])
        catalogue.discover_runs(tmp_path, [])
        (tmp_path / "sim_3" / "simulation_config.json").write_text("{}")
        catalogue.discover_runs(tmp_path, [])
        write_run(tmp_path / "sim_2", "later", "2026-10-20T10:01:00")
        listed = catalogue.discover_runs(tmp_path, [])
        (tmp_path / "sim_2" / "viewer" / "run.json").unlink()
        catalogue.discover_runs(tmp_path, [])
        found = catalogue.discover_runs(tmp_path, [])

    assert listed == [tmp_path / "sim_1", tmp_path / "sim_2"]
    assert found == [tmp_path / "sim_1"]
    assert [record.getMessage() for record in caplog.records] == [
        f"skipped, no viewer/run.json: {tmp_path / 'sim_2'}",
        f"skipped, no viewer/run.json: {tmp_path / 'sim_3'}",
        f"skipped, no viewer/run.json: {tmp_path / 'sim_2'}",
    ]
