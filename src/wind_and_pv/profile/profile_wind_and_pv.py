#!/usr/bin/env python
import argparse
import os
import io
import cProfile
import pstats
from contextlib import contextmanager
from pathlib import Path
import xarray as xr
import line_profiler
from wind_and_pv.processing import create_offline_cutout


@contextmanager
def profile(output_filename, sort_by="cumulative", lines_to_print=50, strip_dirs=False):
    profiler = cProfile.Profile()
    profiler.enable()
    yield
    profiler.disable()
    profiler.dump_stats(f"{output_filename}.prof")
    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats(sort_by)
    if strip_dirs:
        ps.strip_dirs()
    ps.print_stats(lines_to_print)
    with open(f"{output_filename}.txt", "w") as f:
        f.write(s.getvalue())
    print(f"\n--- Profile results for {output_filename} ---")
    print(s.getvalue())


def line_profile_run(func, *args, **kwargs):
    lp = line_profiler.LineProfiler(func)
    result = lp.runcall(func, *args, **kwargs)
    s = io.StringIO()
    lp.print_stats(stream=s)
    print(s.getvalue())
    return result


def profile_data_preparation_line(weather_data, output_dir):
    print("\n=== Line Profiling Data Preparation ===")
    cutout = create_offline_cutout(weather_data, x=slice(-14.0, 2.0), y=slice(61.0, 50.0), time="2024")
    line_profile_run(cutout.prepare, features=["wind", "temperature", "influx"])


def profile_wind_potential_line(weather_data, output_dir):
    print("\n=== Line Profiling Wind Potential Calculation ===")
    cutout = create_offline_cutout(weather_data, x=slice(-14.0, 2.0), y=slice(61.0, 50.0), time="2024")
    cutout.prepare(features=["wind"])
    line_profile_run(cutout.wind, turbine="Vestas_V90_3MW")


def profile_pv_potential_line(weather_data, output_dir):
    print("\n=== Line Profiling PV Potential Calculation ===")
    cutout = create_offline_cutout(weather_data, x=slice(-14.0, 2.0), y=slice(61.0, 50.0), time="2024")
    cutout.prepare(features=["temperature", "influx"])
    line_profile_run(cutout.pv, panel="CSi", orientation={"slope": 30.0, "azimuth": 180.0})


def main():
    parser = argparse.ArgumentParser(description="Profile wind and PV potential calculations")
    parser.add_argument("weather_data", help="Path to the weather data file")
    parser.add_argument("--output-dir", default="profiles", help="Directory to store profile outputs")
    parser.add_argument(
        "--parts", choices=["all", "preparation", "wind", "pv"], default="all", help="Which part to profile"
    )
    parser.add_argument("--line", action="store_true", help="Use line profiler instead of cProfile")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    print(f"Profiling with weather data: {args.weather_data}")
    print(f"Output directory: {output_dir}")

    weather_data = xr.open_dataset(args.weather_data)
    if args.line:
        if args.parts in ["all", "preparation"]:
            profile_data_preparation_line(weather_data, output_dir)
        if args.parts in ["all", "wind"]:
            profile_wind_potential_line(weather_data, output_dir)
        if args.parts in ["all", "pv"]:
            profile_pv_potential_line(weather_data, output_dir)
    else:
        if args.parts in ["all", "preparation"]:
            with profile(os.path.join(args.output_dir, "profile_data_preparation")):
                cutout = create_offline_cutout(weather_data, x=slice(-14.0, 2.0), y=slice(61.0, 50.0), time="2024")
                cutout.prepare(features=["wind", "temperature", "influx"])
        if args.parts in ["all", "wind"]:
            cutout = create_offline_cutout(weather_data, x=slice(-14.0, 2.0), y=slice(61.0, 50.0), time="2024")
            cutout.prepare(features=["wind"])
            with profile(os.path.join(args.output_dir, "profile_wind_potential")):
                cutout.wind(turbine="Vestas_V90_3MW")
        if args.parts in ["all", "pv"]:
            cutout = create_offline_cutout(weather_data, x=slice(-14.0, 2.0), y=slice(61.0, 50.0), time="2024")
            cutout.prepare(features=["temperature", "influx"])
            with profile(os.path.join(args.output_dir, "profile_pv_potential")):
                cutout.pv(panel="CSi", orientation={"slope": 30.0, "azimuth": 180.0})
    weather_data.close()
    print("\nProfiling complete!")
    if not args.line:
        print(f"Binary profiles saved in {output_dir} directory with .prof extension")
        print(f"Text summaries saved in {output_dir} directory with .txt extension")
        print("\nTo visualize the profiles with snakeviz, run:")
        for part in ["data_preparation", "wind_potential", "pv_potential"]:
            if args.parts == "all" or args.parts in part:
                print(f"  snakeviz {output_dir}/profile_{part}.prof")


if __name__ == "__main__":
    main()
