"""
Tests for CLI creating and passing SimulationConfig.
"""

import json
import sys
from unittest.mock import patch, MagicMock, Mock
from pathlib import Path
import tempfile

import pytest

from steelo.data.cache_manager import DataPreparationCache
from steelo.simulation import SimulationConfig
from steelo.entrypoints.cli import run_full_simulation
from steelo.domain import Year


def stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner):
    """Wire the S3 download, data preparation and runner factory out of the CLI path."""
    # Setup the data directory where CLI will create it
    data_dir = cli_temp_base / "data"
    fixtures_dir = data_dir / "fixtures"

    # Mock DataManager to avoid S3 download
    mock_data_manager = MagicMock()
    mock_master_input_path = cli_temp_base / "master-input"
    mock_master_input_path.mkdir(parents=True, exist_ok=True)
    (mock_master_input_path / "master_input.xlsx").touch()
    mock_data_manager.get_package_path.return_value = mock_master_input_path
    mock_data_manager.download_package.return_value = None
    mock_data_manager_class.return_value = mock_data_manager

    # Mock data preparation service
    mock_prep_service = MagicMock()
    mock_prep_result = MagicMock()
    mock_prep_result.data_dir = data_dir
    mock_prep_result.output_directory = fixtures_dir

    # Add specific attributes that will be serialized
    mock_prep_result.master_excel_path = None
    mock_prep_result.master_excel_source = "S3"
    mock_prep_result.data_package_version = "1.0.0"
    mock_prep_result.cache_used = False
    mock_prep_result.preparation_time = 1.23
    mock_prep_result.total_duration = 2.5
    mock_prep_result.files = ["file1.json", "file2.json"]

    # Make prepare_data create the required files
    def prepare_data_side_effect(*args, **kwargs):
        # Get the actual output_dir from kwargs
        actual_output_dir = kwargs.get("output_dir", fixtures_dir)
        # Create the directories and files when prepare_data is called
        actual_output_dir.parent.mkdir(parents=True, exist_ok=True)
        actual_output_dir.mkdir(parents=True, exist_ok=True)

        # Create the fixtures subdirectory where files are expected
        fixtures_subdir = actual_output_dir / "fixtures"
        fixtures_subdir.mkdir(parents=True, exist_ok=True)

        # Create required JSON files in fixtures directory
        (fixtures_subdir / "plants.json").write_text("[]")
        (fixtures_subdir / "demand_centers.json").write_text("[]")
        (fixtures_subdir / "suppliers.json").write_text("[]")
        (fixtures_subdir / "scenarios.json").write_text(
            json.dumps(
                {
                    "demand": {"BAU": "demand_centers.json", "High growth": "demand_centers.json"},
                    "scrap": {"BAU": "suppliers.json", "High recycling": "suppliers.json"},
                },
            ),
        )
        (fixtures_subdir / "country_mappings.json").write_text("[]")
        (fixtures_subdir / "railway_costs.json").write_text("[]")

        # Update the mock result to use the actual directory
        mock_prep_result.output_directory = fixtures_subdir
        mock_prep_result.data_dir = actual_output_dir
        return mock_prep_result

    mock_prep_service.prepare_data.side_effect = prepare_data_side_effect
    mock_data_prep_service_class.return_value = mock_prep_service

    # Mock the runner to avoid actual simulation
    mock_runner = MagicMock()
    mock_runner.run = Mock()
    mock_create_runner.return_value = mock_runner


@patch("steelo.entrypoints.cli.setup_legacy_symlinks")  # Mock to prevent symlink creation
@patch("steelo.entrypoints.cli.update_output_symlink")  # Mock to prevent symlink creation
@patch("steelo.entrypoints.cli.update_data_symlink")  # Mock to prevent symlink creation
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")  # Mock to prevent S3 download
def test_cli_creates_and_passes_simulation_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
):
    """
    Tests that the CLI entrypoint correctly creates a SimulationConfig
    object and passes it to the simulation runner factory.
    """
    # Create a real temporary directory for the test
    with tempfile.TemporaryDirectory() as tmpdir:
        # Setup the CLI's expected temp directory structure
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        # Simulate running the CLI with specific arguments
        with patch.object(sys, "argv", ["run_simulation", "--start-year", "2026", "--end-year", "2035"]):
            # Capture the result to avoid system exit
            with patch("sys.exit"):
                run_full_simulation()

        # Assert that our factory was called
        mock_create_runner.assert_called_once()

        # Get the arguments it was called with
        args, kwargs = mock_create_runner.call_args

        # Check that the first argument is a SimulationConfig instance
        assert len(args) > 0
        assert isinstance(args[0], SimulationConfig)

        # Check that the config reflects the CLI arguments
        config = args[0]
        assert config.start_year == Year(2026)
        assert config.end_year == Year(2035)


