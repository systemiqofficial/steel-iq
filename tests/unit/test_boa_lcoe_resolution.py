"""Unit tests for resolving a promoted BOA LCOE file from run_simulation's --boa-* flags."""

import io

import pytest
from rich.console import Console

from boa.config.paths import PathConfig
from steelo.entrypoints.cli import resolve_boa_lcoe_file
from steelo.simulation import GeoConfig

RUN = "cds-2024__china_test"

# Real filenames come from PathConfig.promoted_lcoe_filename -- deriving them here too
# means a naming-convention change breaks this file loudly instead of leaving a fixture
# that silently stops matching what resolve_boa_lcoe_file actually globs for.
_PATHS = PathConfig.from_root("unused", run=RUN)


def _promoted_filename(load_density: float, coverage: float) -> str:
    return _PATHS.promoted_lcoe_filename(2024, load_density, coverage, 2025, 2060)


@pytest.fixture
def console_output():
    return Console(file=io.StringIO(), width=200)


@pytest.fixture
def promotion_root(tmp_path, monkeypatch):
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path))
    root = tmp_path / "lcoe-for-steel-iq"
    root.mkdir()
    return root


@pytest.fixture
def power_mix(monkeypatch):
    """Set the configured power mix, the only thing that decides which percentile is read."""

    def _set(mix):
        monkeypatch.setattr(GeoConfig, "included_power_mix", mix)

    return _set


def _write(promotion_root, run, filename):
    run_dir = promotion_root / run
    run_dir.mkdir(exist_ok=True)
    path = run_dir / filename
    path.touch()
    return path


def test_no_boa_run_selects_the_bundled_per_year_files(promotion_root, console_output):
    """Without --boa-run there is no local BOA run to read; None keeps the legacy path."""
    _write(promotion_root, RUN, _promoted_filename(1230.0, 0.85))

    assert resolve_boa_lcoe_file(console_output, None, None) is None


def test_boa_load_density_without_a_run_is_rejected(promotion_root, console_output):
    with pytest.raises(SystemExit):
        resolve_boa_lcoe_file(console_output, None, 1230.0)

    assert "--boa-run" in console_output.file.getvalue()


def test_percentile_follows_the_configured_power_mix(promotion_root, console_output, power_mix):
    power_mix("85% baseload + 15% grid")
    expected = _write(promotion_root, RUN, _promoted_filename(1230.0, 0.85))
    _write(promotion_root, RUN, _promoted_filename(1230.0, 0.95))

    assert resolve_boa_lcoe_file(console_output, RUN, None) == expected


def test_a_changed_power_mix_reads_a_different_percentile(promotion_root, console_output, power_mix):
    power_mix("95% baseload + 5% grid")
    _write(promotion_root, RUN, _promoted_filename(1230.0, 0.85))
    expected = _write(promotion_root, RUN, _promoted_filename(1230.0, 0.95))

    assert resolve_boa_lcoe_file(console_output, RUN, None) == expected


@pytest.mark.parametrize("mix", ["Grid only", "Not included"])
def test_a_power_mix_without_baseload_rejects_a_boa_run(promotion_root, console_output, power_mix, mix):
    """No baseload component means the file would never be read; say so instead of resolving one."""
    power_mix(mix)
    _write(promotion_root, RUN, _promoted_filename(1230.0, 0.85))

    with pytest.raises(SystemExit):
        resolve_boa_lcoe_file(console_output, RUN, None)

    assert "no baseload component" in console_output.file.getvalue()


def test_unknown_run_lists_what_is_available(promotion_root, console_output, power_mix):
    power_mix("85% baseload + 15% grid")
    _write(promotion_root, RUN, _promoted_filename(1230.0, 0.85))

    with pytest.raises(SystemExit):
        resolve_boa_lcoe_file(console_output, "cds-2024__typo", None)

    printed = console_output.file.getvalue()
    assert "cds-2024__typo" in printed
    assert RUN in printed and _promoted_filename(1230.0, 0.85) in printed


def test_missing_percentile_names_the_power_mix_that_requires_it(promotion_root, console_output, power_mix):
    power_mix("95% baseload + 5% grid")
    _write(promotion_root, RUN, _promoted_filename(1230.0, 0.85))

    with pytest.raises(SystemExit):
        resolve_boa_lcoe_file(console_output, RUN, None)

    printed = console_output.file.getvalue()
    assert "coverage 0.95" in printed and "95% baseload + 5% grid" in printed
    assert _promoted_filename(1230.0, 0.85) in printed


def test_no_promoted_runs_names_the_promote_command(promotion_root, console_output, power_mix):
    power_mix("85% baseload + 15% grid")

    with pytest.raises(SystemExit):
        resolve_boa_lcoe_file(console_output, RUN, None)

    assert "boa-promote-lcoe" in console_output.file.getvalue()


def test_several_densities_require_boa_load_density(promotion_root, console_output, power_mix):
    power_mix("85% baseload + 15% grid")
    _write(promotion_root, RUN, _promoted_filename(1230.0, 0.85))
    _write(promotion_root, RUN, _promoted_filename(1000.0, 0.85))

    with pytest.raises(SystemExit):
        resolve_boa_lcoe_file(console_output, RUN, None)

    assert "--boa-load-density" in console_output.file.getvalue()


def test_boa_load_density_disambiguates(promotion_root, console_output, power_mix):
    power_mix("85% baseload + 15% grid")
    _write(promotion_root, RUN, _promoted_filename(1230.0, 0.85))
    expected = _write(promotion_root, RUN, _promoted_filename(1000.0, 0.85))

    assert resolve_boa_lcoe_file(console_output, RUN, 1000.0) == expected
