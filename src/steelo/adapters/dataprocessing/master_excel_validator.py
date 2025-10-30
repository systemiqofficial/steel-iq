"""
Master Excel Input File Validator

Shared validation logic for master input Excel files, used by both
steelo-data-prepare CLI and Django forms to ensure consistency.
"""

import pandas as pd
from pathlib import Path
from typing import Callable, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """Represents a validation error with context"""

    sheet_name: str
    error_type: str
    message: str
    row_number: int | None = None
    column_name: str | None = None
    severity: str = "ERROR"  # ERROR, WARNING, INFO

    def __str__(self):
        location = f"Sheet '{self.sheet_name}'"
        if self.row_number is not None:
            location += f", row {self.row_number}"
        if self.column_name:
            location += f", column '{self.column_name}'"
        return f"[{self.severity}] {location}: {self.message}"


@dataclass
class ValidationReport:
    """Contains all validation results for a master input file"""

    errors: list[ValidationError]
    warnings: list[ValidationError]
    info: list[ValidationError]

    def __init__(self):
        self.errors = []
        self.warnings = []
        self.info = []

    def add(self, error: ValidationError):
        """Add a validation error to the appropriate list"""
        if error.severity == "ERROR":
            self.errors.append(error)
        elif error.severity == "WARNING":
            self.warnings.append(error)
        else:
            self.info.append(error)

    def has_errors(self) -> bool:
        """Check if there are any errors (not warnings or info)"""
        return len(self.errors) > 0

    def all_issues(self) -> list[ValidationError]:
        """Get all issues regardless of severity"""
        return self.errors + self.warnings + self.info

    def summary(self) -> str:
        """Get a summary of validation results"""
        return f"Errors: {len(self.errors)}, Warnings: {len(self.warnings)}, Info: {len(self.info)}"


