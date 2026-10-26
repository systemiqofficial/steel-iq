"""Unit tests for the bootstrap check of the baseload LCOE file a run's power mix needs."""

import logging
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from steelo import bootstrap
from steelo.domain.models import Year
from steelo.simulation import GeoConfig, SimulationConfig
from steelo.simulation_types import get_default_technology_settings

CONTRACT = {
    "schema_version": 1,
    "lcoe_basis": "per_mwh_delivered",
    "coverage_fraction": 0.85,
    "run": "test_run",
    "boa_version": "1.9.0",
}


def make_config(tmp_path: Path, power_mix: str, boa_run_dir: Path | None = None) -> SimulationConfig:
    """A run configuration with the given power mix and, optionally, a named BOA run."""
    return SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2026),
        master_excel_path=tmp_path / "test.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
        geo_config=GeoConfig(included_power_mix=power_mix),
        boa_run_dir=boa_run_dir,
    )


def write_lcoe_file(folder: Path, attrs: dict) -> Path:
    """A tiny promoted 85% coverage file with the given attributes."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc"
    xr.Dataset(
        {"lcoe": (("year", "lat", "lon"), np.full((1, 2, 2), 60.0, dtype=np.float32))},
        coords={"year": [2025], "lat": [0.0, 0.25], "lon": [0.0, 0.25]},
        attrs=attrs,
    ).to_netcdf(path)
    return path


@pytest.mark.parametrize("power_mix", ["Grid only", "Not included"])
def test_check_baseload_lcoe_file_needs_no_file_without_a_baseload_share(tmp_path, power_mix):
    """A mix with no baseload share reads nothing, so a missing folder is fine."""
    config = make_config(tmp_path, power_mix)

    assert bootstrap.check_baseload_lcoe_file(config, tmp_path / "absent") is None


def test_check_baseload_lcoe_file_stops_on_a_default_folder_without_files(tmp_path):
    """The default mix without the shipped files stops, saying where they come from and naming --boa-run."""
    config = make_config(tmp_path, "85% baseload + 15% grid")
    default_folder = tmp_path / "data" / "outputs" / "GEO" / "baseload_lcoe"

    with pytest.raises(FileNotFoundError) as excinfo:
        bootstrap.check_baseload_lcoe_file(config, default_folder)

    assert "geo-data 3.0.0" in str(excinfo.value)
    assert "--boa-run" in str(excinfo.value)


def test_check_baseload_lcoe_file_in_a_named_run_leaves_out_the_default_note(tmp_path):
    """A named run without the file stops naming the folder and the token, without the default-files note."""
    run_dir = tmp_path / "my_run"
    run_dir.mkdir()
    config = make_config(tmp_path, "85% baseload + 15% grid", boa_run_dir=run_dir)

    with pytest.raises(FileNotFoundError) as excinfo:
        bootstrap.check_baseload_lcoe_file(config, run_dir)

    assert "_cov0p85_" in str(excinfo.value)
    assert "geo-data" not in str(excinfo.value)


def test_check_baseload_lcoe_file_refuses_a_file_without_the_contract(tmp_path):
    """A file that lacks the contract's attributes stops the run before its first year."""
    run_dir = tmp_path / "my_run"
    write_lcoe_file(run_dir, {"coverage_fraction": 0.85})
    config = make_config(tmp_path, "85% baseload + 15% grid", boa_run_dir=run_dir)

    with pytest.raises(ValueError, match="schema_version missing"):
        bootstrap.check_baseload_lcoe_file(config, run_dir)


def test_check_baseload_lcoe_file_logs_the_file_its_run_and_boa_version(tmp_path, caplog):
    """An accepted file is returned and named at INFO with the BOA run and version that made it."""
    run_dir = tmp_path / "my_run"
    path = write_lcoe_file(run_dir, CONTRACT)
    config = make_config(tmp_path, "85% baseload + 15% grid", boa_run_dir=run_dir)
    caplog.set_level(logging.INFO, logger="steelo.bootstrap")

    assert bootstrap.check_baseload_lcoe_file(config, run_dir) == path
    assert f"Baseload LCOE: {path} (run test_run, BOA 1.9.0)" in caplog.text
