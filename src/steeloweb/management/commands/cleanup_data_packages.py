from django.core.management.base import BaseCommand
from steeloweb.models import DataPreparation, DataPackage
from pathlib import Path
import shutil


class Command(BaseCommand):
    help = "Clean up old DataPreparation and DataPackage objects and their associated files"

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-latest",
            action="store_true",
            help="Keep only the latest version of each package type",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without actually deleting",
        )
        parser.add_argument(
            "--keep-files",
            action="store_true",
            help="Only delete database records, keep files in media directory",
        )

    def handle(self, *args, **options):
        if options["keep_latest"]:
            # Keep only the latest version of each package type
            for package_type in ["core-data", "geo-data"]:
                packages = DataPackage.objects.filter(name=package_type).order_by("-created_at")
                if packages.count() > 1:
                    old_packages = packages[1:]
                    for package in old_packages:
                        if options["dry_run"]:
                            self.stdout.write(f"Would delete: {package}")
                            if package.local_file and not options["keep_files"]:
                                self.stdout.write(f"  Would delete file: {package.local_file.path}")
                        else:
                            # Delete associated file if it exists
                            if package.local_file and not options["keep_files"]:
                                file_path = Path(package.local_file.path)
                                if file_path.exists():
                                    file_path.unlink()
                                    self.stdout.write(self.style.WARNING(f"  Deleted file: {file_path}"))
                            package.delete()
                            self.stdout.write(self.style.SUCCESS(f"Deleted: {package}"))

            # Clean up DataPreparation objects that don't have the latest packages
            latest_core = DataPackage.objects.filter(name="core-data").order_by("-created_at").first()
            latest_geo = DataPackage.objects.filter(name="geo-data").order_by("-created_at").first()

            if latest_core and latest_geo:
                old_preps = DataPreparation.objects.exclude(core_data_package=latest_core, geo_data_package=latest_geo)
                for prep in old_preps:
                    if options["dry_run"]:
                        self.stdout.write(f"Would delete preparation: {prep}")
                        if prep.data_directory and not options["keep_files"]:
                            self.stdout.write(f"  Would delete directory: {prep.data_directory}")
                    else:
                        # Delete associated data directory if it exists
                        if prep.data_directory and not options["keep_files"]:
                            data_path = Path(prep.data_directory)
                            if data_path.exists():
                                shutil.rmtree(data_path)
                                self.stdout.write(self.style.WARNING(f"  Deleted directory: {data_path}"))
                        prep.delete()
                        self.stdout.write(self.style.SUCCESS(f"Deleted preparation: {prep}"))
        else:
            # Delete all DataPreparation objects
            preps = DataPreparation.objects.all()
            if options["dry_run"]:
                self.stdout.write(f"Would delete {preps.count()} DataPreparation objects")
                if not options["keep_files"]:
                    for prep in preps:
                        if prep.data_directory:
                            self.stdout.write(f"  Would delete directory: {prep.data_directory}")
            else:
                count = preps.count()
                # Delete associated directories
                if not options["keep_files"]:
                    for prep in preps:
                        if prep.data_directory:
                            data_path = Path(prep.data_directory)
                            if data_path.exists():
                                shutil.rmtree(data_path)
                                self.stdout.write(self.style.WARNING(f"  Deleted directory: {data_path}"))
                preps.delete()
                self.stdout.write(self.style.SUCCESS(f"Deleted {count} DataPreparation objects"))

            # Delete all DataPackage objects
            packages = DataPackage.objects.all()
            if options["dry_run"]:
                self.stdout.write(f"Would delete {packages.count()} DataPackage objects")
                if not options["keep_files"]:
                    for package in packages:
                        if package.local_file:
                            self.stdout.write(f"  Would delete file: {package.local_file.path}")
            else:
                count = packages.count()
                # Delete associated files
                if not options["keep_files"]:
                    for package in packages:
                        if package.local_file:
                            file_path = Path(package.local_file.path)
                            if file_path.exists():
                                file_path.unlink()
                                self.stdout.write(self.style.WARNING(f"  Deleted file: {file_path}"))
                packages.delete()
                self.stdout.write(self.style.SUCCESS(f"Deleted {count} DataPackage objects"))
