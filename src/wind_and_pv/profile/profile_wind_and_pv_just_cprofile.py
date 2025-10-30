#!/usr/bin/env python
"""
Profiling script for wind and PV potential calculations.

This script profiles different parts of the calculation process:
1. Data loading / preparation
2. Wind potential calculations
3. PV potential calculations

For each part, it generates:
- Binary profile data for visualization with snakeviz
- Text summary of profiling results
"""

import cProfile
import pstats
import io
import os
from pathlib import Path
import argparse
from contextlib import contextmanager

import xarray as xr

# Import the module to profile
from wind_and_pv.processing import create_offline_cutout


@contextmanager
def profile(output_filename, sort_by="cumulative", lines_to_print=50, strip_dirs=False):
    """Context manager for profiling a code block."""
    profiler = cProfile.Profile()
    profiler.enable()
    yield
    profiler.disable()

    # Save binary profile results
    profiler.dump_stats(f"{output_filename}.prof")

    # Create readable stats
    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats(sort_by)
    if strip_dirs:
        ps.strip_dirs()
    ps.print_stats(lines_to_print)

    # Write stats to file
    with open(f"{output_filename}.txt", "w") as f:
        f.write(s.getvalue())

    # Print stats to console
    print(f"\n--- Profile results for {output_filename} ---")
    print(s.getvalue())


def profile_data_preparation(weather_data, output_dir):
    """Profile the data preparation phase."""
    print("\n=== Profiling Data Preparation ===")

    with profile(os.path.join(output_dir, "profile_data_preparation")):
        cutout = create_offline_cutout(
            weather_data,
            x=slice(-14.0, 2.0),
            y=slice(61.0, 50.0),
            time="2024",
        )
        cutout.prepare(features=["wind", "temperature", "influx"])


def profile_wind_potential(weather_data, output_dir):
    """Profile the wind potential calculation phase."""
    print("\n=== Profiling Wind Potential Calculation ===")

    # First prepare the cutout without profiling
    cutout = create_offline_cutout(
        weather_data,
        x=slice(-14.0, 2.0),
        y=slice(61.0, 50.0),
        time="2024",
    )
    cutout.prepare(features=["wind"])

    # Then profile just the wind potential calculation
    with profile(os.path.join(output_dir, "profile_wind_potential")):
        cutout.wind(turbine="Vestas_V90_3MW")


def profile_pv_potential(weather_data, output_dir):
    """Profile the PV potential calculation phase."""
    print("\n=== Profiling PV Potential Calculation ===")

    # First prepare the cutout without profiling
    cutout = create_offline_cutout(
        weather_data,
        x=slice(-14.0, 2.0),
        y=slice(61.0, 50.0),
        time="2024",
    )
    cutout.prepare(features=["temperature", "influx"])

    # Then profile just the PV potential calculation
    with profile(os.path.join(output_dir, "profile_pv_potential")):
        cutout.pv(
            panel="CSi",
            orientation={"slope": 30.0, "azimuth": 180.0},
        )


def main():
    parser = argparse.ArgumentParser(description="Profile wind and PV potential calculations")
    parser.add_argument("weather_data", help="Path to the weather data file")
    parser.add_argument("--output-dir", default="profiles", help="Directory to store profile outputs")
    parser.add_argument(
        "--parts", choices=["all", "preparation", "wind", "pv"], default="all", help="Which part to profile"
    )

    args = parser.parse_args()

    # Create output directory if it doesn't exist
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    print(f"Profiling with weather data: {args.weather_data}")
    print(f"Output directory: {output_dir}")

    # Profile the requested parts
    weather_data = xr.open_dataset(args.weather_data)
    if args.parts in ["all", "preparation"]:
        profile_data_preparation(weather_data, output_dir)

    if args.parts in ["all", "wind"]:
        profile_wind_potential(weather_data, output_dir)

    if args.parts in ["all", "pv"]:
        profile_pv_potential(weather_data, output_dir)

    print("\nProfiling complete!")
    print(f"Binary profiles saved in {output_dir} directory with .prof extension")
    print(f"Text summaries saved in {output_dir} directory with .txt extension")
    print("\nTo visualize the profiles with snakeviz, run:")
    for part in ["data_preparation", "wind_potential", "pv_potential"]:
        if args.parts == "all" or args.parts in part:
            print(f"  snakeviz {output_dir}/profile_{part}.prof")


if __name__ == "__main__":
    main()