@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_successful_run_returns_none_so_the_console_script_exits_zero(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
):
    """The console script calls sys.exit() on the return value, and anything but None or 0 exits non-zero."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stub_cli_dependencies(Path(tmpdir), mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        with patch.object(sys, "argv", ["run_simulation", "--start-year", "2026", "--end-year", "2035"]):
            result = run_full_simulation()

    assert result is None


@pytest.mark.parametrize("cache_flag", ["--cache-stats", "--clear-cache"])
def test_cli_cache_operations_return_none_so_the_console_script_exits_zero(cache_flag, tmp_path):
    """The cache-only flags finish without running a simulation and must not hand sys.exit() a message."""
    with patch.object(sys, "argv", ["run_simulation", cache_flag, "--steelo-home", str(tmp_path)]):
        result = run_full_simulation()

    assert result is None


@pytest.mark.parametrize(
    "flag_argv, expected",
    [([], "Business As Usual"), (["--grid-emissions-scenario", "Net Zero"], "Net Zero")],
)
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_grid_emissions_scenario_flag_lands_on_the_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    flag_argv,
    expected,
):
    """``--grid-emissions-scenario`` selects the grid emissivity projection; absent, BAU applies."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        argv = ["run_simulation", "--start-year", "2026", "--end-year", "2027", *flag_argv]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.chosen_grid_emissions_scenario == expected


@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_runs_differing_only_by_scenario_look_up_one_cached_preparation(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    tmp_path,
    monkeypatch,
):
    """A preparation holds every demand and scrap scenario, so two runs naming different scenarios share one key."""
    stub_cli_dependencies(tmp_path, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)
    master = tmp_path / "master.xlsx"
    master.write_bytes(b"workbook")
    keys = []
    original_get_cache_key = DataPreparationCache.get_cache_key

    def recorded_get_cache_key(cache, master_excel_path):
        keys.append(original_get_cache_key(cache, master_excel_path))
        return keys[-1]

    monkeypatch.setattr(DataPreparationCache, "get_cache_key", recorded_get_cache_key)

    for scenario_argv in [[], ["--demand-scenario", "High growth", "--scrap-scenario", "High recycling"]]:
        argv = [
            "run_simulation",
            "--end-year",
            "2026",
            "--master-excel",
            str(master),
            "--steelo-home",
            str(tmp_path / "home"),
            *scenario_argv,
        ]
        with patch.object(sys, "argv", argv):
            run_full_simulation()

    assert len(keys) == 2
    assert keys[0] == keys[1]


@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_unknown_demand_scenario_exits_with_the_prepared_names_before_any_run_output(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    tmp_path,
    capsys,
):
    """A scenario the preparation lacks exits non-zero naming those it holds; no run file is written, the data kept."""
    stub_cli_dependencies(tmp_path, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)
    home = tmp_path / "home"
    output_base = tmp_path / "runs"
    argv = [
        "run_simulation",
        "--end-year",
        "2026",
        "--steelo-home",
        str(home),
        "--output-dir",
        str(output_base),
        "--demand-scenario",
        "Nope",
    ]

    with patch.object(sys, "argv", argv):
        with pytest.raises(SystemExit) as exit_info:
            run_full_simulation()

    assert exit_info.value.code == 1
    message = " ".join(capsys.readouterr().out.split())
    assert "No demand scenario 'Nope'" in message
    assert "available: ['BAU', 'High growth']" in message
    mock_create_runner.assert_not_called()
    assert [path for path in output_base.rglob("*") if path.is_file()] == []
    assert list(home.glob("temp_prep/prep_*/fixtures/scenarios.json")) != []


