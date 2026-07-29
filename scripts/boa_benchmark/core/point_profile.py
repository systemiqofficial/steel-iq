"""Fast, repeated-use extraction of one grid cell's hourly CF profile from the
consolidated `solar_{year}.nc`/`wind_{year}.nc` files built once by
`preprocessing/preprocess_copernicus.py`. No unzip work happens here -- just a lazy `.sel(...)` on the
already-consolidated files, with the tiny (8760,) result cached to disk so repeat
benchmark runs don't even reopen those files.
"""

from pathlib import Path

import numpy as np
import xarray as xr


def _to_0_360(lon: float) -> float:
    return lon % 360


def load_point_profile(
    data_dir: Path,
    year: int,
    lat: float,
    lon: float,
    cache_dir: Path,
) -> dict[str, np.ndarray]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"point_{year}_{lat:.3f}_{lon:.3f}.npz"
    if cache_path.exists():
        cached = np.load(cache_path)
        return {"solar": cached["solar"], "wind": cached["wind"]}

    lon_0_360 = _to_0_360(lon)
    profile = {}
    for tech, fname in [("solar", f"solar_{year}.nc"), ("wind", f"wind_{year}.nc")]:
        path = data_dir / fname
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Run preprocess_copernicus.py --year {year} first.")
        with xr.open_dataset(path) as ds:
            profile[tech] = ds[tech].sel(latitude=lat, longitude=lon_0_360, method="nearest").values.astype("float64")

    np.savez(cache_path, solar=profile["solar"], wind=profile["wind"])
    return profile
