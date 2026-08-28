"""
Contracts for the layered land-availability model behind the capacity ceiling.

    max_capacity(tech) = pixel_area(lat) x density(tech) x PROD(layers(tech))

Two layers: `lulc` (fractional, from ESA-CCI land cover) and `cds_exclusion`
(binary, from the Copernicus combined exclusion masks). They multiply.

Most of these tests exist because getting a layer subtly wrong is silent. A
sign-flipped mask, a class that falls through to the wrong default, or a dtype
that decodes as int8 all produce a plausible-looking ceiling that is simply the
wrong number, and the only downstream symptom is a world that has become
unexpectedly infeasible.
"""

import numpy as np
import pytest

pytest.importorskip("boa.cds.availability")

from boa.cds import availability  # noqa: E402
from boa.config.settings import CAPACITY_DENSITY_MW_PER_KM2, LULC_CODES  # noqa: E402

CELL_LAT = np.array([89.875])
CELL_LONS = np.array([-179.875, -179.625])


# --------------------------------------------------------------------------
# The LULC layer
# --------------------------------------------------------------------------


def test_lulc_fraction_matches_the_block_mean(lulc_raster):
    """
    A 0.25 deg cell is exactly a 90x90 block of 300 m ESA-CCI pixels, so aggregation
    is an exact block mean with no reprojection involved. Cell 0 is all urban; cell 1
    is half bare and half tree cover.
    """
    frac = availability.lulc_fraction(CELL_LAT, CELL_LONS, "pv", lulc_raster)

    assert frac.shape == (1, 2)
    assert frac[0, 0] == pytest.approx(LULC_CODES["pv"][190])
    assert frac[0, 1] == pytest.approx(LULC_CODES["pv"][200] / 2)


def test_unlisted_class_is_fully_excluded(lulc_raster):
    """
    Classes absent from `LULC_CODES` get fraction 0, not a neutral 1.0. That is what
    makes all forest, wetland, water and snow hard exclusions, and for wind urban too,
    since code 190 appears only in the pv table.
    """
    assert 60 not in LULC_CODES["pv"], "fixture premise: tree cover is unlisted"
    assert 190 not in LULC_CODES["wind"], "fixture premise: urban is unlisted for wind"

    frac_wind = availability.lulc_fraction(CELL_LAT, CELL_LONS, "wind", lulc_raster)
    assert frac_wind[0, 0] == pytest.approx(0.0), "an all-urban cell must be zero for wind"


def test_lccs_dtype_is_pinned_to_uint8(lulc_raster):
    """
    The class codes index a 256-entry lookup table, so the dtype is load-bearing: a
    float decode makes the index raise, and an int8 decode makes codes at or above 128
    index from the wrong end -- silently wrong, not a crash.

    The 2022 v2.1.1 file as shipped does decode to uint8 on a bare `open_dataset`
    (checked: no `scale_factor`, no `add_offset`, and `_FillValue` unset -- only
    `flag_values`, which xarray leaves alone). So this guards a future file rather than
    a present bug: a different vintage, or a provider that starts setting `_FillValue`,
    would change the decode under a lookup table that cannot notice.
    """
    codes = availability.read_lulc_codes(lulc_raster)
    assert codes.dtype == np.uint8


def test_out_of_range_block_raises_a_named_error(lulc_raster):
    """
    Asking for a cell outside the raster currently truncates the slice and fails later
    in an opaque reshape. It must fail immediately, naming the offending coordinates.
    """
    with pytest.raises(ValueError, match="outside"):
        availability.lulc_fraction(np.array([-89.875]), np.array([179.875]), "pv", lulc_raster)


def test_lulc_fraction_is_bounded(lulc_raster):
    """A fraction outside [0, 1] means the lookup table or the block mean is wrong."""
    for tech in ("pv", "wind"):
        frac = availability.lulc_fraction(CELL_LAT, CELL_LONS, tech, lulc_raster)
        assert np.all(frac >= 0.0) and np.all(frac <= 1.0)


# --------------------------------------------------------------------------
# The CDS exclusion layer
# --------------------------------------------------------------------------


def test_cds_mask_is_inverted_to_a_factor(cds_masks_dir):
    """
    Copernicus convention is 1 = excluded, 0 = suitable, so the availability factor is
    one minus the mask. Getting this backwards zeroes exactly the cells that should
    survive, and the only symptom is a world that looks uniformly infeasible.
    """
    y = np.array([0.0, 0.25])
    x = np.array([0.0, 0.25])

    factor = availability.cds_exclusion_factor(y, x, "pv", cds_masks_dir)

    assert factor[0, 1] == pytest.approx(0.0), "an excluded cell must contribute zero"
    assert factor[0, 0] == pytest.approx(1.0), "a suitable cell must pass through unchanged"
    assert set(np.unique(factor)) <= {0.0, 1.0}, "the CDS masks are binary, not fractional"


