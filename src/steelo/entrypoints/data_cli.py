"""CLI commands for data management."""

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..data import DataManager
from ..data.validation import ExcelValidator
from ..data.recreate import DataRecreator
from ..data.geo_extractor import GeoDataExtractor

logger = logging.getLogger(__name__)
console = Console()


def steelo_data_download():
    """Download required data packages from S3."""
    parser = argparse.ArgumentParser(description="Download required data packages from S3.")
    parser.add_argument(
        "--cache-dir",
        type=str,
        help="Cache directory for downloaded data",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if data exists",
    )
    parser.add_argument(
        "--package",
        type=str,
        help="Download specific package instead of all required",
    )

    try:
        args = parser.parse_args()
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        manager = DataManager(cache_dir=cache_dir)

        if args.package:
            console.print(f"Downloading package: {args.package}")
            manager.download_package(args.package, force=args.force)
        else:
            console.print("Downloading all required data packages...")
            manager.download_required_data(args.force)
        console.print("[green]✓ Download complete[/green]")
        return "Download complete!"
    except Exception as e:
        console.print(f"[red]✗ Download failed: {e}[/red]")
        sys.exit(1)


def steelo_data_list():
    """List available data packages and their status."""
    parser = argparse.ArgumentParser(description="List available data packages and their status.")
    parser.add_argument(
        "--cache-dir",
        type=str,
        help="Cache directory for downloaded data",
    )

    try:
        args = parser.parse_args()
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        manager = DataManager(cache_dir=cache_dir)
        packages = manager.list_packages()

        table = Table(title="Available Data Packages")
        table.add_column("Package", style="cyan")
        table.add_column("Version", style="magenta")
        table.add_column("Size (MB)", justify="right")
        table.add_column("Required", style="yellow")
        table.add_column("Cached", style="green")
        table.add_column("Description")

        for pkg in packages:
            table.add_row(
                pkg["name"],
                pkg["version"],
                str(pkg["size_mb"]),
                "Yes" if pkg["required"] else "No",
                "✓" if pkg["cached"] else "✗",
                pkg["description"],
            )

        console.print(table)
        return "List complete!"
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def steelo_data_verify():
    """Verify integrity of cached data packages."""
    parser = argparse.ArgumentParser(description="Verify integrity of cached data packages.")
    parser.add_argument(
        "--cache-dir",
        type=str,
        help="Cache directory for downloaded data",
    )
    parser.add_argument(
        "--package",
        type=str,
        help="Verify specific package instead of all",
    )

    try:
        args = parser.parse_args()
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        manager = DataManager(cache_dir=cache_dir)

        if args.package:
            console.print(f"Verifying package: {args.package}")
            result = manager.verify_package(args.package)
            if result:
                console.print(f"[green]✓ {args.package} is valid[/green]")
            else:
                console.print(f"[red]✗ {args.package} is corrupted or missing[/red]")
        else:
            console.print("Verifying all cached packages...")
            results = manager.verify_all_packages()
            for package, is_valid in results.items():
                if is_valid:
                    console.print(f"[green]✓ {package}[/green]")
                else:
                    console.print(f"[red]✗ {package}[/red]")

        return "Verification complete!"
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def steelo_data_path():
    """Show paths to data files."""
    parser = argparse.ArgumentParser(description="Show paths to data files.")
    parser.add_argument(
        "--cache-dir",
        type=str,
        help="Cache directory for downloaded data",
    )
    parser.add_argument(
        "file",
        type=str,
        help="File to locate (e.g., 'plants.csv', 'cost_of_x.json')",
    )

    try:
        args = parser.parse_args()
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        manager = DataManager(cache_dir=cache_dir)

        try:
            path = manager.get_data_path(args.file)
            console.print(f"{path}")
            return str(path)
        except ValueError as e:
            console.print(f"[red]✗ {e}[/red]")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def steelo_data_validate():
    """Validate master Excel file."""
    parser = argparse.ArgumentParser(description="Validate master Excel file against expected structure.")
    parser.add_argument(
        "excel_file",
        type=str,
        help="Path to master Excel file to validate",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        help="Cache directory for data packages",
    )

    try:
        args = parser.parse_args()
        excel_path = Path(args.excel_file)

        if not excel_path.exists():
            console.print(f"[red]✗ File not found: {excel_path}[/red]")
            sys.exit(1)

        # Get data paths from DataManager if available
        country_mappings_path = None
        location_csv_path = None
        gravity_distances_path = None

        try:
            cache_dir = Path(args.cache_dir) if args.cache_dir else None
            manager = DataManager(cache_dir=cache_dir)

            # Try to get core-data package paths
            try:
                core_data_path = manager.get_package_path("core-data")
                fixtures_path = core_data_path / "fixtures"
                if fixtures_path.exists():
                    country_mappings_path = fixtures_path / "country_mappings.json"
                    # Note: location.csv doesn't exist in current data, this is a known issue
                    gravity_distances_path = core_data_path / "gravity_distances.pkl"
            except Exception:
                # Package not available, continue without paths
                pass
        except Exception:
            # DataManager not available, continue without paths
            pass

        validator = ExcelValidator(
            strict_mode=args.strict,
            country_mappings_path=country_mappings_path,
            location_csv_path=location_csv_path,
            gravity_distances_path=gravity_distances_path,
        )
        console.print(f"Validating: {excel_path}")

        result = validator.validate_excel(excel_path)

        # Display results
        if result["valid"]:
            console.print("\n[green]✓ Excel file is valid[/green]")
        else:
            console.print("\n[red]✗ Excel file has errors[/red]")

        # Show errors
        if result["errors"]:
            console.print("\n[red]Errors:[/red]")
            for error in result["errors"]:
                console.print(f"  • {error}")

        # Show warnings
        if result["warnings"]:
            console.print("\n[yellow]Warnings:[/yellow]")
            for warning in result["warnings"]:
                console.print(f"  • {warning}")

        return "Validation complete!"
    except Exception as e:
        console.print(f"[red]✗ Validation failed: {e}[/red]")
        sys.exit(1)


