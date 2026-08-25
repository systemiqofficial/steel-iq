from pathlib import Path

from boa.config.paths import PathConfig, default_root


def test_default_root_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("BOA_DATA_ROOT", raising=False)
    monkeypatch.delenv("STEELO_HOME", raising=False)
    assert default_root() == Path.home() / ".steelo" / "boa"

    monkeypatch.setenv("STEELO_HOME", str(tmp_path / "home"))
    assert default_root() == tmp_path / "home" / "boa"

    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path / "explicit"))
    assert default_root() == tmp_path / "explicit"


def test_layout_splits_by_provenance(tmp_path):
    cfg = PathConfig.from_root(tmp_path, input_set="cds-2024", cost_set="xlsx-rev3")

    assert cfg.run == "cds-2024__xlsx-rev3"
    assert cfg.iso3_grid_path == tmp_path / "data" / "iso3_grid.nc"
    assert cfg.zarr_dir == tmp_path / "inputs" / "cds-2024" / "cds-zarr"
    assert cfg.cds_dir == tmp_path / "data" / "cds"
    assert cfg.cds_staging_dir == tmp_path / "inputs" / "cds-2024" / "staging"
    assert cfg.design_cache_dir == tmp_path / "inputs" / "cds-2024" / "cache_designs"
    assert cfg.input_data_path == tmp_path / "costs" / "xlsx-rev3" / "boa_cost_data.xlsx"
    assert cfg.cost_cache_dir == tmp_path / "costs" / "xlsx-rev3" / "cache_costs"
    assert cfg.run_manifest_path == tmp_path / "runs" / "cds-2024__xlsx-rev3" / "run.json"
    assert cfg.optimal_sol_path(1230, 5, "GLOBAL", 2030) == (
        tmp_path
        / "runs"
        / "cds-2024__xlsx-rev3"
        / "outputs"
        / "1230MW"
        / "p5"
        / "nc"
        / "GLOBAL"
        / "optimal_sol_1230MW_p5_GLOBAL_2030.nc"
    )


def test_explicit_run_name_and_defaults(tmp_path):
    cfg = PathConfig.from_root(tmp_path, run="baseline")
    assert cfg.input_set == cfg.cost_set == "default"
    assert cfg.outputs_dir == tmp_path / "runs" / "baseline" / "outputs"


def test_from_auto_detect_uses_env_root(monkeypatch, tmp_path):
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path))
    assert PathConfig.from_auto_detect(input_set="x").inputs_dir == tmp_path / "inputs" / "x"
