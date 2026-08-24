"""
Shared schema for the regional profile and max-capacity Zarr stores.

Single source of truth for chunking, Zarr format, and store filenames, so the
writers (boa.cds, scripts/convert_profiles_to_zarr.py) and the reader
(boa.inputs.profiles) can never drift apart.
"""

from boa.config.settings import ERA5_DATA_RESOLUTION
from boa.conversions import convert_resolution_to_string

# Spatial chunking: full time series per chunk, 12x12 grid cells per spatial
# block. ~10 MB raw per chunk, ~3-5 MB after compression. One single-point
# read = one chunk fetch.
PROFILE_CHUNKS = {"time": -1, "x": 12, "y": 12}
# Max-capacity files are 2D and tiny; one chunk is plenty.
MAX_CAP_CHUNKS = {"x": -1, "y": -1}
# Pin Zarr format to v2 — the consolidated-metadata story is stable and tooling
# support is broader than v3. Data is going to S3 for the long haul.
ZARR_FORMAT = 2


def profile_store_stem(region: str, year: int, resolution: float = ERA5_DATA_RESOLUTION) -> str:
    """Filename stem of a regional pv/wind profile store (no extension)."""
    res = convert_resolution_to_string(resolution)
    return f"pv_and_wind_potential_{region}_{year}_{res}_deg"


def max_cap_store_stem(region: str, year: int, resolution: float = ERA5_DATA_RESOLUTION) -> str:
    """Filename stem of a regional max-capacity store (no extension)."""
    res = convert_resolution_to_string(resolution)
    return f"max_capacity_{region}_{year}_{res}_deg"
