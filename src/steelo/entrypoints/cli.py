"""
Command line interface for the steelo package.
"""

import os
import sys
import argparse
from pathlib import Path
import logging
import subprocess
import shutil
import json
from datetime import datetime
from typing import Dict
from collections import defaultdict

from rich.console import Console
from rich.table import Table

from ..domain import Year

from ..simulation import SimulationConfig
from ..bootstrap import bootstrap_simulation
from ..utils.symlink_manager import update_data_symlink, update_output_symlink, setup_legacy_symlinks


def run_full_simulation() -> str:
    """
    Run a full simulation from start to end year with the
    configuration provided via command line arguments.
    """
    console = Console()

    # Initialize the argument parser
    parser = argparse.ArgumentParser(description="Run a full steel model simulation.")

    # Add the options for the simulation
    parser.add_argument("--start-year", type=int, default=2025, help="The year to start the simulation (default: 2025)")
    parser.add_argument("--end-year", type=int, default=2050, help="The year to end the simulation (default: 2050)")
    parser.add_argument(
        "--plants-json",
        type=str,
        default=None,
        help="Path to the plants JSON file (default: ./data/fixtures/plants.json)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Base output directory for simulation results (default: ./outputs)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="Path for the output JSON file (default: <output-dir>/pam_simulation_run.json)",
    )
    parser.add_argument(
        "--demand-excel",
        type=str,
        default=None,
        help="Path to the demand excel file (default: ./data/fixtures/2025_05_27 Demand outputs for trade module.xlsx)",
    )
    parser.add_argument(
        "--demand-sheet",
        type=str,
        default="Steel_Demand_Chris Bataille",
        help="Sheet name in the demand excel file (default: 'Steel_Demand_Chris Bataille')",
    )
    parser.add_argument(
        "--location-csv",
        type=str,
        default=None,
        help="Path to the location CSV file (default: ./data/fixtures/countries.csv)",
    )
    parser.add_argument(
        "--cost-of-x-csv",
        type=str,
        default=None,
        help="Path to the cost of x CSV file (default: ./data/fixtures/cost_of_x.json)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="WARNING",
        help="Set the logging level (default: WARNING)",
    )
    parser.add_argument(
        "--resume-from-year",
        type=int,
        default=None,
        help="Resume simulation from a checkpoint at the specified year",
    )

    # New arguments for caching and paths
    parser.add_argument(
        "--master-excel", type=str, help="Path to master Excel file (uses default or downloads if not provided)"
    )
    parser.add_argument("--no-cache", action="store_true", help="Disable preparation cache")
    parser.add_argument("--force-refresh", action="store_true", help="Force data re-preparation even if cached")
    parser.add_argument(
        "--steelo-home",
        type=str,
        default=os.environ.get("STEELO_HOME", str(Path.home() / ".steelo")),
        help="STEELO_HOME directory (default: ~/.steelo or $STEELO_HOME)",
    )
    parser.add_argument("--cache-stats", action="store_true", help="Show cache statistics and exit")
    parser.add_argument("--clear-cache", action="store_true", help="Clear preparation cache and exit")
    parser.add_argument(
        "--baseload-power-sim-dir",
        type=str,
        default=None,
        help="Path to BOA-generated baseload power simulation output directory (overrides default)",
    )

    # Parse the command-line arguments
    try:
        args = parser.parse_args()

        # Setup directories
        steelo_home = Path(args.steelo_home)
        steelo_home.mkdir(parents=True, exist_ok=True)

        # Handle cache operations
        from ..data.cache_manager import DataPreparationCache

        cache_manager = DataPreparationCache(steelo_home / "preparation_cache")

        if args.cache_stats:
            stats = cache_manager.get_cache_stats()
            console.print("[blue]Cache Statistics:[/blue]")
            for key, value in stats.items():
                console.print(f"  {key}: {value}")
            return "Cache stats displayed"

        if args.clear_cache:
            removed = cache_manager.clear_cache()
            console.print(f"[green]Cleared {removed} cached preparations[/green]")
            return "Cache cleared"

        # Prepare output directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_base = steelo_home / "output"
        output_base.mkdir(exist_ok=True)

        # Unique output directory for this simulation
        output_dir = output_base / f"sim_{timestamp}"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Map log level string to logging constants
        log_levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        log_level = log_levels[args.log_level]

        # Prepare data with caching
        from ..data import DataPreparationService

        prep_service = DataPreparationService(cache_manager=cache_manager, use_cache=not args.no_cache)

        # Determine master Excel path first
        master_excel_path = None
        if args.master_excel:
            master_excel_path = Path(args.master_excel)
            if not master_excel_path.exists():
                console.print(f"[red]Master Excel file not found: {master_excel_path}[/red]")
                sys.exit(1)

        # First resolve master Excel path if not provided
        if not master_excel_path:
            console.print("[blue]Resolving master Excel file...[/blue]")
            # Download master Excel to get a consistent path
            from ..data import DataManager

            data_manager = DataManager()
            data_manager.download_package("master-input", force=False)
            cache_path = data_manager.get_package_path("master-input")
            if cache_path and cache_path.exists():
                master_excel_path = cache_path / "master_input.xlsx"
                if not master_excel_path.exists():
                    console.print(f"[red]Master Excel file not found in package: {cache_path}[/red]")
                    sys.exit(1)
            else:
                console.print("[red]Failed to download master Excel package[/red]")
                sys.exit(1)

        # Check if we can use cached data directly
        cached_data_dir = None
        if not args.no_cache and not args.force_refresh:
            cached_data_dir = cache_manager.get_cached_preparation(master_excel_path)
            if cached_data_dir:
                # cached_data_dir already points to the data directory
                console.print(f"[blue]Using cached preparation from:[/blue] {cached_data_dir}")

        # Decide whether to use cache or prepare fresh data
        if cached_data_dir:
            # Use cached data directory directly
            actual_data_dir = cached_data_dir

            # Create symlink to cached data
            try:
                update_data_symlink(steelo_home, actual_data_dir)
                console.print(f"[green]Created data symlink:[/green] {steelo_home}/data -> {actual_data_dir}")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not create data symlink: {e}[/yellow]")

            # Create simulation config
            config_kwargs = {
                "start_year": Year(args.start_year),
                "end_year": Year(args.end_year),
                "data_dir": actual_data_dir,
                "output_dir": output_dir,
                "master_excel_path": master_excel_path,
                "demand_sheet_name": args.demand_sheet,
                "log_level": log_level,
            }

            # Add custom baseload_power_sim_dir if provided
            if args.baseload_power_sim_dir:
                baseload_dir = Path(args.baseload_power_sim_dir)
                if not baseload_dir.exists():
                    console.print(f"[red]Error: Baseload power simulation directory not found: {baseload_dir}[/red]")
                    sys.exit(1)
                config_kwargs["baseload_power_sim_dir"] = baseload_dir
                console.print(f"[blue]Using custom baseload power simulation directory: {baseload_dir}[/blue]")

            config = SimulationConfig.from_data_directory(**config_kwargs)

            # Save config and metadata
            config_dict = {k: str(v) if isinstance(v, Path) else v for k, v in config.__dict__.items()}
            config_path = output_dir / "simulation_config.json"
            config_path.write_text(json.dumps(config_dict, indent=2, default=str))

            prep_metadata = {
                "preparation_time": datetime.now().isoformat(),
                "cache_used": True,
                "master_excel": str(master_excel_path),
                "cached_from": str(cached_data_dir),
            }
            (output_dir / "preparation_metadata.json").write_text(json.dumps(prep_metadata, indent=2))

        else:
            # Need to prepare fresh data - use a persistent directory
            console.print("[blue]Preparing data...[/blue]")

            # Create a unique directory for this preparation that won't be deleted
            prep_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            prep_dir = steelo_home / "temp_prep" / f"prep_{prep_timestamp}"
            prep_dir.mkdir(parents=True, exist_ok=True)

            try:
                # Prepare data
                prep_result = prep_service.prepare_data(
                    output_dir=prep_dir,
                    master_excel_path=master_excel_path,
                    force_refresh=args.force_refresh,
                    verbose=True,
                )

                actual_data_dir = prep_dir

                # Create symlink to prepared data
                try:
                    update_data_symlink(steelo_home, actual_data_dir)
                    console.print(f"[green]Created data symlink:[/green] {steelo_home}/data -> {actual_data_dir}")
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not create data symlink: {e}[/yellow]")

                # Create simulation config using the prepared data directory
                config_kwargs = {
                    "start_year": Year(args.start_year),
                    "end_year": Year(args.end_year),
                    "data_dir": actual_data_dir,
                    "output_dir": output_dir,
                    "master_excel_path": master_excel_path,
                    "demand_sheet_name": args.demand_sheet,
                    "log_level": log_level,
                }

                # Add custom baseload_power_sim_dir if provided
                if args.baseload_power_sim_dir:
                    baseload_dir = Path(args.baseload_power_sim_dir)
                    if not baseload_dir.exists():
                        console.print(
                            f"[red]Error: Baseload power simulation directory not found: {baseload_dir}[/red]"
                        )
                        sys.exit(1)
                    config_kwargs["baseload_power_sim_dir"] = baseload_dir
                    console.print(f"[blue]Using custom baseload power simulation directory: {baseload_dir}[/blue]")

                config = SimulationConfig.from_data_directory(**config_kwargs)

                # Save config and metadata
                config_dict = {k: str(v) if isinstance(v, Path) else v for k, v in config.__dict__.items()}
                config_path = output_dir / "simulation_config.json"
                config_path.write_text(json.dumps(config_dict, indent=2, default=str))

                prep_metadata = {
                    "preparation_time": datetime.now().isoformat(),
                    "cache_used": False,
                    "master_excel": str(master_excel_path),
                    "preparation_duration": prep_result.total_duration,
                    "files_prepared": len(prep_result.files),
                    "temp_prep_dir": str(prep_dir),
                }
                (output_dir / "preparation_metadata.json").write_text(json.dumps(prep_metadata, indent=2))

                # Clean up old temp_prep directories (keep last 5)
                temp_prep_base = steelo_home / "temp_prep"
                if temp_prep_base.exists():
                    prep_dirs = sorted(
                        [d for d in temp_prep_base.iterdir() if d.is_dir() and d.name.startswith("prep_")]
                    )
                    for old_dir in prep_dirs[:-5]:  # Keep last 5
                        try:
                            shutil.rmtree(old_dir)
                        except Exception:
                            pass  # Ignore cleanup errors

            except Exception as e:
                console.print(f"[red]Data preparation failed: {e}[/red]")
                # Clean up the prep directory on failure
                if prep_dir.exists():
                    shutil.rmtree(prep_dir)
                sys.exit(1)

        # If resuming from checkpoint, override start year
        if args.resume_from_year:
            config.start_year = Year(args.resume_from_year)
            console.print(f"[blue]Resuming simulation from checkpoint at year {args.resume_from_year}[/blue]")

        # Run simulation
        console.print(f"[blue]Running simulation in: {output_dir}[/blue]")
        try:
            runner = bootstrap_simulation(config)
            runner.run()
        except Exception as e:
            console.print(f"[red]Simulation failed: {e}[/red]")
            import traceback

            console.print(f"[red]Traceback: {traceback.format_exc()}[/red]")
            sys.exit(1)

        # Create "latest" symlink within output directory
        latest_link = output_base / "latest"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(output_dir.name)

        # Create output symlink at steelo_home level
        try:
            update_output_symlink(steelo_home, output_dir)
            console.print(f"[green]Created output symlink:[/green] {steelo_home}/output_latest -> {output_dir}")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not create output symlink: {e}[/yellow]")

        # Create legacy symlinks in project root for backward compatibility
        try:
            project_root = Path.cwd()
            setup_legacy_symlinks(project_root, steelo_home)
            console.print("[green]Created legacy symlinks in project root[/green]")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not create legacy symlinks: {e}[/yellow]")

        console.print("[green]Simulation completed![/green]")
        console.print(f"[green]Results in:[/green] {output_dir}")
        console.print(f"[green]Latest symlink:[/green] {latest_link}")

        return f"Simulation completed! Results in: {output_dir}"

    except argparse.ArgumentError as e:
        console.print(f"[red]Argument parsing error: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        import traceback

        console.print(f"[red]Traceback: {traceback.format_exc()}[/red]")
        sys.exit(1)


def count_lines_of_code() -> str:
    """
    Count lines of code in the project using cloc or pure Python fallback.
    Shows both total count and directory breakdown.
    """
    console = Console()

    # Check if cloc is available
    if shutil.which("cloc"):
        return _count_with_cloc(console)
    else:
        console.print("[yellow]cloc not found, using pure Python fallback[/yellow]")
        return _count_with_python(console)


def _count_with_cloc(console: Console) -> str:
    """Count lines of code using cloc."""
    # First get the overall summary
    summary_cmd = [
        "cloc",
        ".",
        "--include-ext=py,html,js",
        "--exclude-dir=.git,.venv,temp-venv,node_modules,__pycache__,build,dist,staticfiles,docs,htmlcov,outputs,notebooks,django-bundle,media,python-standalone,debug-venv-1",
        "--not-match-d=.*migrations.*",
    ]

    # Then get the by-file breakdown for directory grouping
    detail_cmd = summary_cmd + ["--by-file-by-lang"]

    try:
        # Get summary first
        summary_result = subprocess.run(summary_cmd, capture_output=True, text=True, check=True)
        console.print("[blue]Overall Summary:[/blue]")
        console.print(summary_result.stdout)

        # Then get directory breakdown
        detail_result = subprocess.run(detail_cmd, capture_output=True, text=True, check=True)
        lines = detail_result.stdout.strip().split("\n")
        _display_cloc_by_directory(console, lines)

        return "Lines of code counted successfully with cloc!"

    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error running cloc: {e}[/red]")
        console.print(f"[red]stderr: {e.stderr}[/red]")
        return "Error running cloc"


def _count_with_python(console: Console) -> str:
    """Count lines of code using pure Python."""
    project_root = Path(".")

    # File extensions to count
    extensions = {".py", ".html", ".js"}

    # Directories to exclude
    exclude_dirs = {
        ".git",
        ".venv",
        "temp-venv",
        "node_modules",
        "__pycache__",
        "build",
        "dist",
        "staticfiles",
        "docs",
        "htmlcov",
        "outputs",
        "notebooks",
        "django-bundle",
        "media",
        "python-standalone",
        "debug-venv-1",
    }

    total_files = 0
    total_lines = 0
    dir_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    ext_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})

    for file_path in project_root.rglob("*"):
        # Skip if not a file
        if not file_path.is_file():
            continue

        # Skip if wrong extension
        if file_path.suffix not in extensions:
            continue

        # Skip if in excluded directory or migrations
        if any(exclude_dir in file_path.parts for exclude_dir in exclude_dirs):
            continue

        if "migrations" in str(file_path):
            continue

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = len(f.readlines())

            total_files += 1
            total_lines += lines

            # Track by extension
            ext = file_path.suffix
            ext_stats[ext]["files"] += 1
            ext_stats[ext]["lines"] += lines

            # Get directory relative to project root
            rel_path = file_path.relative_to(project_root)
            if rel_path.parent == Path("."):
                dir_name = "."
            else:
                # Show contextual path based on structure
                parts = rel_path.parts

                # Special handling for common project structures
                if parts[0] == "src":
                    if len(parts) >= 2:
                        dir_name = f"src/{parts[1]}"
                    else:
                        dir_name = "src"
                elif parts[0] == "tests":
                    if len(parts) >= 2:
                        dir_name = f"tests/{parts[1]}"
                    else:
                        dir_name = "tests"
                else:
                    # For other directories, show first level
                    dir_name = str(parts[0])

            dir_stats[dir_name]["files"] += 1
            dir_stats[dir_name]["lines"] += lines

        except Exception as e:
            console.print(f"[yellow]Warning: Could not read {file_path}: {e}[/yellow]")
            continue

    # Display overall summary first
    console.print("[blue]Overall Summary:[/blue]")
    console.print(f"[green]Total files:[/green] {total_files}")
    console.print(f"[green]Total lines:[/green] {total_lines}")

    # Show breakdown by file type
    console.print("\n[blue]By Language:[/blue]")
    for ext in sorted(ext_stats.keys()):
        stats = ext_stats[ext]
        lang_name = {"py": "Python", "html": "HTML", "js": "JavaScript"}.get(ext[1:], ext[1:])
        console.print(f"  {lang_name}: {stats['files']} files, {stats['lines']} lines")

    # Then show directory breakdown
    console.print()
    _display_directory_table(console, dir_stats)

    return "Lines of code counted successfully with Python!"


