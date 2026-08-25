from pathlib import Path
import math
import pandas as pd
import numpy as np
import xarray as xr
import logging
from dataclasses import dataclass

from boa.config.constants import EARTH_RADIUS_KM
from boa.config.settings import ERA5_DATA_RESOLUTION, REGION_COORDS


@dataclass
class Coordinate:
    """Represents a geographic coordinate with its ISO3 country code."""

    lat: float
    lon: float
    iso3: str


@dataclass
class CountryMappings:
    """Container for country mapping dictionaries."""

    code_to_irena_region_map: dict[str, str]
    code_to_irena_map: dict[str, str]

    @classmethod
    def from_excel(cls, excel_path: Path) -> "CountryMappings":
        """
        Load country mappings from Excel file.

        Args:
            excel_path: Path to boa_cost_data.xlsx.

        Returns:
            CountryMappings instance with mapping dictionaries:
            - code_to_irena_region_map: ISO3 code -> IRENA region name
            - code_to_irena_map: ISO3 code -> IRENA country name
        """
        if not excel_path.exists():
            raise FileNotFoundError(f"Input data Excel file not found at {excel_path}")

        # Read the Country mapping sheet
        df = pd.read_excel(excel_path, sheet_name="Country mapping")

        # One code = one location; duplicates would silently resolve last-wins below.
        dup_mask = df["Code"].duplicated(keep=False)
        if dup_mask.any():
            dups = sorted(df.loc[dup_mask, "Code"].unique())
            raise ValueError(f"Duplicate codes in the Country mapping sheet: {dups}.")

        # Build the mapping dictionaries
        code_to_irena_region_map = dict(zip(df["Code"], df["irena_region"]))
        code_to_irena_map = dict(zip(df["Code"], df["irena_name"]))

        return cls(
            code_to_irena_region_map=code_to_irena_region_map,
            code_to_irena_map=code_to_irena_map,
        )


@dataclass
class RegionChoice:
    """Outcome of coordinate-based region selection (see ``select_region``)."""

    region: str
    inside: bool
    signed_distance_deg: float
    bounds: tuple[float, float, float, float]  # (north, west, south, east)
    candidates: dict[str, float]


def _wrap_deg(delta: float) -> float:
    """Shortest signed angular difference (degrees) on the -180/180 seam."""
    return (delta + 180.0) % 360.0 - 180.0


def wrap_lon_to_grid(lon: float, grid_lon_min: float, grid_lon_max: float) -> float:
    """
    Shift ``lon`` by ±360 so it lands nearest a cutout's ``[min, max]`` longitude span.

    The regional ERA5 cutouts use a -180..180 x-grid that never crosses the antimeridian,
    so a query just east of +180 (e.g. Samoa at lon -172) must be queried at +188 to snap
    to the grid's east edge rather than the far west edge. Returns whichever of ``lon``,
    ``lon-360`` or ``lon+360`` is closest to (or inside) the span — a no-op for a longitude
    that already sits in range.
    """

    def distance_to_span(candidate: float) -> float:
        if candidate < grid_lon_min:
            return grid_lon_min - candidate
        if candidate > grid_lon_max:
            return candidate - grid_lon_max
        return 0.0

    return min((lon - 360.0, lon, lon + 360.0), key=distance_to_span)


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance between two lon/lat points, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def select_region(lon: float, lat: float) -> RegionChoice:
    """
    Choose the atlite/ERA5 region for a coordinate from ``REGION_COORDS`` geometry.

    Pure geometry — no data access, no logging. For each box ``[N, W, S, E]`` a signed
    planar distance to (lon, lat) is computed: negative inside (magnitude = interior
    margin to the nearest edge), positive outside (Euclidean degrees to the box).
    Longitude differences wrap on the antimeridian, so a point near the dateline attaches
    to the box across the -180/180 seam rather than a far Western-hemisphere box. The box
    with the minimum signed distance wins; inside an overlap the most-interior box wins,
    with exact ties broken by ``REGION_COORDS`` declaration order. A point inside no box
    falls back to the nearest box, and ``inside`` is then ``False``.

    The fallback is restricted to boxes that keep the point in its own hemisphere, when any
    exist: an equator-crossing snap inverts seasonality, which no distance in degrees can
    express. ``candidates`` always reports every box's raw distance, including any excluded
    this way. See docs/methodology.md -> Weather region selection.
    """
    candidates: dict[str, float] = {}
    keeps_hemisphere: dict[str, bool] = {}
    for region, (north, west, south, east) in REGION_COORDS.items():
        lat_off = abs(lat - (north + south) / 2.0) - (north - south) / 2.0
        lon_off = abs(_wrap_deg(lon - (west + east) / 2.0)) - (east - west) / 2.0
        if lat_off <= 0.0 and lon_off <= 0.0:
            signed = max(lat_off, lon_off)  # negative: margin to the nearest edge
        else:
            signed = math.hypot(max(lon_off, 0.0), max(lat_off, 0.0))
        candidates[region] = signed
        # Latitude the point would snap to in this box: its own, or the nearest edge.
        keeps_hemisphere[region] = (min(max(lat, south), north) >= 0.0) == (lat >= 0.0)

    # A fallback may not flip the point's hemisphere: crossing the equator inverts
    # seasonality, whereas snapping along a latitude circle preserves the climate.
    # Nearest-in-degrees alone cannot see that, since it weights lon and lat equally.
    eligible = candidates
    if lat != 0.0 and all(d > 0.0 for d in candidates.values()):
        same_hemisphere = {r: d for r, d in candidates.items() if keeps_hemisphere[r]}
        if same_hemisphere:
            eligible = same_hemisphere

    region = min(eligible, key=lambda r: eligible[r])
    signed = candidates[region]
    north, west, south, east = REGION_COORDS[region]
    return RegionChoice(
        region=region,
        inside=signed <= 0.0,
        signed_distance_deg=signed,
        bounds=(north, west, south, east),
        candidates=candidates,
    )


