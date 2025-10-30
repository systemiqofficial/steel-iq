"""
Centralized data preparation service with clean data structures.

This module provides a unified interface for preparing simulation data
across all entrypoints (CLI, Django management command, and Django UI).
"""

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Dict, Optional, Any

from .cache_manager import DataPreparationCache
from .manager import DataManager
from .recreate import DataRecreator
from .recreation_config import RecreationConfig, FILE_RECREATION_SPECS
from .path_resolver import DataPathResolver
from ..adapters.dataprocessing.master_excel_reader import MasterExcelReader

logger = logging.getLogger(__name__)


class FileSource(Enum):
    """Enumeration of file sources for proper ordering."""

    MASTER_EXCEL = "master-excel"
    CORE_DATA = "core-data"
    GEO_DATA = "geo-data"
    DERIVED = "derived"
    UNKNOWN = "unknown"


@dataclass
class PreparedFile:
    """Represents a single prepared file with its metadata."""

    filename: str
    source: FileSource
    source_detail: str  # e.g., "Bill of Materials" for master excel files
    duration: float  # Time taken to create/copy in seconds
    path: Path
    size_bytes: Optional[int] = None
    skipped: bool = False  # True if file already existed

    @property
    def source_display(self) -> str:
        """Get display string for the source."""
        if self.source == FileSource.MASTER_EXCEL:
            return f"master-excel - {self.source_detail}"
        elif self.source == FileSource.DERIVED:
            return f"derived from {self.source_detail}"
        elif self.source == FileSource.CORE_DATA:
            return "core-data"
        elif self.source == FileSource.GEO_DATA:
            return "geo-data"
        else:
            return self.source_detail or "unknown"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "filename": self.filename,
            "source": self.source.value,
            "source_detail": self.source_detail,
            "source_display": self.source_display,
            "duration": round(self.duration, 2),
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "skipped": self.skipped,
        }


@dataclass
class PreparationStep:
    """Represents a high-level preparation step."""

    name: str
    duration: float
    percentage: float = 0.0  # Percentage of total time (set by PreparationResult)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {"name": self.name, "duration": round(self.duration, 2), "percentage": round(self.percentage, 1)}


