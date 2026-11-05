"""find_top_locations_per_country extracts each country from its bounding box and gives the result
of ranking the whole grid once per country."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from steelo.adapters.geospatial.priority_kpi import (
    _bounding_boxes_by_iso3,
    extract_priority_locations,
    find_top_locations_per_country,
)

PRODUCT = "steel"
SHARE = 0.2


def _reference(ds, top_locations, product, priority_sites_share, random_seed):
    """The extraction as it ran before the bounding boxes: mask and rank the whole grid for every country."""
    label = f"{priority_sites_share * 100:g}"
    top_wlottery = ds[f"top{label}_{product}"].copy().astype(float)
    result_locations = top_locations[product].copy()
    iso3_values = ds["iso3"].values.flatten()
    unique_iso3 = np.unique(iso3_values[(~pd.isnull(iso3_values)) & (iso3_values != "nan")])
    ds_vars = ds[["iso3", f"outgoing_cashflow_{product}", "feasibility_mask"]]
    for iso3 in unique_iso3:
        ds_iso3 = ds_vars.where(ds_vars["iso3"] == iso3)
        if np.any(~np.isnan(ds_iso3[f"outgoing_cashflow_{product}"].values)):
            top_values_iso3, top_locations_iso3 = extract_priority_locations(
                ds_iso3,
                f"outgoing_cashflow_{product}",
                top_pct=(priority_sites_share / 10) * 100,
                random_seed=random_seed,
                invert=True,
            )
            top_wlottery += top_values_iso3
            frames = [df for df in [result_locations, top_locations_iso3] if not df.empty]
            if frames:
                result_locations = pd.concat(frames, ignore_index=True)
    result_locations = result_locations.drop_duplicates(subset=["Latitude", "Longitude"], ignore_index=True)
    return top_wlottery.astype(bool), result_locations


def _grid(seed: int):
    rng = np.random.default_rng(seed)
    n_rows, n_cols = 30, 40
    iso3 = np.full((n_rows, n_cols), "nan", dtype=object)
    iso3[0:10, 0:15] = "AAA"  # many distinct values: quantile path
    iso3[10:20, 0:15] = "BBB"  # few distinct values: stepwise sampling path
    iso3[20:30, 0:15] = "CCC"  # one value: uniform random path
    iso3[28:30, 0:15] = "nan"  # no country
    iso3[0:30, 15:25] = "DDD"  # no cashflow at all
    iso3[0:12, 25:40] = "EEE"
    iso3[12:30, 35:40] = "EEE"  # L shape; its box covers cells of another country
    iso3[12:30, 25:35] = "FFF"
    iso3[5, 30] = "FFF"  # an enclave inside the box of EEE
    cashflow = rng.normal(100.0, 20.0, size=iso3.shape)
    cashflow[iso3 == "BBB"] = rng.integers(1, 6, size=int((iso3 == "BBB").sum()))
    cashflow[iso3 == "CCC"] = 42.0
    cashflow[iso3 == "DDD"] = np.nan
    cashflow[iso3 == "nan"] = np.nan
    cashflow[rng.random(iso3.shape) < 0.05] = 0.0
    feasible = (rng.random(iso3.shape) < 0.8).astype(float)
    top_global = (rng.random(iso3.shape) < 0.1).astype(float)
    coords = {"lat": np.linspace(-14.5, 14.5, n_rows), "lon": np.linspace(-19.5, 19.5, n_cols)}
    ds = xr.Dataset(
        {
            "iso3": (("lat", "lon"), iso3),
            f"outgoing_cashflow_{PRODUCT}": (("lat", "lon"), cashflow),
            "feasibility_mask": (("lat", "lon"), feasible),
            f"top20_{PRODUCT}": (("lat", "lon"), top_global),
        },
        coords=coords,
    )
    rows, cols = np.nonzero(top_global)
    top_locations = {
        PRODUCT: pd.DataFrame({"Latitude": coords["lat"][rows], "Longitude": coords["lon"][cols]}),
    }
    return ds, top_locations


@pytest.mark.parametrize("seed", [0, 1, 7])
def test_matches_whole_grid_extraction(seed):
    ds, top_locations = _grid(seed)

    expected_values, expected_locations = _reference(ds, top_locations, PRODUCT, SHARE, random_seed=seed)
    actual_values, actual_locations = find_top_locations_per_country(ds, top_locations, PRODUCT, SHARE, seed)

    assert actual_values.dtype == expected_values.dtype
    np.testing.assert_array_equal(actual_values.values, expected_values.values)
    pd.testing.assert_frame_equal(actual_locations, expected_locations)
    assert expected_values.values.sum() > (ds[f"top20_{PRODUCT}"] > 0).values.sum()  # the lottery adds cells


def test_bounding_boxes_cover_every_cell_of_a_country():
    ds, _ = _grid(0)
    iso3 = ds["iso3"].values
    boxes = _bounding_boxes_by_iso3(iso3)

    assert "nan" in boxes  # callers skip it
    for code, (row_start, row_stop, col_start, col_stop) in boxes.items():
        rows, cols = np.nonzero(iso3 == code)
        assert (row_start, row_stop, col_start, col_stop) == (rows.min(), rows.max() + 1, cols.min(), cols.max() + 1)
