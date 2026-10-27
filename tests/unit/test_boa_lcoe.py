"""Unit tests for finding, checking and reading promoted BOA LCOE files (steelo.adapters.geospatial.boa_lcoe)."""

import logging
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from steelo.adapters.geospatial import boa_lcoe

GOOD_ATTRS = {"schema_version": 1, "lcoe_basis": "per_mwh_delivered", "coverage_fraction": 0.85}


def write_lcoe_file(folder: Path, name: str, attrs: dict | None = None) -> Path:
    """
    Write a tiny promoted LCOE file: 2025 at 60 and 2035 at 80 USD/MWh on a 2 x 2 grid.

    Args:
        folder: Folder to write into; created when missing.
        name: File name.
        attrs: File attributes; the contract's three by default.

    Returns:
        The file's path.
    """
    folder.mkdir(parents=True, exist_ok=True)
    lcoe = np.stack([np.full((2, 2), 60.0), np.full((2, 2), 80.0)]).astype(np.float32)
    ds = xr.Dataset(
        {"lcoe": (("year", "lat", "lon"), lcoe)},
        coords={"year": np.array([2025, 2035], dtype=np.int32), "lat": [0.0, 0.25], "lon": [0.0, 0.25]},
        attrs=GOOD_ATTRS if attrs is None else attrs,
    )
    ds.to_netcdf(folder / name)
    return folder / name


def test_boa_root_reads_boa_data_root(tmp_path, monkeypatch):
    """$BOA_DATA_ROOT, when set, is the root."""
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "boa"))

    assert boa_lcoe.boa_root() == tmp_path / "boa"


def test_boa_root_defaults_to_dot_boa_in_home(tmp_path, monkeypatch):
    """Without $BOA_DATA_ROOT the root is ~/.boa."""
    monkeypatch.delenv("BOA_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert boa_lcoe.boa_root() == tmp_path / ".boa"


def test_resolve_boa_run_uses_an_existing_folder_as_given(tmp_path, monkeypatch):
    """A folder that exists is the run, wherever it is."""
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "boa"))
    folder = tmp_path / "anywhere" / "my_run"
    folder.mkdir(parents=True)

    assert boa_lcoe.resolve_boa_run(str(folder)) == folder


def test_resolve_boa_run_finds_a_run_name_under_the_root(tmp_path, monkeypatch):
    """A name that is no folder is looked up in <root>/lcoe-only/."""
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "boa"))
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "boa" / "lcoe-only" / "default_49e8"
    run.mkdir(parents=True)

    assert boa_lcoe.resolve_boa_run("default_49e8") == run


def test_resolve_boa_run_lists_the_runs_for_an_unknown_name(tmp_path, monkeypatch):
    """An unknown name names both places tried and lists every run with its files."""
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "boa"))
    monkeypatch.chdir(tmp_path)
    write_lcoe_file(tmp_path / "boa" / "lcoe-only" / "default_49e8", "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc")

    with pytest.raises(FileNotFoundError) as excinfo:
        boa_lcoe.resolve_boa_run("no_such_run")

    message = str(excinfo.value)
    assert str(tmp_path / "no_such_run") in message
    assert str(tmp_path / "boa" / "lcoe-only" / "no_such_run") in message
    assert "default_49e8: optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc" in message


@pytest.mark.parametrize("relative", [False, True])
def test_resolve_boa_run_with_an_unknown_path_lists_the_runs_under_the_root(tmp_path, monkeypatch, relative):
    """A path that is no folder, absolute or relative, still lists the runs under <root>/lcoe-only/."""
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "boa"))
    monkeypatch.chdir(tmp_path)
    write_lcoe_file(tmp_path / "boa" / "lcoe-only" / "default_49e8", "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc")
    value = "runs/default_49e8" if relative else str(tmp_path / "nowhere" / "default_49e8")

    with pytest.raises(FileNotFoundError) as excinfo:
        boa_lcoe.resolve_boa_run(value)

    message = str(excinfo.value)
    if not relative:
        assert f"no folder at {tmp_path / 'nowhere' / 'default_49e8'}.\n" in message
    assert f"Promoted BOA runs under {tmp_path / 'boa' / 'lcoe-only'}:" in message
    assert "default_49e8: optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc" in message