class MasterExcelValidator:
    """Validates master input Excel files for steel model simulations"""

    # Define required columns for each sheet - using actual column names from the file
    REQUIRED_COLUMNS = {
        "Iron and steel plants": [
            "Plant ID",
            "Plant name (English)",
            "Country",
            "Region",
            "Coordinates",
            "Capacity operating status",
        ],
        "Iron ore mines": [
            # Will need to check actual column names
        ],
        "Bill of Materials": [
            # BOM sheet appears to need restructuring
        ],
        "Techno-economic details": [
            # Will need to check actual column names
        ],
        "Allowed tech switches": [
            # Will need to check actual column names
        ],
        "Demand and scrap availability": ["Country", "ISO-3 code", "Metric", "Scenario", "Unit"],
        "Input costs": ["ISO-3 code", "Commodity", "Unit"],
        "Carbon cost": [
            # Carbon cost sheet can have different formats, so we don't enforce specific columns
        ],
        "Tariffs": [
            # Will need to check actual column names
        ],
        "Trade bloc definitions": [
            "ISO 3-letter code"  # This sheet still uses "ISO 3-letter code" not "ISO-3 code"
        ],
        "Power grid emissivity": [
            # Power grid sheet can have different column names
        ],
        "Met coal & gas emissions": [
            # Met coal & gas sheet can have different column names
        ],
    }

    # Column name mappings for standardization
    COLUMN_MAPPINGS = {
        "Iron and steel plants": {
            "Plant ID": "plant_id",
            "Plant name (English)": "plant_name",
            "Country": "country",
            "Region": "region",
            "Coordinates": "coordinates",
            "Capacity operating status": "status",
            "Municipality": "municipality",
            "Subnational unit (province/state)": "province",
            # Capacity columns
            "Nominal crude steel capacity (ttpa)": "steel_capacity",
            "Nominal iron capacity (ttpa)": "iron_capacity",
            "Nominal BF capacity (ttpa)": "bf_capacity",
            "Nominal BOF steel capacity (ttpa)": "bof_capacity",
            "Nominal EAF steel capacity (ttpa)": "eaf_capacity",
            "Nominal DRI capacity (ttpa)": "dri_capacity",
        },
        "Demand and scrap availability": {
            "Country": "country",
            "ISO-3 code": "iso3",
            "Metric": "metric",
            "Scenario": "scenario",
            "Unit": "unit",
        },
        "Input costs": {
            "ISO-3 code": "iso3",
            "Commodity": "commodity",
            "Unit": "unit",
        },
        "Trade bloc definitions": {
            "ISO 3-letter code": "iso3",
        },
    }

    # Define data types for validation
    COLUMN_TYPES = {
        "capacity": "numeric",
        "lat": "numeric",
        "lon": "numeric",
        "capex": "numeric",
        "opex_fixed": "numeric",
        "opex_variable": "numeric",
        "efficiency": "numeric",
        "lifetime": "numeric",
        "year": "integer",
        "steel_demand": "numeric",
        "scrap_availability": "numeric",
        "cost": "numeric",
        "carbon_price": "numeric",
        "tariff_rate": "numeric",
        "fe_content": "numeric",
        "quantity": "numeric",
    }

    # Valid values for categorical columns
    VALID_VALUES = {
        "technology": ["BF", "BOF", "DRI", "EAF", "ESF", "MOE", "Smelting Reduction", "Other"],
        "product": ["iron", "steel", "pig_iron", "hot_metal"],
        "status": [
            "operating",
            "construction",
            "planned",
            "announced",
            "closed",
            "cancelled",
            "discarded",
            "considered",
        ],
        "ore_type": ["hematite", "magnetite", "pellets", "sinter", "other"],
        "unit": ["kg", "t", "kt", "Mt", "MWh", "GJ", "USD", "EUR"],
        "scope": ["scope1", "scope2", "scope3", "total"],
        "allowed": ["yes", "no", "true", "false", "1", "0"],
    }

    def __init__(self):
        self.report = ValidationReport()

    def validate_file(self, excel_path: Path) -> ValidationReport:
        """Validate entire Excel file"""
        self.report = ValidationReport()

        try:
            # Check if file exists and is readable
            if not excel_path.exists():
                self.report.add(
                    ValidationError(
                        sheet_name="FILE", error_type="FILE_NOT_FOUND", message=f"File not found: {excel_path}"
                    )
                )
                return self.report

            # Load Excel file
            xl_file = pd.ExcelFile(excel_path)

            # Check for required sheets
            self._validate_required_sheets(xl_file.sheet_names)

            # Validate each sheet
            for sheet_name in self.REQUIRED_COLUMNS.keys():
                if sheet_name in xl_file.sheet_names:
                    df = pd.read_excel(xl_file, sheet_name=sheet_name)
                    # Clean up column names (remove nan columns)
                    df = df.loc[:, df.columns.notna()]
                    self._validate_sheet(sheet_name, df)

            # Cross-sheet validation
            if not self.report.has_errors():
                self._validate_cross_references(xl_file)

        except Exception as e:
            self.report.add(
                ValidationError(
                    sheet_name="FILE", error_type="READ_ERROR", message=f"Error reading Excel file: {str(e)}"
                )
            )

        return self.report

    def _validate_required_sheets(self, sheet_names: list[int | str]):
        """Check if all required sheets are present"""
        required_sheets = set(self.REQUIRED_COLUMNS.keys())
        # Filter only string sheet names as required sheets are strings
        present_sheets = {name for name in sheet_names if isinstance(name, str)}
        missing_sheets = required_sheets - present_sheets

        for sheet in missing_sheets:
            self.report.add(
                ValidationError(
                    sheet_name=sheet, error_type="MISSING_SHEET", message=f"Required sheet '{sheet}' is missing"
                )
            )

    def _validate_sheet(self, sheet_name: str, df: pd.DataFrame):
        """Validate a single sheet"""
        # Check for empty sheet
        if df.empty:
            self.report.add(
                ValidationError(
                    sheet_name=sheet_name, error_type="EMPTY_SHEET", message="Sheet is empty", severity="WARNING"
                )
            )
            return

        # Check required columns
        self._validate_required_columns(sheet_name, df)

        # Validate data types
        self._validate_data_types(sheet_name, df)

        # Validate categorical values
        self._validate_categorical_values(sheet_name, df)

        # Clean up column names (remove nan columns)
        df = df.loc[:, df.columns.notna()]

        # Sheet-specific validation
        method_name = f"_validate_{sheet_name.lower().replace(' ', '_')}"
        validation_method = getattr(self, method_name, None)
        if validation_method:
            try:
                validation_method(df)
            except Exception as e:
                self.report.add(
                    ValidationError(
                        sheet_name=sheet_name,
                        error_type="VALIDATION_ERROR",
                        message=f"Error in sheet-specific validation: {str(e)}",
                        severity="WARNING",
                    )
                )

    def _validate_required_columns(self, sheet_name: str, df: pd.DataFrame):
        """Check if all required columns are present"""
        required_cols = self.REQUIRED_COLUMNS.get(sheet_name, [])
        missing_cols = set(required_cols) - set(df.columns)

        for col in missing_cols:
            self.report.add(
                ValidationError(
                    sheet_name=sheet_name, error_type="MISSING_COLUMN", message=f"Required column '{col}' is missing"
                )
            )

    def _validate_data_types(self, sheet_name: str, df: pd.DataFrame):
        """Validate data types for columns"""
        for col in df.columns:
            if col in self.COLUMN_TYPES:
                expected_type = self.COLUMN_TYPES[col]

                if expected_type == "numeric":
                    # Check if column can be converted to numeric
                    try:
                        pd.to_numeric(df[col], errors="coerce")
                        non_numeric = df[~pd.to_numeric(df[col], errors="coerce").notna()]
                        if not non_numeric.empty:
                            for idx in non_numeric.index[:5]:  # Report first 5 errors
                                self.report.add(
                                    ValidationError(
                                        sheet_name=sheet_name,
                                        error_type="INVALID_TYPE",
                                        message=f"Non-numeric value '{df.loc[idx, col]}' in numeric column",
                                        row_number=idx + 2,  # +2 for header and 0-indexing
                                        column_name=col,
                                    )
                                )
                    except Exception:
                        pass

                elif expected_type == "integer":
                    # Check if column contains integers
                    try:
                        if (
                            not df[col]
                            .apply(lambda x: isinstance(x, (int, float)) and (pd.isna(x) or x == int(x)))
                            .all()
                        ):
                            self.report.add(
                                ValidationError(
                                    sheet_name=sheet_name,
                                    error_type="INVALID_TYPE",
                                    message=f"Column '{col}' should contain integers only",
                                    column_name=col,
                                )
                            )
                    except Exception:
                        pass

    def _validate_categorical_values(self, sheet_name: str, df: pd.DataFrame):
        """Validate categorical column values"""
        for col in df.columns:
            if col in self.VALID_VALUES:
                valid_values = self.VALID_VALUES[col]
                try:
                    # Convert to lowercase for comparison, handling mixed types
                    col_values = df[col].dropna()
                    # Convert all values to strings first
                    col_values_str = col_values.astype(str).str.lower().unique()
                    invalid_values = set(col_values_str) - set(v.lower() for v in valid_values)

                    if invalid_values:
                        self.report.add(
                            ValidationError(
                                sheet_name=sheet_name,
                                error_type="INVALID_VALUE",
                                message=f"Invalid values in column '{col}': {', '.join(invalid_values)}. Valid values are: {', '.join(valid_values)}",
                                column_name=col,
                            )
                        )
                except Exception as e:
                    self.report.add(
                        ValidationError(
                            sheet_name=sheet_name,
                            error_type="VALIDATION_ERROR",
                            message=f"Error validating column '{col}': {str(e)}",
                            column_name=col,
                            severity="WARNING",
                        )
                    )

    def _validate_iron_and_steel_plants(self, df: pd.DataFrame):
        """Specific validation for plants sheet"""
        # Check for duplicate plant IDs (as warning for now)
        if "Plant ID" in df.columns:
            duplicates = df[df.duplicated(subset=["Plant ID"], keep=False)]
            if not duplicates.empty:
                duplicate_ids = duplicates["Plant ID"].unique()
                self.report.add(
                    ValidationError(
                        sheet_name="Iron and steel plants",
                        error_type="DUPLICATE_ID",
                        message=f"Duplicate plant IDs found: {', '.join(map(str, duplicate_ids[:5]))}",
                        severity="WARNING",  # Changed from ERROR to WARNING
                    )
                )

        # Validate coordinates if present
        if "Coordinates" in df.columns:
            # Check if coordinates column contains valid lat/lon pairs
            invalid_coords = df[
                df["Coordinates"].notna() & ~df["Coordinates"].str.match(r"^-?\d+\.?\d*,\s*-?\d+\.?\d*$")
            ]
            if not invalid_coords.empty:
                for idx in invalid_coords.index[:5]:
                    self.report.add(
                        ValidationError(
                            sheet_name="Iron and steel plants",
                            error_type="INVALID_COORDINATES",
                            message=f"Invalid coordinate format: {df.loc[idx, 'Coordinates']}. Expected format: 'lat, lon'",
                            row_number=idx + 2,
                        )
                    )

        # Note: Capacity validation would need the actual capacity column names
        capacity_columns = [col for col in df.columns if "capacity" in col.lower()]
        if capacity_columns:
            self.report.add(
                ValidationError(
                    sheet_name="Iron and steel plants",
                    error_type="INFO",
                    message=f"Found capacity-related columns: {capacity_columns}",
                    severity="INFO",
                )
            )

    def _validate_cross_references(self, xl_file: pd.ExcelFile):
        """Validate references between sheets"""
        try:
            # Example: Check if all technologies in plants exist in techno-economic details
            if "Iron and steel plants" in xl_file.sheet_names and "Techno-economic details" in xl_file.sheet_names:
                plants_df = pd.read_excel(xl_file, sheet_name="Iron and steel plants")
                tech_df = pd.read_excel(xl_file, sheet_name="Techno-economic details")

                # Remove nan columns
                plants_df = plants_df.loc[:, plants_df.columns.notna()]
                tech_df = tech_df.loc[:, tech_df.columns.notna()]

                # Check if technology columns exist before using them
                tech_columns_in_plants = [
                    col
                    for col in plants_df.columns
                    if isinstance(col, str) and ("technology" in col.lower() or "equipment" in col.lower())
                ]
                tech_columns_in_tech = [
                    col for col in tech_df.columns if isinstance(col, str) and "technology" in col.lower()
                ]

                if tech_columns_in_plants and tech_columns_in_tech:
                    # For now, just log what we found
                    self.report.add(
                        ValidationError(
                            sheet_name="Cross-reference",
                            error_type="INFO",
                            message=f"Technology columns found - Plants: {tech_columns_in_plants[:3]}, Tech details: {tech_columns_in_tech[:3]}",
                            severity="INFO",
                        )
                    )
        except Exception as e:
            self.report.add(
                ValidationError(
                    sheet_name="Cross-reference",
                    error_type="VALIDATION_ERROR",
                    message=f"Error in cross-reference validation: {str(e)}",
                    severity="WARNING",
                )
            )

    def get_validators(self) -> dict[str, Callable]:
        """Return a mapping of sheet names to their validation methods"""
        validators = {}
        for sheet_name in self.REQUIRED_COLUMNS.keys():
            validators[sheet_name] = lambda df, sn=sheet_name: self._validate_sheet(sn, df)
        return validators