@pytest.mark.parametrize(
    "flag_argv, expected",
    [([], 42), (["--random-seed", "7"], 7)],
)
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_random_seed_lands_on_the_config_and_geo_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    flag_argv,
    expected,
):
    """``--random-seed`` sets the run-time seed and propagates into GeoConfig; absent, the default 42 applies."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        argv = ["run_simulation", "--start-year", "2026", "--end-year", "2027", *flag_argv]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.random_seed == expected
        assert config.geo_config.random_seed == expected


@pytest.mark.parametrize(
    "flag_argv, expected_percentile, expected_trade",
    [
        ([], 100.0, False),
        (["--hydrogen-ceiling-percentile", "20"], 20.0, False),
        (["--intraregional-trade"], 100.0, True),
        (["--no-intraregional-trade"], 100.0, False),
        (["--hydrogen-ceiling-percentile", "20", "--intraregional-trade"], 20.0, True),
    ],
)
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_hydrogen_trade_flags_land_on_the_geo_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    flag_argv,
    expected_percentile,
    expected_trade,
):
    """``--hydrogen-ceiling-percentile`` and ``--[no-]intraregional-trade`` override the GeoConfig defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        argv = ["run_simulation", "--start-year", "2026", "--end-year", "2027", *flag_argv]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.geo_config.hydrogen_ceiling_percentile == expected_percentile
        assert config.geo_config.intraregional_trade_allowed is expected_trade


@pytest.mark.parametrize(
    "flag_argv, expected_enabled, expected_scope",
    [
        ([], True, "plant"),
        (["--clustering"], True, "plant"),
        (["--no-clustering"], False, "plant"),
        (["--enable-clustering", "--clustering-scope", "plant"], True, "plant"),
        (["--clustering-scope", "iso3"], True, "iso3"),
        (["--clustering-scope", "plant_group"], True, "plant_group"),
    ],
)
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_clustering_flags_land_on_the_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    flag_argv,
    expected_enabled,
    expected_scope,
):
    """Clustering by plant is the default; ``--[no-]clustering`` and ``--clustering-scope`` override it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        argv = ["run_simulation", "--start-year", "2026", "--end-year", "2027", *flag_argv]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.enable_furnace_group_clustering is expected_enabled
        assert config.geographical_clustering_scope == expected_scope


@pytest.mark.parametrize(
    "flag_argv, expected",
    [([], None), (["--run-name", "china BAU"], "china BAU")],
)
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_run_name_lands_on_the_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    flag_argv,
    expected,
):
    """``--run-name`` names the run in the interactive plot titles; absent, the config carries None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        argv = ["run_simulation", "--start-year", "2026", "--end-year", "2027", *flag_argv]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.run_name == expected


@pytest.mark.parametrize(
    "taken_names, run_name, expected",
    [
        (["test"], "test", "test_2"),
        (["test", "test_2"], "test", "test_3"),
        (["test"], "other", "other"),
        (["  test  "], "test", "test_2"),
        ([], "   ", None),
    ],
)
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_run_name_in_use_under_the_output_base_gets_a_suffix(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    taken_names,
    run_name,
    expected,
):
    """
    A ``--run-name`` already held by a ``simulation_config.json`` under the output base gets the
    first free ``_2``, ``_3`` suffix, even in a folder without ``run.json``; names compare trimmed,
    any other name is kept, and a blank name records no name.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)
        output_base = cli_temp_base / "my-runs"
        for folder, taken in zip(["sim_a", "sim_b"], taken_names):
            (output_base / folder).mkdir(parents=True)
            (output_base / folder / "simulation_config.json").write_text(json.dumps({"run_name": taken}))

        argv = [
            "run_simulation",
            "--start-year",
            "2026",
            "--end-year",
            "2027",
            "--output-dir",
            str(output_base),
            "--run-name",
            run_name,
        ]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.run_name == expected
        assert json.loads((config.output_dir / "simulation_config.json").read_text())["run_name"] == expected


@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_output_dir_is_the_base_of_the_run_folder(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
):
    """``--output-dir`` is the base folder: the run's ``sim_<timestamp>`` folder is created inside it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)
        output_base = cli_temp_base / "my-runs"

        argv = ["run_simulation", "--start-year", "2026", "--end-year", "2027", "--output-dir", str(output_base)]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.output_dir.parent == output_base
        assert config.output_dir.name.startswith("sim_")


