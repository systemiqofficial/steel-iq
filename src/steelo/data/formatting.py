"""
Formatting utilities for displaying preparation results.
"""

from typing import List, Optional
from rich.console import Console
from rich.table import Table

from .preparation import PreparationResult, FileSource, PreparedFile


class ResultFormatter:
    """Formats preparation results for display."""

    def __init__(self, console: Optional[Console] = None):
        """Initialize formatter with optional console."""
        self.console = console or Console()

    def print_summary(self, result: PreparationResult) -> None:
        """Print a summary of the preparation result."""
        # Print files by source
        files_by_source = result.get_files_by_source()

        if FileSource.MASTER_EXCEL in files_by_source:
            self.console.print("\n[bold cyan]Files from MASTER EXCEL:[/bold cyan]")
            # Sort alphabetically within source
            sorted_files = sorted(files_by_source[FileSource.MASTER_EXCEL], key=lambda f: f.filename.lower())
            for file in sorted_files:
                status = " (skipped)" if file.skipped else ""
                self.console.print(f"  - {file.filename} (from {file.source_detail}){status}")

        if FileSource.CORE_DATA in files_by_source:
            self.console.print("\n[bold blue]Files from CORE-DATA archive:[/bold blue]")
            # Sort alphabetically within source
            sorted_files = sorted(files_by_source[FileSource.CORE_DATA], key=lambda f: f.filename.lower())
            for file in sorted_files:
                status = " (skipped)" if file.skipped else ""
                self.console.print(f"  - {file.filename}{status}")

        if FileSource.DERIVED in files_by_source:
            self.console.print("\n[bold magenta]Derived files:[/bold magenta]")
            # Sort alphabetically within source
            sorted_files = sorted(files_by_source[FileSource.DERIVED], key=lambda f: f.filename.lower())
            for file in sorted_files:
                status = " (skipped)" if file.skipped else ""
                self.console.print(f"  - {file.filename} (from {file.source_detail}){status}")

        if FileSource.GEO_DATA in files_by_source:
            self.console.print("\n[bold green]Files from GEO-DATA package:[/bold green]")
            # Sort alphabetically within source
            sorted_files = sorted(files_by_source[FileSource.GEO_DATA], key=lambda f: f.filename.lower())
            # Show first 10 geo files to avoid clutter
            for file in sorted_files[:10]:
                self.console.print(f"  - {file.filename}")
            if len(sorted_files) > 10:
                self.console.print(f"  ... and {len(sorted_files) - 10} more geo files")

        if FileSource.UNKNOWN in files_by_source:
            self.console.print("\n[bold yellow]Files with unknown source:[/bold yellow]")
            # Sort alphabetically within source
            sorted_files = sorted(files_by_source[FileSource.UNKNOWN], key=lambda f: f.filename.lower())
            for file in sorted_files:
                self.console.print(f"  - {file.filename}")

        # Print summary stats
        stats = result.get_summary_stats()
        self.console.print("\n[bold green]✓ Data preparation complete![/bold green]")
        self.console.print(
            f"Total files: {stats['total_files']} ({stats['created_files']} created, {stats['skipped_files']} skipped)"
        )
        if result.output_directory:
            self.console.print(f"Output directory: {result.output_directory}")
        self.console.print(f"Total time: {result.total_duration:.2f} seconds")

    def print_timing_table(self, result: PreparationResult, max_files: int = 50) -> None:
        """Print detailed timing table."""
        # Step timing table
        if result.steps:
            self.console.print("\n[bold cyan]High-level Step Timing:[/bold cyan]")
            step_table = Table(show_header=True, header_style="bold magenta")
            step_table.add_column("Step", style="cyan", width=40)
            step_table.add_column("Time (seconds)", justify="right", style="green")
            step_table.add_column("Percentage", justify="right", style="yellow")

            for step in result.steps:
                step_table.add_row(step.name, f"{step.duration:.2f}", f"{step.percentage:.1f}%")

            self.console.print(step_table)

        # File timing table
        if result.files:
            self.console.print("\n[bold cyan]Detailed File Creation Timing:[/bold cyan]")
            file_table = Table(show_header=True, header_style="bold magenta")
            file_table.add_column("File", style="cyan", width=35)
            file_table.add_column("Source", style="blue", width=45)
            file_table.add_column("Time (s)", justify="right", style="green", width=10)

            # Sort by source priority first, then alphabetically
            source_priority = {
                FileSource.MASTER_EXCEL: 1,
                FileSource.CORE_DATA: 2,
                FileSource.DERIVED: 3,
                FileSource.GEO_DATA: 4,
                FileSource.UNKNOWN: 5,
            }
            sorted_files = sorted(result.files, key=lambda f: (source_priority.get(f.source, 99), f.filename.lower()))

            # Display limit
            display_files = sorted_files[:max_files]
            for file in display_files:
                file_table.add_row(file.filename, file.source_display, f"{file.duration:.2f}")

            if len(sorted_files) > max_files:
                file_table.add_row(f"... and {len(sorted_files) - max_files} more files", "various sources", "...")

            # Total row
            total_file_time = sum(f.duration for f in result.files)
            file_table.add_row(
                "[bold]Total[/bold]", "[bold]All sources[/bold]", f"[bold]{total_file_time:.2f}[/bold]", style="bold"
            )

            self.console.print(file_table)

            # Summary by source
            self.console.print("\n[bold cyan]Summary by Source:[/bold cyan]")
            for source, files in result.get_files_by_source().items():
                total_time = sum(f.duration for f in files)
                self.console.print(f"\n[bold]{source.value}:[/bold] {len(files)} files, {total_time:.2f}s total")

                # Show top 5 slowest
                sorted_source_files = sorted(files, key=lambda f: f.duration, reverse=True)
                for file in sorted_source_files[:5]:
                    self.console.print(f"  - {file.filename}: {file.duration:.2f}s")
                if len(files) > 5:
                    self.console.print(f"  ... and {len(files) - 5} more files")

    def format_for_html(self, files: List[PreparedFile]) -> List[dict]:
        """Format files for HTML display (Django template)."""
        return [
            {
                "filename": file.filename,
                "source": file.source_display,
                "duration": file.duration,
                "path": str(file.path.relative_to(file.path.parent.parent)) if file.path else "-",
                "skipped": file.skipped,
            }
            for file in files
        ]