def steelo_data_convert():
    """Convert Excel files to JSON repositories."""
    parser = argparse.ArgumentParser(description="Convert Excel files to JSON repositories.")
    parser.add_argument(
        "input_dir",
        type=str,
        help="Directory containing Excel files",
    )
    parser.add_argument(
        "output_dir",
        type=str,
        help="Directory for JSON output",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        help="Cache directory for data packages",
    )

    try:
        args = parser.parse_args()
        input_dir = Path(args.input_dir)
        output_dir = Path(args.output_dir)

        if not input_dir.exists():
            raise ValueError(f"Input directory does not exist: {input_dir}")

        # Get data paths from DataManager if available
        country_mappings_path = None
        location_csv_path = None
        gravity_distances_path = None

        try:
            cache_dir = Path(args.cache_dir) if args.cache_dir else None
            manager = DataManager(cache_dir=cache_dir)

            # Try to get core-data package paths
            try:
                core_data_path = manager.get_package_path("core-data")
                fixtures_path = core_data_path / "fixtures"
                if fixtures_path.exists():
                    country_mappings_path = fixtures_path / "country_mappings.json"
                    # Note: location.csv doesn't exist in current data, this is a known issue
                    gravity_distances_path = core_data_path / "gravity_distances.pkl"
            except Exception:
                # Package not available, continue without paths
                pass
        except Exception:
            # DataManager not available, continue without paths
            pass

        validator = ExcelValidator(
            strict_mode=args.strict,
            country_mappings_path=country_mappings_path,
            location_csv_path=location_csv_path,
            gravity_distances_path=gravity_distances_path,
        )
        console.print(f"Converting Excel files from: {input_dir}")
        console.print(f"Output directory: {output_dir}")

        repo_paths = validator.convert_to_repositories(input_dir, output_dir)

        console.print("[green]✓ Conversion complete[/green]")
        console.print("Created repositories:")
        for repo_name, repo_path in repo_paths.items():
            console.print(f"  - {repo_name}: {repo_path}")

        return "Conversion complete!"
    except Exception as e:
        console.print(f"[red]✗ Conversion failed: {e}[/red]")
        sys.exit(1)


def steelo_data_extract_geo():
    """Extract geo data files to their expected locations."""
    parser = argparse.ArgumentParser(description="Extract geo data files")
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path("data"),
        help="Target directory for geo files (default: data)",
    )

    args = parser.parse_args()

    try:
        extractor = GeoDataExtractor()
        extracted_files = extractor.extract_geo_data(target_dir=args.target_dir)

        console.print(f"\n[green]✓ Extracted {len(extracted_files)} geo data files[/green]")
        for file_name, file_path in extracted_files.items():
            console.print(f"  - {file_name} → {file_path}")

    except Exception as e:
        console.print(f"[red]✗ Extraction failed: {e}[/red]")
        sys.exit(1)


def steelo_data_recreate():
    """Recreate JSON repositories from downloaded data packages."""
    parser = argparse.ArgumentParser(
        description="Recreate JSON repositories from downloaded data packages (similar to recreate_sample_data)."
    )
    parser.add_argument(
        "--package",
        type=str,
        help="Specific package to recreate from (default: all required packages)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./data/repositories",
        help="Output directory for JSON repositories (default: ./data/repositories)",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        help="Cache directory for downloaded data",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Force re-download of packages",
    )

    try:
        args = parser.parse_args()
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        output_dir = Path(args.output_dir)

        manager = DataManager(cache_dir=cache_dir)
        recreator = DataRecreator(manager)

        if args.package:
            # Recreate from specific package
            console.print(f"Recreating data from package: {args.package}")
            created_paths = recreator.recreate_from_package(
                args.package,
                output_dir,
                force_download=args.force_download,
            )

            console.print("\n[green]✓ Recreation complete![/green]")
            console.print("Created repositories:")
            for name, path in created_paths.items():
                console.print(f"  - {name}: {path}")
        else:
            # Recreate from all required packages
            console.print("Recreating data from all required packages...")
            results = recreator.recreate_all_packages(output_dir)

            console.print("\n[green]✓ Recreation complete![/green]")
            for package_name, paths in results.items():
                console.print(f"\nPackage: {package_name}")
                for name, path in paths.items():
                    console.print(f"  - {name}: {path}")

        return "Recreation complete!"
    except Exception as e:
        console.print(f"[red]✗ Recreation failed: {e}[/red]")
        sys.exit(1)


