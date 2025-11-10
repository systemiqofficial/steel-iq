from dataclasses import dataclass
import hashlib
import shutil
import uuid
from typing import Optional

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.utils import timezone
from pathlib import Path

# Cache for technology configurations
# Cache structure: key -> {ts: timestamp, fp: (mtime_ns, size), data: dict}
_TECH_CACHE: dict[str, dict] = {}
_MAX_TECH_CACHE_ENTRIES = 128  # Prevent unbounded growth
_TECH_CACHE_TTL = 60  # TTL in seconds as safety valve


def master_excel_upload_path(instance, filename) -> str:
    """Generate upload path for master Excel files"""
    return f"master_excel/{instance.name.lower().replace(' ', '_')}_{filename}"


def data_package_upload_path(instance, filename) -> str:
    """Generate upload path for data package files"""
    return f"data_packages/{instance.name}/{instance.version}/{filename}"


class DataPackage(models.Model):
    """
    Model to store data packages (archives) that can be from S3 or uploaded locally.
    """

    class PackageType(models.TextChoices):
        CORE_DATA = "core-data", _("Core Data")
        GEO_DATA = "geo-data", _("Geo Data")

    class SourceType(models.TextChoices):
        S3 = "s3", _("S3")
        LOCAL = "local", _("Local Upload")

    name = models.CharField(max_length=50, choices=PackageType.choices, help_text="Type of data package")
    version = models.CharField(max_length=50, help_text="Version of the data package")
    source_type = models.CharField(max_length=10, choices=SourceType.choices, help_text="Source of the data package")
    source_url = models.URLField(blank=True, help_text="S3 URL if source is S3")
    local_file = models.FileField(upload_to=data_package_upload_path, blank=True, help_text="Local archive file")
    checksum = models.CharField(max_length=64, blank=True, help_text="SHA256 checksum of the archive")
    size_mb = models.FloatField(null=True, blank=True, help_text="Size of the archive in MB")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, help_text="Whether this package is currently active")

    class Meta:
        unique_together = ["name", "version"]
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.get_name_display()} v{self.version} ({self.get_source_type_display()})"

    def get_file_path(self) -> Optional[Path]:
        """Return the path to the archive file"""
        if self.source_type == self.SourceType.LOCAL and self.local_file:
            return Path(settings.MEDIA_ROOT) / self.local_file.name
        return None

    def calculate_checksum(self) -> Optional[str]:
        """Calculate SHA256 checksum of the file"""
        file_path = self.get_file_path()
        if not file_path or not file_path.exists():
            return None

        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def save(self, *args, **kwargs):
        # Calculate checksum and size if local file
        if self.source_type == self.SourceType.LOCAL and self.local_file:
            if not self.checksum:
                self.checksum = self.calculate_checksum() or ""
            if not self.size_mb and hasattr(self.local_file, "size"):
                self.size_mb = self.local_file.size / (1024 * 1024)
        super().save(*args, **kwargs)


class MasterExcelFile(models.Model):
    """
    Model to store master Excel input files that users can upload and manage.
    """

    name = models.CharField(max_length=255, help_text="Name to identify this master Excel file")
    description = models.TextField(
        blank=True, help_text="Description of what this master Excel file contains or changes"
    )
    file = models.FileField(upload_to=master_excel_upload_path, help_text="Master Excel input file (.xlsx)")
    is_template = models.BooleanField(default=False, help_text="Whether this is the official template file from S3")
    is_example = models.BooleanField(
        default=False, help_text="Whether this is an example file provided with the application"
    )
    validation_report = models.JSONField(
        default=dict, blank=True, help_text="Validation report from master Excel validator"
    )
    validation_status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("valid", "Valid"),
            ("warnings", "Valid with Warnings"),
            ("invalid", "Invalid"),
        ],
        default="pending",
        help_text="Validation status of the file",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Master Excel File"
        verbose_name_plural = "Master Excel Files"

    def __str__(self):
        if self.is_template:
            return f"{self.name} (Template)"
        return self.name

    def get_file_path(self) -> Path:
        """Return the absolute path to the master Excel file"""
        return Path(settings.MEDIA_ROOT) / self.file.name

    def validate(self) -> dict:
        """
        Run validation on the Excel file and update validation fields.

        Returns:
            dict: Validation report in the same format as DataPreparationForm
        """
        from steelo.adapters.dataprocessing.master_excel_validator import MasterExcelValidator
        from datetime import datetime

        validator = MasterExcelValidator()
        try:
            report = validator.validate_file(self.get_file_path())

            # Store the validation report in the same format as DataPreparationForm
            self.validation_report = {
                "errors": [str(e) for e in report.errors],
                "warnings": [str(w) for w in report.warnings],
                "info": [str(i) for i in report.info],
                "validated_at": datetime.now().isoformat(),
                "summary": {
                    "error_count": len(report.errors),
                    "warning_count": len(report.warnings),
                    "info_count": len(report.info),
                },
            }

            # Update status based on validation
            if report.has_errors():
                self.validation_status = "invalid"
            elif len(report.warnings) > 0:
                self.validation_status = "warnings"
            else:
                self.validation_status = "valid"

        except Exception as e:
            self.validation_status = "invalid"
            self.validation_report = {
                "errors": [f"Error validating Excel file: {e}"],
                "warnings": [],
                "info": [],
                "validated_at": datetime.now().isoformat(),
                "summary": {
                    "error_count": 1,
                    "warning_count": 0,
                    "info_count": 0,
                },
            }

        self.save()
        return self.validation_report


