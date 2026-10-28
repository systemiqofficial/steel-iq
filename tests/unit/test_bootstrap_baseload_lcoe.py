"""Unit tests for the bootstrap check of the baseload LCOE file a run's power mix needs."""

import json
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


def write_minimal_data(data_dir: Path) -> Path:
    """
    A prepared data folder just large enough for bootstrap_simulation: one country, two years, no plants.

    Args:
        data_dir: The folder to write; its ``fixtures`` subfolder is created.

    Returns:
        ``data_dir``.

    Notes:
        Its iso3 grid lies on the LCOE fixture's lattice with every cell in DEU, so a baseload mix prices
        existing plants' hydrogen from the file's cells.
    """
    fixtures = data_dir / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "tech_switches_allowed.csv").write_text("Technology,BF-BOF\nBF-BOF,YES\n")
    for name in (
        "plants",
        "demand_centers",
        "suppliers",
        "plant_groups",
        "tariffs",
        "subsidies",
        "carbon_costs",
        "primary_feedstocks",
        "region_emissivity",
        "capex",
        "cost_of_capital",
    ):
        (fixtures / f"{name}.json").write_text('{"root": []}')
    (fixtures / "scenarios.json").write_text(
        json.dumps({"demand": {"BAU": "demand_centers.json"}, "scrap": {"BAU": "suppliers.json"}}),
    )
    (fixtures / "input_costs.json").write_text(
        json.dumps(
            {"root": [{"iso3": "DEU", "year": year, "costs": {"electricity": 0.05}} for year in (2025, 2026)]},
        ),
    )
    (fixtures / "legal_process_connectors.json").write_text("[]")
    (fixtures / "country_mappings.json").write_text(
        json.dumps(
            [
                {
                    "country": "Germany",
                    "iso2": "DE",
                    "iso3": "DEU",
                    "irena_name": "Germany",
                    "region_for_outputs": "Europe",
                    "ssp_region": "EUR",
                    "tiam-ucl_region": "Rest of World",
                },
            ],
        ),
    )
    (fixtures / "hydrogen_efficiency.json").write_text(
        json.dumps([{"year": 2025, "efficiency": 0.05}, {"year": 2026, "efficiency": 0.05}]),
    )
    (fixtures / "hydrogen_capex_opex.json").write_text(
        json.dumps([{"country_code": "DEU", "values": {"2025": 1.0, "2026": 1.0}}]),
    )
    (fixtures / "transport_emissions.json").write_text("[]")
    (fixtures / "biomass_availability.json").write_text("[]")
    (fixtures / "fallback_material_costs.json").write_text(
        json.dumps(
            [
                {
                    "iso3": "DEU",
                    "technology": "BF",
                    "metric": "Unit material cost",
                    "unit": "USD/t HM",
                    "costs_by_year": {"2025": 100.0},
                },
            ],
        ),
    )
    (data_dir / "railway_costs.json").write_text('{"root": []}')
    geo_dir = data_dir / "outputs" / "GEO"
    geo_dir.mkdir(parents=True)
    xr.DataArray(
        np.full((2, 2), "DEU", dtype=object),
        coords={"lat": [0.0, 0.25], "lon": [0.0, 0.25]},
        dims=("lat", "lon"),
        name="iso3",
    ).to_netcdf(geo_dir / "global_grid_with_iso3.nc")
    return data_dir


@pytest.mark.parametrize("named_run", [True, False])
def test_bootstrap_reads_the_lcoe_folder_the_run_names_else_the_prepared_data(tmp_path, named_run):
    """
    With boa_run_dir set, bootstrap reads that folder and not the prepared data's default one; without it, the
    default. Only the expected folder holds a file, so reading the other one would stop bootstrap.
    """
    data_dir = write_minimal_data(tmp_path / "data")
    default_dir = data_dir / "outputs" / "GEO" / "baseload_lcoe"
    run_dir = tmp_path / "boa" / "lcoe-only" / "my_run"
    expected = run_dir if named_run else default_dir
    write_lcoe_file(expected, CONTRACT)
    config = make_config(tmp_path, "85% baseload + 15% grid", boa_run_dir=run_dir if named_run else None)
    config.data_dir = data_dir

    runner = bootstrap.bootstrap_simulation(config)

    env = runner.bus.env
    assert env.geo_paths.baseload_lcoe_dir == expected
    assert env.baseload_lcoe_by_geo_key[Year(2025)] == {"DEU": pytest.approx(0.06)}
    inputs = env.hydrogen_price_inputs_by_year[Year(2025)]["DEU"]
    assert inputs["electricity"] == pytest.approx(0.15 * 0.05 + 0.85 * 0.06)
    existing_plants = (config.output_dir / "data" / "LCOH" / "lcoh_existing_plants.csv").read_text().splitlines()
    assert [line.split(",")[:4] for line in existing_plants[1:]] == [
        ["country", "DEU", "", "2025"],
        ["country", "DEU", "", "2026"],
    ]


def test_bootstrap_on_grid_only_builds_no_lcoe_series(tmp_path):
    """Grid only reads no LCOE file and prices existing plants' hydrogen at the grid price."""
    data_dir = write_minimal_data(tmp_path / "data")
    config = make_config(tmp_path, "Grid only")
    config.data_dir = data_dir

    runner = bootstrap.bootstrap_simulation(config)

    env = runner.bus.env
    assert env.baseload_lcoe_by_geo_key == {}
    inputs = env.hydrogen_price_inputs_by_year[Year(2025)]["DEU"]
    assert inputs["lcoe"] is None
    assert inputs["electricity"] == 0.05
    assert env.capped_hydrogen_costs_by_year[Year(2025)]["DEU"] == pytest.approx(50.0 * 0.05 + 1.0)


def test_bootstrap_on_a_baseload_mix_without_a_data_folder_stops(tmp_path, monkeypatch):
    """Without config.data_dir there is no LCOE file to price existing plants' hydrogen from, so bootstrap stops."""
    monkeypatch.chdir(tmp_path)
    write_minimal_data(tmp_path / "data")
    config = make_config(tmp_path, "85% baseload + 15% grid")

    with pytest.raises(ValueError, match="needs config.data_dir"):
        bootstrap.bootstrap_simulation(config)
