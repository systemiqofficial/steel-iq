from pathlib import Path

from steelo.config import settings


class GeoChunk:
    """
    Represents a spatial-temporal chunk of weather data.

    Each GeoChunk is defined by a longitude (`x`) and latitude (`y`) slice,
    a time string (e.g., "2024"), and a directory in which chunked data files may be stored.

    Attributes:
        x (slice): Longitude range of the chunk.
        y (slice): Latitude range of the chunk.
        time (str): Time period for the chunk (e.g., "2024").
        chunks_dir (Path): Base directory for storing/retrieving chunk files.
    """

    def __init__(self, *, x: slice, y: slice, time: str, chunks_dir: Path) -> None:
        self.x = x
        self.y = y
        self.time = time
        self.chunks_dir = chunks_dir

    def get_prefixed_name(self, prefix: str) -> str:
        x, y = self.x, self.y
        x_start = str(int(x.start))
        x_stop = str(int(x.stop))
        y_start = str(int(y.start))
        y_stop = str(int(y.stop))
        return f"{prefix}_{x_start}_{x_stop}_{y_start}_{y_stop}.nc"

    def get_path(self, prefix: str) -> Path:
        return self.chunks_dir / self.get_prefixed_name(prefix)

    @property
    def west(self) -> float:
        return min(self.x.start, self.x.stop)

    @property
    def east(self) -> float:
        return max(self.x.start, self.x.stop)

    @property
    def south(self) -> float:
        return min(self.y.start, self.y.stop)

    @property
    def north(self) -> float:
        return max(self.y.start, self.y.stop)

    def to_bounds_dict(self) -> dict:
        return {"N": self.north, "S": self.south, "E": self.east, "W": self.west}

    @classmethod
    def from_bounds_dict(cls, bounds: dict, time: str, chunks_dir: Path) -> "GeoChunk":
        x = slice(bounds["W"], bounds["E"])
        y = slice(bounds["N"], bounds["S"])
        return cls(x=x, y=y, time=time, chunks_dir=chunks_dir)

    def get_tech_supply_path(self, tech: str) -> Path:
        return self.get_path(f"{tech}_supply")


class GeoChunker:
    """
    Splits a global spatial-temporal domain into smaller rectangular GeoChunks.

    This class is useful for managing and processing large weather datasets
    by dividing them into manageable pieces for parallel or batched computation.

    Attributes:
        x_chunk_size (float): Width of each chunk in longitude degrees.
        y_chunk_size (float): Height of each chunk in latitude degrees.
        time (str): Time range to assign to all chunks (e.g., "2024").
        chunks_dir (Path): Directory to use for storing or locating chunk files.
        x_min (float): Minimum longitude of the domain.
        x_max (float): Maximum longitude of the domain.
        y_min (float): Minimum latitude of the domain.
        y_max (float): Maximum latitude of the domain.
        delta (float): Small offset to prevent overlap between chunk edges.
    """

    def __init__(
        self,
        *,
        x_chunk_size: float,
        y_chunk_size: float,
        time: str,
        chunks_dir: Path = settings.project_root / "data" / "weather_data" / "chunks",
        x_min: float = -180,
        x_max: float = 180,
        y_min: float = -90,
        y_max: float = 90,
    ) -> None:
        self.x_chunk_size = x_chunk_size
        self.y_chunk_size = y_chunk_size
        self.time = time
        self.chunks_dir = chunks_dir
        self.x_min = x_min
        self.x_max = x_max
        self.y_min = y_min
        self.y_max = y_max

    @classmethod
    def from_weather_data_path(
        cls, weather_data_path: Path, x_chunk_size: float, y_chunk_size: float, time: str, chunks_dir: Path
    ) -> "GeoChunker":
        import xarray as xr

        weather_data = xr.open_dataset(weather_data_path, engine="netcdf4")
        corners = {
            "x_min": float(weather_data["longitude"].min()),
            "x_max": float(weather_data["longitude"].max()),
            "y_min": float(weather_data["latitude"].min()),
            "y_max": float(weather_data["latitude"].max()),
        }
        return cls(x_chunk_size=x_chunk_size, y_chunk_size=y_chunk_size, time=time, chunks_dir=chunks_dir, **corners)

    def _create_slices(self, min_val, max_val, chunk_size, delta=1e-6):
        slices = []
        current = min_val
        while current < max_val:
            next_val = current + chunk_size
            if next_val < max_val:
                slices.append(slice(current, next_val - delta))
            else:
                slices.append(slice(current, max_val))
            current += chunk_size
        return slices

    @property
    def chunks(self) -> list[GeoChunk]:
        # build chunks in longitude (assumed increasing)
        x_slices = self._create_slices(self.x_min, self.x_max, chunk_size=self.x_chunk_size)

        # For latitude, if the coordinates are descending (from y_max down to y_min),
        # you can reverse the logic or flip the slice endpoints:
        y_slices = self._create_slices(self.y_min, self.y_max, chunk_size=self.y_chunk_size, delta=1e-6)

        # then reverse the order since y is typically descending:
        y_slices = [slice(s.stop, s.start) for s in y_slices]

        chunks: list[GeoChunk] = []
        for xs in x_slices:
            for ys in y_slices:
                chunks.append(GeoChunk(x=xs, y=ys, time=self.time, chunks_dir=self.chunks_dir))

        return chunks
