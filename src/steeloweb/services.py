"""
Data preparation services for Django integration.
"""

import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Tuple

from django.conf import settings
from django.utils import timezone

from .models import DataPackage, DataPreparation
from steelo.data import DataPreparationService as SteelDataPreparationService, DataManager

logger = logging.getLogger(__name__)


class DataPreparationService:
    """Service to handle data preparation from packages."""

    def __init__(self):
        # Lazy import to avoid circular imports
        self._manager = None
        self._service = None

    @property
    def manager(self):
        if self._manager is None:
            self._manager = DataManager()
        return self._manager

    @property
    def service(self):
        if self._service is None:
            # Disable caching for Django to avoid conflicts with CLI cache structure
            self._service = SteelDataPreparationService(data_manager=self.manager, use_cache=False)
        return self._service

    def prepare_data(
        self, preparation: DataPreparation, progress_callback=None, verbose=False, geo_version=None
    ) -> Tuple[bool, str]:
        """
        Prepare data from the given data packages.

        Args:
            preparation: DataPreparation model instance
            progress_callback: Optional callback for progress updates
            verbose: Enable verbose output
            geo_version: Specific version of geo-data to use (optional)

        Returns:
            Tuple of (success, message)
        """
        start_time = timezone.now()
        try:
            # Update status
            preparation.status = DataPreparation.Status.DOWNLOADING
            preparation.save()
            self._log(preparation, "Starting data preparation")
            self._update_progress(preparation, 5)

            # Create temporary directory for preparation, or reuse existing if incrementally updating
            if preparation.data_directory and Path(preparation.data_directory).exists():
                # Reuse existing directory for incremental updates
                temp_dir = Path(preparation.data_directory)
                self._log(preparation, f"Reusing existing directory for incremental update: {temp_dir}")
            else:
                # Create new temporary directory
                temp_dir = Path(tempfile.mkdtemp(prefix="steelo_prep_"))
                self._log(preparation, f"Created new temporary directory: {temp_dir}")

            data_dir = temp_dir / "data"
            fixtures_dir = data_dir / "fixtures"
            fixtures_dir.mkdir(parents=True, exist_ok=True)

            # Determine master Excel path
            master_excel_path = None

            # Case 1: MasterExcelFile reference (highest priority)
            if preparation.master_excel:
                master_excel_path = preparation.master_excel.get_file_path()
                self._log(preparation, f"Using MasterExcelFile: {preparation.master_excel.name}")

                # Copy validation report if available
                if preparation.master_excel.validation_report:
                    preparation.master_excel_validation_report = preparation.master_excel.validation_report

            # Case 2: Uploaded file (second priority)
            elif preparation.master_excel_file:
                excel_path = Path(settings.MEDIA_ROOT) / preparation.master_excel_file.name
                if excel_path.exists():
                    # Copy to temp directory
                    master_excel_path = temp_dir / "master_input.xlsx"
                    shutil.copy2(excel_path, master_excel_path)
                    self._log(preparation, f"Using uploaded master Excel file: {excel_path.name}")

            # Case 3: From DataPackage if specified
            elif preparation.master_excel_package:
                package = preparation.master_excel_package

                # Download if from S3
                if package.source_type == DataPackage.SourceType.S3:
                    self._log(preparation, f"Downloading master Excel from S3: {package.source_url}")
                    self.manager.download_package(package.name, force=False)

                    # The Excel file is inside the package directory
                    cache_path = self.manager.get_package_path(package.name)
                    if cache_path and cache_path.exists():
                        excel_source = cache_path / "master_input.xlsx"
                        if excel_source.exists():
                            master_excel_path = temp_dir / "master_input.xlsx"
                            shutil.copy2(excel_source, master_excel_path)
                        else:
                            raise ValueError(f"Master Excel file not found in package directory: {cache_path}")
                    else:
                        raise ValueError("Failed to download master Excel package")
                else:
                    # Local file
                    master_excel_path = package.get_file_path()

                self._log(preparation, f"Using master Excel from package: {master_excel_path.name}")

            # Update status
            preparation.status = DataPreparation.Status.PREPARING
            preparation.save()
            self._update_progress(preparation, 20)

            # Create custom progress callback that updates Django model
            def django_progress_callback(message: str, percent: int):
                self._log(preparation, message)
                # Map percent from data preparation (0-100) to our range (20-90)
                django_percent = 20 + int(percent * 0.7)
                self._update_progress(preparation, django_percent)

            # Get geo_version from environment if not explicitly provided
            if geo_version is None:
                import os

                geo_version = os.environ.get("STEELO_GEO_VERSION")

            if geo_version:
                self._log(preparation, f"Using geo-data version: {geo_version}")

            # Run centralized data preparation
            # Pass data_dir instead of fixtures_dir since prepare_data creates its own fixtures subdirectory
            result = self.service.prepare_data(
                output_dir=data_dir,
                master_excel_path=master_excel_path,
                skip_existing=True,
                verbose=verbose,
                progress_callback=django_progress_callback if progress_callback else None,
                geo_version=geo_version,
            )

            # Move to permanent location (only if using temporary directory)
            permanent_dir = Path(settings.MEDIA_ROOT) / "prepared_data" / f"prep_{preparation.pk}"

            if (
                preparation.data_directory
                and Path(preparation.data_directory).exists()
                and temp_dir == Path(preparation.data_directory)
            ):
                # We're already working in the permanent directory, no move needed
                self._log(preparation, f"Already working in permanent directory: {temp_dir}")
            else:
                # Move from temporary to permanent location
                if permanent_dir.exists():
                    shutil.rmtree(permanent_dir)
                shutil.move(str(temp_dir), str(permanent_dir))

            # Update preparation
            if not (preparation.data_directory and Path(preparation.data_directory).exists()):
                preparation.data_directory = str(permanent_dir)
            preparation.status = DataPreparation.Status.READY
            preparation.prepared_at = timezone.now()
            preparation.progress = 100

            # Calculate processing time
            end_time = timezone.now()
            preparation.processing_time = (end_time - start_time).total_seconds()

            # Build timing data for JSON field
            timing_data = self._build_timing_data_from_result(result, permanent_dir)
            preparation.timing_data = timing_data

            preparation.save()

            self._log(preparation, f"Data preparation complete. Data stored at: {permanent_dir}")

            return True, "Data preparation successful"

        except Exception as e:
            logger.exception("Error preparing data")
            preparation.status = DataPreparation.Status.FAILED
            preparation.error_message = str(e)
            preparation.save()
            self._log(preparation, f"Error: {e}")
            return False, str(e)

    def _build_timing_data_from_result(self, result, preparation_dir: Path) -> dict:
        """Build structured timing data from PreparationResult."""
        # Convert steps to timing data
        step_data = [
            {"name": step.name, "duration": round(step.duration, 2), "percentage": round(step.percentage, 1)}
            for step in result.steps
        ]

        # Convert files to timing data
        file_data = []
        file_summary = {"master_excel": [], "core_data": [], "derived": [], "geo_data": [], "unknown": []}
        file_paths = {}

        for file in result.files:
            # Make path relative to preparation directory
            try:
                if file.path.exists():
                    rel_path = file.path.relative_to(preparation_dir)
                else:
                    rel_path = Path(file.filename)
            except ValueError:
                rel_path = Path(file.filename)

            file_entry = {
                "filename": file.filename,
                "source": file.source_display,
                "duration": round(file.duration, 2),
                "path": str(rel_path),
            }
            file_data.append(file_entry)
            file_paths[file.filename] = str(rel_path)

            # Categorize for summary
            if file.source.value == "master-excel":
                file_summary["master_excel"].append(file.filename)
            elif file.source.value == "derived":
                file_summary["derived"].append(file.filename)
            elif file.source.value == "geo-data":
                file_summary["geo_data"].append(file.filename)
            elif file.source.value == "core-data":
                file_summary["core_data"].append(file.filename)
            else:
                file_summary["unknown"].append(file.filename)

        return {
            "step_timings": step_data,
            "file_timings": file_data,
            "total_time": round(result.total_duration, 2),
            "file_summary": file_summary,
            "file_paths": file_paths,
        }

    def _log(self, preparation: DataPreparation, message: str):
        """Log a message to the preparation log."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        preparation.preparation_log += log_entry
        preparation.save()
        logger.info(f"Preparation {preparation.pk}: {message}")

    def _update_progress(self, preparation: DataPreparation, progress: int):
        """Update preparation progress."""
        preparation.progress = progress
        preparation.save(update_fields=["progress"])
