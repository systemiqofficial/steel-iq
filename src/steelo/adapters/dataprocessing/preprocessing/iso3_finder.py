import pycountry
from functools import lru_cache
import gc
import numpy as np
import xarray as xr
import inspect
from io import StringIO
from dataclasses import dataclass
import logging
from geopy import distance  # type: ignore
import reverse_geocoder as rg  # type: ignore

logger = logging.getLogger(__name__)

# geocoder instance singleton
REVERSE_GEOCODER = None


@dataclass
class Coordinate:
    """Represents a geographic coordinate with its ISO3 country code."""

    lat: float
    lon: float
    iso3: str


def rounded_coords(lat, lon, precision=3):
    return round(lat, precision), round(lon, precision)


@lru_cache(maxsize=100_000)
def cache_derive_iso3(lat: float, lon: float, max_distance_km=200) -> str:
    """
    Cache the result of derive_iso3 to speed up repeated calls with the same coordinates.

    Note: This cached version cannot accept coordinates parameter since it would break caching.
    The reverse geocoder must be initialized before calling this function.

    Args:
        lat: Latitude (-90 to 90)
        lon: Longitude (-180 to 180)
        max_distance_km: Maximum allowed distance between input and result in km

    Returns:
        ISO3 country code
    """
    return derive_iso3(lat, lon, coordinates=None, max_distance_km=max_distance_km)


def reset_singleton(singleton_class):
    """
    Reset the rg.RGeocoder singleton class by clearing its instances dictionary
    """
    # Get the decorator closure
    decorator_closure = inspect.getclosurevars(singleton_class).nonlocals
    # Clear the instances dictionary
    if "instances" in decorator_closure:
        instances_dict = decorator_closure["instances"]
        for key in list(instances_dict.keys()):
            del instances_dict[key]
    else:
        # Alternative method if we can't find the instances directly
        for obj in gc.get_objects():
            if isinstance(obj, dict) and singleton_class in obj:
                obj.pop(singleton_class, None)
                break


def set_global_reverse_geocoder(geocoder: rg.RGeocoder) -> None:
    reset_singleton(rg.RGeocoder)
    global REVERSE_GEOCODER
    REVERSE_GEOCODER = geocoder


def get_global_reverse_geocoder() -> rg.RGeocoder:
    return REVERSE_GEOCODER


def reset_reverse_geocoder() -> None:
    """Reset the global reverse geocoder to force reinitialization."""
    global REVERSE_GEOCODER
    REVERSE_GEOCODER = None


def _coordinates_to_csv_string(coordinates: list[Coordinate]) -> str:
    """
    Convert a list of Coordinate objects to a CSV string suitable for reverse_geocoder.

    Format expected by reverse_geocoder:
    lat,lon,name,admin1,admin2,cc

    Args:
        coordinates: List of Coordinate objects with lat, lon, and iso3 fields

    Returns:
        CSV string with header and coordinate data
    """
    lines = ["lat,lon,name,admin1,admin2,cc"]
    for coord in coordinates:
        # Put ISO3 in the cc field, leave others empty
        lines.append(f"{coord.lat},{coord.lon},,,,{coord.iso3}")
    return "\n".join(lines)


def search(point: tuple[float, float], coordinates: list[Coordinate] | None = None, mode: int = 1) -> list[dict]:
    """
    Search for the ISO3 code of a given point using reverse_geocoder.

    On first call, coordinates must be provided to initialize the reverse geocoder.
    Subsequent calls can omit coordinates unless you want to reinitialize.

    Args:
        point: Tuple of (latitude, longitude)
        coordinates: Optional list of Coordinate objects to seed the reverse geocoder.
                    Required on first call. If provided after initialization, a warning is logged.
        mode: Reverse geocoder mode (default: 1)

    Returns:
        List of reverse geocoding results (including ISO3 code) for the given point.

    Raises:
        ValueError: If coordinates are not provided on first call
    """
    global REVERSE_GEOCODER

    if not REVERSE_GEOCODER:
        if coordinates is None:
            raise ValueError(
                "coordinates must be provided on first call to initialize reverse geocoder. "
                "Use reset_reverse_geocoder() to force reinitialization with new coordinates."
            )

        # Convert provided coordinates to CSV string
        coordinates_csv = _coordinates_to_csv_string(coordinates)

        # Initialize geocoder with user-provided coordinates
        set_global_reverse_geocoder(rg.RGeocoder(stream=StringIO(coordinates_csv), mode=mode))
    else:
        # Geocoder already initialized
        if coordinates is not None:
            logger.warning(
                "Coordinates provided but reverse geocoder is already initialized. "
                "Use reset_reverse_geocoder() before calling search() to reinitialize with new coordinates."
            )

    reverse_geocoder = get_global_reverse_geocoder()
    return reverse_geocoder.query([point])


