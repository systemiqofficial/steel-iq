"""Cache management CLI commands."""

import argparse
import shutil
from pathlib import Path
from rich.console import Console
from rich.table import Table

from ..data.cache_manager import DataPreparationCache


def steelo_cache() -> str:
    """Main entry point for cache management commands."""
    console = Console()

    parser = argparse.ArgumentParser(description="Manage Steel Model preparation cache")

    subparsers = parser.add_subparsers(dest="command", help="Cache commands")

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show cache statistics")
    stats_parser.add_argument("--cache-dir", type=str, help="Cache directory (default: $STEELO_HOME/preparation_cache)")

    # Clear command
    clear_parser = subparsers.add_parser("clear", help="Clear all caches (preparation and data)")
    clear_parser.add_argument("--keep-recent", type=int, help="Keep N most recent cached preparations")
    clear_parser.add_argument("--cache-dir", type=str, help="Cache directory (default: $STEELO_HOME/preparation_cache)")
    clear_parser.add_argument(
        "--data-cache-dir", type=str, help="Data cache directory (default: $HOME/.steelo/data_cache)"
    )

    # List command
    list_parser = subparsers.add_parser("list", help="List cached preparations")
    list_parser.add_argument("--cache-dir", type=str, help="Cache directory (default: $STEELO_HOME/preparation_cache)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return "No command specified"

    # Get cache directory
    if args.cache_dir:
        cache_root = Path(args.cache_dir)
    else:
        steelo_home = Path.home() / ".steelo"
        cache_root = steelo_home / "preparation_cache"

    cache_manager = DataPreparationCache(cache_root)

    if args.command == "stats":
        stats = cache_manager.get_cache_stats()
        console.print("[bold]Cache Statistics[/bold]")
        console.print(f"Directory: {stats['cache_directory']}")
        console.print(f"Total preparations: {stats['total_preparations']}")
        console.print(f"Total size: {stats['total_size_mb']:.1f} MB")
        if stats["oldest_cache"]:
            console.print(f"Oldest cache: {stats['oldest_cache']}")
        if stats["newest_cache"]:
            console.print(f"Newest cache: {stats['newest_cache']}")

    elif args.command == "clear":
        # Get data cache directory
        data_cache_dir = Path(args.data_cache_dir) if args.data_cache_dir else Path.home() / ".steelo" / "data_cache"

        # Get data directory (follow symlinks to find actual location)
        data_dir = Path("data")
        if data_dir.exists() and data_dir.is_symlink():
            # If it's a symlink, we'll remove the symlink but not the target
            # The target will be removed when we clear the preparation cache
            pass

        # Show what will be cleared
        console.print("[yellow]This will clear:[/yellow]")
        console.print("  - All cached data preparations")
        console.print("  - All downloaded data packages")
        console.print("  - All prepared data files (data/ directory)")

        # Special handling for keep-recent (only applies to preparation cache)
        if args.keep_recent is not None:
            removed = cache_manager.clear_cache(keep_recent=args.keep_recent)
            console.print(f"[green]Removed {removed} cached preparations[/green]")
            if args.keep_recent > 0:
                console.print(f"[blue]Kept {args.keep_recent} most recent[/blue]")

            # Clear data cache too
            console.print("\n[yellow]Clear downloaded data packages as well?[/yellow]")
            confirm = console.input("Are you sure? (y/N): ")
            if confirm.lower() == "y":
                from ..data import DataManager

                data_manager = DataManager(cache_dir=data_cache_dir)
                data_manager.clear_cache()
                console.print("[green]Cleared all downloaded data packages[/green]")

                # Clear data directory
                if data_dir.exists():
                    if data_dir.is_symlink():
                        data_dir.unlink()
                    else:
                        shutil.rmtree(data_dir)
                    console.print("[green]Removed data/ directory[/green]")
        else:
            # Confirm before clearing all
            confirm = console.input("Are you sure? (y/N): ")
            if confirm.lower() == "y":
                # Clear preparation cache
                removed = cache_manager.clear_cache()
                console.print(f"[green]Removed {removed} cached preparations[/green]")

                # Clear data cache
                from ..data import DataManager

                data_manager = DataManager(cache_dir=data_cache_dir)
                data_manager.clear_cache()
                console.print("[green]Cleared all downloaded data packages[/green]")

                # Clear data directory
                if data_dir.exists():
                    if data_dir.is_symlink():
                        data_dir.unlink()
                    else:
                        shutil.rmtree(data_dir)
                    console.print("[green]Removed data/ directory[/green]")
            else:
                console.print("[blue]Cancelled[/blue]")

    elif args.command == "list":
        # List all cached preparations
        table = Table(title="Cached Preparations")
        table.add_column("Hash", style="cyan")
        table.add_column("Created", style="green")
        table.add_column("Size (MB)", style="magenta", justify="right")
        table.add_column("Files", style="blue", justify="right")
        table.add_column("Master Excel", style="yellow")

        for prep_dir in sorted(cache_root.glob("prep_*")):
            if not prep_dir.is_dir():
                continue

            metadata_path = prep_dir / "metadata.json"
            if metadata_path.exists():
                try:
                    import json

                    metadata = json.loads(metadata_path.read_text())

                    hash_id = prep_dir.name.replace("prep_", "")
                    created = metadata.get("created_at", "Unknown")[:19]
                    size_mb = metadata.get("total_size_bytes", 0) / (1024 * 1024)
                    file_count = metadata.get("file_count", 0)
                    excel_path = Path(metadata.get("master_excel_path", "Unknown")).name

                    table.add_row(hash_id[:8] + "...", created, f"{size_mb:.1f}", str(file_count), excel_path)
                except Exception:
                    pass

        console.print(table)

    return f"Cache {args.command} completed"


if __name__ == "__main__":
    steelo_cache()