def test_pv_and_wind_get_different_exclusion_masks(cds_masks_dir):
    """
    PV reads ANCI_SPVM and wind reads ANCI_WPM: the real wind mask omits the water
    layer so offshore stays assessable. Reading one mask for both technologies would
    be invisible in aggregate statistics.
    """
    y = np.array([0.0, 0.25])
    x = np.array([0.0, 0.25])

    pv = availability.cds_exclusion_factor(y, x, "pv", cds_masks_dir)
    wind = availability.cds_exclusion_factor(y, x, "wind", cds_masks_dir)

    assert not np.array_equal(pv, wind)


def test_latitude_weighting_is_never_a_layer():
    """
    `latitude_weighting_coefficients` is cos(lat), which `pixel_area` already contains,
    so multiplying it in would apply cos squared. It ships in the same mask bundle as
    the exclusion masks, which puts the mistake one autocomplete away.
    """
    assert "latitude_weighting_coefficients" not in availability.LAYER_FUNCS
    for name in availability.LAYER_FUNCS:
        assert "weight" not in name


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


def test_layers_multiply_in_registry_order(lulc_raster, cds_masks_dir):
    """
    The composed factor is the product of the individual layers. Order does not change
    the value, but it does change the recorded provenance, so it is fixed and named.
    """
    specs = availability.layer_specs(["lulc", "cds_exclusion"], lulc_path=lulc_raster, masks_dir=cds_masks_dir)

    combined = availability.availability_factor(CELL_LAT, CELL_LONS, "pv", specs)
    lulc = availability.lulc_fraction(CELL_LAT, CELL_LONS, "pv", lulc_raster)
    excl = availability.cds_exclusion_factor(CELL_LAT, CELL_LONS, "pv", cds_masks_dir)

    np.testing.assert_allclose(combined, lulc * excl)


def test_no_layers_means_pure_geometry():
    """An empty layer set must leave the ceiling exactly as geometry gives it."""
    factor = availability.availability_factor(CELL_LAT, CELL_LONS, "pv", [])
    np.testing.assert_allclose(factor, np.ones((1, 2)))


def test_composed_factor_is_bounded(lulc_raster, cds_masks_dir):
    """Every layer lies in [0, 1], so their product must too."""
    specs = availability.layer_specs(["lulc", "cds_exclusion"], lulc_path=lulc_raster, masks_dir=cds_masks_dir)
    for tech in ("pv", "wind"):
        f = availability.availability_factor(CELL_LAT, CELL_LONS, tech, specs)
        assert np.all(f >= 0.0) and np.all(f <= 1.0)


# --------------------------------------------------------------------------
# Provenance, and the silent-reuse defect
# --------------------------------------------------------------------------


def test_availability_signature_changes_with_layer_set(lulc_raster, cds_masks_dir):
    """
    A store built with a different layer set must not be reusable. Today the rebuild
    check is presence-only, so the layer set can change with no signal at all and a
    warm design cache keeps the ceilings it was built against.
    """
    both = availability.layer_specs(["lulc", "cds_exclusion"], lulc_path=lulc_raster, masks_dir=cds_masks_dir)
    lulc_only = availability.layer_specs(["lulc"], lulc_path=lulc_raster, masks_dir=cds_masks_dir)

    assert availability.availability_signature(
        both, CAPACITY_DENSITY_MW_PER_KM2
    ) != availability.availability_signature(lulc_only, CAPACITY_DENSITY_MW_PER_KM2)


def test_availability_signature_changes_with_densities(lulc_raster, cds_masks_dir):
    """
    This is a live defect, not a hypothetical: changing --pv-density today reuses the
    old store silently. Densities belong in the signature for the same reason layers do.
    """
    specs = availability.layer_specs(["lulc"], lulc_path=lulc_raster, masks_dir=cds_masks_dir)
    base = availability.availability_signature(specs, {"pv": 140, "wind": 10})
    other = availability.availability_signature(specs, {"pv": 140, "wind": 20.5})
    assert base != other


def test_availability_signature_is_stable_across_calls(lulc_raster, cds_masks_dir):
    """
    The signature gates a rebuild, so an unstable hash (dict ordering, float
    stringification) would rebuild every store on every run. The mtime-based reuse test
    in test_cds_pipeline.py is the downstream regression guard for this.
    """
    specs = availability.layer_specs(["lulc", "cds_exclusion"], lulc_path=lulc_raster, masks_dir=cds_masks_dir)
    a = availability.availability_signature(specs, CAPACITY_DENSITY_MW_PER_KM2)
    b = availability.availability_signature(specs, CAPACITY_DENSITY_MW_PER_KM2)
    assert a == b
    assert len(a) >= 8


def test_availability_tag_is_filesystem_safe():
    """
    The tag goes into the input-set name (cds-2024-lulc+excl), which becomes a
    directory and, through it, a separate design-cache directory. That path separation
    is what stops a cache built against one ceiling leaking into a run with another.
    """
    tag = availability.availability_tag(["lulc", "cds_exclusion"])
    assert tag and "/" not in tag and "\\" not in tag and " " not in tag
    assert availability.availability_tag([]) != tag
