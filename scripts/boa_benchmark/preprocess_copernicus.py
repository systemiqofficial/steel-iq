"""One-time preprocessing: concatenate a full year of Copernicus C3S Energy hourly
capacity-factor data into two compact global NetCDF files (solar, onshore wind).

`data_copernicus/` only ships full years as 12 monthly zip archives
(`sis-energy-global-reanalysis_{year}_{mm}_SPF_WOF_WON.zip`), each containing three
~6 GB (float64) global grids (SPV/WOF/WON). Extracting and holding all 12 months of all
three variables at once would need ~200 GB of scratch space, so this script processes one
month at a time: extract only the needed member (SPV, WON -- WOF/offshore is skipped, not
used by this benchmark), load it, cast to float32, append it to the output file (via the
netCDF4 library directly, using an unlimited `time` dimension -- xarray's own `to_netcdf`
does not support extending an existing unlimited dimension), then delete the extracted
file before moving to the next month. At most one month's raw data is ever resident on
disk or in memory at once.

Output: `{output_dir}/spv_{year}.nc` and `{output_dir}/won_{year}.nc`, each a single
`(time, latitude, longitude)` float32 array for the full year (8760 hours; leap years
have Feb 29 dropped before appending, so downstream BOA code -- which only accepts array
lengths in `[HOURS_IN_YEAR, HOURS_IN_YEAR + HOURS_IN_DAY)` -- is satisfied). These are the
reusable artifacts: every later site extraction just opens these two files and does a
cheap `.sel(...)`; this script should only need to run once per year.

Disk footprint: at float32, expect roughly 3 GB/month/variable * 12 = ~36 GB per output
file, ~72 GB total per year. Run this on a volume with enough free space.
"""

import argparse
import calendar
import logging
import tempfile
import zipfile
from pathlib import Path

import netCDF4
import numpy as np
import xarray as xr

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# out_name -> (source variable name in the .nc file, short code used in filenames)
VARIABLES = {
    "solar": ("spv_cf", "SPV"),
    "wind": ("won", "WON"),
}

ZIP_NAME_TEMPLATE = "sis-energy-global-reanalysis_{year}_{month:02d}_SPF_WOF_WON.zip"
PREEXTRACTED_NAME_TEMPLATE = "{code}_{year}_{month:02d}.nc"


def _find_zip_member(zf: zipfile.ZipFile, code: str) -> str:
    matches = [n for n in zf.namelist() if f"_{code}_" in n]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one member containing '_{code}_' in {zf.filename}, found {matches}")
    return matches[0]


def _load_month(
    data_dir: Path, year: int, month: int, code: str, source_var: str, scratch_dir: Path
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load and fully materialize one month's global grid for one variable, trimming
    Feb 29 in leap years so the year always lands on exactly 8760 hours.

    Returns (values [float32, (time, lat, lon)], hours_since_epoch [float64], latitude, longitude).
    """
    preextracted = data_dir / PREEXTRACTED_NAME_TEMPLATE.format(code=code, year=year, month=month)
    extracted_here = None
    if preextracted.exists():
        logger.info(f"Using already-extracted {preextracted.name}")
        path = preextracted
    else:
        zip_path = data_dir / ZIP_NAME_TEMPLATE.format(year=year, month=month)
        if not zip_path.exists():
            raise FileNotFoundError(f"Neither {preextracted.name} nor {zip_path.name} found in {data_dir}")
        logger.info(f"Extracting {code} {year}-{month:02d} from {zip_path.name}")
        with zipfile.ZipFile(zip_path) as zf:
            member = _find_zip_member(zf, code)
            path = Path(zf.extract(member, path=scratch_dir))
        extracted_here = path

    with xr.open_dataset(path) as ds:
        values = ds[source_var].astype("float32").values
        time = ds["time"].values
        lat = ds["latitude"].values
        lon = ds["longitude"].values

    if extracted_here is not None:
        extracted_here.unlink(missing_ok=True)

    if month == 2 and calendar.isleap(year):
        values = values[: 28 * 24]
        time = time[: 28 * 24]

    hours_since_epoch = (time - np.datetime64("1970-01-01")) / np.timedelta64(1, "h")
    return values, hours_since_epoch, lat, lon


def _create_output_file(out_path: Path, out_name: str, lat: np.ndarray, lon: np.ndarray) -> None:
    with netCDF4.Dataset(out_path, "w", format="NETCDF4") as ds:
        ds.createDimension("time", None)
        ds.createDimension("latitude", len(lat))
        ds.createDimension("longitude", len(lon))

        lat_var = ds.createVariable("latitude", "f4", ("latitude",))
        lat_var[:] = lat
        lon_var = ds.createVariable("longitude", "f8", ("longitude",))
        lon_var[:] = lon

        time_var = ds.createVariable("time", "f8", ("time",))
        time_var.units = "hours since 1970-01-01"

        data_var = ds.createVariable(
            out_name, "f4", ("time", "latitude", "longitude"), zlib=True, complevel=4, chunksizes=(24, 90, 180)
        )
        data_var.long_name = f"{out_name} capacity factor"


def _append_month(out_path: Path, out_name: str, values: np.ndarray, hours: np.ndarray) -> None:
    with netCDF4.Dataset(out_path, "a") as ds:
        time_var = ds.variables["time"]
        data_var = ds.variables[out_name]
        n_existing = time_var.shape[0]
        n_new = len(hours)
        time_var[n_existing : n_existing + n_new] = hours
        data_var[n_existing : n_existing + n_new, :, :] = values


def preprocess_year(data_dir: Path, year: int, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"boa_benchmark_{year}_") as scratch:
        scratch_dir = Path(scratch)
        for out_name, (source_var, code) in VARIABLES.items():
            out_path = output_dir / f"{out_name}_{year}.nc"
            if out_path.exists():
                logger.info(f"{out_path} already exists, skipping.")
                continue

            logger.info(f"Building full-year {out_name} ({code}) for {year}")
            tmp_out_path = out_path.with_suffix(".nc.partial")
            tmp_out_path.unlink(missing_ok=True)

            for month in range(1, 13):
                values, hours, lat, lon = _load_month(data_dir, year, month, code, source_var, scratch_dir)
                if month == 1:
                    _create_output_file(tmp_out_path, out_name, lat, lon)
                _append_month(tmp_out_path, out_name, values, hours)
                logger.info(f"  appended {year}-{month:02d} ({len(hours)} hours)")

            tmp_out_path.rename(out_path)
            with netCDF4.Dataset(out_path, "r") as check:
                n_hours = check.dimensions["time"].size
            if n_hours != 8760:
                raise ValueError(f"{out_path} has {n_hours} hours, expected exactly 8760.")
            logger.info(f"Wrote {out_path} ({n_hours} hours).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data_copernicus"))
    parser.add_argument("--year", type=int, required=True, choices=[2015, 2020, 2025])
    parser.add_argument("--output-dir", type=Path, default=Path("scripts/boa_benchmark/preprocessed_data"))
    args = parser.parse_args()

    preprocess_year(args.data_dir, args.year, args.output_dir)
    logger.info("Done.")


if __name__ == "__main__":
    main()