def test_resolve_boa_run_says_when_the_root_has_no_runs(tmp_path, monkeypatch):
    """With no lcoe-only folder at all, the error says there are no runs."""
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "boa"))
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError, match="No promoted BOA runs under"):
        boa_lcoe.resolve_boa_run("no_such_run")


@pytest.mark.parametrize(
    ("coverage", "token"),
    [
        (0.75, "0p75"),
        (0.8, "0p8"),
        (0.85, "0p85"),
        (0.9, "0p9"),
        (0.95, "0p95"),
        (1.0, "1"),
    ],
)
def test_coverage_token_follows_boa_file_names(coverage, token):
    """The token drops the dot and trailing zeros, as BOA writes it."""
    assert boa_lcoe.coverage_token(coverage) == token


def test_pick_lcoe_file_does_not_take_a_neighbouring_coverage(tmp_path):
    """cov0p9 matches only its own file, not cov0p95."""
    write_lcoe_file(tmp_path, "optimal_lcoe_wy2024_01MWkm2_cov0p95_2025_2060.nc")

    with pytest.raises(FileNotFoundError):
        boa_lcoe.pick_lcoe_file(tmp_path, 0.9)

    ninety = write_lcoe_file(tmp_path, "optimal_lcoe_wy2024_01MWkm2_cov0p9_2025_2060.nc")
    assert boa_lcoe.pick_lcoe_file(tmp_path, 0.9) == ninety


def test_pick_lcoe_file_finds_an_old_shape_name_by_its_token(tmp_path):
    """A file named in the older pattern is found by its coverage token alone."""
    old_shape = write_lcoe_file(tmp_path, "optimal_lcoe_100MW_cov0p85_2025_2060.nc")

    assert boa_lcoe.pick_lcoe_file(tmp_path, 0.85) == old_shape


def test_pick_lcoe_file_without_a_match_names_folder_token_and_files(tmp_path):
    """No file for the coverage: the error names the folder, the token and what the folder holds."""
    write_lcoe_file(tmp_path, "optimal_lcoe_wy2024_01MWkm2_cov0p95_2025_2060.nc")

    with pytest.raises(FileNotFoundError) as excinfo:
        boa_lcoe.pick_lcoe_file(tmp_path, 0.85)

    message = str(excinfo.value)
    assert str(tmp_path) in message
    assert "_cov0p85_" in message
    assert "optimal_lcoe_wy2024_01MWkm2_cov0p95_2025_2060.nc" in message
    assert "geo-data" not in message


def test_pick_lcoe_file_with_several_matches_lists_them(tmp_path):
    """Two files for one coverage are an error listing both."""
    write_lcoe_file(tmp_path, "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc")
    write_lcoe_file(tmp_path, "optimal_lcoe_wy2023_01MWkm2_cov0p85_2025_2060.nc")

    with pytest.raises(ValueError) as excinfo:
        boa_lcoe.pick_lcoe_file(tmp_path, 0.85)

    assert "optimal_lcoe_wy2023_01MWkm2_cov0p85_2025_2060.nc" in str(excinfo.value)
    assert "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc" in str(excinfo.value)


def test_pick_lcoe_file_in_the_default_folder_says_where_its_files_come_from(tmp_path):
    """For the prepared data's default folder, the error names geo-data and --boa-run."""
    tmp_path.joinpath("baseload_lcoe").mkdir()

    with pytest.raises(FileNotFoundError) as excinfo:
        boa_lcoe.pick_lcoe_file(tmp_path / "baseload_lcoe", 0.85, default_folder=True)

    message = str(excinfo.value)
    assert "no .nc files" in message
    assert "geo-data 3.0.0" in message
    assert "--boa-run" in message


