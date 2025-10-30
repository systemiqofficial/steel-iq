"""
Management command to prepare default data packages for simulation.
"""

from django.core.management.base import BaseCommand
from steeloweb.models import MasterExcelFile, DataPreparation
from steeloweb.services import DataPreparationService as DataPreparationServiceDjango


class Command(BaseCommand):
    help = "Prepare default data packages for simulation"

    def add_arguments(self, parser):
        parser.add_argument("--name", type=str, default="Default Data", help="Name for the data preparation")
        parser.add_argument("--force", action="store_true", help="Force re-preparation even if data exists")
        parser.add_argument(
            "--with-master-excel",
            action="store_true",
            default=True,
            help="Include master Excel file in preparation (default: True, deprecated flag - master Excel is always used)",
        )
        parser.add_argument("--master-excel-id", type=int, help="ID of MasterExcelFile to use for preparation")
        parser.add_argument("--quiet", action="store_true", help="Hide detailed output (only show summary)")
        parser.add_argument(
            "--geo-version",
            type=str,
            help="Specific version of geo-data to use (e.g., '1.1.0-dev'). Can also be set via STEELO_GEO_VERSION env var.",
        )

    def handle(self, *args, **options):
        verbose = not options.get("quiet", False)

        self.stdout.write("Preparing default data...")

        # Check if default data preparation already exists
        existing_prep = DataPreparation.objects.filter(
            name=options["name"], status=DataPreparation.Status.READY
        ).first()
        if existing_prep and not options["force"]:
            self.stdout.write(self.style.SUCCESS(f"Default data already prepared: {existing_prep}"))
            self.stdout.write(f"Data location: {existing_prep.get_data_path()}")
            return

        # Determine master Excel path
        master_excel_path = None
        master_excel_file = None

        # Handle master Excel options
        if options.get("master_excel_id"):
            # Use specific MasterExcelFile
            try:
                master_excel_file = MasterExcelFile.objects.get(pk=options["master_excel_id"])
                if master_excel_file.validation_status not in ["valid", "warnings"]:
                    self.stdout.write(
                        self.style.ERROR(
                            f"Master Excel file '{master_excel_file.name}' has validation status: {master_excel_file.validation_status}"
                        )
                    )
                    return
                master_excel_path = master_excel_file.get_file_path()
                self.stdout.write(f"Using MasterExcelFile: {master_excel_file}")
                # Update the preparation name to include master Excel info
                if options["name"] == "Default Data":
                    options["name"] = f"Data with {master_excel_file.name}"
            except MasterExcelFile.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"MasterExcelFile with ID {options['master_excel_id']} not found"))
                return
        else:
            # No specific master Excel provided - will download from S3
            self.stdout.write("No master Excel provided, will download from S3")

        # Get geo version from args or environment
        import os

        geo_version = options.get("geo_version") or os.environ.get("STEELO_GEO_VERSION")

        # Initialize Django service
        service = DataPreparationServiceDjango()

        if verbose:
            self.stdout.write("Preparing data with detailed output...\n")
            if geo_version:
                self.stdout.write(f"Using geo-data version: {geo_version}\n")

        # Create data preparation record
        try:
            preparation = service.prepare_and_save(
                name=options["name"],
                master_excel_file=master_excel_file if options.get("master_excel_id") else None,
                master_excel_path=master_excel_path,
                geo_version=geo_version,
                run_async=False,
                verbose=verbose,
            )

            # Display results
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nData preparation successful!\n"
                    f"Preparation ID: {preparation.pk}\n"
                    f"Name: {preparation.name}\n"
                    f"Status: {preparation.status}\n"
                    f"Data directory: {preparation.get_data_path()}"
                )
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Data preparation failed: {e}"))
            if verbose:
                import traceback

                traceback.print_exc()