def _display_cloc_by_directory(console: Console, lines: list[str]) -> None:
    """Display cloc results grouped by directory."""
    dir_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})

    # Parse cloc --by-file-by-lang output
    for line in lines:
        if (
            not line.strip()
            or line.startswith("github.com/AlDanial/cloc")
            or line.startswith("---")
            or line.startswith("File")
        ):
            continue

        # Look for lines that start with ./
        if line.startswith("./"):
            # Split the line - format is: ./path/to/file.py    blank   comment   code
            parts = line.split()
            if len(parts) >= 4:
                file_path = parts[0]  # First column is the file path
                try:
                    # Last column is the code lines
                    lines_count = int(parts[-1])

                    # Extract directory - show meaningful path context
                    path_obj = Path(file_path)
                    if len(path_obj.parts) == 1:  # ./file.py (no parent directory)
                        dir_name = "."
                    else:
                        # Show contextual path based on structure
                        # Don't skip anything - use all parts directly
                        path_parts = path_obj.parts

                        # Special handling for common project structures
                        if len(path_parts) >= 2 and path_parts[0] == "src":
                            # For src/*, show src/module_name
                            dir_name = f"src/{path_parts[1]}"
                        elif len(path_parts) >= 2 and path_parts[0] == "tests":
                            # For tests/*, show tests/test_type
                            dir_name = f"tests/{path_parts[1]}"
                        else:
                            # For other directories, show first level
                            dir_name = str(path_parts[0])

                    dir_stats[dir_name]["files"] += 1
                    dir_stats[dir_name]["lines"] += lines_count
                except (ValueError, IndexError):
                    continue

    _display_directory_table(console, dir_stats)


