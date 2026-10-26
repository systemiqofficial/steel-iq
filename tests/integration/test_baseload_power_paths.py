"""Tests for the default BOA LCOE files geo-data ships and their extraction into the prepared data."""

from unittest.mock import Mock

from steelo.data import geo_extractor, manager

DEFAULT_LCOE_FILES = [
    "optimal_lcoe_wy2024_01MWkm2_cov0p85_2025_2060.nc",
    "optimal_lcoe_wy2024_01MWkm2_cov0p95_2025_2060.nc",
    "optimal_lcoe_wy2024_01MWkm2_cov0p9_2025_2060.nc",
]


def test_manifest_default_geo_data_is_3_0_0_with_the_default_lcoe_files(tmp_path):
    """A fresh home downloads geo-data 3.0.0, which lists the three default LCOE files and no per-year file."""
    package = manager.DataManager(cache_dir=tmp_path).manifest.get_package("geo-data")

    assert package is not None
    assert package.version == "3.0.0"
    assert package.url == "https://steelo-data.s3.eu-north-1.amazonaws.com/geo-data-v3.0.0.zip"
    assert package.checksum == "d6e65fbd079616879cb7731e9ef1f5bdb14a7323a589bdadcd72d189b262ca97"
    assert [f for f in package.files if f.startswith("outputs/GEO/baseload_lcoe/")] == [
        f"outputs/GEO/baseload_lcoe/{name}" for name in DEFAULT_LCOE_FILES
    ]
    assert not [f for f in package.files if "baseload_power_simulation" in f or "optimal_sol" in f]


def test_geo_extractor_puts_the_default_lcoe_files_in_baseload_lcoe_and_leaves_the_rest(tmp_path):
    """The three LCOE files land under their own names; the 1:50m map and any per-year file stay in the package."""
    package_dir = tmp_path / "geo-data-v3.0.0"
    shipped = [
        "terrain_025_deg.nc",
        *(f"outputs/GEO/baseload_lcoe/{name}" for name in DEFAULT_LCOE_FILES),
        "ne_50m_admin_0_map_subunits/ne_50m_admin_0_map_subunits.shp",
        "outputs/GEO/baseload_power_simulation/p15/GLOBAL/optimal_sol_GLOBAL_2025_p15.nc",
    ]
    for name in shipped:
        (package_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (package_dir / name).write_bytes(b"x")
    data_manager = Mock(get_package_path=Mock(return_value=package_dir))
    target = tmp_path / "prepared"

    extracted = geo_extractor.GeoDataExtractor(data_manager).extract_geo_data(target_dir=target)

    lcoe_dir = target / "outputs" / "GEO" / "baseload_lcoe"
    assert sorted(path.name for path in lcoe_dir.iterdir()) == sorted(DEFAULT_LCOE_FILES)
    assert extracted[f"outputs/GEO/baseload_lcoe/{DEFAULT_LCOE_FILES[0]}"] == lcoe_dir / DEFAULT_LCOE_FILES[0]
    assert not (target / "ne_50m_admin_0_map_subunits").exists()
    assert not (target / "outputs" / "GEO" / "baseload_power_simulation").exists()