def validate_master_input_file(excel_path: Path) -> ValidationReport:
    """Convenience function to validate a master input file"""
    validator = MasterExcelValidator()
    return validator.validate_file(excel_path)


def print_validation_report(report: ValidationReport):
    """Print validation report in a user-friendly format"""
    print(f"\nValidation Report Summary: {report.summary()}")
    print("=" * 60)

    if report.errors:
        print("\nERRORS:")
        for error in report.errors:
            print(f"  {error}")

    if report.warnings:
        print("\nWARNINGS:")
        for warning in report.warnings:
            print(f"  {warning}")

    if report.info:
        print("\nINFO:")
        for info in report.info:
            print(f"  {info}")

    if not report.all_issues():
        print("\n✓ No issues found. File is valid!")


def analyze_excel_structure(excel_path: Path) -> dict[str | int, dict[str, Any]]:
    """Analyze the structure of an Excel file and return sheet/column information"""
    structure: dict[str | int, dict[str, Any]] = {}

    try:
        xl_file = pd.ExcelFile(excel_path)

        for sheet_name in xl_file.sheet_names:
            try:
                df = pd.read_excel(xl_file, sheet_name=sheet_name, nrows=5)
                structure[sheet_name] = {
                    "columns": list(df.columns),
                    "shape": df.shape,
                    "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                }
            except Exception as e:
                structure[sheet_name] = {"error": str(e)}

    except Exception as e:
        structure["error"] = {"error": str(e)}

    return structure