def _display_directory_table(console: Console, dir_stats: dict) -> None:
    """Display directory statistics in a table."""
    table = Table(title="Lines of Code by Directory")
    table.add_column("Directory", style="cyan")
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Lines", justify="right", style="green")

    # Sort by lines (descending)
    sorted_dirs = sorted(dir_stats.items(), key=lambda x: x[1]["lines"], reverse=True)

    for dir_name, stats in sorted_dirs:
        table.add_row(dir_name, str(stats["files"]), str(stats["lines"]))

    console.print(table)


def show_geo_plots() -> None:
    """
    Create geographic plots from simulation output data.

    This CLI entrypoint replaces the old beth_GEO4executable.py script,
    following the clean architecture pattern by accepting configuration
    through command-line arguments.
    """
    import xarray as xr
    from ..simulation import SimulationConfig
    from ..domain.models import PlotPaths
    from ..utilities.plotting import plot_geo_layers

    parser = argparse.ArgumentParser(description="Generate geographic plots from simulation output")
    parser.add_argument("--data-dir", type=Path, required=True, help="Path to the prepared data directory")
    parser.add_argument("--output-dir", type=Path, required=True, help="Path to the output directory")
    parser.add_argument("--year", type=int, default=2025, help="Year to use in plot titles (default: 2025)")

    args = parser.parse_args()

    # Create SimulationConfig from data directory
    config = SimulationConfig.from_data_directory(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        start_year=args.year,
        end_year=args.year,
    )

    # Create PlotPaths object
    plot_paths = PlotPaths(geo_plots_dir=config.geo_plots_dir)

    # Load the datasets
    geo_layers_path = args.output_dir / "geo_priority_kpi.nc"
    lcoh_path = args.output_dir / f"lcoh_map_{args.year}.nc"

    if not geo_layers_path.exists():
        console = Console()
        console.print(f"[red]Error: Could not find geo_priority_kpi.nc in {args.output_dir}[/red]")
        console.print("[yellow]Make sure you've run the simulation first.[/yellow]")
        sys.exit(1)

    # Load data
    dataset = xr.open_dataset(geo_layers_path)
    lcoh_dataset = None
    if lcoh_path.exists():
        lcoh_dataset = xr.open_dataset(lcoh_path)

    # Create plots
    console = Console()
    console.print(f"[green]Creating geographic plots in {config.geo_plots_dir}...[/green]")

    plot_geo_layers(
        dataset=dataset,
        plot_paths=plot_paths,
        year=args.year,
        lcoh_dataset=lcoh_dataset,
    )

    console.print("[green]✓ Geographic plots created successfully![/green]")
