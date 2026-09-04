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
    assert cfg.input_data_path == tmp_path / "costs" / "xlsx-rev3" / "boa_cost_data.xlsx"
    assert cfg.cost_cache_dir == tmp_path / "costs" / "xlsx-rev3" / "cache_costs"
    assert cfg.run_manifest_path == tmp_path / "runs" / "cds-2024__xlsx-rev3" / "run.json"
    assert cfg.optimal_sol_path(1230, 0.95, "GLOBAL", 2030) == (
        tmp_path
        / "runs"
        / "cds-2024__xlsx-rev3"
        / "outputs"
        / "1230MW"
        / "cov0.95"
        / "nc"
        / "GLOBAL"
        / "optimal_sol_1230MW_cov0.95_GLOBAL_2030.nc"
    )


def test_explicit_run_name_and_defaults(tmp_path):
    cfg = PathConfig.from_root(tmp_path, run="baseline")
    assert cfg.input_set == cfg.cost_set == "default"
    assert cfg.outputs_dir == tmp_path / "runs" / "baseline" / "outputs"


def test_from_auto_detect_uses_env_root(monkeypatch, tmp_path):
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path))
    assert PathConfig.from_auto_detect(input_set="x").inputs_dir == tmp_path / "inputs" / "x"


def test_lulc_dir_is_input_set_independent(tmp_path):
    """
    The 2.35 GB land-cover raster is provider data, identical for every input set. Under
    `inputs/<set>/` it would have to be re-fetched per set, which is the likeliest reason
    `lulc_dir` was declared and never wired up.
    """
    a = PathConfig.from_root(tmp_path, input_set="cds-2024")
    b = PathConfig.from_root(tmp_path, input_set="cds-2024-lulc+excl")

    assert a.lulc_dir == tmp_path / "data" / "lulc"
    assert a.lulc_dir == b.lulc_dir
    assert a.zarr_dir != b.zarr_dir, "the derived stores must still separate by input set"


def test_frontier_cache_is_shared_across_availability_layer_sets(tmp_path):
    """
    The point of D4. A frontier store holds no land-availability assumption, so two layer
    sets on the same weather must land in one cache: revising the LULC table then rebuilds
    the ceiling and the Grid 2 sidecars but not the physics, and an A/B of two ceilings
    reads the same bytes rather than two independently rebuilt copies.
    """
    plain = PathConfig.from_root(tmp_path, input_set="cds-2024")
    layered = PathConfig.from_root(tmp_path, input_set="cds-2024-lulc+excl")

    assert plain.frontier_cache_dir(2024) == layered.frontier_cache_dir(2024)
    assert plain.frontier_cache_dir(2024) == tmp_path / "inputs" / "cds-2024" / "cache_frontiers"
    # The ceiling stores themselves stay separate -- they are what the layer set changes.
    assert plain.zarr_dir != layered.zarr_dir


def test_frontier_cache_separates_weather_years(tmp_path):
    cfg = PathConfig.from_root(tmp_path, input_set="cds-2024")
    assert cfg.frontier_cache_dir(2024) != cfg.frontier_cache_dir(2023)


def test_frontier_cache_dir_ignores_the_input_set_name(tmp_path):
    """
    The weather stem is composed from the year, never split out of `input_set`. That name is
    user-supplied through `--inputs`, so a tag containing a hyphen would mis-key anything
    derived from a split -- silently, and only for the sets unlucky enough to be named that
    way.
    """
    for name in ("cds-2024", "my-experiment", "cds-2024-lulc+excl", "a-b-c-d"):
        cfg = PathConfig.from_root(tmp_path, input_set=name)
        assert cfg.frontier_cache_dir(2024) == tmp_path / "inputs" / "cds-2024" / "cache_frontiers"


def test_weather_set_name_is_composed_not_parsed():
    from boa.config.paths import weather_set_name

    assert weather_set_name(2024) == "cds-2024"
    assert weather_set_name("2024") == "cds-2024"  # type: ignore[arg-type]


def test_a_missing_max_capacity_store_raises_rather_than_defaulting(tmp_path, monkeypatch):
    """
    The counterweight to sharing one frontier cache across layer sets. The store carries no
    ceiling, so the query reads it separately -- which makes that read the only thing left
    keeping a stale or absent ceiling out. A fallback, a default or a "continue anyway"
    branch here would lose the guarantee silently, with no error and no wrong-looking output.
    """
    import pytest

    from boa.inputs.profiles import open_regional_dataset

    from boa.inputs.profiles import dataset_path, profile_store_stem

    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PROFILE_DATA_SOURCE", "local_zarr")
    cfg = PathConfig.from_auto_detect(input_set="cds-2024")
    cfg.zarr_dir.mkdir(parents=True, exist_ok=True)
    # A real-looking profile store, so the weather year resolves and the failure below is
    # about the absent ceiling rather than an unbuilt input set.
    (cfg.zarr_dir / (profile_store_stem("EUROPE", 2024) + ".zarr")).mkdir()
    missing = dataset_path("max_cap", "EUROPE", cfg, 2024)
    assert not missing.exists()

    with pytest.raises(FileNotFoundError, match=str(missing.name)):
        open_regional_dataset("max_cap", "EUROPE", cfg)
