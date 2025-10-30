"""Excel validation and conversion to JSON repositories."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ..adapters.dataprocessing import excel_reader
from ..adapters.repositories.json_repository import (
    PlantJsonRepository,
    DemandCenterJsonRepository,
    SupplierJsonRepository,
)
from ..domain.constants import PLANT_LIFETIME
from .exceptions import DataValidationError

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of data validation."""

    valid: bool
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    data: Any = None

    def add_error(self, field: str, message: str, row: int | None = None) -> None:
        """Add a validation error."""
        error: dict[str, Any] = {"field": field, "message": message}
        if row is not None:
            error["row"] = row
        self.errors.append(error)
        self.valid = False

    def add_warning(self, field: str, message: str, row: int | None = None) -> None:
        """Add a validation warning."""
        warning: dict[str, Any] = {"field": field, "message": message}
        if row is not None:
            warning["row"] = row
        self.warnings.append(warning)


class ExcelValidator:
    """Validates and converts Excel files to JSON repositories."""

    def __init__(
        self,
        strict_mode: bool = False,
        country_mappings_path: Path | None = None,
        location_csv_path: Path | None = None,
        gravity_distances_path: Path | None = None,
    ):
        """Initialize validator.

        Args:
            strict_mode: If True, warnings become errors
            country_mappings_path: Path to country mappings JSON file
            location_csv_path: Path to location CSV file
            gravity_distances_path: Path to gravity distances pickle file
        """
        self.strict_mode = strict_mode
        self.country_mappings_path = country_mappings_path
        self.location_csv_path = location_csv_path
        self.gravity_distances_path = gravity_distances_path

    def validate_plants_file(self, file_path: Path) -> ValidationResult:
        """Validate plants Excel/CSV file.

        Args:
            file_path: Path to Excel or CSV file

        Returns:
            ValidationResult with errors and validated data
        """
        result = ValidationResult(valid=True)

        try:
            # Read the file
            if file_path.suffix.lower() in [".xlsx", ".xls"]:
                df = pd.read_excel(file_path)
            else:
                df = pd.read_csv(file_path)

            # Check required columns
            required_columns = [
                "name",
                "country",
                "technologies",
                "start_year",
                "latitude",
                "longitude",
            ]
            missing_columns = set(required_columns) - set(df.columns)
            if missing_columns:
                result.add_error(
                    "columns",
                    f"Missing required columns: {', '.join(missing_columns)}",
                )
                return result

            # Validate each row
            for idx_raw, row in df.iterrows():
                # Convert idx to int for row numbers
                idx = int(idx_raw) if isinstance(idx_raw, (int, float, str)) else 0
                # Check for missing values
                for col in required_columns:
                    if pd.isna(row[col]):
                        result.add_error(col, "Missing value", row=int(idx) + 2)

                # Validate coordinates
                if not pd.isna(row["latitude"]):
                    lat = float(row["latitude"])
                    if not -90 <= lat <= 90:
                        result.add_error(
                            "latitude",
                            f"Invalid latitude: {lat}",
                            row=int(idx) + 2,
                        )

                if not pd.isna(row["longitude"]):
                    lon = float(row["longitude"])
                    if not -180 <= lon <= 180:
                        result.add_error(
                            "longitude",
                            f"Invalid longitude: {lon}",
                            row=int(idx) + 2,
                        )

                # Validate start year
                if not pd.isna(row["start_year"]):
                    year = int(row["start_year"])
                    if year < 1900 or year > 2100:
                        result.add_warning(
                            "start_year",
                            f"Unusual start year: {year}",
                            row=int(idx) + 2,
                        )

            # If valid, we could optionally parse with MasterExcelReader in the future
            # For now, just mark as valid if basic structure is correct
            if result.valid or (not self.strict_mode and not result.errors):
                # Validation passed - we don't need to actually parse the plants
                # since that requires additional CSV files that may not be available
                result.add_warning(
                    "parsing", "Full parsing skipped - use MasterExcelReader for complete plant data loading"
                )

        except Exception as e:
            result.add_error("file", f"Failed to read file: {str(e)}")

        return result

    def validate_demand_file(self, file_path: Path) -> ValidationResult:
        """Validate demand centers Excel file."""
        result = ValidationResult(valid=True)

        try:
            # Read the file
            df = pd.read_excel(file_path, sheet_name=None)

            # Check required sheets
            required_sheets = ["info", "total_demand", "commodity_demand"]
            missing_sheets = set(required_sheets) - set(df.keys())
            if missing_sheets:
                result.add_error(
                    "sheets",
                    f"Missing required sheets: {', '.join(missing_sheets)}",
                )
                return result

            # Validate info sheet
            info_df = df["info"]
            info_columns = ["demand_center", "country", "latitude", "longitude"]
            missing_columns = set(info_columns) - set(info_df.columns)
            if missing_columns:
                result.add_error(
                    "info_sheet",
                    f"Missing columns in info sheet: {', '.join(missing_columns)}",
                )

            # Validate demand data
            if result.valid or (not self.strict_mode and not result.errors):
                try:
                    if self.location_csv_path and self.gravity_distances_path:
                        demand_centers = excel_reader.read_demand_centers(
                            gravity_distances_path=self.gravity_distances_path,
                            demand_excel_path=file_path,
                            demand_sheet_name="Sheet1",
                            location_csv=self.location_csv_path,
                        )
                        result.data = demand_centers
                    else:
                        result.add_warning(
                            "data_paths",
                            "Location CSV or gravity distances path not provided - skipping full validation",
                        )
                except Exception as e:
                    result.add_error("parsing", f"Failed to parse file: {str(e)}")

        except Exception as e:
            result.add_error("file", f"Failed to read file: {str(e)}")

        return result

    def validate_suppliers_file(self, file_path: Path) -> ValidationResult:
        """Validate suppliers Excel file."""
        result = ValidationResult(valid=True)

        try:
            # Determine if it's scrap or mines based on filename
            is_scrap = "scrap" in file_path.stem.lower()

            if self.location_csv_path:
                if is_scrap:
                    suppliers = excel_reader.read_scrap_as_suppliers(
                        str(file_path), "Sheet1", str(self.location_csv_path), gravity_distances_pkl_path=None
                    )
                else:
                    suppliers = excel_reader.read_mines_as_suppliers(
                        str(file_path), "Outputs for Model", str(self.location_csv_path)
                    )

                result.data = suppliers
            else:
                result.add_warning("data_paths", "Location CSV path not provided - skipping supplier validation")

        except Exception as e:
            result.add_error("file", f"Failed to read suppliers file: {str(e)}")

        return result

    def validate_excel(self, file_path: Path) -> dict[str, Any]:
        """Validate a single Excel file and return results in CLI format.

        Args:
            file_path: Path to Excel file

        Returns:
            Dictionary with 'valid', 'errors', and 'warnings' keys
        """
        file_type = self._determine_file_type(file_path)

        if file_type == "plants":
            result = self.validate_plants_file(file_path)
        elif file_type == "demand":
            result = self.validate_demand_file(file_path)
        elif file_type in ["scrap_suppliers", "mine_suppliers"]:
            result = self.validate_suppliers_file(file_path)
        else:
            return {"valid": False, "errors": [f"Unknown file type for: {file_path.name}"], "warnings": []}

        return {
            "valid": result.valid,
            "errors": [f"{e.get('field', 'general')}: {e.get('message', str(e))}" for e in result.errors],
            "warnings": [f"{w.get('field', 'general')}: {w.get('message', str(w))}" for w in result.warnings],
        }

    def _determine_file_type(self, file_path: Path) -> str:
        """Determine file type based on filename."""
        name_lower = file_path.stem.lower()

        if "plant" in name_lower:
            return "plants"
        elif "demand" in name_lower:
            return "demand"
        elif "scrap" in name_lower:
            return "scrap_suppliers"
        elif "mine" in name_lower:
            return "mine_suppliers"
        else:
            return "unknown"

    def validate_all_files(self, directory: Path) -> dict[str, ValidationResult]:
        """Validate all Excel files in a directory.

        Args:
            directory: Directory containing Excel files

        Returns:
            Dictionary mapping file types to validation results
        """
        results = {}

        # Define file patterns
        patterns = {
            "plants": ["*plant*.xlsx", "*plant*.csv"],
            "demand": ["*demand*.xlsx"],
            "scrap_suppliers": ["*scrap*.xlsx"],
            "mine_suppliers": ["*mine*.xlsx"],
            "tariffs": ["*tariff*.xlsx"],
        }

        for file_type, file_patterns in patterns.items():
            for pattern in file_patterns:
                files = list(directory.glob(pattern))
                if files:
                    # Validate first matching file
                    file_path = files[0]
                    if file_type == "plants":
                        results[file_type] = self.validate_plants_file(file_path)
                    elif file_type == "demand":
                        results[file_type] = self.validate_demand_file(file_path)
                    elif file_type in ["scrap_suppliers", "mine_suppliers"]:
                        results[file_type] = self.validate_suppliers_file(file_path)
                    break

        return results

    def convert_to_repositories(self, directory: Path, output_dir: Path) -> dict[str, Path]:
        """Convert validated Excel files to JSON repositories.

        Args:
            directory: Directory containing Excel files
            output_dir: Directory to write JSON repositories

        Returns:
            Dictionary mapping repository names to file paths

        Raises:
            DataValidationError: If validation fails
        """
        # First validate all files
        validation_results = self.validate_all_files(directory)

        # Check for errors
        all_errors = []
        for file_type, result in validation_results.items():
            if not result.valid:
                all_errors.extend([{"file_type": file_type, **error} for error in result.errors])

        if all_errors and self.strict_mode:
            raise DataValidationError("Validation failed for input files", errors=all_errors)

        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)

        # Convert to repositories
        repo_paths = {}

        # Plants
        if "plants" in validation_results and validation_results["plants"].data:
            plants_repo = PlantJsonRepository(output_dir / "plants.json", PLANT_LIFETIME)
            for plant in validation_results["plants"].data:
                plants_repo.add(plant)
            # JsonRepository saves automatically
            repo_paths["plants"] = output_dir / "plants.json"

        # Demand centers
        if "demand" in validation_results and validation_results["demand"].data:
            demand_repo = DemandCenterJsonRepository(output_dir / "demand_centers.json")
            for dc in validation_results["demand"].data:
                demand_repo.add(dc)
            # JsonRepository saves automatically
            repo_paths["demand_centers"] = output_dir / "demand_centers.json"

        # Suppliers
        suppliers_repo = SupplierJsonRepository(output_dir / "suppliers.json")
        for supplier_type in ["scrap_suppliers", "mine_suppliers"]:
            if supplier_type in validation_results and validation_results[supplier_type].data:
                for supplier in validation_results[supplier_type].data:
                    suppliers_repo.add(supplier)
        # Check if we added any suppliers
        if supplier_type in validation_results and any(
            validation_results[st].data for st in ["scrap_suppliers", "mine_suppliers"] if st in validation_results
        ):
            repo_paths["suppliers"] = output_dir / "suppliers.json"

        return repo_paths