@pytest.mark.parametrize("flag_argv, expected", [([], False), (["--enable-capacity-policy"], True)])
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_capacity_policy_flag_drives_the_nested_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    flag_argv,
    expected,
):
    """``--enable-capacity-policy`` is the only policy surface; absent, the run stays off."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        argv = ["run_simulation", "--start-year", "2026", "--end-year", "2027", *flag_argv]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.capacity_policy.enabled is expected


@pytest.mark.parametrize("flag_argv, expected", [([], False), (["--cbam-rebate"], True)])
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_cbam_rebate_flag_lands_on_the_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    flag_argv,
    expected,
):
    """``--cbam-rebate`` switches on carbon border export rebates; absent, they stay off."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        argv = ["run_simulation", "--start-year", "2026", "--end-year", "2027", *flag_argv]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.carbon_border_export_rebates is expected


@pytest.mark.parametrize(
    "argv_tail, message",
    [
        (["--enable-capacity-policy", "--credit-validity-years", "0"], "must be a positive number of years"),
        (["--credit-validity-years", "3"], "requires --enable-capacity-policy"),
    ],
)
def test_cli_credit_validity_years_is_refused_at_parse_time(argv_tail, message, capsys):
    """A non-positive shelf life, or one given without the policy flag, stops the run before any setup."""
    with patch.object(sys, "argv", ["run_simulation", *argv_tail]):
        with pytest.raises(SystemExit) as raised:
            run_full_simulation()

    assert raised.value.code == 2
    assert message in capsys.readouterr().err


@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_run_without_steelo_home_writes_into_the_test_home(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    tmp_path,
):
    """A CLI call without ``--steelo-home`` lands in the test's own home, never in the developer's ``~/.steelo``."""
    stub_cli_dependencies(tmp_path / "stubs", mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

    with patch.object(sys, "argv", ["run_simulation", "--start-year", "2026", "--end-year", "2027"]):
        with patch("sys.exit"):
            run_full_simulation()

    config = mock_create_runner.call_args[0][0]
    assert config.output_dir.parent == tmp_path / "home" / "output"


def test_cli_output_file_flag_is_refused_at_parse_time(capsys):
    """``--output-file`` names a JSON that nothing writes, so the flag no longer exists and stops the run at parsing."""
    with patch.object(sys, "argv", ["run_simulation", "--output-file", "x.json"]):
        with pytest.raises(SystemExit) as raised:
            run_full_simulation()

    assert raised.value.code == 2
    assert "--output-file" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flag",
    ["--plants-json", "--demand-excel", "--location-csv", "--cost-of-x-csv", "--demand-sheet"],
)
def test_cli_unread_input_file_flags_are_refused_at_parse_time(flag, capsys):
    """
    The four single-file flags and the demand sheet name were never read, so they no longer exist and stop the run at
    parsing.
    """
    with patch.object(sys, "argv", ["run_simulation", flag, "x"]):
        with pytest.raises(SystemExit) as raised:
            run_full_simulation()

    assert raised.value.code == 2
    assert flag in capsys.readouterr().err