# Note: steelo_data_clear_cache has been removed.
# Use 'steelo-cache clear --type data' instead.


def steelo_data_prepare():
    """Prepare all data files needed for simulation by extracting raw files and creating JSON repositories."""
    from steelo.data import DataPreparationService
    from steelo.data.formatting import ResultFormatter

    parser = argparse.ArgumentParser(description="Prepare all data files for simulation.")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data",
        help="Output directory for data files (default: data)",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        help="Cache directory for downloaded data",
    )
    parser.add_argument(
        "--master-excel-file",
        type=str,
        help="Path to custom master Excel file (overrides S3 download).",
    )
    parser.add_argument(
        "--geo-version",
        type=str,
        help="Specific version of geo-data to use (e.g., '1.1.0-dev'). Can also be set via STEELO_GEO_VERSION env var.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip files that already exist (default: True). Use --no-skip-existing to force recreation.",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Force recreation of all files, even if they already exist",
    )
    parser.add_argument(
        "--list-files",
        action="store_true",
        help="List all available files for recreation and exit",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed progress during preparation",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force re-preparation even if cached (bypasses cache)",
    )

    try:
        args = parser.parse_args()

        # Handle --list-files option
        if args.list_files:
            from steelo.data.recreation_config import FILE_RECREATION_SPECS
            from rich.table import Table

            table = Table(title="Available Files for Recreation")
            table.add_column("File", style="cyan")
            table.add_column("Source", style="magenta")
            table.add_column("Sheet/Dependencies", style="yellow")
            table.add_column("Description")

            for filename, spec in FILE_RECREATION_SPECS.items():
                source_info = (
                    spec.master_excel_sheet if spec.source_type == "master-excel" else ", ".join(spec.dependencies[:2])
                )
                if len(spec.dependencies) > 2:
                    source_info += f" (+{len(spec.dependencies) - 2} more)"

                table.add_row(
                    filename,
                    spec.source_type,
                    source_info or "-",
                    spec.description,
                )

            console.print(table)
            return "Listed available files"

        # Prepare output directory
        output_dir = Path(args.output_dir)

        # Resolve any symlinks in the path (including parent directories)
        # This handles cases like "data/fixtures" where "data" is a symlink
        try:
            # Try to resolve the full path
            resolved_dir = output_dir.resolve(strict=False)
            # Create the directory if it doesn't exist
            if not resolved_dir.exists():
                resolved_dir.mkdir(parents=True, exist_ok=True)
            elif not resolved_dir.is_dir():
                raise ValueError(f"{resolved_dir} exists but is not a directory")
            # Use the resolved directory for the rest of the operations
            output_dir = resolved_dir
        except Exception:
            # If resolution fails, try to handle it differently
            # Check if any parent is a symlink
            parts = output_dir.parts
            for i in range(len(parts)):
                partial_path = Path(*parts[: i + 1])
                if partial_path.is_symlink():
                    # Resolve from this point
                    resolved_partial = partial_path.resolve(strict=False)
                    remaining_parts = parts[i + 1 :]
                    resolved_dir = resolved_partial.joinpath(*remaining_parts)
                    resolved_dir.mkdir(parents=True, exist_ok=True)
                    output_dir = resolved_dir
                    break
            else:
                # No symlinks found, just create normally
                output_dir.mkdir(parents=True, exist_ok=True)

        # Parse master Excel path
        master_excel_path = Path(args.master_excel_file) if args.master_excel_file else None

        # Get geo version from args or environment
        import os

        geo_version = args.geo_version or os.environ.get("STEELO_GEO_VERSION")

        # Initialize service
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        manager = DataManager(cache_dir=cache_dir)
        service = DataPreparationService(data_manager=manager)

        console.print("[bold blue]Preparing data for simulation...[/bold blue]")
        if geo_version:
            console.print(f"Using geo-data version: [cyan]{geo_version}[/cyan]")

        # Run preparation
        # Force refresh should imply not skipping existing files
        skip_existing = args.skip_existing and not args.force_refresh

        result = service.prepare_data(
            output_dir=output_dir,
            master_excel_path=master_excel_path,
            skip_existing=skip_existing,
            verbose=args.verbose,
            geo_version=geo_version,
            force_refresh=args.force_refresh,
        )

        # Format and display results
        formatter = ResultFormatter(console)
        formatter.print_summary(result)
        formatter.print_timing_table(result)

        console.print("\nYou can now run the simulation with: [cyan]run_simulation[/cyan]")
        return "Data preparation complete!"

    except Exception as e:
        console.print(f"[red]✗ Data preparation failed: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)