@dataclass
class PreparationResult:
    """Complete result of a data preparation operation."""

    files: List[PreparedFile] = field(default_factory=list)
    steps: List[PreparationStep] = field(default_factory=list)
    total_duration: float = 0.0
    output_directory: Optional[Path] = None
    master_excel_path: Optional[Path] = None

    def add_file(self, file: PreparedFile) -> None:
        """Add a prepared file to the result."""
        self.files.append(file)

    def add_step(self, step: PreparationStep) -> None:
        """Add a preparation step to the result."""
        self.steps.append(step)

    def finalize(self) -> None:
        """Finalize the result, calculating percentages and sorting."""
        # Calculate step percentages
        if self.total_duration > 0:
            for step in self.steps:
                step.percentage = (step.duration / self.total_duration) * 100

        # Sort steps by duration (descending)
        self.steps.sort(key=lambda s: s.duration, reverse=True)

        # Sort files by source priority, then alphabetically
        source_priority = {
            FileSource.MASTER_EXCEL: 1,
            FileSource.CORE_DATA: 2,
            FileSource.DERIVED: 3,
            FileSource.GEO_DATA: 4,
            FileSource.UNKNOWN: 5,
        }
        self.files.sort(key=lambda f: (source_priority.get(f.source, 99), f.filename))

    def get_files_by_source(self) -> Dict[FileSource, List[PreparedFile]]:
        """Get files grouped by source."""
        result: Dict[FileSource, List[PreparedFile]] = {}
        for file in self.files:
            if file.source not in result:
                result[file.source] = []
            result[file.source].append(file)
        return result

    def get_summary_stats(self) -> Dict[str, Any]:
        """Get summary statistics."""
        files_by_source = self.get_files_by_source()
        return {
            "total_files": len(self.files),
            "total_duration": round(self.total_duration, 2),
            "files_by_source": {source.value: len(files) for source, files in files_by_source.items()},
            "skipped_files": sum(1 for f in self.files if f.skipped),
            "created_files": sum(1 for f in self.files if not f.skipped),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "files": [f.to_dict() for f in self.files],
            "steps": [s.to_dict() for s in self.steps],
            "total_duration": round(self.total_duration, 2),
            "output_directory": str(self.output_directory) if self.output_directory else None,
            "master_excel_path": str(self.master_excel_path) if self.master_excel_path else None,
            "summary": self.get_summary_stats(),
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Reconstruct from dictionary."""
        self.files = []
        for file_data in data.get("files", []):
            file = PreparedFile(
                filename=file_data["filename"],
                source=FileSource(file_data["source"]),
                source_detail=file_data["source_detail"],
                duration=file_data["duration"],
                path=Path(file_data["path"]),
                size_bytes=file_data.get("size_bytes"),
                skipped=file_data.get("skipped", False),
            )
            self.files.append(file)

        self.steps = []
        for step_data in data.get("steps", []):
            step = PreparationStep(
                name=step_data["name"],
                duration=step_data["duration"],
                percentage=step_data.get("percentage", 0.0),
            )
            self.steps.append(step)

        self.total_duration = data.get("total_duration", 0.0)
        self.output_directory = Path(data["output_directory"]) if data.get("output_directory") else None
        self.master_excel_path = Path(data["master_excel_path"]) if data.get("master_excel_path") else None


class DataPreparationService:
    """Unified service for preparing simulation data."""

    def __init__(
        self,
        data_manager: Optional[DataManager] = None,
        cache_manager: Optional[DataPreparationCache] = None,
        use_cache: bool = True,
    ):
        """Initialize the service.

        Args:
            data_manager: DataManager instance for S3 downloads
            cache_manager: Cache manager for preparation caching
            use_cache: Whether to use caching (default: True)
        """
        self.data_manager = data_manager or DataManager()
        self.cache_manager = cache_manager or DataPreparationCache()
        self.use_cache = use_cache
        self._suppress_metallic_charge_warnings()

    def _suppress_metallic_charge_warnings(self):
        """Suppress annoying metallic charge warnings."""

        class MetallicChargeFilter(logging.Filter):
            def filter(self, record):
                return "Skipping invalid metallic charge" not in record.getMessage()

        logging.getLogger("steelo.adapters.dataprocessing.excel_reader").addFilter(MetallicChargeFilter())

    def prepare_data(
        self,
        output_dir: Path,
        master_excel_path: Optional[Path] = None,
        skip_existing: bool = True,
        verbose: bool = False,
        progress_callback: Optional[Any] = None,
        geo_version: Optional[str] = None,
        force_refresh: bool = False,
    ) -> PreparationResult:
        """
        Prepare all data files for simulation.

        Args:
            output_dir: Directory to output prepared files (fixtures directory)
            master_excel_path: Path to master Excel file (will download if not provided)
            skip_existing: Skip files that already exist
            verbose: Print detailed progress
            progress_callback: Optional callback for progress updates
            geo_version: Specific version of geo-data to use (optional)
            force_refresh: Force re-preparation even if cached

        Returns:
            PreparationResult with all file and timing information
        """
        # First, ensure we have a master Excel path (download if needed)
        if not master_excel_path or not master_excel_path.exists():
            # Download master Excel to get a consistent path for caching
            if verbose:
                logging.info("Resolving master Excel file...")
            self.data_manager.download_package("master-input", force=False)
            cache_path = self.data_manager.get_package_path("master-input")
            if cache_path and cache_path.exists():
                master_excel_path = cache_path / "master_input.xlsx"
                if not master_excel_path.exists():
                    raise ValueError(f"Master Excel file not found in package: {cache_path}")
            else:
                raise ValueError("Failed to download master Excel package")

        # Now check cache with the resolved master Excel path
        if self.use_cache and not force_refresh and master_excel_path.exists():
            cached_dir = self.cache_manager.get_cached_preparation(master_excel_path)
            if cached_dir:
                if verbose:
                    logging.info(f"Using cached preparation from: {cached_dir}")

                # Copy from cache to output directory
                start_time = time.time()
                output_dir.mkdir(parents=True, exist_ok=True)

                # Always copy to the specified output_dir
                # The caller is responsible for providing the correct path
                # Copy all files from cache to the output directory
                for src_file in cached_dir.rglob("*"):
                    if src_file.is_file():
                        rel_path = src_file.relative_to(cached_dir)
                        dst_file = output_dir / rel_path
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        if not skip_existing or not dst_file.exists():
                            shutil.copy2(src_file, dst_file)

                # Load cached result
                cache_metadata_path = cached_dir.parent / "metadata.json"
                if cache_metadata_path.exists():
                    metadata = json.loads(cache_metadata_path.read_text())
                    if "timing_details" in metadata:
                        # Reconstruct PreparationResult from cached data
                        result = PreparationResult()
                        result.from_dict(metadata["timing_details"])
                        result.output_directory = output_dir
                        result.master_excel_path = master_excel_path
                        return result

                # Fallback: create minimal result
                copy_time = time.time() - start_time
                result = PreparationResult()
                result.output_directory = output_dir
                result.master_excel_path = master_excel_path
                result.total_duration = copy_time
                result.add_step(PreparationStep("Copy from cache", copy_time))
                result.finalize()
                return result

        # Not cached, proceed with normal preparation
        result = self._prepare_data_internal(
            output_dir=output_dir,
            master_excel_path=master_excel_path,
            skip_existing=skip_existing,
            verbose=verbose,
            progress_callback=progress_callback,
            geo_version=geo_version,
        )

        # Ensure master_excel_path is set in result (it might have been resolved in _prepare_data_internal)
        if not result.master_excel_path:
            result.master_excel_path = master_excel_path

        # Save to cache if applicable
        if self.use_cache and master_excel_path and master_excel_path.exists() and result.total_duration > 0:
            try:
                self.cache_manager.save_preparation(
                    source_dir=output_dir.parent if output_dir.name == "fixtures" else output_dir,  # Handle both cases
                    master_excel_path=master_excel_path,
                    preparation_time=result.total_duration,
                    result=result,
                )
                if verbose:
                    logging.info("Saved preparation to cache")
            except Exception as e:
                # Cache save failed, log but don't fail preparation
                logging.warning(f"Failed to save preparation to cache: {e}")

        return result

    def _prepare_data_internal(
        self,
        output_dir: Path,
        master_excel_path: Optional[Path] = None,
        skip_existing: bool = True,
        verbose: bool = False,
        progress_callback: Optional[Any] = None,
        geo_version: Optional[str] = None,
    ) -> PreparationResult:
        """Internal method - existing prepare_data logic."""
        start_time = time.time()
        result = PreparationResult()

        # Interpret output_dir as the data root directory
        data_dir = output_dir
        fixtures_dir = data_dir / "fixtures"

        # Ensure directories exist
        data_dir.mkdir(parents=True, exist_ok=True)
        fixtures_dir.mkdir(parents=True, exist_ok=True)

        result.output_directory = data_dir

        # Step 1: Handle master Excel file
        step_start = time.time()
        master_excel_path = self._ensure_master_excel(master_excel_path, data_dir, result, verbose)
        result.master_excel_path = master_excel_path
        result.add_step(PreparationStep("Master Excel processing", time.time() - step_start))

        # Step 2: Extract tech switches from master Excel
        step_start = time.time()
        self._extract_tech_switches(master_excel_path, fixtures_dir, result, skip_existing, verbose)
        result.add_step(PreparationStep("Tech switches extraction", time.time() - step_start))

        # Step 3: Extract railway cost from master Excel
        step_start = time.time()
        self._extract_railway_cost(master_excel_path, data_dir, result, skip_existing, verbose)
        result.add_step(PreparationStep("Railway cost extraction", time.time() - step_start))

        # Step 4: Extract technologies from master Excel
        step_start = time.time()
        self._extract_technologies(master_excel_path, data_dir, result, skip_existing, verbose)
        result.add_step(PreparationStep("Technologies extraction", time.time() - step_start))

        # Step 6: Process core data package
        step_start = time.time()
        self._process_core_data(fixtures_dir, result, skip_existing, verbose)
        result.add_step(PreparationStep("Core data processing", time.time() - step_start))

        # Step 7: Create JSON repositories
        step_start = time.time()
        self._create_json_repositories(
            fixtures_dir, master_excel_path, result, skip_existing, verbose, progress_callback
        )
        result.add_step(PreparationStep("JSON repository creation", time.time() - step_start))

        # Step 8: Extract geo data
        step_start = time.time()
        self._extract_geo_data(data_dir, result, verbose, geo_version)
        result.add_step(PreparationStep("Geo data extraction", time.time() - step_start))

        # Finalize result
        result.total_duration = time.time() - start_time
        result.finalize()

        return result

    def _ensure_master_excel(
        self, master_excel_path: Optional[Path], data_dir: Path, result: PreparationResult, verbose: bool
    ) -> Path:
        """Ensure we have a master Excel file, downloading if necessary."""
        if master_excel_path and master_excel_path.exists():
            # Check if this is the cached version from data_cache
            if "data_cache" in str(master_excel_path):
                # It's already the cached version, just track it
                result.add_file(
                    PreparedFile(
                        filename="master_input.xlsx",
                        source=FileSource.MASTER_EXCEL,
                        source_detail="Cached from S3",
                        duration=0.0,
                        path=master_excel_path,
                        skipped=True,
                    )
                )
            else:
                # It's a user-provided file
                result.add_file(
                    PreparedFile(
                        filename="master_input.xlsx",
                        source=FileSource.MASTER_EXCEL,
                        source_detail="Source File",
                        duration=0.0,
                        path=master_excel_path,
                        skipped=True,
                    )
                )
            return master_excel_path

        # Download from S3
        if verbose:
            logging.info("Downloading master Excel from S3...")

        start_time = time.time()
        self.data_manager.download_package("master-input", force=False)
        cache_path = self.data_manager.get_package_path("master-input")

        if not cache_path or not cache_path.exists():
            raise ValueError("Failed to download master Excel package")

        # Return the cached file directly instead of copying
        excel_source = cache_path / "master_input.xlsx"
        if not excel_source.exists():
            raise ValueError(f"Master Excel file not found in package: {cache_path}")

        duration = time.time() - start_time
        result.add_file(
            PreparedFile(
                filename="master_input.xlsx",
                source=FileSource.MASTER_EXCEL,
                source_detail="Downloaded from S3",
                duration=duration,
                path=excel_source,
                size_bytes=excel_source.stat().st_size,
                skipped=True,
            )
        )

        return excel_source

    def _extract_tech_switches(
        self, master_excel_path: Path, output_dir: Path, result: PreparationResult, skip_existing: bool, verbose: bool
    ) -> None:
        """Extract tech switches from master Excel."""
        dest_path = output_dir / "tech_switches_allowed.csv"

        if skip_existing and dest_path.exists():
            if verbose:
                logging.info("⏭ Skipped tech_switches_allowed.csv (already exists)")
            result.add_file(
                PreparedFile(
                    filename="tech_switches_allowed.csv",
                    source=FileSource.MASTER_EXCEL,
                    source_detail="Allowed tech switches",
                    duration=0.0,
                    path=dest_path,
                    skipped=True,
                )
            )
            return

        # Extract tech switches
        start_time = time.time()
        extraction_dir = output_dir.parent / "extraction"
        extraction_dir.mkdir(exist_ok=True)

        with MasterExcelReader(master_excel_path, output_dir=extraction_dir) as reader:
            tech_switches_result = reader.read_tech_switches()

            if not tech_switches_result.success:
                error_msgs = (
                    [e.message for e in tech_switches_result.errors]
                    if tech_switches_result.errors
                    else ["Unknown error"]
                )
                raise ValueError(f"Failed to extract tech switches: {'; '.join(error_msgs)}")

            # Copy to destination
            shutil.copy2(tech_switches_result.file_path, dest_path)

        duration = time.time() - start_time
        result.add_file(
            PreparedFile(
                filename="tech_switches_allowed.csv",
                source=FileSource.MASTER_EXCEL,
                source_detail="Allowed tech switches",
                duration=duration,
                path=dest_path,
                size_bytes=dest_path.stat().st_size,
            )
        )

        if verbose:
            logging.info(f"✓ Extracted tech_switches_allowed.csv ({duration:.2f}s)")

    def _extract_railway_cost(
        self, master_excel_path: Path, data_dir: Path, result: PreparationResult, skip_existing: bool, verbose: bool
    ) -> None:
        """Extract railway cost data from master Excel."""
        dest_path = data_dir / "railway_costs.json"

        if skip_existing and dest_path.exists():
            if verbose:
                logging.info("⏭ Skipped railway_costs.json (already exists)")
            result.add_file(
                PreparedFile(
                    filename="railway_costs.json",
                    source=FileSource.MASTER_EXCEL,
                    source_detail="Railway cost",
                    duration=0.0,
                    path=dest_path,
                    skipped=True,
                )
            )
            return

        # Extract railway cost
        start_time = time.time()
        extraction_dir = data_dir / "extraction"
        extraction_dir.mkdir(exist_ok=True)

        with MasterExcelReader(master_excel_path, output_dir=extraction_dir) as reader:
            railway_cost_result = reader.read_railway_cost()

            if not railway_cost_result.success:
                error_msgs = (
                    [e.message for e in railway_cost_result.errors] if railway_cost_result.errors else ["Unknown error"]
                )
                raise ValueError(f"Failed to extract railway cost: {'; '.join(error_msgs)}")

            # Copy to destination
            shutil.copy2(railway_cost_result.file_path, dest_path)

        duration = time.time() - start_time
        result.add_file(
            PreparedFile(
                filename="railway_costs.json",
                source=FileSource.MASTER_EXCEL,
                source_detail="Railway cost",
                duration=duration,
                path=dest_path,
                size_bytes=dest_path.stat().st_size,
            )
        )

        if verbose:
            logging.info(f"✓ Extracted railway_costs.json ({duration:.2f}s)")

    def _extract_technologies(
        self, master_excel_path: Path, data_dir: Path, result: PreparationResult, skip_existing: bool, verbose: bool
    ) -> None:
        """Extract technology configuration from master Excel."""
        dest_path = data_dir / "fixtures" / "technologies.json"

        if skip_existing and dest_path.exists():
            if verbose:
                logging.info("⏭ Skipped technologies.json (already exists)")
            result.add_file(
                PreparedFile(
                    filename="technologies.json",
                    source=FileSource.MASTER_EXCEL,
                    source_detail="Techno-economic details",
                    duration=0.0,
                    path=dest_path,
                    skipped=True,
                )
            )
            return

        # Extract technologies
        start_time = time.time()
        extraction_dir = data_dir / "extraction"
        extraction_dir.mkdir(exist_ok=True)

        with MasterExcelReader(master_excel_path, output_dir=extraction_dir) as reader:
            technologies_result = reader.read_technologies_config()

            if not technologies_result.success:
                error_msgs = (
                    [e.message for e in technologies_result.errors] if technologies_result.errors else ["Unknown error"]
                )
                raise ValueError(f"Failed to extract technologies: {'; '.join(error_msgs)}")

            # Copy to destination
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(technologies_result.file_path, dest_path)

        duration = time.time() - start_time
        result.add_file(
            PreparedFile(
                filename="technologies.json",
                source=FileSource.MASTER_EXCEL,
                source_detail="Techno-economic details",
                duration=duration,
                path=dest_path,
                size_bytes=dest_path.stat().st_size,
            )
        )

        if verbose:
            logging.info(f"✓ Extracted technologies.json ({duration:.2f}s)")

    def _process_core_data(
        self, output_dir: Path, result: PreparationResult, skip_existing: bool, verbose: bool
    ) -> None:
        """Process core data package - copy raw files."""
        # Download core data if needed
        self.data_manager.download_package("core-data", force=False)
        package_dir = self.data_manager.get_package_path("core-data")

        # Files to copy from core-data (excluding tech_switches_allowed.csv)
        raw_files = [
            "steel_plants_input_data_2025-03.csv",
            "historical_production_data.csv",
            "iron_production_2019to2022.csv",
            "steel_production_2019to2022.csv",
            "technology_lcop.csv",
            "2025_05_27 Demand outputs for trade module.xlsx",
            "countries.csv",
            "BOM_ghg_system_boundary_v6.xlsx",
            "cost_of_x.json",
            "gravity_distances_dict.pkl",
            "geolocator_raster.csv",
            "carbon_costs_iso3_year.xlsx",
            "input_costs_for_python_model.csv",
            "mining_data.xlsx",
            "regional_input_costs.json",
        ]

        # Copy raw files
        for filename in raw_files:
            src_path = package_dir / filename
            dst_path = output_dir / filename

            if not src_path.exists():
                logger.warning(f"File not found in core-data package: {filename}")
                continue

            if skip_existing and dst_path.exists():
                if verbose:
                    logging.info(f"⏭ Skipped {filename} (already exists)")
                result.add_file(
                    PreparedFile(
                        filename=filename,
                        source=FileSource.CORE_DATA,
                        source_detail="",
                        duration=0.0,
                        path=dst_path,
                        skipped=True,
                    )
                )
            else:
                start_time = time.time()
                shutil.copy2(src_path, dst_path)
                duration = time.time() - start_time

                result.add_file(
                    PreparedFile(
                        filename=filename,
                        source=FileSource.CORE_DATA,
                        source_detail="",
                        duration=duration,
                        path=dst_path,
                        size_bytes=dst_path.stat().st_size,
                    )
                )

                if verbose:
                    logging.info(f"✓ Copied {filename} ({duration:.2f}s)")

        # Copy master workbook (prefer latest naming, fall back if required)
        master_workbook_candidates = DataPathResolver.MASTER_WORKBOOK_CANDIDATES
        chosen_master_filename: str | None = None
        for candidate in master_workbook_candidates:
            src_path = package_dir / candidate
            if not src_path.exists():
                continue

            dst_path = output_dir / candidate
            if skip_existing and dst_path.exists():
                if verbose:
                    logging.info(f"⏭ Skipped {candidate} (already exists)")
                result.add_file(
                    PreparedFile(
                        filename=candidate,
                        source=FileSource.CORE_DATA,
                        source_detail="",
                        duration=0.0,
                        path=dst_path,
                        skipped=True,
                    )
                )
            else:
                start_time = time.time()
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                duration = time.time() - start_time

                result.add_file(
                    PreparedFile(
                        filename=candidate,
                        source=FileSource.CORE_DATA,
                        source_detail="",
                        duration=duration,
                        path=dst_path,
                        size_bytes=dst_path.stat().st_size,
                    )
                )

                if verbose:
                    logging.info(f"✓ Copied {candidate} ({duration:.2f}s)")

            chosen_master_filename = candidate
            if candidate != master_workbook_candidates[0]:
                logger.warning(
                    "Using legacy master workbook '%s'. Please migrate to '%s' when available.",
                    candidate,
                    master_workbook_candidates[0],
                )
            break

        if not chosen_master_filename:
            logger.warning(
                "No master workbook found in core-data package. Expected one of: %s",
                ", ".join(master_workbook_candidates),
            )

        # Copy Regional_Energy_prices.xlsx to parent directory
        energy_src = package_dir / "Regional_Energy_prices.xlsx"
        energy_dst = output_dir.parent / "Regional_Energy_prices.xlsx"

        if energy_src.exists():
            if skip_existing and energy_dst.exists():
                if verbose:
                    logging.info("⏭ Skipped Regional_Energy_prices.xlsx (already exists)")
                result.add_file(
                    PreparedFile(
                        filename="Regional_Energy_prices.xlsx",
                        source=FileSource.CORE_DATA,
                        source_detail="",
                        duration=0.0,
                        path=energy_dst,
                        skipped=True,
                    )
                )
            else:
                start_time = time.time()
                shutil.copy2(energy_src, energy_dst)
                duration = time.time() - start_time

                result.add_file(
                    PreparedFile(
                        filename="Regional_Energy_prices.xlsx",
                        source=FileSource.CORE_DATA,
                        source_detail="",
                        duration=duration,
                        path=energy_dst,
                        size_bytes=energy_dst.stat().st_size,
                    )
                )

    def _create_json_repositories(
        self,
        output_dir: Path,
        master_excel_path: Path,
        result: PreparationResult,
        skip_existing: bool,
        verbose: bool,
        progress_callback: Optional[Any] = None,
    ) -> None:
        """Create JSON repositories using the centralized recreation system."""
        # Create recreation config
        config = RecreationConfig(
            files_to_recreate=None,  # Create all files
            skip_existing=skip_existing,
            force_recreation=False,
            validate_after_creation=False,
            verbose=verbose,
            progress_callback=progress_callback,
            continue_on_error=False,
        )

        # Use DataRecreator to create JSON files
        recreator = DataRecreator(self.data_manager)
        created_paths = recreator.recreate_with_config(
            output_dir=output_dir,
            config=config,
            master_excel_path=master_excel_path,
            package_name="core-data",
        )

        # Track all created files
        for filename, path in created_paths.items():
            if not filename.endswith((".json", ".csv")):
                filename = f"{filename}.json"

            # Determine source from FILE_RECREATION_SPECS
            spec = FILE_RECREATION_SPECS.get(filename)
            if spec:
                if spec.source_type == "master-excel":
                    source = FileSource.MASTER_EXCEL
                    source_detail = spec.master_excel_sheet or "Unknown sheet"
                elif spec.source_type == "derived":
                    source = FileSource.DERIVED
                    deps = spec.dependencies[:2]
                    source_detail = ", ".join(deps)
                    if len(spec.dependencies) > 2:
                        source_detail += f" (+{len(spec.dependencies) - 2} more)"
                else:
                    source = FileSource.CORE_DATA
                    source_detail = ""

                # Check if file was skipped (0 size change or very quick)
                skipped = skip_existing and path.exists() and path.stat().st_mtime < (time.time() - 1)

                result.add_file(
                    PreparedFile(
                        filename=filename,
                        source=source,
                        source_detail=source_detail,
                        duration=0.0,  # We don't have individual timing from recreate_with_config
                        path=path,
                        size_bytes=path.stat().st_size if path.exists() else None,
                        skipped=skipped,
                    )
                )

    def _extract_geo_data(
        self, data_dir: Path, result: PreparationResult, verbose: bool, geo_version: Optional[str] = None
    ) -> None:
        """Extract geo data files."""
        from .geo_extractor import GeoDataExtractor

        try:
            extractor = GeoDataExtractor(self.data_manager)
            start_time = time.time()
            geo_files = extractor.extract_geo_data(target_dir=data_dir, version=geo_version)
            total_duration = time.time() - start_time

            # Track each geo file
            per_file_duration = total_duration / len(geo_files) if geo_files else 0
            for source_name, target_path in geo_files.items():
                # Use the source_name as filename (it includes subdirectories)
                result.add_file(
                    PreparedFile(
                        filename=source_name,
                        source=FileSource.GEO_DATA,
                        source_detail="",
                        duration=per_file_duration,
                        path=target_path,
                        size_bytes=target_path.stat().st_size if target_path.exists() else None,
                    )
                )

            if verbose:
                logging.info(f"✓ Extracted {len(geo_files)} geo data files")

        except Exception as e:
            error_msg = f"Failed to extract geo data: {e}"
            logger.error(error_msg)
            if verbose:
                logging.info(f"❌ {error_msg}")
            # Re-raise the exception to stop the preparation process
            raise RuntimeError(error_msg) from e