@pytest.mark.parametrize("default_folder", [False, True])
def test_pick_lcoe_file_says_when_the_folder_does_not_exist(tmp_path, default_folder):
    """A missing folder is named as missing; the default note comes only for the default folder."""
    with pytest.raises(FileNotFoundError) as excinfo:
        boa_lcoe.pick_lcoe_file(tmp_path / "absent", 0.85, default_folder=default_folder)

    message = str(excinfo.value)
    assert f"{tmp_path / 'absent'} does not exist" in message
    assert ("--boa-run" in message) is default_folder


def test_open_lcoe_file_accepts_a_file_stating_the_contract(tmp_path):
    """A file with schema version 1, a delivered basis and the coverage priced opens."""
    path = write_lcoe_file(tmp_path, "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc")

    with boa_lcoe.open_lcoe_file(path, 0.85) as ds:
        assert ds["lcoe"].dims == ("year", "lat", "lon")


@pytest.mark.parametrize(
    ("attribute", "value", "shown"),
    [
        ("schema_version", 2, "schema_version 2"),
        ("schema_version", None, "schema_version missing"),
        ("lcoe_basis", "per_mwh_generated", "lcoe_basis 'per_mwh_generated'"),
        ("lcoe_basis", None, "lcoe_basis missing"),
        ("coverage_fraction", 0.9, "coverage_fraction 0.9"),
        ("coverage_fraction", "abc", "coverage_fraction 'abc'"),
        ("coverage_fraction", None, "coverage_fraction missing"),
    ],
)
def test_open_lcoe_file_refuses_a_wrong_or_missing_attribute(tmp_path, attribute, value, shown):
    """Each contract attribute, wrong or missing, refuses the file with the file and attribute named."""
    attrs = {key: val for key, val in GOOD_ATTRS.items() if key != attribute}
    if value is not None:
        attrs[attribute] = value
    path = write_lcoe_file(tmp_path, "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc", attrs)

    with pytest.raises(ValueError) as excinfo:
        boa_lcoe.open_lcoe_file(path, 0.85)

    assert str(path) in str(excinfo.value)
    assert shown in str(excinfo.value)


def test_open_lcoe_file_refuses_an_older_file_without_schema_version_or_basis(tmp_path):
    """A file from before the contract, with only its coverage and percentile, is refused."""
    path = write_lcoe_file(
        tmp_path,
        "optimal_lcoe_100MW_cov0p85_2025_2060.nc",
        {"coverage_fraction": 0.85, "p_percentile": 15},
    )

    with pytest.raises(ValueError, match="schema_version missing"):
        boa_lcoe.open_lcoe_file(path, 0.85)


def test_lcoe_for_year_reads_a_year_in_the_file(tmp_path):
    """A year the file holds is returned as it is, without a year coordinate."""
    path = write_lcoe_file(tmp_path, "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc")

    with boa_lcoe.open_lcoe_file(path, 0.85) as ds:
        lcoe = boa_lcoe.lcoe_for_year(ds, 2035).load()

    assert "year" not in lcoe.coords
    np.testing.assert_allclose(lcoe.values, 80.0)


def test_lcoe_for_year_interpolates_between_two_years(tmp_path):
    """A year between two of the file's years is interpolated linearly."""
    path = write_lcoe_file(tmp_path, "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc")

    with boa_lcoe.open_lcoe_file(path, 0.85) as ds:
        lcoe = boa_lcoe.lcoe_for_year(ds, 2030).load()

    np.testing.assert_allclose(lcoe.values, 70.0)


@pytest.mark.parametrize(("year", "held"), [(2024, 60.0), (2040, 80.0)])
def test_lcoe_for_year_holds_the_nearest_year_outside_the_span_with_one_warning(tmp_path, caplog, year, held):
    """A year before or after the span takes the nearest year's values and warns once."""
    path = write_lcoe_file(tmp_path, "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc")
    caplog.set_level(logging.WARNING, logger="steelo.adapters.geospatial.boa_lcoe")

    with boa_lcoe.open_lcoe_file(path, 0.85) as ds:
        lcoe = boa_lcoe.lcoe_for_year(ds, year).load()

    np.testing.assert_allclose(lcoe.values, held)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "[BOA LCOE]" in r.getMessage()]
    assert len(warnings) == 1
    assert str(year) in warnings[0].getMessage()
