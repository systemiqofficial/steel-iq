"""Unit tests for the boa_cds pipeline (no real data; CI-safe).

Synthetic CDS-style NetCDFs exercise the descending-latitude / 0..360-longitude
conversion, the geometry-only max-capacity build, and the staging -> install
promotion. Ported from upstream BOA minus the download stage (not vendored) and
with geometry-only as the installable default.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from boa.cds import install as cds_install
from boa.cds import max_capacity as cds_max_capacity
from boa.cds.convert import (
    build_global_store,
    cds_month_files,
    convert_region,
    global_store_path,
    load_cds_tech,
)
from boa.cds.spec import CDS_VARS, cf_extract_dir_name, cf_zip_name
from boa.cli import run_cds
from boa.config.constants import EARTH_RADIUS_KM
from boa.config.paths import PathConfig
from boa.config.settings import (
    CAPACITY_DENSITY_MW_PER_KM2,
    ERA5_DATA_RESOLUTION,
    ERA5_DATA_YEAR,
    REGION_COORDS,
)
from boa.inputs.profiles import _filename_stem
from boa.store_schema import max_cap_store_stem, profile_store_stem


@pytest.fixture
def tmp_config(tmp_path):
    return PathConfig.from_root(tmp_path)


# ---- naming ------------------------------------------------------------------


def test_zip_and_extract_dir_names_agree():
    assert cf_zip_name("solar", 2024) == "cds_solar_cf_ic6hh135_0_25_degree_2024.zip"
    assert cf_zip_name("wind", 2024, "_m01") == "cds_wind_onshore_cf_ic6hh135_0_25_degree_2024_m01.zip"
    assert cf_extract_dir_name("solar", 2024) == "cds_solar_cf_ic6hh135_0_25_degree_2024"


def test_store_stem_parity_with_profiles():
    assert _filename_stem("profile", "DE") == profile_store_stem("DE", ERA5_DATA_YEAR)
    assert _filename_stem("max_cap", "EU") == max_cap_store_stem("EU", ERA5_DATA_YEAR)


# ---- CDS -> profile conversion ---------------------------------------------

# Synthetic region: 4x4 cells straddling negative longitudes so the 0..360
# conversion is exercised.
GRID_Y = np.arange(9.75, 10.625, 0.25)  # [9.75, 10.0, 10.25, 10.5]
GRID_X = np.arange(-30.5, -29.625, 0.25)  # [-30.5, -30.25, -30.0, -29.75]


def _write_cds_months(folder: Path, tech: str, year: int, months: range = range(1, 13)) -> None:
    """Monthly global-style files: latitude descending, longitude 0..360."""
    folder.mkdir(parents=True, exist_ok=True)
    lats_desc = np.arange(10.75, 9.4, -0.25)
    lons_0360 = np.arange(329.25, 330.6, 0.25)
    for month in months:
        time = pd.date_range(f"{year}-{month:02d}-01", periods=2, freq="h")
        values = np.full((len(time), len(lats_desc), len(lons_0360)), month / 100.0)
        da = xr.DataArray(
            values,
            coords={"time": time, "latitude": lats_desc, "longitude": lons_0360},
            dims=("time", "latitude", "longitude"),
            name=CDS_VARS[tech],
        )
        da.to_dataset().to_netcdf(folder / f"{tech}_{year}{month:02d}.nc")


def test_load_cds_tech_converts_conventions(tmp_path):
    _write_cds_months(tmp_path, "solar", 2024)
    files = sorted(tmp_path.glob("*.nc"))
    out = load_cds_tech(files, "solar", GRID_Y, GRID_X)

    assert out.dims == ("time", "y", "x")
    assert out.dtype == np.float32
    np.testing.assert_allclose(out.y.values, GRID_Y)
    np.testing.assert_allclose(out.x.values, GRID_X)  # ascending, -180..180
    assert out.get_index("time").is_monotonic_increasing
    assert out.sizes["time"] == 24
    # January values first, December last (time concatenation in month order).
    np.testing.assert_allclose(out.isel(time=0).values, 0.01)
    np.testing.assert_allclose(out.isel(time=-1).values, 0.12)


def test_cds_month_files_requires_twelve(tmp_path):
    folder = tmp_path / cf_extract_dir_name("solar", 2024)
    _write_cds_months(folder, "solar", 2024, months=range(1, 12))  # 11 files
    with pytest.raises(FileNotFoundError, match="expected 12"):
        cds_month_files(tmp_path, "solar", 2024)


def test_cds_month_files_missing_folder(tmp_path):
    with pytest.raises(FileNotFoundError, match="download"):
        cds_month_files(tmp_path, "wind", 2024)


def test_convert_region_writes_model_ready_store(tmp_path, tmp_config, monkeypatch):
    monkeypatch.setitem(REGION_COORDS, "TEST", [10.5, -30.5, 9.75, -29.75])
    cds_dir = tmp_path / "cds"
    for tech in ("solar", "wind"):
        _write_cds_months(cds_dir / cf_extract_dir_name(tech, 2024), tech, 2024)
    out_dir = tmp_path / "staging"
    out_dir.mkdir()

    convert_region("TEST", 2024, ["solar", "wind"], cds_dir, out_dir, tmp_config)

    store = out_dir / (profile_store_stem("TEST", 2024) + ".zarr")
    ds = xr.open_zarr(store, consolidated=True)
    assert set(ds.data_vars) == {"solar", "wind"}
    assert tuple(ds["solar"].dims) == ("time", "y", "x")
    assert ds["solar"].dtype == np.float32
    assert ds.attrs["units"] == "p.u."
    assert ds.attrs["source_dataset"] == "sis-energy-global-reanalysis"
    assert ds.attrs["converted_by"] == "boa.cds.convert"
    ds.close()


def test_global_store_path_parity_with_direct(tmp_path, tmp_config, monkeypatch):
    """Cutting a region from the global intermediate must equal the direct path bit-for-bit."""
    monkeypatch.setitem(REGION_COORDS, "TEST", [10.5, -30.5, 9.75, -29.75])
    # Deliberately misaligned with PROFILE_CHUNKS so the write must shed the
    # intermediate's chunk encoding (regression guard).
    monkeypatch.setattr("boa.cds.convert.GLOBAL_CHUNKS", {"time": 12, "y": 3, "x": 3})
    cds_dir = tmp_path / "cds"
    for tech in ("solar", "wind"):
        _write_cds_months(cds_dir / cf_extract_dir_name(tech, 2024), tech, 2024)

    direct_dir = tmp_path / "direct"
    direct_dir.mkdir()
    convert_region("TEST", 2024, ["solar", "wind"], cds_dir, direct_dir, tmp_config)

    gs = build_global_store(cds_dir, 2024, global_store_path(cds_dir, 2024))
    via_dir = tmp_path / "via_global"
    via_dir.mkdir()
    convert_region("TEST", 2024, ["solar", "wind"], cds_dir, via_dir, tmp_config, global_store=gs)

    name = profile_store_stem("TEST", 2024) + ".zarr"
    direct = xr.open_zarr(direct_dir / name, consolidated=True)
    via = xr.open_zarr(via_dir / name, consolidated=True)
    for var in ("solar", "wind"):
        xr.testing.assert_equal(direct[var], via[var])
        assert direct[var].dtype == via[var].dtype == np.float32
    # Second build call reuses the existing store rather than rebuilding.
    assert build_global_store(cds_dir, 2024, gs) == gs
    direct.close()
    via.close()


# ---- max-capacity -----------------------------------------------------------


def test_pixel_area_spot_values():
    y_size = ERA5_DATA_RESOLUTION * 2 * np.pi * EARTH_RADIUS_KM / 360
    np.testing.assert_allclose(cds_max_capacity.pixel_area(np.array([0.0])), y_size**2)
    np.testing.assert_allclose(cds_max_capacity.pixel_area(np.array([60.0])), y_size**2 * 0.5, rtol=1e-12)


def test_geometry_default_is_pure_geometry_and_installable(tmp_path, tmp_config):
    """Default build has no land-use term, writes plain names, and passes install validation."""
    out = cds_max_capacity.build_region("EU", tmp_path, tmp_config)
    ds = xr.open_dataset(out)
    expected = cds_max_capacity.pixel_area(ds.y.values)[:, None] * CAPACITY_DENSITY_MW_PER_KM2["pv"]
    np.testing.assert_array_equal(ds["pv"].values, np.broadcast_to(expected, ds["pv"].shape))
    assert ds.attrs["lulc_source"] == "none"
    assert out.name == max_cap_store_stem("EU", ERA5_DATA_YEAR) + ".nc"
    ds.close()

    zarr_twin = tmp_path / (max_cap_store_stem("EU", ERA5_DATA_YEAR) + ".zarr")
    assert zarr_twin.exists()
    cds_install.validate_store(zarr_twin, "max-cap")  # must not raise


def test_density_overrides_propagate(tmp_path, tmp_config):
    out = cds_max_capacity.build_region("EU", tmp_path, tmp_config, pv_density=100.0, wind_density=10.42)
    ds = xr.open_dataset(out)
    ratio = ds["wind"].values / ds["pv"].values
    np.testing.assert_allclose(ratio, 10.42 / 100.0, rtol=1e-12)
    assert "10.42" in ds.attrs["density_mw_per_km2"]
    ds.close()


def test_apply_lulc_requires_lulc_path(tmp_path, tmp_config):
    with pytest.raises(ValueError, match="lulc_path"):
        cds_max_capacity.build_region("EU", tmp_path, tmp_config, apply_lulc=True)


def _write_lulc(path: Path, codes: np.ndarray) -> None:
    """ESA-CCI-style file: uint8 lccs_class with a leading time dim, lat descending."""
    da = xr.DataArray(
        codes[None, :, :].astype("uint8"),
        coords={
            "time": [np.datetime64("2022-01-01")],
            "lat": np.linspace(90, 89, codes.shape[0]),
            "lon": np.linspace(-180, -179, codes.shape[1]),
        },
        dims=("time", "lat", "lon"),
        name="lccs_class",
    )
    da.to_dataset().to_netcdf(path)


def test_usable_fraction_block_mean_and_flip(tmp_path):
    # Two 0.25 deg cells stacked in latitude near the north-west corner of the
    # global ESA grid, so the computed start indices stay small: y rows begin at
    # (90 - (89.75 + 0.125)) * 360 = 45, x columns at (-179.875 + 180) * 360 = 45.
    y = np.array([89.5, 89.75])
    x = np.array([-179.75])
    block = cds_max_capacity.BLOCK  # 90
    codes = np.zeros((45 + 2 * block, 45 + block), dtype=np.uint8)
    # Top ESA block (higher latitude, y=89.75): urban, pv fraction 0.024.
    codes[45 : 45 + block, 45:] = 190
    # Bottom block (y=89.5): half bare (0.33), half unlisted code 50 (-> 0).
    codes[45 + block :, 45 : 45 + block // 2] = 200
    codes[45 + block :, 45 + block // 2 :] = 50
    lulc_path = tmp_path / "lulc.nc"
    _write_lulc(lulc_path, codes)

    frac = cds_max_capacity.usable_fraction(y, x, "pv", lulc_path)
    assert frac.shape == (2, 1)
    np.testing.assert_allclose(frac[1, 0], 0.024, rtol=1e-6)  # y=89.75 (top block)
    np.testing.assert_allclose(frac[0, 0], 0.33 / 2, rtol=1e-6)  # y=89.5, half usable
    # Wind has no urban entry -> the 190 block is fully excluded.
    frac_wind = cds_max_capacity.usable_fraction(y, x, "wind", lulc_path)
    np.testing.assert_allclose(frac_wind[1, 0], 0.0)


# ---- install ----------------------------------------------------------------


def _stage_stores(staging: Path, region: str, year: int) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    time = pd.date_range(f"{year}-01-01", periods=4, freq="h")
    profile = xr.Dataset(
        {
            "solar": (("time", "y", "x"), np.zeros((4, 2, 2), dtype="float32")),
            "wind": (("time", "y", "x"), np.zeros((4, 2, 2), dtype="float32")),
        },
        coords={"time": time, "y": [0.0, 0.25], "x": [0.0, 0.25]},
    )
    profile.to_zarr(staging / (profile_store_stem(region, year) + ".zarr"), consolidated=True)
    max_cap = xr.Dataset(
        {"pv": (("y", "x"), np.ones((2, 2))), "wind": (("y", "x"), np.ones((2, 2)))},
        coords={"y": [0.0, 0.25], "x": [0.0, 0.25]},
    )
    max_cap.to_zarr(staging / (max_cap_store_stem(region, year) + ".zarr"), consolidated=True)


def test_install_moves_both_kinds(tmp_path):
    staging, live = tmp_path / "staging", tmp_path / "live"
    _stage_stores(staging, "EU", ERA5_DATA_YEAR)
    installed = cds_install.install_regions(["EU"], ERA5_DATA_YEAR, ["profile", "max-cap"], staging, live)
    assert len(installed) == 2
    assert (live / (profile_store_stem("EU", ERA5_DATA_YEAR) + ".zarr")).exists()
    assert (live / (max_cap_store_stem("EU", ERA5_DATA_YEAR) + ".zarr")).exists()
    assert not list(staging.glob("*.zarr"))  # moved, not copied


def test_install_refuses_existing_without_force(tmp_path):
    staging, live = tmp_path / "staging", tmp_path / "live"
    _stage_stores(staging, "EU", ERA5_DATA_YEAR)
    cds_install.install_regions(["EU"], ERA5_DATA_YEAR, ["profile", "max-cap"], staging, live)
    _stage_stores(staging, "EU", ERA5_DATA_YEAR)
    with pytest.raises(FileExistsError, match="--force"):
        cds_install.install_regions(["EU"], ERA5_DATA_YEAR, ["profile", "max-cap"], staging, live)
    cds_install.install_regions(["EU"], ERA5_DATA_YEAR, ["profile", "max-cap"], staging, live, force=True)


def test_install_dry_run_touches_nothing(tmp_path):
    staging, live = tmp_path / "staging", tmp_path / "live"
    _stage_stores(staging, "EU", ERA5_DATA_YEAR)
    cds_install.install_regions(["EU"], ERA5_DATA_YEAR, ["profile", "max-cap"], staging, live, dry_run=True)
    assert not live.exists()
    assert len(list(staging.glob("*.zarr"))) == 2


def test_install_keep_staged_copies(tmp_path):
    staging, live = tmp_path / "staging", tmp_path / "live"
    _stage_stores(staging, "EU", ERA5_DATA_YEAR)
    cds_install.install_regions(["EU"], ERA5_DATA_YEAR, ["profile", "max-cap"], staging, live, keep_staged=True)
    assert len(list(staging.glob("*.zarr"))) == 2
    assert len(list(live.glob("*.zarr"))) == 2


def test_install_rejects_wrong_variables(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    bad = xr.Dataset({"foo": (("y", "x"), np.ones((2, 2)))}, coords={"y": [0, 1], "x": [0, 1]})
    bad.to_zarr(staging / (profile_store_stem("EU", ERA5_DATA_YEAR) + ".zarr"), consolidated=True)
    with pytest.raises(ValueError, match="data vars"):
        cds_install.install_regions(["EU"], ERA5_DATA_YEAR, ["profile"], staging, tmp_path / "live", kind_explicit=True)


def test_install_refuses_half_region_pair(tmp_path):
    staging = tmp_path / "staging"
    _stage_stores(staging, "EU", ERA5_DATA_YEAR)
    import shutil

    shutil.rmtree(staging / (max_cap_store_stem("EU", ERA5_DATA_YEAR) + ".zarr"))
    with pytest.raises(FileNotFoundError, match="pair"):
        cds_install.install_regions(["EU"], ERA5_DATA_YEAR, ["profile", "max-cap"], staging, tmp_path / "live")


def test_install_warns_on_non_default_year(tmp_path, caplog):
    staging, live = tmp_path / "staging", tmp_path / "live"
    year = ERA5_DATA_YEAR + 1
    _stage_stores(staging, "EU", year)
    with caplog.at_level("WARNING"):
        cds_install.install_regions(["EU"], year, ["profile", "max-cap"], staging, live)
    assert any("ERA5_DATA_YEAR" in message for message in caplog.messages)


def test_missing_live_store_raises_actionable_error(tmp_config, monkeypatch):
    monkeypatch.setenv("PROFILE_DATA_SOURCE", "local_zarr")
    from boa.inputs.profiles import open_regional_dataset

    with pytest.raises(FileNotFoundError, match="boa_cds install"):
        open_regional_dataset("profile", "EU", tmp_config)


# ---- download (skip logic only; network calls live upstream) -----------------


def test_download_skips_when_extracted_dir_exists(tmp_path):
    """Zips may be deleted after extraction without triggering a re-download."""
    from boa.cds.download import download

    extracted = tmp_path / "cds_solar_cf_ic6hh135_0_25_degree_2024"
    extracted.mkdir()
    (extracted / "jan.nc").touch()
    target = tmp_path / "cds_solar_cf_ic6hh135_0_25_degree_2024.zip"
    # Returns before any network access; would raise if it tried to retrieve.
    download("sis-energy-global-reanalysis", {}, target, dry_run=False, extract=True)
    assert not target.exists()


# ---- prepare -----------------------------------------------------------------


def _seed_prepare_root(root: Path, monkeypatch, year: int = ERA5_DATA_YEAR) -> None:
    monkeypatch.setenv("BOA_DATA_ROOT", str(root))
    monkeypatch.setitem(REGION_COORDS, "TEST", [10.5, -30.5, 9.75, -29.75])
    for tech in ("solar", "wind"):
        _write_cds_months(root / "data" / "cds" / cf_extract_dir_name(tech, year), tech, year)


def test_prepare_builds_reuses_and_forces(tmp_path, monkeypatch):
    _seed_prepare_root(tmp_path, monkeypatch)
    assert run_cds.main_prepare(["--region", "TEST", "--inputs", "setA"]) == 0
    live = tmp_path / "inputs" / "setA" / "cds-zarr"
    prof = live / (profile_store_stem("TEST", ERA5_DATA_YEAR) + ".zarr")
    assert prof.exists()
    assert (live / (max_cap_store_stem("TEST", ERA5_DATA_YEAR) + ".zarr")).exists()

    # Second run reuses the installed stores untouched.
    marker = prof / ".zmetadata"
    mtime = marker.stat().st_mtime_ns
    assert run_cds.main_prepare(["--region", "TEST", "--inputs", "setA"]) == 0
    assert marker.stat().st_mtime_ns == mtime

    # --force rebuilds and reinstalls.
    assert run_cds.main_prepare(["--region", "TEST", "--inputs", "setA", "--force"]) == 0
    assert marker.stat().st_mtime_ns > mtime


def test_prepare_auto_tags_input_set_by_year(tmp_path, monkeypatch):
    _seed_prepare_root(tmp_path, monkeypatch)
    assert run_cds.main_prepare(["--region", "TEST"]) == 0
    live = tmp_path / "inputs" / f"cds-{ERA5_DATA_YEAR}" / "cds-zarr"
    assert (live / (profile_store_stem("TEST", ERA5_DATA_YEAR) + ".zarr")).exists()
    assert (live / (max_cap_store_stem("TEST", ERA5_DATA_YEAR) + ".zarr")).exists()


def test_prepare_missing_raw_year_names_download_command(tmp_path, monkeypatch, capsys):
    _seed_prepare_root(tmp_path, monkeypatch)
    assert run_cds.main_prepare(["--region", "TEST", "--inputs", "setA", "--weather_year", "2031"]) == 1
    assert "boa-cds-download --year 2031" in capsys.readouterr().out
    assert not (tmp_path / "inputs" / "setA" / "cds-zarr").exists()


# ---- CLI entry points --------------------------------------------------------


def test_cli_entry_points_exist():
    assert callable(run_cds.prepare)
    assert callable(run_cds.download)