def derive_iso3(
    lat: float, lon: float, coordinates: list[Coordinate] | None = None, max_distance_km: float = 400
) -> str:
    """
    Derive the ISO3 code from latitude and longitude using reverse_geocoder.

    Args:
        lat: Latitude (-90 to 90)
        lon: Longitude (-180 to 180)
        coordinates: Optional list of Coordinate objects to seed the reverse geocoder.
                    Required on first call. If provided after initialization, a warning is logged.
        max_distance_km: Maximum allowed distance between input and result in km

    Returns:
        ISO3 country code

    Raises:
        ValueError: If coordinates are invalid or if no result is found
    """
    # Validate coordinate ranges
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError(
            f"Invalid coordinates: ({lat}, {lon}). Latitude must be between -90 and 90, longitude between -180 and 180."
        )

    try:
        result = search((lat, lon), coordinates=coordinates)
        if not result or len(result) == 0:
            raise ValueError(f"No result found for coordinates ({lat}, {lon}).")

        result_lat, result_lon = float(result[0]["lat"]), float(result[0]["lon"])
        dist = distance.geodesic((lat, lon), (result_lat, result_lon)).kilometers

        # If the result is too far away, it's likely unreliable
        if dist > max_distance_km:
            raise ValueError(
                f"Result is {dist:.1f}km away from input coordinates. "
                f"This exceeds the maximum allowed distance of {max_distance_km}km. "
                f"Input: ({lat}, {lon}), Result: ({result_lat}, {result_lon})"
            )

        # Extract the country code from the result
        country_code = result[0]["cc"]

        # Check if it's already an ISO3 code (3 letters)
        if len(country_code) == 3:
            country = pycountry.countries.get(alpha_3=country_code)
            # Manually set Kosovo to Serbia, since it is not officially recognized by all countries and therefore not in pycountry
            if country_code == "XKX":
                country = pycountry.countries.get(alpha_3="SRB")
        else:
            # It's an ISO2 code, convert it
            country = pycountry.countries.get(alpha_2=country_code)

        if country:
            return country.alpha_3
        else:
            raise ValueError(f"Could not find country with code {country_code} from result {result}.")

    except Exception as e:
        if isinstance(e, ValueError):
            raise
        raise ValueError(f"Error processing coordinates ({lat}, {lon}): {str(e)}")


def frange(start, stop, step):
    while start <= stop:
        yield start
        start += step


def iso3_to_latlons_geocoder(iso3: str | list[str], grid_step: float) -> list[tuple[float, float]]:
    """
    Given an ISO3 code or a list of ISO3 codes, return all (lat, lon) pairs on a global grid that map to this/these ISO3(s) using reverse_geocoder.

    Args:
        iso3: ISO3 country code (e.g., "USA") or list of ISO3 country codes (e.g., ["USA", "CAN"])
        grid_step: Step size for the grid in degrees (e.g., 0.25)

    Returns:
        List of (lat, lon) tuples belonging to the country or countries
    """
    grid_cells = []
    lat_range = [round(x, 6) for x in frange(-90, 90, grid_step)]
    lon_range = [round(x, 6) for x in frange(-180, 180, grid_step)]
    # Create meshgrid of all lat/lon combinations
    lat_mesh, lon_mesh = np.meshgrid(lat_range, lon_range, indexing="ij")
    lat_flat = lat_mesh.ravel()
    lon_flat = lon_mesh.ravel()

    # Use search and pycountry in a vectorized way
    def get_iso3(lat, lon):
        try:
            result = search((lat, lon))[0]
            cc = result.get("cc")
            if cc and pycountry.countries.get(alpha_3=cc):
                country = pycountry.countries.get(alpha_3=cc)
                if hasattr(country, "alpha_3"):
                    if isinstance(iso3, str):
                        if country.alpha_3 == iso3:
                            return (lat, lon)
                    elif isinstance(iso3, list):
                        if country.alpha_3 in iso3:
                            return (lat, lon)
            return None
        except Exception:
            return None

    coords = np.array(
        xr.apply_ufunc(
            get_iso3,
            xr.DataArray(lat_flat, dims="points"),
            xr.DataArray(lon_flat, dims="points"),
            vectorize=True,
            output_dtypes=[object],
        )
    )
    grid_cells = [c for c in coords if c is not None]
    return grid_cells