class DataPreparation(models.Model):
    """
    Model to track data preparation status and link prepared data to model runs.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        DOWNLOADING = "downloading", _("Downloading")
        EXTRACTING = "extracting", _("Extracting")
        PREPARING = "preparing", _("Preparing JSON repositories")
        READY = "ready", _("Ready")
        FAILED = "failed", _("Failed")

    name = models.CharField(max_length=255, help_text="Name for this data preparation")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    core_data_package = models.ForeignKey(
        DataPackage,
        on_delete=models.CASCADE,
        related_name="core_preparations",
        limit_choices_to={"name": DataPackage.PackageType.CORE_DATA},
    )
    geo_data_package = models.ForeignKey(
        DataPackage,
        on_delete=models.CASCADE,
        related_name="geo_preparations",
        limit_choices_to={"name": DataPackage.PackageType.GEO_DATA},
    )
    data_directory = models.CharField(max_length=500, blank=True, help_text="Path to prepared data directory")
    prepared_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    preparation_log = models.TextField(blank=True, help_text="Log of preparation steps")
    progress = models.IntegerField(default=0, help_text="Progress percentage (0-100)")
    processing_time = models.FloatField(null=True, blank=True, help_text="Processing time in seconds")
    timing_data = models.JSONField(
        default=dict, blank=True, help_text="Detailed timing data for file creation and processing steps"
    )

    # Master Excel fields
    master_excel_file = models.FileField(
        upload_to="data_preparations/master_excel/",
        blank=True,
        null=True,
        help_text="Optional master Excel input file to override default data files",
    )
    master_excel_package = models.ForeignKey(
        DataPackage,
        on_delete=models.SET_NULL,
        related_name="master_excel_preparations",
        blank=True,
        null=True,
        help_text="Optional master Excel data package to override default data files",
    )
    master_excel_validation_report = models.JSONField(
        default=dict, blank=True, help_text="Validation report from master Excel file"
    )
    master_excel = models.ForeignKey(
        MasterExcelFile,
        on_delete=models.SET_NULL,
        related_name="data_preparations",
        blank=True,
        null=True,
        help_text="Master Excel file to use for this data preparation",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        base_str = f"{self.name} - {self.get_status_display()}"
        if self.master_excel:
            base_str += f" (Master Excel: {self.master_excel.name})"
        return base_str

    def get_data_path(self) -> Optional[Path]:
        """Return the path to the prepared data directory"""
        if self.data_directory:
            return Path(self.data_directory)
        return None

    def is_ready(self) -> bool:
        """Check if data is ready to use"""
        return self.status == self.Status.READY and self.data_directory

    def get_status_color(self) -> str:
        """Get Bootstrap color class for the current status"""
        status_colors = {
            self.Status.PENDING: "secondary",
            self.Status.DOWNLOADING: "info",
            self.Status.EXTRACTING: "info",
            self.Status.PREPARING: "primary",
            self.Status.READY: "success",
            self.Status.FAILED: "danger",
        }
        return status_colors.get(self.status, "secondary")

    @property
    def log_messages(self) -> str:
        """Alias for preparation_log for backward compatibility"""
        return self.preparation_log

    @property
    def output_path(self) -> Optional[str]:
        """Alias for data_directory"""
        return self.data_directory if self.data_directory else None

    def cleanup(self) -> None:
        """Remove prepared data directory"""
        if self.data_directory:
            data_path = Path(self.data_directory)
            if data_path.exists():
                shutil.rmtree(data_path)
        self.data_directory = ""
        self.status = self.Status.PENDING
        self.save()

    def get_technologies(self) -> dict:
        """
        Get validated technology configuration from prepared data.

        Uses a per-process cache keyed by file path and modification time
        to avoid re-reading unchanged files. Cache is limited to 128 entries
        to prevent unbounded memory growth.

        Returns:
            Dictionary with technology slugs as keys
            Empty dict if data not available or invalid
        """
        import json
        import logging
        import time
        from pydantic import ValidationError
        from steelo.adapters.dataprocessing.technology_extractor import TechnologyConfig
        from steelo.core.parse import normalize_code_for_dedup

        logger = logging.getLogger("steeloweb.tech.ui")

        if not self.data_directory:
            logger.warning(f"DataPreparation {self.id} has no data_directory")
            return {}

        # Fixed: Consistent path
        tech_path = Path(self.data_directory) / "data" / "fixtures" / "technologies.json"

        if not tech_path.exists():
            logger.info(f"Technologies file not found at {tech_path}")
            return {}

        try:
            # Check cache first with TTL and strong fingerprint
            st = tech_path.stat()
            key = str(tech_path)
            fingerprint = (st.st_mtime_ns, st.st_size)
            now = time.time()

            cached = _TECH_CACHE.get(key)
            if cached and cached["fp"] == fingerprint and (now - cached["ts"]) < _TECH_CACHE_TTL:
                logger.debug(f"Using cached technologies for {key}")
                return cached["data"]

            # Not in cache or file changed - read and parse with retry for atomic replace races
            for attempt in range(2):
                try:
                    with open(tech_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    break
                except json.JSONDecodeError:
                    if attempt == 0:
                        time.sleep(0.001)  # Brief retry for FS race
                    else:
                        raise

            # Handle v1→v2 migration with same normalization
            schema_v = int(data.get("schema_version", 1))

            if schema_v == 1:
                # Add normalized_code to v1 files using SAME normalization
                for t in data.get("technologies", {}).values():
                    if "normalized_code" not in t:
                        t["normalized_code"] = normalize_code_for_dedup(t.get("code", ""))
                data["schema_version"] = 2

            # Validate with Pydantic schema (handle cache eviction on failure)
            try:
                config = TechnologyConfig(**data)
            except ValidationError:
                # Evict bad cache entry
                if key in _TECH_CACHE:
                    del _TECH_CACHE[key]
                raise

            if config.schema_version not in (1, 2, 3):
                logger.warning("Unknown technologies schema_version=%s", config.schema_version)

            # Log metadata for debugging
            logger.debug(
                f"Loaded technologies from {config.source.get('excel_path', 'unknown')}, "
                f"generated at {config.generated_at}"
            )

            # Prepare result and cache it (with size limit)
            result = {k: v.model_dump() for k, v in config.technologies.items()}

            # Simple FIFO trim if cache is too large
            if len(_TECH_CACHE) >= _MAX_TECH_CACHE_ENTRIES:
                _TECH_CACHE.pop(next(iter(_TECH_CACHE)))  # Remove oldest

            _TECH_CACHE[key] = {"ts": now, "fp": fingerprint, "data": result}

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in technologies file: {e}")
            return {}
        except ValidationError as e:
            logger.error(f"Technology data validation failed: {e}")
            # Check if the error is due to missing product_type (indicates old schema)
            error_str = str(e)
            if "product_type" in error_str and "Field required" in error_str:
                logger.error(
                    "Technologies file is using an outdated schema (< v3). Please refresh data preparation to update to the latest format with product_type field."
                )
            return {}
        except Exception as e:
            logger.error(f"Unexpected error loading technologies: {e}")
            return {}


@dataclass
class Progress:
    start_year: int
    end_year: int
    current_year: int

    @property
    def years(self) -> int:
        return self.end_year - self.start_year

    @property
    def percentage_completed(self) -> float:
        total_years = self.end_year - self.start_year

        if total_years <= 0:
            # Single-year run or misconfigured range; treat as not started unless we've moved past end_year
            return 0 if self.current_year <= self.end_year else 100

        progress_ratio = (self.current_year - self.start_year) / total_years
        progress_ratio = max(0.0, min(progress_ratio, 1.0))
        return round(progress_ratio * 100)


class ModelRun(models.Model):
    """
    Model to track steel model simulation runs.
    """

    name = models.CharField(
        max_length=255,
        help_text="Descriptive name for this model run",
        default="",
        blank=True,
    )
    started_at = models.DateTimeField(auto_now_add=True, help_text="When the model run was started")
    finished_at = models.DateTimeField(null=True, blank=True, help_text="When the model run finished")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the model run was last updated")
    output_directory = models.CharField(
        max_length=500, blank=True, help_text="Path to isolated output directory for this run"
    )

    class RunState(models.TextChoices):
        CREATED = "created", _("Created")
        RUNNING = "running", _("Running")
        FINISHED = "finished", _("Finished")
        FAILED = "failed", _("Failed")
        CANCELLING = "cancelling", _("Cancelling")
        CANCELLED = "cancelled", _("Cancelled")

    state = models.CharField(
        max_length=20, choices=RunState.choices, default=RunState.CREATED, help_text="Current state of the model run"
    )
    config = models.JSONField(default=dict, blank=True, help_text="Configuration parameters for the model run")
    results = models.JSONField(default=dict, blank=True, help_text="Results from the model run")
    progress = models.JSONField(default=dict, blank=True, help_text="Progress from the model run")
    error_message = models.TextField(blank=True, null=True, help_text="Error message if the run failed")
    task_id = models.CharField(
        max_length=255, blank=True, null=True, help_text="Django-tasks task ID for tracking execution"
    )
    log_file_uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text="UUID used for log file naming to prevent collisions across app reinstalls",
    )
    data_preparation = models.ForeignKey(
        DataPreparation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="model_runs",
        help_text="Data preparation used for this run",
    )
    result_csv = models.FileField(
        upload_to="results/csv/",
        null=True,
        blank=True,
        help_text="CSV file containing the simulation results",
    )

    # NEW: Scenario system integration
    scenario = models.ForeignKey(
        'Scenario',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Scenario this run belongs to"
    )
    scenario_variation = models.ForeignKey(
        'ScenarioVariation',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Specific variation of the scenario"
    )
    sensitivity_sweep = models.ForeignKey(
        'SensitivitySweep',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Sensitivity sweep this run belongs to"
    )
    sweep_parameter_value = models.FloatField(
        null=True,
        blank=True,
        help_text="Parameter value for this sweep point"
    )

    def __str__(self):
        if self.name:
            return f"{self.name} - {self.state} ({self.started_at})"
        return f"ModelRun {self.id} - {self.state} ({self.started_at})"

    class Meta:
        verbose_name = "Model Run"
        verbose_name_plural = "Model Runs"
        ordering = ["-started_at"]

    @classmethod
    def from_scenario(cls, scenario, variation=None, name=None):
        """Factory method to create ModelRun from Scenario"""
        # Note: actual config building will come in Wave 2
        # For now, just create the relationship

        if name is None:
            name = scenario.name
            if variation:
                name += f" - {variation.name}"

        return cls.objects.create(
            name=name,
            scenario=scenario,
            scenario_variation=variation,
            # data_preparation will be set when we have the full system
            config={},  # Placeholder for now
        )

    def get_scenario_label(self):
        """Human-readable scenario description"""
        if not self.scenario:
            return "Manual run"

        label = self.scenario.name
        if self.scenario_variation:
            label += f" > {self.scenario_variation.name}"
        if self.sensitivity_sweep:
            label += f" ({self.sweep_parameter_value})"
        return label

    def get_output_path(self) -> Optional[Path]:
        """Return the path to the output directory"""
        if self.output_directory:
            return Path(self.output_directory)
        return None

    def ensure_output_directories(self):
        """Create output directory structure for this model run"""
        if not self.output_directory:
            # Create isolated directory structure
            base_dir = Path(settings.MEDIA_ROOT) / "model_outputs" / f"run_{self.pk}"
            base_dir.mkdir(parents=True, exist_ok=True)

            # Create subdirectories matching current structure
            (base_dir / "TM").mkdir(exist_ok=True)
            (base_dir / "GEO").mkdir(exist_ok=True)
            (base_dir / "GEO" / "baseload_power_simulation").mkdir(parents=True, exist_ok=True)
            (base_dir / "plots").mkdir(exist_ok=True)
            (base_dir / "plots" / "PAM").mkdir(exist_ok=True)
            (base_dir / "plots" / "GEO").mkdir(exist_ok=True)

            self.output_directory = str(base_dir)
            self.save(update_fields=["output_directory"])

    def cleanup_output_directory(self):
        """Remove the output directory and all its contents"""
        if self.output_directory:
            output_path = Path(self.output_directory)
            if output_path.exists():
                import shutil

                shutil.rmtree(output_path)

    def delete(self, *args, **kwargs):
        """Override delete to cleanup output directory"""
        self.cleanup_output_directory()
        super().delete(*args, **kwargs)

    def run(self):
        import logging
        from steelo.simulation_types import TechnologySettings
        from steelo.validation import SimulationConfigError, validate_technology_settings

        logger = logging.getLogger(__name__)
        logger.info(f"Starting ModelRun {self.id} with data_preparation: {self.data_preparation}")

        # Ensure output directories exist
        self.ensure_output_directories()
        output_path = self.get_output_path()

        # Create simulation config from stored JSON
        config_data = self.config.copy()

        # CRITICAL: Reject legacy fields BEFORE any file I/O
        LEGACY_PREFIXES = ("bf_", "bof_", "dri_", "eaf_", "esf_", "moe_")
        legacy_fields = [k for k in config_data if any(k.startswith(p) for p in LEGACY_PREFIXES)]
        if legacy_fields:
            self.state = ModelRun.RunState.FAILED
            self.error_message = (
                f"Legacy technology fields present: {', '.join(legacy_fields)}. "
                "Reconfigure this run with the new technology table."
            )
            self.save(update_fields=["state", "error_message"])
            return

        # Add isolated output directory configuration
        if output_path:
            config_data["output_dir"] = str(output_path)
            config_data["plots_dir"] = str(output_path / "plots")
            config_data["geo_plots_dir"] = str(output_path / "plots" / "GEO")
            config_data["pam_plots_dir"] = str(output_path / "plots" / "PAM")

            logger.info(f"Using isolated output directory: {output_path}")

        # Set data directory and master excel path for factory method
        data_dir = None
        master_excel_path = None

        # Set master excel path based on data preparation
        if self.data_preparation and self.data_preparation.is_ready():
            data_path = self.data_preparation.get_data_path()
            if data_path:
                # Set variables for factory method
                data_dir = data_path / "data"

                # Check if preparation has an associated master Excel file
                if self.data_preparation.master_excel:
                    master_excel_path = self.data_preparation.master_excel.get_file_path()
                elif self.data_preparation.master_excel_file:
                    # Fallback to FileField if no MasterExcelFile object
                    master_excel_path = Path(settings.MEDIA_ROOT) / self.data_preparation.master_excel_file.name
                else:
                    raise ValueError(
                        f"Data preparation {self.data_preparation.pk} has no associated master Excel file. "
                        "Cannot run simulation without master Excel data."
                    )

                # Also set in config_data for backwards compatibility
                config_data["data_dir"] = str(data_dir)
                if master_excel_path:
                    config_data["master_excel_path"] = str(master_excel_path)

                # Geo data paths will be auto-detected by the factory method
                pass  # Factory method will auto-detect all geo paths

        # Add circularity file path if custom circularity file was uploaded
        if "circularity_file_path" in config_data and config_data["circularity_file_path"]:
            # The path is already stored in the config
            pass

        # Filter config_data to only include keys that SimulationConfig expects
        expected_params = {
            # Core parameters (required)
            "start_year",
            "end_year",
            "master_excel_path",
            "output_dir",
            # Data directory (optional - for locating fixtures)
            "data_dir",
            # Economic parameters
            "plant_lifetime",
            "global_risk_free_rate",
            "steel_price_buffer",
            "iron_price_buffer",
            # Output paths (optional, will be derived from output_dir if not provided)
            "plots_dir",
            "geo_plots_dir",
            "pam_plots_dir",
            # Core simulation parameters (have defaults)
            "active_statuses",
            "capacity_limit",
            "soft_minimum_capacity_percentage",
            "hot_metal_radius",
            "random_seed",
            "construction_time",
            "consideration_time",
            "probabilistic_agents",
            "probability_of_construction",
            "probability_of_announcement",
            "top_n_loctechs_as_business_op",
            # Plant capacity parameters
            "expanded_capacity",
            "capacity_limit_iron",
            "capacity_limit_steel",
            "new_capacity_share_from_new_plants",
            "priority_pct",
            "hydrogen_ceiling_percentile",
            "intraregional_trade_allowed",
            "long_dist_pipeline_transport_cost",
            # Trade module parameters
            "lp_epsilon",
            "minimum_active_utilisation_rate",
            "minimum_margin",
            # Status lists
            "announced_statuses",
            # Scenario and policy settings
            "chosen_emissions_boundary_for_carbon_costs",
            "chosen_grid_emissions_scenario",
            "use_iron_ore_premiums",
            "green_steel_emissions_limit",
            # Feature flags
            "include_infrastructure_cost",
            "include_transport_cost",
            "include_lulc_cost",
            "use_master_excel",
            "transportation_cost_per_km_per_ton",
            # Technology availability settings
            "technology_settings",
            # Optional geo data paths
            "terrain_nc_path",
            "land_cover_tif_path",
            "rail_distance_nc_path",
            "countries_shapefile_dir",
            "disputed_areas_shapefile_dir",
            "landtype_percentage_nc_path",
            # Other optional parameters
            "log_level",
            "scrap_generation_scenario",
            "demand_sheet_name",
            "technology_lifetimes",
            # Baseload power simulation parameters
            "baseload_demand",
            "years_of_operation",
            "included_power_mix",
            # Data processing parameters
            "steel_plant_gem_data_year",
            "production_gem_data_years",
            "excel_reader_start_year",
            "excel_reader_end_year",
            # Geo processing parameters
            "max_altitude",
            "max_slope",
            "max_latitude",
        }

        # Helper function for defensive extraction with type casting
        def _pick(cfg, key, default):
            """Extract value from config with defensive handling of None/empty values."""
            val = cfg.get(key, default)
            return default if val in (None, "", []) else val

        # Create a deep copy of config data first to avoid modifying the original
        from copy import deepcopy

        filtered_config = deepcopy(config_data)

        # Handle deprecated parameters with warning
        deprecated_params = ["global_bf_ban"]
        for param in deprecated_params:
            if param in filtered_config:
                logger.warning(f"Ignoring deprecated parameter '{param}' from stored configuration")
                filtered_config.pop(param)

        # Remove any parameters not in the expected set
        for key in list(filtered_config.keys()):
            if key not in expected_params:
                filtered_config.pop(key)

        # Load and validate technology settings if not already present
        if "technology_settings" not in filtered_config:
            self.state = ModelRun.RunState.FAILED
            self.error_message = "No technology_settings in configuration"
            self.save(update_fields=["state", "error_message"])
            return

        # Get technology settings from config
        tech_settings_raw = filtered_config.get("technology_settings")
        if not tech_settings_raw:
            self.state = ModelRun.RunState.FAILED
            self.error_message = "Empty technology_settings in configuration"
            self.save(update_fields=["state", "error_message"])
            return

        # Load available technologies from data preparation
        if self.data_preparation:
            try:
                techs = self.data_preparation.get_technologies()  # slug -> dict
                available_codes = {t["normalized_code"] for t in techs.values()}

                # Check for collisions
                codes = [t["normalized_code"] for t in techs.values()]
                dupes = {c for c in codes if codes.count(c) > 1}
                if dupes:
                    raise SimulationConfigError(f"Duplicate normalized codes: {', '.join(sorted(dupes))}")
            except Exception as e:
                self.state = ModelRun.RunState.FAILED
                self.error_message = f"Failed to load technologies: {e}"
                self.save(update_fields=["state", "error_message"])
                return
        else:
            self.state = ModelRun.RunState.FAILED
            self.error_message = "No data preparation available"
            self.save(update_fields=["state", "error_message"])
            return

        # Convert to TechnologySettings objects
        try:
            tech_map = {code: TechnologySettings(**vals) for code, vals in tech_settings_raw.items()}
        except Exception as e:
            self.state = ModelRun.RunState.FAILED
            self.error_message = f"Invalid technology settings format: {e}"
            self.save(update_fields=["state", "error_message"])
            return

        # Get scenario years - no silent defaults
        start_year = filtered_config.get("start_year")
        end_year = filtered_config.get("end_year")

        if start_year is None or end_year is None:
            self.state = ModelRun.RunState.FAILED
            self.error_message = "start_year and end_year are required in config"
            self.save(update_fields=["state", "error_message"])
            return

        # Validate technology settings against available technologies and scenario horizon
        try:
            validate_technology_settings(
                tech_map,
                available_codes,
                year_min=int(start_year) if hasattr(start_year, "__int__") else start_year,
                year_max=int(end_year) if hasattr(end_year, "__int__") else end_year,
            )
        except SimulationConfigError as e:
            self.state = ModelRun.RunState.FAILED
            self.error_message = str(e)
            self.save(update_fields=["state", "error_message"])
            logger.error(f"Configuration validation failed: {e}")
            return

        # Remove raw technology_settings and any legacy fields, then add validated tech_map
        filtered_config.pop("technology_settings", None)

        # Remove any legacy fields that might have snuck through
        LEGACY_PREFIXES = ("bf_", "bof_", "dri_", "eaf_", "esf_", "moe_")
        for key in list(filtered_config.keys()):
            if any(key.startswith(p) for p in LEGACY_PREFIXES):
                filtered_config.pop(key)

        # Add the validated technology_settings map
        filtered_config["technology_settings"] = tech_map

        # Convert year fields to Year objects
        from steelo.domain import Year

        if "start_year" in filtered_config:
            filtered_config["start_year"] = Year(filtered_config["start_year"])
        if "end_year" in filtered_config:
            filtered_config["end_year"] = Year(filtered_config["end_year"])

        # Convert file paths to Path objects
        path_fields = [
            "master_excel_path",
            "output_dir",
            "data_dir",
            "plots_dir",
            "geo_plots_dir",
            "pam_plots_dir",
            # Optional geo data paths
            "terrain_nc_path",
            "land_cover_tif_path",
            "rail_distance_nc_path",
            "countries_shapefile_dir",
            "disputed_areas_shapefile_dir",
            "landtype_percentage_nc_path",
            "baseload_power_sim_dir",
            "feasibility_mask_path",
        ]
        for field in path_fields:
            if field in filtered_config and filtered_config[field]:
                filtered_config[field] = Path(filtered_config[field])

        # Create the SimulationConfig using the factory method when data preparation is available,
        # otherwise use the manual config creation for backwards compatibility
        from steelo.simulation import SimulationConfig, GeoConfig
        from steelo.domain import Year

        if data_dir is not None and data_dir.exists():
            # Separate GeoConfig parameters from SimulationConfig parameters
            geo_params = [
                "max_altitude",
                "max_slope",
                "max_latitude",
                "hydrogen_ceiling_percentile",
                "included_power_mix",
                "intraregional_trade_allowed",
                "long_dist_pipeline_transport_cost",
                "include_infrastructure_cost",
                "include_transport_cost",
                "include_lulc_cost",
                "transportation_cost_per_km_per_ton",
                "priority_pct",
            ]
            # Only include geo parameters that are not None to avoid overriding defaults
            # Apply defensive type casting for specific fields
            geo_config_data = {}
            for k in geo_params:
                if k in filtered_config and filtered_config[k] is not None:
                    val = filtered_config[k]
                    # Cast max_latitude to float to ensure no Decimal leakage
                    if k == "max_latitude":
                        val = float(_pick(filtered_config, k, 65.0))
                    # Cast other numeric fields appropriately
                    elif k == "max_altitude":
                        val = float(_pick(filtered_config, k, 1500.0))
                    elif k == "max_slope":
                        val = float(_pick(filtered_config, k, 2.0))
                    elif k == "hydrogen_ceiling_percentile":
                        val = float(_pick(filtered_config, k, 20.0))
                    elif k == "long_dist_pipeline_transport_cost":
                        val = float(_pick(filtered_config, k, 1.0))
                    elif k == "priority_pct":
                        val = int(_pick(filtered_config, k, 5))
                    elif k == "transportation_cost_per_km_per_ton":
                        val = {route: float(v) for route, v in filtered_config[k].items() if v not in (None, "")}
                    geo_config_data[k] = val
            sim_config_data = {k: v for k, v in filtered_config.items() if k not in geo_params}
            # Apply type casting for consideration_time
            if "consideration_time" in sim_config_data:
                sim_config_data["consideration_time"] = int(_pick(sim_config_data, "consideration_time", 3))

            # Create GeoConfig if we have geo parameters
            extra_kwargs = {}
            if geo_config_data:
                extra_kwargs["geo_config"] = GeoConfig(**geo_config_data)

            # Use factory method with auto-detected paths
            config = SimulationConfig.from_data_directory(
                start_year=Year(sim_config_data["start_year"]),
                end_year=Year(sim_config_data["end_year"]),
                data_dir=data_dir,
                output_dir=Path(sim_config_data["output_dir"]),
                master_excel_path=master_excel_path if master_excel_path else None,
                **{
                    k: v
                    for k, v in sim_config_data.items()
                    if k not in ["start_year", "end_year", "output_dir", "master_excel_path", "data_dir"]
                },
                **extra_kwargs,
            )
        else:
            # Fallback to manual config creation
            # Separate GeoConfig parameters from SimulationConfig parameters
            geo_params = [
                "max_altitude",
                "max_slope",
                "max_latitude",
                "hydrogen_ceiling_percentile",
                "included_power_mix",
                "intraregional_trade_allowed",
                "long_dist_pipeline_transport_cost",
                "include_infrastructure_cost",
                "include_transport_cost",
                "include_lulc_cost",
                "transportation_cost_per_km_per_ton",
                "priority_pct",
            ]
            # Only include geo parameters that are not None to avoid overriding defaults
            # Apply defensive type casting for specific fields
            geo_config_data = {}
            for k in geo_params:
                if k in filtered_config and filtered_config[k] is not None:
                    val = filtered_config[k]
                    # Cast max_latitude to float to ensure no Decimal leakage
                    if k == "max_latitude":
                        val = float(_pick(filtered_config, k, 65.0))
                    # Cast other numeric fields appropriately
                    elif k == "max_altitude":
                        val = float(_pick(filtered_config, k, 1500.0))
                    elif k == "max_slope":
                        val = float(_pick(filtered_config, k, 2.0))
                    elif k == "hydrogen_ceiling_percentile":
                        val = float(_pick(filtered_config, k, 20.0))
                    elif k == "long_dist_pipeline_transport_cost":
                        val = float(_pick(filtered_config, k, 1.0))
                    elif k == "priority_pct":
                        val = int(_pick(filtered_config, k, 5))
                    elif k == "transportation_cost_per_km_per_ton":
                        val = {route: float(v) for route, v in filtered_config[k].items() if v not in (None, "")}
                    geo_config_data[k] = val
            sim_config_data = {k: v for k, v in filtered_config.items() if k not in geo_params}
            # Apply type casting for consideration_time
            if "consideration_time" in sim_config_data:
                sim_config_data["consideration_time"] = int(_pick(sim_config_data, "consideration_time", 3))

            # Create GeoConfig if we have geo parameters
            if geo_config_data:
                sim_config_data["geo_config"] = GeoConfig(**geo_config_data)

            config = SimulationConfig(**sim_config_data)

        def progress_callback(progress):
            # Convert Year objects to integers for JSON serialization
            progress_data = {
                "start_year": int(progress.start_year),
                "end_year": int(progress.end_year),
                "current_year": int(progress.current_year),
            }

            # Store each update in a list for history
            years = self.progress.get("years", [])
            years.append(progress_data)

            # Also store current progress at the top level for easier access
            self.progress["years"] = years
            self.progress["current_year"] = progress_data.get("current_year")
            self.progress["start_year"] = progress_data.get("start_year")
            self.progress["end_year"] = progress_data.get("end_year")

            self.save()

        # Run the real simulation
        from steelo.bootstrap import bootstrap_simulation

        runner = bootstrap_simulation(config)
        # Manually set the callbacks after creation
        runner.progress_callback = progress_callback
        runner.modelrun_id = self.id

        results = runner.run()
        return results

    @property
    def is_finished(self) -> bool:
        return self.state in [self.RunState.FINISHED, self.RunState.FAILED, self.RunState.CANCELLED]

    @property
    def is_running(self) -> bool:
        return self.state == self.RunState.RUNNING

    @property
    def has_progress(self) -> bool:
        return len(self.progress.get("years", [])) > 0

    @property
    def appears_stuck(self) -> bool:
        """Check if the simulation appears to be stuck (no updates for some time)"""
        from datetime import timedelta

        if self.state not in [self.RunState.RUNNING, self.RunState.CANCELLING]:
            return False

        # Check time since last update
        time_since_update = timezone.now() - self.updated_at

        # Simulations can take 30+ minutes per iteration, so we use a generous timeout
        # to avoid false positives. Only flag as stuck after 45 minutes of no updates.
        timeout = timedelta(minutes=45)

        return time_since_update > timeout

    @property
    def time_since_last_update(self) -> str:
        """Return human-readable time since last update"""
        from datetime import timedelta

        delta = timezone.now() - self.updated_at

        if delta < timedelta(minutes=1):
            return f"{delta.seconds} seconds ago"
        elif delta < timedelta(hours=1):
            minutes = delta.seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        else:
            hours = delta.seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"

    @property
    def current_progress(self) -> Progress | None:
        if not self.has_progress:
            return None
        current_year = self.progress["years"][-1]
        return Progress(**current_year)

    @property
    def failed_with_exception(self) -> bool:
        """Return True when the run failed due to an exception with a traceback."""
        if self.state != self.RunState.FAILED:
            return False
        if not self.error_message:
            return False
        return "Traceback" in self.error_message

    @property
    def can_rerun(self) -> bool:
        """Allow reruns for cancelled runs or failures caused by exceptions."""
        if self.state == self.RunState.CANCELLED:
            return True
        return self.failed_with_exception

    def reset_for_rerun(self) -> None:
        """Restore the run to a pristine CREATED state so it can be queued again."""
        self.state = self.RunState.CREATED
        self.error_message = ""
        self.results = {}
        self.progress = {}
        self.finished_at = None
        self.task_id = None
        self.save(
            update_fields=[
                "state",
                "error_message",
                "results",
                "progress",
                "finished_at",
                "task_id",
                "updated_at",
            ]
        )

    def mark_as_failed_if_stuck(self) -> bool:
        """
        Check if simulation is stuck and mark as failed if it is.
        Uses progress update heartbeat to detect crashed workers.
        Returns True if the simulation was marked as failed, False otherwise.
        """
        # Only check for running simulations
        if self.state != self.RunState.RUNNING:
            return False

        from datetime import timedelta

        # Give a grace period after starting - simulations take time to start
        time_since_start = timezone.now() - self.started_at
        if time_since_start < timedelta(minutes=1):
            return False

        # Check if we've had heartbeat updates recently
        # Tasks now send heartbeat every 30 seconds, so lack of updates indicates crash
        time_since_update = timezone.now() - self.updated_at

        # If no heartbeat for 25 minutes, worker likely crashed (heartbeat is every 30 seconds)
        if time_since_update > timedelta(minutes=25):
            self.state = self.RunState.FAILED
            self.error_message = (
                f"Worker process crashed or was terminated - no heartbeat for {self.time_since_last_update}."
            )
            self.finished_at = timezone.now()
            self.save()
            return True

        return False

    def get_state_color(self) -> str:
        """Get Bootstrap color class for the current state"""
        state_colors = {
            self.RunState.CREATED: "secondary",
            self.RunState.RUNNING: "primary",
            self.RunState.FINISHED: "success",
            self.RunState.FAILED: "danger",
            self.RunState.CANCELLING: "warning",
            self.RunState.CANCELLED: "warning",
        }
        return state_colors.get(self.state, "secondary")

    def capture_result_csv(self, output_dir: Path | None = None) -> bool:
        """
        Capture the CSV result file from the simulation output directory.

        Args:
            output_dir: Override the default output directory for testing

        Returns:
            bool: True if CSV was successfully captured, False otherwise
        """
        from django.core.files.base import ContentFile

        if output_dir is None:
            # Use the model run's isolated directory
            output_dir = self.get_output_path()
            if not output_dir:
                raise ValueError("ModelRun must have an output path set")

        # Append TM subdirectory
        output_dir = output_dir / "TM"

        if not output_dir.exists():
            return False

        # Find the most recent post_processed CSV file
        csv_files = list(output_dir.glob("post_processed_*.csv"))
        if not csv_files:
            return False

        # Get the most recent file
        latest_csv = max(csv_files, key=lambda f: f.stat().st_mtime)

        # Read the file and save it to the model
        try:
            with open(latest_csv, "rb") as f:
                content = f.read()
                self.result_csv.save(f"simulation_results_{self.id}.csv", ContentFile(content), save=False)
            self.save()  # Save the model to persist the file reference
            return True
        except Exception as e:
            import logging

            logging.exception(f"Failed to capture CSV: {e}")
            return False

    def get_technology_switches(self) -> dict[str, list[str]]:
        """
        Get the allowed technology switches from the tech_switches_allowed.csv file.

        Returns a dictionary where keys are source technologies and values are lists of allowed target technologies.

        Raises:
            FileNotFoundError: If tech_switches_allowed.csv is not available
        """
        # Tech switches are only available from data preparation
        if not self.data_preparation or not self.data_preparation.is_ready():
            raise FileNotFoundError("Technology switches data not available - no data preparation configured")

        data_path = self.data_preparation.get_data_path()
        if not data_path:
            raise FileNotFoundError("Technology switches data not available - data preparation path not found")

        fixtures_dir = data_path / "data" / "fixtures"
        tech_switches_path = fixtures_dir / "tech_switches_allowed.csv"
        if not tech_switches_path.exists():
            raise FileNotFoundError(f"Technology switches file not found at: {tech_switches_path}")

        # Read and parse the CSV file
        allowed_transitions = {}
        try:
            with open(tech_switches_path, "r") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]

            # Header row: drop the first cell (index name), keep tech names
            tech_names = lines[0].split(",")[1:]

            # Process each data row
            for row in lines[1:]:
                parts = row.split(",")
                origin = parts[0]
                flags = parts[1:]

                allowed = []
                for tech, flag in zip(tech_names, flags):
                    if flag == "YES" and not tech.endswith("CCUS"):
                        allowed.append(tech)

                allowed_transitions[origin] = allowed

        except Exception as e:
            import logging

            logging.exception(f"Failed to read technology switches: {e}")
            # Return empty dict if file reading fails
            return {}

        return allowed_transitions


def result_image_upload_path(instance, filename) -> str:
    """Generate upload path for result image files"""
    return f"results/{instance.modelrun.id}/{filename}"


class ResultImages(models.Model):
    """
    Result Images (maps) for a model run.
    """

    lcoe_map = models.ImageField(upload_to=result_image_upload_path, verbose_name="LCOE Map", blank=True)
    lcoh_map = models.ImageField(upload_to=result_image_upload_path, verbose_name="LCOH Map", blank=True)
    priority_locations_iron = models.ImageField(
        upload_to=result_image_upload_path, verbose_name="Priority Locations for Iron", blank=True
    )
    priority_locations_steel = models.ImageField(
        upload_to=result_image_upload_path, verbose_name="Priority Locations for Steel", blank=True
    )
    new_plants_iron_construction = models.ImageField(
        upload_to=result_image_upload_path, verbose_name="New Iron Plants Under Construction", blank=True
    )
    new_plants_steel_construction = models.ImageField(
        upload_to=result_image_upload_path, verbose_name="New Steel Plants Under Construction", blank=True
    )
    new_plants_iron_status = models.ImageField(
        upload_to=result_image_upload_path, verbose_name="New Iron Plants by Status", blank=True
    )
    new_plants_steel_status = models.ImageField(
        upload_to=result_image_upload_path, verbose_name="New Steel Plants by Status", blank=True
    )
    modelrun = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="result_images")

    class Meta:
        verbose_name = "Result Images"
        verbose_name_plural = "Result Images"

    def __str__(self):
        return f"Result Images for Model Run #{self.modelrun.id}"

    @classmethod
    def create_from_plots(cls, modelrun, plots_dir: Path | None = None):
        """
        Create ResultImages by loading available plots from the simulation output directory.

        Args:
            modelrun: The ModelRun instance
            plots_dir: Override the default output directory for testing

        Returns:
            ResultImages: The created instance with image fields populated for available plots
        """
        from django.core.files.base import ContentFile
        import logging
        import re

        logger = logging.getLogger(__name__)

        def sort_plots_by_year(paths: list[Path]) -> list[Path]:
            """
            Sort plot files by year (descending) extracted from filename.
            Falls back to modification time if no year is found.

            Examples:
                optimal_lcoe_2035.png -> year 2035
                lcoh_2030_p15.png -> year 2030
                top20_priority_locations_iron_2027.png -> year 2027
                lcoe_map.png -> use mtime

            Args:
                paths: List of Path objects to sort

            Returns:
                List of Path objects sorted by year (newest first)
            """

            def get_sort_key(path: Path) -> tuple[int, float]:
                # Try to extract year from filename
                # Matches patterns like: _2025.png, _2025_p15.png, _2027_p20.png
                match = re.search(r"_(\d{4})(?:_p\d+)?\.png$", path.name)
                if match:
                    year = int(match.group(1))
                    # Return negative year for descending sort, and mtime as secondary key
                    mtime = path.stat().st_mtime if path.exists() else 0
                    return (-year, -mtime)
                else:
                    # No year found - sort by mtime only (put these after year-specific files)
                    mtime = path.stat().st_mtime if path.exists() else 0
                    return (0, -mtime)  # 0 puts non-year files after year files

            return sorted(paths, key=get_sort_key)

        if plots_dir is None:
            # Use the model run's isolated directory if available
            output_path = modelrun.get_output_path()
            if output_path:
                plots_dir = output_path / "plots"
            else:
                raise ValueError("ModelRun must have an output path set")

        # Directory names for PAM and GEO plots
        pam_plots_dir = plots_dir / "PAM"
        geo_plots_dir = plots_dir / "GEO"

        # Mapping of field names to potential plot files
        # Note: Using glob patterns to match files with any priority percentage (e.g., top5, top20, etc.)
        # and year suffixes, sorted by year (newest first) to prefer recent plots over stale ones
        plot_mappings = {
            "lcoe_map": [
                *sort_plots_by_year(
                    list(geo_plots_dir.glob("optimal_lcoe_*.png"))
                ),  # Year-specific files (newest year first)
                geo_plots_dir / "lcoe_map.png",
                geo_plots_dir / "optimal_lcoe.png",
                pam_plots_dir / "lcoe_visualization.png",
            ],
            "lcoh_map": [
                # Match actual filename pattern: lcoh_{year}_p{percentage}.png
                *sort_plots_by_year(list(geo_plots_dir.glob("lcoh_*_p*.png"))),  # e.g., lcoh_2025_p15.png
                *sort_plots_by_year(list(geo_plots_dir.glob("optimal_lcoh_*.png"))),  # Keep for backward compatibility
                geo_plots_dir / "lcoh_map.png",
                geo_plots_dir / "optimal_lcoh.png",
                pam_plots_dir / "lcoh_visualization.png",
            ],
            "priority_locations_iron": [
                # Match any percentage (top5, top20, etc.) with year suffix, sorted by year (newest first)
                *sort_plots_by_year(list(geo_plots_dir.glob("top*_priority_locations_iron_*.png"))),
                # Match any percentage without year suffix, sorted by mtime
                *sort_plots_by_year(list(geo_plots_dir.glob("top*_priority_locations_iron.png"))),
                pam_plots_dir / "iron_priority_locations.png",
            ],
            "priority_locations_steel": [
                # Match any percentage (top5, top20, etc.) with year suffix, sorted by year (newest first)
                *sort_plots_by_year(list(geo_plots_dir.glob("top*_priority_locations_steel_*.png"))),
                # Match any percentage without year suffix, sorted by mtime
                *sort_plots_by_year(list(geo_plots_dir.glob("top*_priority_locations_steel.png"))),
                pam_plots_dir / "steel_priority_locations.png",
            ],
            "new_plants_iron_construction": [
                geo_plots_dir / "new_iron_plants_map.png",  # New filename (operating plants)
                geo_plots_dir / "new_iron_plants_under_construction_map.png",  # Old filename for backward compatibility
                pam_plots_dir / "iron_plants_construction.png",
            ],
            "new_plants_steel_construction": [
                geo_plots_dir / "new_steel_plants_map.png",  # New filename (operating plants)
                geo_plots_dir
                / "new_steel_plants_under_construction_map.png",  # Old filename for backward compatibility
                pam_plots_dir / "steel_plants_construction.png",
            ],
            "new_plants_iron_status": [
                geo_plots_dir / "new_iron_plants_by_status.png",
                pam_plots_dir / "iron_plants_status.png",
            ],
            "new_plants_steel_status": [
                geo_plots_dir / "new_steel_plants_by_status.png",
                pam_plots_dir / "steel_plants_status.png",
            ],
        }

        # Create empty ResultImages object
        result_images = cls(modelrun=modelrun)

        logger.info(f"Creating ResultImages for ModelRun {modelrun.id} from plots directory: {plots_dir}")

        # Load and save each image
        for field_name, potential_paths in plot_mappings.items():
            file_content = None
            file_name = None

            # First, try to find a real plot
            for plot_path in potential_paths:
                if plot_path.exists():
                    with open(plot_path, "rb") as f:
                        file_content = f.read()
                    file_name = plot_path.name
                    break

            # For priority locations, also check for year-specific files
            if file_content is None and "priority_locations" in field_name:
                product = "iron" if "iron" in field_name else "steel"
                # Look for any year-specific file
                pattern = f"top5_priority_locations_{product}_*.png"
                for year_file in sorted(geo_plots_dir.glob(pattern)):
                    with open(year_file, "rb") as f:
                        file_content = f.read()
                    file_name = year_file.name
                    break

            # Log warning if no plot found
            if file_content is None:
                logger.warning(f"No plot found for {field_name} - checked paths: {[str(p) for p in potential_paths]}")

            # Save the content to the model field
            if file_content and file_name:
                getattr(result_images, field_name).save(file_name, ContentFile(file_content))
                logger.debug(f"Saved image for {field_name}: {file_name}")
            else:
                logger.info(f"No image saved for {field_name} - field will remain empty")

        result_images.save()
        return result_images


def simulation_plot_upload_path(instance, filename) -> str:
    """Generate upload path for simulation plot files"""
    return f"results/{instance.modelrun.id}/plots/{filename}"


class SimulationPlot(models.Model):
    """
    Individual simulation plots generated from CSV results.
    """

    class PlotType(models.TextChoices):
        CAPACITY_ADDED = "capacity_added", "Added Capacity by Technology"
        CAPACITY_DEVELOPMENT = "capacity_development", "Capacity Development by Technology"
        COST_CURVE = "cost_curve", "Cost Curve"
        PRODUCTION_REGION = "production_region", "Production by Region"
        PRODUCTION_TECHNOLOGY = "production_technology", "Production by Technology"
        CAPACITY_REGION = "capacity_region", "Capacity by Region"
        OTHER = "other", "Other"

    modelrun = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="simulation_plots")
    plot_type = models.CharField(max_length=50, choices=PlotType.choices)
    title = models.CharField(max_length=200)
    image = models.ImageField(upload_to=simulation_plot_upload_path)
    product_type = models.CharField(max_length=20, blank=True, help_text="steel or iron, if applicable")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["plot_type", "-product_type", "title"]

    def __str__(self):
        return f"{self.title} - {self.modelrun.id}"

    @classmethod
    def capture_simulation_plots(cls, modelrun, pam_plots_dir: Path | None = None):
        """
        Find and capture all simulation plots generated from CSV processing.
        """
        if pam_plots_dir is None:
            # Use the model run's isolated directory if available
            output_path = modelrun.get_output_path()
            if output_path:
                pam_plots_dir = output_path / "plots" / "PAM"
            else:
                raise ValueError("ModelRun must have an output path set")

        if not pam_plots_dir.exists():
            return []

        # Define plot mappings with metadata
        plot_configs = [
            {
                "pattern": "year2year_added_capacity_by_technology.png",
                "plot_type": cls.PlotType.CAPACITY_ADDED,
                "title": "Added Capacity by Technology",
            },
            {
                "pattern": "Capacity_development_by_technology.png",
                "plot_type": cls.PlotType.CAPACITY_DEVELOPMENT,
                "title": "Capacity Development by Technology",
            },
            {
                "pattern": "steel_cost_curve_*.png",
                "plot_type": cls.PlotType.COST_CURVE,
                "title": "Steel Cost Curve",
                "product_type": "steel",
            },
            {
                "pattern": "iron_cost_curve_*.png",
                "plot_type": cls.PlotType.COST_CURVE,
                "title": "Iron Cost Curve",
                "product_type": "iron",
            },
            {
                "pattern": "steel_production_development_by_region.png",
                "plot_type": cls.PlotType.PRODUCTION_REGION,
                "title": "Steel Production by Region",
                "product_type": "steel",
            },
            {
                "pattern": "iron_production_development_by_region.png",
                "plot_type": cls.PlotType.PRODUCTION_REGION,
                "title": "Iron Production by Region",
                "product_type": "iron",
            },
            {
                "pattern": "steel_production_development_by_technology.png",
                "plot_type": cls.PlotType.PRODUCTION_TECHNOLOGY,
                "title": "Steel Production by Technology",
                "product_type": "steel",
            },
            {
                "pattern": "iron_production_development_by_technology.png",
                "plot_type": cls.PlotType.PRODUCTION_TECHNOLOGY,
                "title": "Iron Production by Technology",
                "product_type": "iron",
            },
            {
                "pattern": "steel_capacity_development_by_region.png",
                "plot_type": cls.PlotType.CAPACITY_REGION,
                "title": "Steel Capacity by Region",
                "product_type": "steel",
            },
            {
                "pattern": "iron_capacity_development_by_region.png",
                "plot_type": cls.PlotType.CAPACITY_REGION,
                "title": "Iron Capacity by Region",
                "product_type": "iron",
            },
        ]

        created_plots = []

        for config in plot_configs:
            pattern = config["pattern"]

            # Handle wildcard patterns
            if "*" in pattern:
                for plot_file in pam_plots_dir.glob(pattern):
                    plot = cls._create_plot_from_file(modelrun, plot_file, config)
                    if plot:
                        created_plots.append(plot)
            else:
                plot_file = pam_plots_dir / pattern
                if plot_file.exists():
                    plot = cls._create_plot_from_file(modelrun, plot_file, config)
                    if plot:
                        created_plots.append(plot)

        return created_plots

    @classmethod
    def _create_plot_from_file(cls, modelrun, plot_file, config):
        """Helper to create a SimulationPlot from a file."""
        from django.core.files.base import ContentFile

        try:
            with open(plot_file, "rb") as f:
                content = f.read()

            plot = cls(
                modelrun=modelrun,
                plot_type=config["plot_type"],
                title=config.get("title", plot_file.stem.replace("_", " ").title()),
                product_type=config.get("product_type", ""),
            )

            plot.image.save(plot_file.name, ContentFile(content), save=False)
            plot.save()

            return plot

        except Exception as e:
            import logging

            logging.exception(f"Failed to capture plot {plot_file}: {e}")
            return None


class Worker(models.Model):
    """
    Database model representing a worker process with production-grade features:
    - PID reuse protection via create_time tracking
    - Launch token handshake for security
    - Proper state machine with DRAINING support
    """

    class WorkerState(models.TextChoices):
        STARTING = "STARTING", _("Starting")  # Just launched, waiting for handshake
        RUNNING = "RUNNING", _("Running")  # Active and processing tasks
        DRAINING = "DRAINING", _("Draining")  # Finishing current task then exiting
        FAILED = "FAILED", _("Failed")  # Failed to start or crashed
        DEAD = "DEAD", _("Dead")  # Process terminated

    # Unique identifier for this worker
    worker_id = models.CharField(max_length=40, unique=True, db_index=True)

    # Process ID when running, null when not started/dead
    pid = models.IntegerField(null=True, blank=True, db_index=True)

    # PID create time for reuse protection
    pid_started_at = models.DateTimeField(null=True, blank=True)

    # Worker state machine
    state = models.CharField(max_length=10, choices=WorkerState.choices, default=WorkerState.STARTING, db_index=True)

    # Timestamps for lifecycle tracking
    started_at = models.DateTimeField(auto_now_add=True)
    heartbeat = models.DateTimeField(null=True, blank=True)

    # Launch token for secure handshake
    launch_token = models.CharField(max_length=32, null=True, blank=True)

    # Process and error information
    log_path = models.TextField(null=True, blank=True)
    last_error_tail = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "steeloweb_worker"
        indexes = [
            models.Index(fields=["state", "started_at"]),
            models.Index(fields=["state", "heartbeat"]),
        ]

    def is_alive(self):
        """Check if the worker process exists (may be different process with reused PID)"""
        if not self.pid:
            return False

        try:
            import psutil

            return psutil.pid_exists(self.pid)
        except Exception:
            # Fallback using os.kill
            import os

            try:
                os.kill(self.pid, 0)
                return True
            except (OSError, ProcessLookupError):
                return False

    def is_same_process(self):
        """Check if it's the SAME process (not just same PID) - prevents PID reuse issues"""
        if not self.pid or not self.pid_started_at:
            return False

        try:
            import psutil

            process = psutil.Process(self.pid)
            # Compare create times (allow 1 second tolerance for clock skew)
            create_time = process.create_time()
            stored_time = self.pid_started_at.timestamp()
            return abs(create_time - stored_time) < 1.0
        except psutil.NoSuchProcess:
            return False
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Error checking process {self.pid}: {e}")
            # Fall back to simple existence check
            return self.is_alive()

    def get_process_stats(self):
        """Get process statistics if it's the same process"""
        if not self.pid or not self.is_same_process():
            return None

        try:
            import psutil

            process = psutil.Process(self.pid)
            memory_info = process.memory_info()

            # Get children for complete stats
            children = process.children(recursive=True)
            child_memory = sum(child.memory_info().rss for child in children)

            return {
                "memory": memory_info.rss,
                "memory_with_children": memory_info.rss + child_memory,
                "cpu": process.cpu_percent(interval=0.1),
                "threads": process.num_threads(),
                "num_children": len(children),
                "status": process.status(),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def time_since_heartbeat(self):
        """Calculate time since last heartbeat - human readable"""
        if not self.heartbeat:
            return "Never"

        delta = timezone.now() - self.heartbeat

        if delta.days > 0:
            return f"{delta.days} days ago"
        elif delta.total_seconds() > 3600:
            hours = int(delta.total_seconds() // 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif delta.total_seconds() > 60:
            minutes = int(delta.total_seconds() // 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        else:
            seconds = int(delta.total_seconds())
            return f"{seconds} second{'s' if seconds != 1 else ''} ago"

    def kill_process_tree(self):
        """Kill this worker and all child processes"""
        if not self.pid or not self.is_same_process():
            return False

        try:
            import psutil
            import logging

            logger = logging.getLogger(__name__)

            process = psutil.Process(self.pid)

            # Get all children
            children = process.children(recursive=True)

            # Kill children first
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass

            # Kill parent
            process.kill()

            logger.info(f"Killed worker {self.worker_id} (PID {self.pid}) and {len(children)} children")
            return True

        except psutil.NoSuchProcess:
            import logging

            logger = logging.getLogger(__name__)
            logger.info(f"Worker {self.worker_id} process already dead")
            return False
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Failed to kill worker {self.worker_id}: {e}")
            return False

    def __str__(self):
        return f"Worker {self.worker_id} ({self.state})"


class AdmissionControl(models.Model):
    """
    Singleton model for SQLite-safe admission control.
    Used to force SQLite writer lock during worker spawning.
    """

    id = models.IntegerField(primary_key=True, default=1)  # Always use ID=1
    timestamp = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "steeloweb_admission_control"


class Scenario(models.Model):
    """Base scenario with parameter overrides"""

    # Metadata
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    # Links
    master_excel = models.ForeignKey(
        'MasterExcelFile',
        on_delete=models.PROTECT,
        related_name='scenarios'
    )
    base_scenario = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='derived_scenarios'
    )

    # Simulation timeframe
    start_year = models.IntegerField(default=2025)
    end_year = models.IntegerField(default=2050)

    # Override storage (JSON fields)
    technology_overrides = models.JSONField(default=dict, blank=True)
    economic_overrides = models.JSONField(default=dict, blank=True)
    geospatial_overrides = models.JSONField(default=dict, blank=True)
    policy_overrides = models.JSONField(default=dict, blank=True)
    agent_overrides = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Scenario'
        verbose_name_plural = 'Scenarios'

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('scenario_detail', kwargs={'pk': self.pk})

    def get_all_overrides(self):
        """Combine all override categories into a single dict"""
        return {
            'technology': self.technology_overrides,
            'economic': self.economic_overrides,
            'geospatial': self.geospatial_overrides,
            'policy': self.policy_overrides,
            'agent': self.agent_overrides,
        }

    def count_variations(self):
        """Count the number of variations for this scenario"""
        return self.variations.filter(is_active=True).count()

    def count_runs(self):
        """Count total model runs across all variations"""
        # This would be implemented when ModelRun links to scenarios
        return 0


class ScenarioVariation(models.Model):
    """Named variation of a base scenario"""

    scenario = models.ForeignKey(
        Scenario,
        related_name='variations',
        on_delete=models.CASCADE
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    additional_overrides = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scenario', 'name']
        verbose_name = 'Scenario Variation'
        verbose_name_plural = 'Scenario Variations'

    def __str__(self):
        return f"{self.scenario.name} - {self.name}"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('scenario_variation_detail', kwargs={'pk': self.pk})

    def get_merged_overrides(self):
        """Merge base scenario overrides with variation-specific overrides"""
        base = self.scenario.get_all_overrides()

        # Deep merge additional_overrides into base
        for category, overrides in self.additional_overrides.items():
            if category in base:
                base[category] = self._deep_merge(base[category], overrides)
            else:
                base[category] = overrides

        return base

    def _deep_merge(self, base, overrides):
        """Deep merge two dictionaries"""
        result = base.copy()
        for key, value in overrides.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


class SensitivitySweep(models.Model):
    """Automated parameter sweep for sensitivity analysis"""

    VARIATION_TYPES = [
        ('percentage', 'Percentage (±%)'),
        ('absolute', 'Absolute (±value)'),
        ('range', 'Range (min to max)'),
    ]

    scenario = models.ForeignKey(
        Scenario,
        related_name='sweeps',
        on_delete=models.CASCADE
    )
    name = models.CharField(max_length=200)
    parameter_path = models.CharField(
        max_length=200,
        help_text='Dot-separated path to parameter (e.g., "economic.discount_rate")'
    )
    base_value = models.FloatField()
    variation_type = models.CharField(max_length=20, choices=VARIATION_TYPES)
    variation_values = models.JSONField(
        help_text='List of values or parameters for the variation type'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scenario', 'name']
        verbose_name = 'Sensitivity Sweep'
        verbose_name_plural = 'Sensitivity Sweeps'

    def __str__(self):
        return f"{self.scenario.name} - {self.name}"

    def count_runs(self):
        """Calculate number of runs this sweep will generate"""
        if isinstance(self.variation_values, list):
            return len(self.variation_values)
        return 0
