"""
Management command to test plotting functions with existing simulation output.
"""

from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots import generate_post_run_cap_prod_plots


class Command(BaseCommand):
    help = "Test plotting functions with existing simulation output CSV files"

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            type=str,
            help="Path to post-processed CSV file from a previous simulation",
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            help="Directory to save test plots (default: outputs/test_plots)",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_file"])

        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        if not csv_path.suffix == ".csv":
            raise CommandError("File must be a CSV file")

        self.stdout.write(f"Testing plots with: {csv_path}")

        # Set output directory for test plots
        if options["output_dir"]:
            output_dir = Path(options["output_dir"])
        else:
            output_dir = settings.BASE_DIR.parent.parent / "outputs" / "test_plots"

        output_dir.mkdir(parents=True, exist_ok=True)

        # Save original output directories
        from steelo.utilities import plotting

        original_pam_dir = plotting.PAM_PLOTS_DIR
        original_geo_dir = plotting.GEO_PLOTS_DIR

        try:
            # Temporarily redirect plot output
            plotting.PAM_PLOTS_DIR = output_dir / "pam_plots"
            plotting.GEO_PLOTS_DIR = output_dir / "geo_plots"

            # Test the plotting functions
            self.stdout.write("Running generate_post_run_cap_prod_plots...")
            generate_post_run_cap_prod_plots(csv_path)

            self.stdout.write(self.style.SUCCESS(f"✓ Plots generated successfully in {output_dir}"))

            # List generated files
            plot_files = list(output_dir.rglob("*.png"))
            self.stdout.write(f"\nGenerated {len(plot_files)} plot files:")
            for plot_file in sorted(plot_files):
                self.stdout.write(f"  - {plot_file.relative_to(output_dir)}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Plotting failed: {str(e)}"))
            import traceback

            traceback.print_exc()

        finally:
            # Restore original directories
            plotting.PAM_PLOTS_DIR = original_pam_dir
            plotting.GEO_PLOTS_DIR = original_geo_dir
