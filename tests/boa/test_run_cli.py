"""Unit tests for the boa-run CLI: weather-year detection, preflight, arg surface."""

import pytest

from boa.cli.run_simulation import main_point, main_run, preflight
from boa.config.paths import PathConfig
from boa.config.settings import REGION_COORDS
from boa.inputs.profiles import detect_weather_year
from boa.store_schema import max_cap_store_stem, profile_store_stem


@pytest.fixture
def tmp_config(tmp_path):
    return PathConfig.from_root(tmp_path)


def _make_store_dirs(config, year, regions=None, kinds=("profile", "max_cap")):
    for region in regions or REGION_COORDS:
        if "profile" in kinds:
            (config.zarr_dir / (profile_store_stem(region, year) + ".zarr")).mkdir(parents=True)
        if "max_cap" in kinds:
            (config.zarr_dir / (max_cap_store_stem(region, year) + ".zarr")).mkdir(parents=True)


# ---- weather-year detection --------------------------------------------------


def test_detect_weather_year_from_store_names(tmp_config):
    _make_store_dirs(tmp_config, 2023, regions=["EU"])
    assert detect_weather_year(tmp_config) == 2023


def test_detect_weather_year_empty_dir_points_at_prepare(tmp_config):
    tmp_config.zarr_dir.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="boa-cds-prepare"):
        detect_weather_year(tmp_config)


def test_detect_weather_year_rejects_mixed_years(tmp_config):
    _make_store_dirs(tmp_config, 2023, regions=["EU"], kinds=("profile",))
    _make_store_dirs(tmp_config, 2024, regions=["EU"], kinds=("profile",))
    with pytest.raises(ValueError, match="2023, 2024"):
        detect_weather_year(tmp_config)


def test_detect_weather_year_ignores_max_capacity_stores(tmp_config):
    # Only profile stores carry the year; a stray max-cap store for another year must not confuse it.
    _make_store_dirs(tmp_config, 2023, regions=["EU"], kinds=("profile",))
    _make_store_dirs(tmp_config, 2024, regions=["EU"], kinds=("max_cap",))
    assert detect_weather_year(tmp_config) == 2023


# ---- preflight ---------------------------------------------------------------


def test_preflight_reports_missing_regions(tmp_config):
    _make_store_dirs(tmp_config, 2024, regions=["EU"])
    with pytest.raises(FileNotFoundError, match="boa-cds-prepare --weather_year 2024"):
        preflight(tmp_config)


def test_preflight_reports_missing_cost_workbook(tmp_config):
    _make_store_dirs(tmp_config, 2024)
    with pytest.raises(FileNotFoundError, match="boa-data-prepare"):
        preflight(tmp_config)


def test_preflight_ok_returns_weather_year(tmp_config):
    _make_store_dirs(tmp_config, 2024)
    tmp_config.input_data_path.parent.mkdir(parents=True)
    tmp_config.input_data_path.touch()
    assert preflight(tmp_config) == 2024


def test_preflight_point_mode_skips_store_completeness(tmp_config):
    _make_store_dirs(tmp_config, 2024, regions=["EU"])
    tmp_config.input_data_path.parent.mkdir(parents=True)
    tmp_config.input_data_path.touch()
    assert preflight(tmp_config, require_all_stores=False) == 2024


# ---- CLI surface -------------------------------------------------------------


def test_bare_run_rejects_region_flag(capsys):
    with pytest.raises(SystemExit):
        main_run(["--region", "EU"])
    assert "--region" in capsys.readouterr().err


def test_bare_run_rejects_lat_lon(capsys):
    with pytest.raises(SystemExit):
        main_run(["--lat", "52.5", "--lon", "13.4"])


def test_point_requires_coordinates(capsys):
    with pytest.raises(SystemExit):
        main_point([])
    assert "--lat" in capsys.readouterr().err


def test_point_validates_coordinate_ranges():
    assert main_point(["--lat", "95", "--lon", "13.4"]) == 1


def test_dry_run_preflights_without_writing_run_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path))
    config = PathConfig.from_root(tmp_path)
    _make_store_dirs(config, 2024)
    config.input_data_path.parent.mkdir(parents=True)
    config.input_data_path.touch()
    assert main_run(["--dry-run"]) == 0
    assert not config.run_manifest_path.exists()


def test_dry_run_fails_cleanly_on_missing_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path))
    assert main_run(["--dry-run"]) == 1
