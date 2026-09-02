"""Unit tests for LCOE promotion: the combined-file build and the boa-run flag."""

import numpy as np
import pytest
import xarray as xr

from boa.cli.promote_lcoe import main as promote_main
from boa.cli.run_simulation import main_query
from boa.config.paths import PathConfig
from boa.model.lcoe_promotion import discover_scenarios, promote_lcoe, year_files

DEMAND = 1230.0
COVERAGE = 0.85
YEARS = (2025, 2030, 2035)


@pytest.fixture
def tmp_config(tmp_path):
    return PathConfig.from_root(tmp_path, run="cds-2024__test")


def _write_year(config, year, lcoe_offset=0.0, cost_keys=None, status=None, **attr_overrides):
    """One synthetic GLOBAL per-year NetCDF on a 2x3 grid."""
    lat, lon = np.array([0.0, 0.25]), np.array([10.0, 10.25, 10.5])
    lcoe = np.arange(6, dtype=np.float64).reshape(2, 3) * 10 + lcoe_offset
    lcoe[0, 0] = np.nan
    ds = xr.Dataset(
        coords={"lat": lat, "lon": lon},
        data_vars={
            "lcoe": (("lat", "lon"), lcoe),
            "solar_factor": (("lat", "lon"), np.ones((2, 3))),
            "cost_key": (("lat", "lon"), np.array(cost_keys or [["", "DEU", "DEU"], ["FRA", "FRA", "CHN:Sichuan"]])),
            "status": (("lat", "lon"), np.array(status or [[0, 1, 1], [1, 1, 1]], dtype=np.int8)),
        },
        attrs={
            "investment_year": year,
            "investment_horizon_years": 25,
            "baseload_demand_mw": DEMAND,
            "coverage_fraction": 0.85,
            "p_percentile": round((1 - COVERAGE) * 100),
            "n_samples": 1000,
            "random_seed": 42,
            "min_survivor_fraction": 0.01,
            "min_survivors": 10,
            "era5_weather_year": 2024,
            "era5_resolution_deg": 0.25,
            "region": "GLOBAL",
            **attr_overrides,
        },
    )
    path = config.optimal_sol_path(DEMAND, COVERAGE, "GLOBAL", year)
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path)
    return path


def _write_run(config, years=YEARS):
    for i, year in enumerate(years):
        _write_year(config, year, lcoe_offset=i)
    return config


# ---- discovery ---------------------------------------------------------------


def test_discover_scenarios_finds_demand_and_percentile(tmp_config):
    _write_run(tmp_config)
    assert discover_scenarios(tmp_config) == [(DEMAND, COVERAGE)]


def test_discover_scenarios_empty_run(tmp_config):
    assert discover_scenarios(tmp_config) == []


def test_year_files_are_year_ordered(tmp_config):
    _write_run(tmp_config, years=(2035, 2025, 2030))
    assert list(year_files(tmp_config, DEMAND, COVERAGE)) == [2025, 2030, 2035]


# ---- the combined file -------------------------------------------------------


def test_promoted_file_shape_and_dtypes(tmp_config):
    _write_run(tmp_config)
    with xr.open_dataset(promote_lcoe(tmp_config, DEMAND, COVERAGE)) as out:
        assert out["lcoe"].dims == ("year", "lat", "lon")
        assert out["lcoe"].shape == (len(YEARS), 2, 3)
        assert out["lcoe"].dtype == np.float32
        assert out["cost_key_id"].dtype == np.int16
        assert out["status"].dtype == np.int8
        assert out["cost_key_id"].dims == ("lat", "lon")
        assert out["lat"].dtype == np.float64 and out["lon"].dtype == np.float64
        assert [int(y) for y in out["year"].values] == list(YEARS)


def test_promoted_filename_carries_demand_and_span(tmp_config):
    _write_run(tmp_config)
    assert promote_lcoe(tmp_config, DEMAND, COVERAGE).name == "optimal_lcoe_1230MW_cov0.85_2025_2035.nc"


def test_promoted_lcoe_matches_source_years(tmp_config):
    _write_run(tmp_config)
    with xr.open_dataset(promote_lcoe(tmp_config, DEMAND, COVERAGE)) as out:
        for year in YEARS:
            with xr.open_dataset(tmp_config.optimal_sol_path(DEMAND, COVERAGE, "GLOBAL", year)) as source:
                np.testing.assert_allclose(
                    out["lcoe"].sel(year=year).values, source["lcoe"].values.astype(np.float32), rtol=1e-6
                )
                assert np.array_equal(np.isnan(out["lcoe"].sel(year=year).values), np.isnan(source["lcoe"].values))


