"""
Management command to import data packages from S3 or create them from local files.
"""

from pathlib import Path

from django.core.management.base import BaseCommand

from steelo.data import DataManifest
from steeloweb.models import DataPackage


class Command(BaseCommand):
    help = "Import data packages from S3 manifest or create from local files"

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-s3",
            action="store_true",
            help="Import packages from S3 manifest",
        )
        parser.add_argument(
            "--core-data",
            type=str,
            help="Path to local core-data ZIP file",
        )
        parser.add_argument(
            "--geo-data",
            type=str,
            help="Path to local geo-data ZIP file",
        )
        parser.add_argument(
            "--package-version",
            type=str,
            default="1.0.0",
            help="Version for local packages (default: 1.0.0)",
        )

    def handle(self, *args, **options):
        if options["from_s3"]:
            self.import_from_s3()

        if options["core_data"]:
            self.import_local_package("core-data", options["core_data"], options["package_version"])

        if options["geo_data"]:
            self.import_local_package("geo-data", options["geo_data"], options["package_version"])

        if not any([options["from_s3"], options["core_data"], options["geo_data"]]):
            self.stdout.write(self.style.WARNING("No action specified. Use --from-s3, --core-data, or --geo-data"))

    def import_from_s3(self):
        """Import package definitions from S3 manifest."""
        self.stdout.write("Importing packages from S3 manifest...")

        # Load the default manifest from steelo.data
        # The DataManifest.load() will use the manifest.json in the steelo.data package
        from steelo.data import manifest as data_manifest_module

        manifest_path = Path(data_manifest_module.__file__).parent / "manifest.json"

        if not manifest_path.exists():
            self.stdout.write(self.style.ERROR(f"Manifest file not found at {manifest_path}"))
            return

        manifest = DataManifest.load(manifest_path)

        # Filter for required packages with default or production tags
        packages = []
        for package in manifest.packages:
            if package.required:
                # Only include packages tagged as default or production
                if "default" in package.tags or "production" in package.tags or not package.tags:
                    packages.append(package)

        for package in packages:
            # Check if already exists
            existing = DataPackage.objects.filter(name=package.name, version=package.version).first()

            if existing:
                self.stdout.write(self.style.WARNING(f"Package {package.name} v{package.version} already exists"))
                continue

            # Create package reference
            data_package = DataPackage.objects.create(
                name=package.name,
                version=package.version,
                source_type=DataPackage.SourceType.S3,
                source_url=package.url,
                checksum=package.checksum,
                size_mb=package.size_mb,
            )

            self.stdout.write(self.style.SUCCESS(f"Created S3 package: {data_package}"))

    def import_local_package(self, package_type: str, file_path: str, version: str):
        """Import a local package file."""
        path = Path(file_path)

        if not path.exists():
            self.stdout.write(self.style.ERROR(f"File not found: {path}"))
            return

        # Check if already exists
        existing = DataPackage.objects.filter(name=package_type, version=version).first()

        if existing:
            self.stdout.write(self.style.WARNING(f"Package {package_type} v{version} already exists"))
            return

        # Create package with file
        from django.core.files import File

        data_package = DataPackage(
            name=package_type,
            version=version,
            source_type=DataPackage.SourceType.LOCAL,
        )

        with open(path, "rb") as f:
            data_package.local_file.save(path.name, File(f))

        data_package.save()

        self.stdout.write(self.style.SUCCESS(f"Created local package: {data_package}"))
