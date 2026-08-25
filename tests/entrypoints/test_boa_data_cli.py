import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from steelo.entrypoints import boa_data_cli

TECHS = ["Solar PV", "Onshore wind", "Battery"]


def _write_master(path: Path, europe_solar_capex: float = 700.0) -> None:
    """Minimal master-excel variant: the four boa sheets, master-style column names."""
    country = pd.DataFrame(
        {
            "Country": ["Germany", "Australia", "France"],
            "ISO 3-letter code": ["DEU", "AUS", "FRA"],
            "irena_name": ["Germany", "Australia", "France"],
            "irena_region": ["Europe", "Oceania", "Europe"],
        }
    )
    # Tasmania duplicates AUS at the same value, like the master sheet's territory rows.
    # It sits mid-sheet so dropping it leaves an index gap (regression: upsert no-op detection).
    cost_of_capital = pd.DataFrame(
        {
            "Country": ["Germany", "Germany", "Australia", "Tasmania", "France"],
            "ISO-3 Code": ["DEU", "DEU", "AUS", "AUS", "FRA"],
            "Day of update": ["2026-01-01"] * 5,
            "Tech": ["Steel", "Renewables", "Renewables", "Renewables", "Renewables"],
            "Cost of capital": [0.09, 0.05, 0.07, 0.07, 0.04],
        }
    )
    capex_rows = []
    for region in ("Europe", "Oceania"):
        for tech in TECHS:
            value = europe_solar_capex if (region, tech) == ("Europe", "Solar PV") else 500.0
            capex_rows.append([region, tech, "USD/kW", value, value * 0.9])
    capex = pd.DataFrame(capex_rows, columns=["irena_region", "Technology", "Unit", 2024, 2025])
    opex = pd.DataFrame(
        {
            "Region": ["World"] * 3,
            "Technology": TECHS,
            "Unit": ["%"] * 3,
            "Opex": [0.02, 0.03, 0.025],
        }
    )
    with pd.ExcelWriter(path) as writer:
        country.to_excel(writer, sheet_name="Country mapping", index=False)
        cost_of_capital.to_excel(writer, sheet_name="Cost of capital", index=False)
        capex.to_excel(writer, sheet_name="RES CAPEX projections", index=False)
        opex.to_excel(writer, sheet_name="RES OPEX", index=False)


@pytest.fixture
def boa_root(tmp_path, monkeypatch):
    root = tmp_path / "boa"
    monkeypatch.setenv("BOA_DATA_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def geo_package(tmp_path, monkeypatch):
    """Fake boa-data package cache + stubbed iso3 grid build, so no network or slow sjoin in tests."""
    package_dir = tmp_path / "boa-data-cache"
    for item in boa_data_cli.GEO_DATA_ITEMS:
        source = package_dir / item
        if item.endswith(".nc"):
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"fake-nc")
        else:
            source.mkdir(parents=True, exist_ok=True)
            (source / f"{item}.shp").write_bytes(b"fake-shp")
    downloads: list[str] = []

    class FakeDataManager:
        def download_package(self, name, version=None, force=False):
            downloads.append(name)

        def get_package_path(self, name, version=None):
            return package_dir

    monkeypatch.setattr(boa_data_cli, "DataManager", FakeDataManager)

    import boa.geo.iso3_grid_builder as grid_builder

    def fake_build(output_path, *, shapefile_path, resolution=0.25, force=False, on_stage=None):
        assert shapefile_path.exists()
        if on_stage is not None:
            for stage in range(grid_builder.BUILD_STAGE_COUNT):
                on_stage(f"stage {stage}")
        output_path.write_bytes(b"fake-grid")
        return output_path

    monkeypatch.setattr(grid_builder, "build_iso3_grid_from_shapefile", fake_build)
    monkeypatch.setattr(
        grid_builder,
        "iso3_grid_is_current",
        lambda grid_path, shapefile_path: grid_path.exists() and grid_path.read_bytes() == b"fake-grid",
    )
    return SimpleNamespace(package_dir=package_dir, downloads=downloads)


def _run(monkeypatch, master: Path, scenario: str = "test") -> str:
    monkeypatch.setattr(sys, "argv", ["boa-data-prepare", "--input-file", str(master), "--scenario", scenario])
    return boa_data_cli.boa_data_prepare()


def test_creates_fixture_with_renamed_columns(tmp_path, monkeypatch, boa_root):
    master = tmp_path / "master.xlsx"
    _write_master(master)

    result = _run(monkeypatch, master)

    assert result == "Created scenario 'test'"
    fixture = boa_root / "costs" / "test" / "boa_cost_data.xlsx"
    assert fixture.exists()
    assert list(pd.read_excel(fixture, sheet_name="Country mapping").columns)[:2] == ["Country", "Code"]
    cost_of_capital = pd.read_excel(fixture, sheet_name="Cost of capital")
    assert "Code" in cost_of_capital.columns
    assert list(cost_of_capital["Code"]) == ["DEU", "AUS", "FRA"]  # same-value territory duplicate collapsed
    assert set(cost_of_capital["Tech"]) == {"Renewables"}  # steel/hydrogen tech rows dropped
    provenance = json.loads((boa_root / "costs" / "test" / "source.json").read_text())
    assert provenance["scenario"] == "test"
    assert provenance["source_workbook"] == str(master.resolve())
    assert set(provenance) == {"scenario", "prepared_at", "source_workbook", "source_sha256"}