def test_cost_key_legend_round_trips(tmp_config):
    _write_run(tmp_config)
    with xr.open_dataset(promote_lcoe(tmp_config, DEMAND, COVERAGE)) as out:
        legend = np.array(out.attrs["cost_key_legend"].split(","), dtype=object)
        decoded = legend[out["cost_key_id"].values]
    with xr.open_dataset(tmp_config.optimal_sol_path(DEMAND, COVERAGE, "GLOBAL", YEARS[0])) as source:
        assert (decoded == np.asarray(source["cost_key"].values)).all()


def test_status_legend_is_semicolon_separated(tmp_config):
    # One status label contains a comma, so the legend cannot use commas as cost keys do.
    _write_run(tmp_config)
    with xr.open_dataset(promote_lcoe(tmp_config, DEMAND, COVERAGE)) as out:
        entries = dict(entry.split("=", 1) for entry in out.attrs["status_legend"].split(";"))
    assert entries["1"] == "Optimum found"


def test_provenance_attrs_identify_the_run(tmp_config):
    _write_run(tmp_config)
    with xr.open_dataset(promote_lcoe(tmp_config, DEMAND, COVERAGE)) as out:
        assert out.attrs["run"] == "cds-2024__test"
        assert out.attrs["baseload_demand_mw"] == DEMAND
        assert out.attrs["p_percentile"] == round((1 - COVERAGE) * 100)
        assert out.attrs["coverage_fraction"] == 0.85
        assert out.attrs["n_samples"] == 1000
        assert out.attrs["era5_weather_year"] == 2024
        assert list(out.attrs["promoted_years"]) == list(YEARS)
        assert out.attrs["promoted_at"]


def test_promotion_is_much_smaller_than_its_sources(tmp_config):
    _write_run(tmp_config)
    sources = sum(f.stat().st_size for f in year_files(tmp_config, DEMAND, COVERAGE).values())
    assert promote_lcoe(tmp_config, DEMAND, COVERAGE).stat().st_size < sources


# ---- the year-invariance contract --------------------------------------------


def test_varying_cost_key_is_rejected(tmp_config):
    _write_run(tmp_config)
    _write_year(tmp_config, YEARS[1], cost_keys=[["", "DEU", "DEU"], ["FRA", "FRA", "ESP"]])
    with pytest.raises(ValueError, match="cost_key differs"):
        promote_lcoe(tmp_config, DEMAND, COVERAGE)


def test_varying_status_is_rejected(tmp_config):
    _write_run(tmp_config)
    _write_year(tmp_config, YEARS[1], status=[[0, 1, 1], [1, 1, 4]])
    with pytest.raises(ValueError, match="status differs"):
        promote_lcoe(tmp_config, DEMAND, COVERAGE)


def test_varying_scenario_settings_are_rejected(tmp_config):
    _write_run(tmp_config)
    _write_year(tmp_config, YEARS[1], n_samples=2000)
    with pytest.raises(ValueError, match="different settings"):
        promote_lcoe(tmp_config, DEMAND, COVERAGE)


def test_cost_key_containing_the_separator_is_rejected(tmp_config):
    _write_run(tmp_config, years=(2025,))
    _write_year(tmp_config, 2025, cost_keys=[["", "DEU", "DEU"], ["FRA", "FRA", "CHN,Sichuan"]])
    with pytest.raises(ValueError, match="comma"):
        promote_lcoe(tmp_config, DEMAND, COVERAGE)


def test_missing_run_names_the_command_to_run(tmp_config):
    with pytest.raises(FileNotFoundError, match="boa-run"):
        promote_lcoe(tmp_config, DEMAND, COVERAGE)


# ---- CLI surfaces ------------------------------------------------------------


def test_promote_cli_promotes_every_scenario(tmp_config, monkeypatch):
    _write_run(tmp_config)
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_config.root))
    assert promote_main(["--run", tmp_config.run]) == 0
    assert (tmp_config.lcoe_promotion_dir / "optimal_lcoe_1230MW_cov0.85_2025_2035.nc").exists()


def test_promote_cli_reports_an_empty_run(tmp_config, monkeypatch):
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_config.root))
    assert promote_main(["--run", tmp_config.run]) == 1


def test_boa_run_query_promote_lcoe_flag(tmp_config, monkeypatch):
    """--promote-lcoe promotes the scenario the query just produced."""
    _write_run(tmp_config)
    monkeypatch.setenv("BOA_DATA_ROOT", str(tmp_config.root))
    monkeypatch.setattr("boa.cli.run_simulation.preflight", lambda *a, **k: 2024)
    monkeypatch.setattr("boa.cli.run_simulation.run_manifest.record_invocation", lambda *a, **k: {})
    monkeypatch.setattr("boa.cli.run_simulation.query_all_years", lambda *a, **k: None)

    argv = ["--demand", str(DEMAND), "--coverage", "0.85", "--run", tmp_config.run, "--promote-lcoe"]
    assert main_query(argv) == 0
    assert (tmp_config.lcoe_promotion_dir / "optimal_lcoe_1230MW_cov0.85_2025_2035.nc").exists()