@pytest.mark.parametrize(
    "flag_argv, expected",
    [(["--enable-capacity-policy"], None), (["--enable-capacity-policy", "--credit-validity-years", "2"], 2)],
)
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_credit_validity_years_lands_on_the_policy_config(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    flag_argv,
    expected,
):
    """``--credit-validity-years`` sets the banked-credit shelf life; absent, credits never expire."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_temp_base = Path(tmpdir)
        stub_cli_dependencies(cli_temp_base, mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)

        argv = ["run_simulation", "--start-year", "2026", "--end-year", "2027", *flag_argv]
        with patch.object(sys, "argv", argv):
            with patch("sys.exit"):
                run_full_simulation()

        config = mock_create_runner.call_args[0][0]
        assert config.capacity_policy.credit_validity_years == expected


def write_prepared_data(data_dir: Path) -> Path:
    """A prepared data folder holding what the CLI checks before a run: the plants and both BAU scenarios."""
    fixtures = data_dir / "fixtures"
    fixtures.mkdir(parents=True)
    for name in ("plants", "demand_centers", "suppliers", "country_mappings", "railway_costs"):
        (fixtures / f"{name}.json").write_text("[]")
    (fixtures / "scenarios.json").write_text(
        json.dumps({"demand": {"BAU": "demand_centers.json"}, "scrap": {"BAU": "suppliers.json"}}),
    )
    return data_dir


@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_boa_run_folder_sets_both_fields_on_a_fresh_preparation(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    tmp_path,
):
    """``--boa-run`` with a folder records the folder and its name, in the config and in simulation_config.json."""
    stub_cli_dependencies(tmp_path / "stubs", mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)
    run_dir = tmp_path / "anywhere" / "my_run"
    run_dir.mkdir(parents=True)
    argv = ["run_simulation", "--end-year", "2026", "--steelo-home", str(tmp_path / "home"), "--boa-run", str(run_dir)]

    with patch.object(sys, "argv", argv):
        run_full_simulation()

    mock_data_prep_service_class.return_value.prepare_data.assert_called_once()
    config = mock_create_runner.call_args[0][0]
    assert config.boa_run_dir == run_dir
    assert config.boa_run == "my_run"
    saved = json.loads((config.output_dir / "simulation_config.json").read_text())
    assert saved["boa_run_dir"] == str(run_dir)
    assert saved["boa_run"] == "my_run"


@pytest.mark.parametrize(
    ("boa_argv", "expected_name"),
    [(["--boa-run", "default_49e8"], "default_49e8"), ([], None)],
)
@patch("steelo.entrypoints.cli.setup_legacy_symlinks")
@patch("steelo.entrypoints.cli.update_output_symlink")
@patch("steelo.entrypoints.cli.update_data_symlink")
@patch("steelo.entrypoints.cli.bootstrap_simulation")
@patch("steelo.data.DataPreparationService")
@patch("steelo.data.DataManager")
def test_cli_boa_run_name_sets_both_fields_on_a_cached_preparation(
    mock_data_manager_class,
    mock_data_prep_service_class,
    mock_create_runner,
    mock_update_data_symlink,
    mock_update_output_symlink,
    mock_setup_legacy_symlinks,
    tmp_path,
    monkeypatch,
    boa_argv,
    expected_name,
):
    """A run name resolves under $BOA_DATA_ROOT/lcoe-only/; without the flag both fields stay empty."""
    stub_cli_dependencies(tmp_path / "stubs", mock_data_manager_class, mock_data_prep_service_class, mock_create_runner)
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "boa"))
    run_dir = tmp_path / "boa" / "lcoe-only" / "default_49e8"
    run_dir.mkdir(parents=True)
    prepared = write_prepared_data(tmp_path / "prepared")
    monkeypatch.setattr(DataPreparationCache, "get_cached_preparation", lambda cache, master: prepared)
    argv = ["run_simulation", "--end-year", "2026", "--steelo-home", str(tmp_path / "home"), *boa_argv]

    with patch.object(sys, "argv", argv):
        run_full_simulation()

    mock_data_prep_service_class.return_value.prepare_data.assert_not_called()
    config = mock_create_runner.call_args[0][0]
    assert config.boa_run == expected_name
    assert config.boa_run_dir == (run_dir if expected_name else None)


@patch("steelo.data.DataPreparationService")
def test_cli_unknown_boa_run_exits_before_the_home_or_any_preparation(
    mock_data_prep_service_class,
    tmp_path,
    monkeypatch,
    capsys,
):
    """An unknown run name exits 1 listing the runs there, before the home, the cache or a preparation exists."""
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "boa"))
    (tmp_path / "boa" / "lcoe-only" / "default_49e8").mkdir(parents=True)
    home = tmp_path / "fresh_home"
    argv = ["run_simulation", "--steelo-home", str(home), "--boa-run", "no_such_run"]

    with patch.object(sys, "argv", argv):
        with pytest.raises(SystemExit) as raised:
            run_full_simulation()

    assert raised.value.code == 1
    mock_data_prep_service_class.assert_not_called()
    assert not home.exists()
    printed = capsys.readouterr().out
    assert "no_such_run" in printed
    assert "default_49e8" in printed


def test_cli_baseload_power_sim_dir_flag_is_refused_at_parse_time(capsys):
    """``--baseload-power-sim-dir`` never reached a run's prices, so the flag no longer exists and stops the run at parsing."""
    with patch.object(sys, "argv", ["run_simulation", "--baseload-power-sim-dir", "x"]):
        with pytest.raises(SystemExit) as raised:
            run_full_simulation()

    assert raised.value.code == 2
    assert "--baseload-power-sim-dir" in capsys.readouterr().err