def test_rerun_with_unchanged_data_is_noop_and_keeps_cache(tmp_path, monkeypatch, boa_root):
    master = tmp_path / "master.xlsx"
    _write_master(master)
    _run(monkeypatch, master)

    fixture = boa_root / "costs" / "test" / "boa_cost_data.xlsx"
    cache_marker = boa_root / "costs" / "test" / "cache_costs" / "marker.nc"
    cache_marker.touch()
    mtime = fixture.stat().st_mtime_ns

    result = _run(monkeypatch, master)

    assert result == "Already up to date"
    assert fixture.stat().st_mtime_ns == mtime
    assert cache_marker.exists()


def test_changed_data_replaces_fixture_and_rebuilds_cache(tmp_path, monkeypatch, boa_root):
    master = tmp_path / "master.xlsx"
    _write_master(master)
    _run(monkeypatch, master)

    cache_dir = boa_root / "costs" / "test" / "cache_costs"
    stale_marker = cache_dir / "marker.nc"
    stale_marker.touch()
    _write_master(master, europe_solar_capex=350.0)

    result = _run(monkeypatch, master)

    assert result == "Updated scenario 'test'"
    assert not stale_marker.exists()  # stale cache cleared before the rebuild
    assert sorted(p.name for p in cache_dir.iterdir()) == [
        "cost_of_renewables_2024_investment_year.nc",
        "cost_of_renewables_2025_investment_year.nc",
    ]
    capex = pd.read_excel(boa_root / "costs" / "test" / "boa_cost_data.xlsx", sheet_name="RES CAPEX projections")
    europe_solar = capex[(capex["irena_region"] == "Europe") & (capex["Technology"] == "Solar PV")]
    assert europe_solar[2024].item() == 350.0


def test_builds_cost_cache_for_all_sheet_years(tmp_path, monkeypatch, boa_root):
    master = tmp_path / "master.xlsx"
    _write_master(master)

    result = _run(monkeypatch, master)

    assert result == "Created scenario 'test'"
    cache_dir = boa_root / "costs" / "test" / "cache_costs"
    assert sorted(p.name for p in cache_dir.iterdir()) == [
        "cost_of_renewables_2024_investment_year.nc",
        "cost_of_renewables_2025_investment_year.nc",
    ]


def test_year_flags_narrow_cache(tmp_path, monkeypatch, boa_root):
    master = tmp_path / "master.xlsx"
    _write_master(master)

    monkeypatch.setattr(
        sys, "argv", ["boa-data-prepare", "--input-file", str(master), "--scenario", "test", "--year_start", "2025"]
    )
    boa_data_cli.boa_data_prepare()

    cache_dir = boa_root / "costs" / "test" / "cache_costs"
    assert sorted(p.name for p in cache_dir.iterdir()) == ["cost_of_renewables_2025_investment_year.nc"]


def test_geo_data_installed_and_grid_built(tmp_path, monkeypatch, boa_root, geo_package):
    master = tmp_path / "master.xlsx"
    _write_master(master)

    _run(monkeypatch, master)

    assert geo_package.downloads == ["boa-data"]
    data_dir = boa_root / "data"
    assert (data_dir / "ne_50m_admin_0_map_subunits" / "ne_50m_admin_0_map_subunits.shp").exists()
    assert (data_dir / "ne_10m_admin_1_states_provinces" / "ne_10m_admin_1_states_provinces.shp").exists()
    assert (data_dir / "lsm_025_deg.nc").exists()
    assert (data_dir / "iso3_grid.nc").read_bytes() == b"fake-grid"


def test_geo_data_skips_download_and_build_when_present(tmp_path, monkeypatch, boa_root, geo_package):
    master = tmp_path / "master.xlsx"
    _write_master(master)
    _run(monkeypatch, master)
    geo_package.downloads.clear()
    grid = boa_root / "data" / "iso3_grid.nc"
    grid_mtime = grid.stat().st_mtime_ns

    _run(monkeypatch, master)

    assert geo_package.downloads == []
    assert grid.stat().st_mtime_ns == grid_mtime


def test_stale_iso3_grid_is_rebuilt(tmp_path, monkeypatch, boa_root, geo_package):
    master = tmp_path / "master.xlsx"
    _write_master(master)
    _run(monkeypatch, master)
    grid = boa_root / "data" / "iso3_grid.nc"
    grid.write_bytes(b"grid-from-old-ne-shapefile")

    _run(monkeypatch, master)

    assert grid.read_bytes() == b"fake-grid"


def test_missing_sheet_fails(tmp_path, monkeypatch, boa_root, capsys):
    master = tmp_path / "master.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(master, sheet_name="Country mapping", index=False)

    monkeypatch.setattr(sys, "argv", ["boa-data-prepare", "--input-file", str(master)])
    with pytest.raises(SystemExit):
        boa_data_cli.boa_data_prepare()
    assert "missing sheet(s)" in capsys.readouterr().out
