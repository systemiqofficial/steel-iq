"""One-time exploratory helper: propose a shortlist of candidate grid cells spanning
distinct wind/solar regimes, for the user to hand-pick 5 sites from and assign a
region name + cost-region to (writing the result to `sites.yaml`).

Land/siting suitability comes directly from Copernicus C3S Energy's own exclusion masks
(`data_copernicus/masks/SPVM_mask.nc`, `WPM_mask.nc` -- 1=excluded water/protected/polar/
high-slope area, 0=suitable), not a steelo-coupled land-mask pipeline: standalone by
construction.

Requires `preprocess_copernicus.py --year {year}` to have been run first.
"""

import argparse
from pathlib import Path

import numpy as np
import xarray as xr


def _to_pm180(lon_0_360: np.ndarray) -> np.ndarray:
    return np.where(lon_0_360 > 180, lon_0_360 - 360, lon_0_360)


def compute_annual_stats(data_dir: Path, year: int) -> xr.Dataset:
    solar = xr.open_dataset(data_dir / f"solar_{year}.nc", chunks={"time": -1, "latitude": 90, "longitude": 180})[
        "solar"
    ]
    wind = xr.open_dataset(data_dir / f"wind_{year}.nc", chunks={"time": -1, "latitude": 90, "longitude": 180})["wind"]

    stats = xr.Dataset(
        {
            "solar_mean": solar.mean("time"),
            "solar_std": solar.std("time"),
            "wind_mean": wind.mean("time"),
            "wind_std": wind.std("time"),
        }
    ).compute()
    return stats


def apply_land_mask(stats: xr.Dataset, masks_dir: Path) -> xr.Dataset:
    pv_mask = xr.open_dataset(masks_dir / "SPVM_mask.nc")["PVmask"].sel(
        latitude=stats.latitude, longitude=stats.longitude, method="nearest"
    )
    wp_mask = xr.open_dataset(masks_dir / "WPM_mask.nc")["wp_mask"].sel(
        latitude=stats.latitude, longitude=stats.longitude, method="nearest"
    )
    # Suitable for at least one of solar or wind (0 = not excluded).
    suitable = (pv_mask.values == 0) | (wp_mask.values == 0)
    return stats.where(xr.DataArray(suitable, dims=["latitude", "longitude"], coords=stats.coords))


def propose_candidates(stats: xr.Dataset, n_per_bucket: int = 4) -> list[dict]:
    df = stats.to_dataframe().dropna(subset=["solar_mean", "wind_mean"]).reset_index()
    df = df[(df["solar_mean"] > 0.01) | (df["wind_mean"] > 0.01)]  # drop residual zero-potential cells

    df["solar_rank"] = df["solar_mean"].rank(pct=True)
    df["wind_rank"] = df["wind_mean"].rank(pct=True)
    df["seasonality"] = df["solar_std"].rank(pct=True) + df["wind_std"].rank(pct=True)
    df["balance"] = -(df["solar_rank"] - df["wind_rank"]).abs()
    df["both_low"] = -(df["solar_mean"] + df["wind_mean"])

    buckets = {
        "high_wind_low_solar": df[df["solar_rank"] < 0.4].nlargest(n_per_bucket, "wind_mean"),
        "high_solar_low_wind": df[df["wind_rank"] < 0.4].nlargest(n_per_bucket, "solar_mean"),
        "balanced_mid": df[(df["solar_rank"] > 0.4) & (df["wind_rank"] > 0.4)].nlargest(n_per_bucket, "balance"),
        "high_seasonality": df.nlargest(n_per_bucket, "seasonality"),
        "low_both_hard_case": df[(df["solar_mean"] > 0.01) & (df["wind_mean"] > 0.01)].nlargest(
            n_per_bucket, "both_low"
        ),
    }

    candidates = []
    for bucket, rows in buckets.items():
        for _, row in rows.iterrows():
            candidates.append(
                {
                    "bucket": bucket,
                    "lat": round(float(row["latitude"]), 3),
                    "lon_0_360": round(float(row["longitude"]), 3),
                    "lon": round(float(_to_pm180(np.array(row["longitude"]))), 3),
                    "solar_mean_cf": round(float(row["solar_mean"]), 3),
                    "wind_mean_cf": round(float(row["wind_mean"]), 3),
                }
            )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("scripts/boa_benchmark/preprocessed_data"))
    parser.add_argument("--masks-dir", type=Path, default=Path("data_copernicus/masks"))
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--n-per-bucket", type=int, default=4)
    args = parser.parse_args()

    stats = compute_annual_stats(args.data_dir, args.year)
    stats = apply_land_mask(stats, args.masks_dir)
    candidates = propose_candidates(stats, args.n_per_bucket)

    print(f"{'bucket':<22}{'lat':>8}{'lon':>9}{'solar_cf':>10}{'wind_cf':>9}")
    for c in candidates:
        print(f"{c['bucket']:<22}{c['lat']:>8}{c['lon']:>9}{c['solar_mean_cf']:>10}{c['wind_mean_cf']:>9}")


if __name__ == "__main__":
    main()