def build_region_selection(
    requested_lon: float,
    requested_lat: float,
    snapped_lon: float,
    snapped_lat: float,
    choice: RegionChoice,
) -> dict:
    """
    Assemble the ``region_selection`` metadata for a resolved query and warn on displacement.

    ``snapped_lon``/``snapped_lat`` are the grid cell actually returned by
    ``dataset.sel(x=lon, y=lat, method="nearest")``; the caller passes them in so this
    stays pure (no data access) and unit-testable. Emits a single ``logging.warning`` when
    the point fell outside every box (``choice.inside`` is ``False``) or snapped further
    than one ERA5 cell (``> ERA5_DATA_RESOLUTION``) — i.e. genuine displacement, not
    nearest-cell rounding. Never raises.
    """
    north, west, south, east = choice.bounds
    snap_distance_deg = math.hypot(_wrap_deg(requested_lon - snapped_lon), requested_lat - snapped_lat)
    snap_distance_km = _haversine_km(requested_lon, requested_lat, snapped_lon, snapped_lat)

    if not choice.inside or snap_distance_deg > ERA5_DATA_RESOLUTION:
        logging.warning(
            f"Region weather for (lon={requested_lon:.2f}, lat={requested_lat:.2f}) snapped "
            f"{snap_distance_deg:.1f}° (~{snap_distance_km:.0f} km) to grid cell "
            f"(lon={snapped_lon:.2f}, lat={snapped_lat:.2f}) in box {choice.region} — "
            "result reflects that cell, not the requested point."
        )

    return {
        "region": choice.region,
        "inside_box": choice.inside,
        "requested": {"lon": requested_lon, "lat": requested_lat},
        "snapped_to": {"lon": snapped_lon, "lat": snapped_lat},
        "snap_distance_deg": snap_distance_deg,
        "snap_distance_km": snap_distance_km,
        "box_bounds": {"north": north, "west": west, "south": south, "east": east},
    }


def convert_coordinates(dataset: xr.Dataset | xr.DataArray) -> xr.Dataset | xr.DataArray:
    """
    Convert coordinates from lon 0,360 to lon -180,180 and lat 90,-90 to lat -90,90.

    Parameters:
        dataset (xr.Dataset | xr.DataArray): Input dataset or data array with coordinates to be converted.

    Returns:
        xr.Dataset | xr.DataArray: Dataset or data array with converted coordinates.
    """
    # Convert longitude from 0,360 to -180,180
    dataset = dataset.assign_coords(
        longitude=np.where(dataset.longitude > 180, dataset.longitude - 360, dataset.longitude)
    )
    # Convert latitude from 90,-90 to -90,90
    dataset = dataset.assign_coords(latitude=np.where(dataset.latitude > 90, dataset.latitude - 180, dataset.latitude))
    return dataset


def choose_land_points_in_cutout(
    data: xr.Dataset,
    lsm_path: Path,
) -> tuple[
    np.ndarray[tuple[int, ...], np.dtype[np.float64]],
    np.ndarray[tuple[int, ...], np.dtype[np.float64]],
    np.ndarray[tuple[int, ...], np.dtype[np.float64]],
]:
    """
    Choose land points in the cutout area based on the land-sea mask.
    Note: The land-sea mask must be in the same resolution than the data.

    Args:
        data: xarray Dataset containing the cutout area
        lsm_path: Path to the land-sea mask NC file
    """

    # Get the land-sea mask and filter for land points
    all_lats = data.y.values
    all_lons = data.x.values

    try:
        landsea_mask = xr.open_dataset(lsm_path, engine="netcdf4").drop_vars("valid_time")["lsm"]
    except (ImportError, ValueError):
        try:
            landsea_mask = xr.open_dataset(lsm_path, engine="h5netcdf").drop_vars("valid_time")["lsm"]
        except (ImportError, ValueError):
            landsea_mask = xr.open_dataset(lsm_path, engine="scipy").drop_vars("valid_time")["lsm"]
    landsea_mask_ = convert_coordinates(landsea_mask)
    landsea_mask_bin = (landsea_mask_ > 0.5).astype(int)
    landsea_mask_bin_cutout = landsea_mask_bin.sortby(["latitude", "longitude"]).sel(
        latitude=all_lats, longitude=all_lons, method="nearest"
    )
    all_points = np.array(np.meshgrid(landsea_mask_bin_cutout.latitude, landsea_mask_bin_cutout.longitude)).T.reshape(
        -1, 2
    )
    mask_values = np.array(landsea_mask_bin_cutout.values)
    land_points_lsm = all_points[mask_values.flatten() == 1]

    # Map nearest lat/lon to the data grid
    land_points_list = [
        (data.y.sel(y=lat, method="nearest").values.item(), data.x.sel(x=lon, method="nearest").values.item())
        for lat, lon in land_points_lsm
    ]
    land_points = np.array(sorted(set(land_points_list), key=lambda x: (x[0], x[1])))

    logging.info(
        f"Number of land grid points: {len(land_points)}; {len(land_points) / len(all_points) * 100:.2f}% of total"
    )

    return land_points, all_lats, all_lons
