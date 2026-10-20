"""The ISO3 lookups over the geo grid give the same cells as the np.isin passes they replace."""

from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import xarray as xr

from steelo.adapters.geospatial.geospatial_calculations import calculate_regional_hydrogen_ceiling
from steelo.adapters.geospatial.geospatial_layers import add_grid_power_price
from steelo.domain.models import CountryMapping, CountryMappingService

# Codes the lookups know, codes they do not know, and the missing-value forms that appear in the iso3 layer.
GRID_ISO3 = np.array(
    [
        ["USA", "CAN", None, "nan"],
        ["MEX", "ZZZ", np.nan, "USA"],
        ["BRA", "ARG", "CAN", "XXX"],
    ],
    dtype=object,
)


def _mapping(iso3: str, tiam_ucl_region: str) -> CountryMapping:
    return CountryMapping(
        country=iso3,
        iso2=iso3[:2],
        iso3=iso3,
        irena_name=iso3,
        region_for_outputs="World",
        ssp_region="World",
        tiam_ucl_region=tiam_ucl_region,
    )


def test_hydrogen_ceiling_region_layer_matches_region_by_region_isin():
    mappings = [
        _mapping("USA", "USA"),
        _mapping("CAN", "CAN"),
        _mapping("MEX", "CSA"),
        _mapping("BRA", "CSA"),
        _mapping("ARG", "CSA"),
    ]
    ds = xr.Dataset(
        {
            "lcoh": (["lat", "lon"], np.linspace(2.0, 6.0, GRID_ISO3.size).reshape(GRID_ISO3.shape)),
            "iso3": (["lat", "lon"], GRID_ISO3),
        },
        coords={"lat": np.arange(GRID_ISO3.shape[0]), "lon": np.arange(GRID_ISO3.shape[1])},
    )

    calculate_regional_hydrogen_ceiling(ds, CountryMappingService(mappings), 50)

    expected = np.full(GRID_ISO3.shape, np.nan, dtype=object)
    for region, iso3_list in {"USA": ["USA"], "CAN": ["CAN"], "CSA": ["MEX", "BRA", "ARG"]}.items():
        expected[np.isin(GRID_ISO3, iso3_list)] = region
    actual = ds["tiam_ucl_region"].values
    assert [None if isinstance(v, float) else v for v in actual.ravel()] == [
        None if isinstance(v, float) else v for v in expected.ravel()
    ]


def test_grid_power_price_matches_isin_known_mask():
    grid_iso3 = np.where(pd.isnull(GRID_ISO3), "nan", GRID_ISO3).astype(object)  # the layer holds strings only
    ds = xr.Dataset(
        {
            "iso3": (["lat", "lon"], grid_iso3),
            "feasibility_mask": (["lat", "lon"], np.ones(GRID_ISO3.shape)),
        },
        coords={"lat": np.arange(GRID_ISO3.shape[0]), "lon": np.arange(GRID_ISO3.shape[1])},
    )
    input_costs = {
        "USA": {2025: {"electricity": 0.08}},
        "CAN": {2025: {"electricity": 0.06}},
        "MEX": {2025: {"electricity": 0.10}},
        "BRA:SP": {2025: {"electricity": 9.99}},  # sub-national rows never reach the country grid price
    }
    geo_paths = Mock()
    geo_paths.geo_plots_dir = Mock(spec=Path)

    with patch("steelo.adapters.geospatial.geospatial_layers.plot_screenshot"):
        result = add_grid_power_price(ds, input_costs=input_costs, year=2025, geo_paths=geo_paths)

    known = np.isin(grid_iso3, ["USA", "CAN", "MEX"])
    expected = np.full(GRID_ISO3.shape, 0.10)  # maximum grid price for the cells without a record
    for iso3, price in (("USA", 0.08), ("CAN", 0.06), ("MEX", 0.10)):
        expected[grid_iso3 == iso3] = price
    assert known.sum() == 5
    np.testing.assert_allclose(result["grid_price"].values, expected)
