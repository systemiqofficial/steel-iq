"""
Shared synthetic profiles and cost fixtures for the bisection-search tests.

The four new test modules (`test_bisection_kernel`, `test_frontier_build`,
`test_query`, `test_design_cache`) all need the same handful of hourly profiles,
so they live here instead of being copy-pasted four times. `test_sampler.py` and
`test_lcoe_promotion.py` keep their own local helpers — they predate this file
and are not worth churning.

Every profile obeys the contract `calculate_served_fraction` documents
(`logic.py:352-361`): supply is non-negative and demand is normalised to 1.0, so
`net_energy >= -1` always holds. Tests that deliberately violate that contract
say so at the callsite.
"""

import numpy as np
import pytest

# Three weeks of hourly data. Long enough for multi-day battery behaviour to
# show up, short enough that a full grid sweep in a test stays sub-second.
HOURS = 24 * 21


def _daily_solar(hours: int = HOURS, peak: float = 0.55) -> np.ndarray:
    """Clipped half-sine, zero at night. Mean capacity factor ~0.17."""
    t = np.arange(hours)
    return np.clip(peak * np.sin((t % 24 - 6) * np.pi / 12), 0.0, None)


def _noisy_wind(hours: int = HOURS, mean: float = 0.30, seed: int = 0) -> np.ndarray:
    """Positively-autocorrelated wind with realistic lulls. Deterministic per seed."""
    rng = np.random.RandomState(seed)
    raw = rng.rand(hours)
    # Smooth with a 12-hour box filter so lulls last long enough to size a battery.
    kernel = np.ones(12) / 12
    smoothed = np.convolve(np.concatenate([raw[-11:], raw]), kernel, mode="valid")
    return np.clip(smoothed / smoothed.mean() * mean, 0.0, None)


@pytest.fixture(scope="session")
def profiles() -> dict[str, np.ndarray]:
    """A well-resourced pixel: both technologies usable, neither dominant."""
    return {"solar": _daily_solar(), "wind": _noisy_wind()}


@pytest.fixture(scope="session")
def solar_only_profiles() -> dict[str, np.ndarray]:
    """Zero wind. Exercises the degenerate-mu path in `overscale_mus_from_cf`."""
    return {"solar": _daily_solar(), "wind": np.zeros(HOURS)}


@pytest.fixture(scope="session")
def dead_profiles() -> dict[str, np.ndarray]:
    """No resource at all -> status 3, never searched."""
    return {"solar": np.zeros(HOURS), "wind": np.zeros(HOURS)}


@pytest.fixture(scope="session")
def poor_profiles() -> dict[str, np.ndarray]:
    """Very low capacity factor on both technologies -> dismissal-screen territory."""
    return {"solar": _daily_solar(peak=0.04), "wind": _noisy_wind(mean=0.02, seed=1)}


@pytest.fixture(scope="session")
def anchor_costs():
    """
    Frozen cost coefficients standing in for `PATCH_ANCHOR_COST_RATIOS`.

    Values are illustrative, not calibrated: the tests assert relationships that
    hold for any positive coefficients, never a specific LCOE number.
    """
    from boa.model.bisection import CostCoefficients

    return CostCoefficients(a_s=1.0e6, a_w=1.6e6, a_b=0.30e6, d0=8.76e6)


# --------------------------------------------------------------------------
# Capacity box
# --------------------------------------------------------------------------
#
# The box is `L = max_capacity / baseload` in overscale units. Which regime a
# pixel is in drives almost every behavioural difference in the query, so the
# three regimes get named fixtures rather than magic numbers at each callsite.
#
# Values are the real ones from the plan's worked example at 45 deg N, a 546 km2
# cell, at --demand 500.


@pytest.fixture
def roomy_limits() -> dict[str, float]:
    """Geometry-only ceilings: the box is so wide it never binds."""
    return {"solar": 108.2, "wind": 7.73}


@pytest.fixture
def tight_limits() -> dict[str, float]:
    """LULC-on cropland at 500 MW: binds on both axes but stays feasible."""
    return {"solar": 3.06, "wind": 1.64}


@pytest.fixture
def infeasible_limits() -> dict[str, float]:
    """A box so small the corner design cannot meet coverage at any battery size."""
    return {"solar": 0.05, "wind": 0.02}


# --------------------------------------------------------------------------
# Availability layers
# --------------------------------------------------------------------------


@pytest.fixture
def lulc_raster(tmp_path):
    """
    A synthetic ESA-CCI land-cover file on the exact grid `usable_fraction` assumes.

    Two 0.25 deg cells side by side, each an exact 90x90 block of 300 m pixels
    (ESA_CCI_CELLS_PER_DEG = 360, so 0.25 deg is exactly 90 cells). Cell 0 is all
    urban (190), cell 1 is half bare (200) and half tree cover (60). Tree cover is
    absent from LULC_CODES, so it must contribute exactly zero.

    Latitude descends, as in the real product, so the reader has to flip it.
    """
    import numpy as np
    import xarray as xr

    block = 90
    codes = np.zeros((block, 2 * block), dtype=np.uint8)
    codes[:, :block] = 190
    codes[:, block:] = 200
    codes[: block // 2, block:] = 60

    lat = 90.0 - (np.arange(block) + 0.5) / 360.0
    lon = -180.0 + (np.arange(2 * block) + 0.5) / 360.0
    ds = xr.Dataset(
        {"lccs_class": (("time", "lat", "lon"), codes[None, :, :])},
        coords={"time": [0], "lat": lat, "lon": lon},
    )
    path = tmp_path / "C3S-LC-L4-LCCS-Map-300m-P1Y-2022-v2.1.1.nc"
    ds.to_netcdf(path)
    return path


@pytest.fixture
def cds_masks_dir(tmp_path):
    """
    Synthetic CDS combined exclusion masks, one per technology.

    Shaped to match the real files, which were inspected rather than assumed:

      * variable names differ per technology -- `PVmask` in ANCI_SPVM, `wp_mask` in
        ANCI_WPM (the published documentation table calls the latter `m_rest`, which
        the delivered file does not use);
      * dimensions are `latitude`/`longitude`, not `y`/`x`;
      * latitude descends 90 -> -90 and longitude runs 0..360, so a reader has to
        flip and fold before the grid lines up with the profile stores;
      * values are float64, not an integer type.

    Convention is the Copernicus one, confirmed against the delivered data: **1 =
    excluded, 0 = suitable**, so the layer factor is `1 - mask`. The two technologies
    get deliberately different masks -- the real wind mask omits the water layer, so
    they are never interchangeable.
    """
    import numpy as np
    import xarray as xr

    d = tmp_path / "cds_masks"
    d.mkdir()
    lat = np.array([0.25, 0.0])  # descending, as delivered
    lon = np.array([0.0, 0.25])

    pv = np.array([[0.0, 1.0], [0.0, 0.0]])  # one cell excluded
    wind = np.array([[0.0, 0.0], [1.0, 0.0]])  # a different cell excluded

    coords = {"latitude": lat, "longitude": lon}
    xr.Dataset({"PVmask": (("latitude", "longitude"), pv)}, coords=coords).to_netcdf(
        d / "ANCI_SPVM-mask_C3S2LOT1_025d_v1.00.nc"
    )
    xr.Dataset({"wp_mask": (("latitude", "longitude"), wind)}, coords=coords).to_netcdf(
        d / "ANCI_WPM-mask_C3S2LOT1_025d_v1.00.nc"
    )
    return d
