"""
Master Excel Input File Reader

Reads and transforms data from the master input Excel file into formats
expected by the simulation system. Uses MasterExcelValidator for validation.
"""

import pandas as pd
from pathlib import Path
import logging
from typing import Any, Optional, cast
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
import pycountry
# import pickle

from steelo.adapters.dataprocessing.master_excel_validator import (
    MasterExcelValidator,
    ValidationError,
    ValidationReport,
)
from steelo.adapters.dataprocessing.preprocessing.iso3_finder import derive_iso3, Coordinate
from steelo.domain.constants import KT_TO_T, PLANT_LIFETIME

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    """Result of extracting data from a sheet"""

    success: bool
    file_path: Path | None = None
    errors: list[ValidationError] | None = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class MasterExcelReader:
    """Reads and transforms master input Excel file data"""

    def __init__(self, excel_path: Path, output_dir: Path | None = None):
        """
        Initialize the reader with an Excel file path.

        Args:
            excel_path: Path to the master input Excel file
            output_dir: Directory for output files (uses temp dir if not specified)
        """
        self.excel_path = excel_path
        self.output_dir = output_dir or Path(tempfile.mkdtemp(prefix="master_excel_"))
        self.validator = MasterExcelValidator()
        self._excel_file: pd.ExcelFile | None = None
        self._validation_report: ValidationReport | None = None

    def __enter__(self):
        """Context manager entry"""
        self._excel_file = pd.ExcelFile(self.excel_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if self._excel_file:
            self._excel_file.close()
            self._excel_file = None

    def validate_all(self) -> ValidationReport:
        """
        Validate the entire Excel file.

        Returns:
            ValidationReport with all validation results
        """
        if self._validation_report is None:
            self._validation_report = self.validator.validate_file(self.excel_path)
        return self._validation_report

    def read_tech_switches(self) -> ExtractionResult:
        """
        Read and transform the 'Allowed tech switches' sheet to CSV format.

        The sheet is expected to be a matrix where:
        - Row headers are source technologies
        - Column headers are target technologies
        - Cell values are 'YES' (allowed) or empty (not allowed)

        Returns:
            ExtractionResult with path to generated CSV file
        """
        sheet_name = "Allowed tech switches"

        try:
            # Check if sheet exists
            if not self._excel_file:
                self._excel_file = pd.ExcelFile(self.excel_path)

            if sheet_name not in self._excel_file.sheet_names:
                return ExtractionResult(
                    success=False,
                    errors=[
                        ValidationError(
                            sheet_name=sheet_name,
                            error_type="MISSING_SHEET",
                            message=f"Sheet '{sheet_name}' not found in Excel file",
                        )
                    ],
                )

            # Read the sheet
            df = pd.read_excel(self._excel_file, sheet_name=sheet_name, index_col=0)

            # Clean up: remove any unnamed columns
            df = df.loc[:, ~df.columns.str.contains("^Unnamed")]

            # Validate that it's a square matrix (technologies should match)
            if not set(df.index).issubset(set(df.columns)):
                missing_cols = set(df.index) - set(df.columns)
                return ExtractionResult(
                    success=False,
                    errors=[
                        ValidationError(
                            sheet_name=sheet_name,
                            error_type="INVALID_FORMAT",
                            message=f"Matrix is not square. Missing columns for: {missing_cols}",
                        )
                    ],
                )

            # Create output CSV path
            output_path = self.output_dir / "tech_switches_allowed.csv"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Write to CSV in the expected format
            # The current format appears to be a matrix with technologies as both rows and columns
            # Handle NaN values properly - replace with empty string to preserve YES/NO values
            df.to_csv(output_path, index=True, na_rep="")

            logger.info(f"Successfully extracted tech switches to {output_path}")

            # Validate the content
            validation_errors = self._validate_tech_switches_content(df)
            if validation_errors:
                return ExtractionResult(
                    success=True,  # File was created, but with warnings
                    file_path=output_path,
                    errors=validation_errors,
                )

            return ExtractionResult(success=True, file_path=output_path)

        except Exception as e:
            logger.error(f"Error reading tech switches: {e}")
            return ExtractionResult(
                success=False,
                errors=[
                    ValidationError(
                        sheet_name=sheet_name, error_type="READ_ERROR", message=f"Failed to read sheet: {str(e)}"
                    )
                ],
            )

    def _validate_tech_switches_content(self, df: pd.DataFrame) -> list[ValidationError]:
        """Validate the content of tech switches matrix"""
        errors = []

        # Check for valid values (should be YES, yes, or empty/NaN)
        for row_idx, row in df.iterrows():
            for col_idx, value in row.items():
                if pd.notna(value) and str(value).upper() not in ["YES", ""]:
                    errors.append(
                        ValidationError(
                            sheet_name="Allowed tech switches",
                            error_type="INVALID_VALUE",
                            message=f"Invalid value '{value}' at [{row_idx}, {col_idx}]. Expected 'YES' or empty.",
                            row_number=int(row_idx) if isinstance(row_idx, (int, float)) else None,
                            column_name=str(col_idx),
                            severity="WARNING",
                        )
                    )

        # Check for self-transitions (technology to itself)
        for tech in df.index:
            if tech in df.columns:
                if pd.notna(df.loc[tech, tech]) and str(df.loc[tech, tech]).upper() == "YES":
                    errors.append(
                        ValidationError(
                            sheet_name="Allowed tech switches",
                            error_type="SELF_TRANSITION",
                            message=f"Technology '{tech}' has self-transition enabled",
                            severity="INFO",
                        )
                    )

        return errors

    def read_railway_cost(self) -> ExtractionResult:
        """
        Extract railway cost data from the 'Railway cost' sheet.

        Expected format:
        - Sheet name: 'Railway cost'
        - Columns: 'ISO-3 Code', 'Railway capex', 'Unit'
        - Values in Million USD per km

        Returns:
            ExtractionResult containing the path to the created JSON file
        """
        try:
            sheet_name = "Railway cost"

            # Check if sheet exists
            if self._excel_file is None or sheet_name not in self._excel_file.sheet_names:
                return ExtractionResult(
                    success=False,
                    errors=[
                        ValidationError(
                            sheet_name=sheet_name,
                            error_type="MISSING_SHEET",
                            message=f"Sheet '{sheet_name}' not found in Excel file",
                        )
                    ],
                )

            # Read the sheet
            df = pd.read_excel(self._excel_file, sheet_name=sheet_name)

            # Check required columns
            required_cols = ["ISO-3 Code", "Railway capex"]
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                return ExtractionResult(
                    success=False,
                    errors=[
                        ValidationError(
                            sheet_name=sheet_name,
                            error_type="MISSING_COLUMNS",
                            message=f"Missing required columns: {missing_cols}",
                        )
                    ],
                )

            # Create domain objects
            from ...domain.models import RailwayCost

            railway_costs = []

            for _, row in df.iterrows():
                iso3 = row["ISO-3 Code"]
                cost_per_km = row["Railway capex"]

                # Skip rows with NaN values
                if pd.isna(iso3) or pd.isna(cost_per_km):
                    continue

                railway_costs.append(RailwayCost(iso3=str(iso3), cost_per_km=float(cost_per_km)))

            # Create output JSON path
            output_path = self.output_dir / "railway_costs.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Create repository and save
            from ..repositories.json_repository import RailwayCostJsonRepository

            repo = RailwayCostJsonRepository(output_path)
            repo.add_list(railway_costs)

            logger.info(f"Successfully extracted {len(railway_costs)} railway cost entries to {output_path}")

            return ExtractionResult(success=True, file_path=output_path)

        except Exception as e:
            logger.error(f"Failed to extract railway cost data: {e}")
            return ExtractionResult(
                success=False,
                errors=[
                    ValidationError(
                        sheet_name="Railway cost",
                        error_type="EXTRACTION_ERROR",
                        message=str(e),
                    )
                ],
            )

    def _read_steel_production_sheet(self) -> pd.DataFrame:
        """Read the 'Steel production by plant' sheet."""
        sheet_name = "Steel production by plant"

        if self._excel_file is None or sheet_name not in self._excel_file.sheet_names:
            logger.warning(f"Sheet '{sheet_name}' not found, historical production will be empty")
            return pd.DataFrame()

        # Read with header at row 1 (0-indexed)
        production_df = pd.read_excel(self._excel_file, sheet_name=sheet_name, header=1)
        return production_df

    def _extract_historical_production(
        self, production_df: pd.DataFrame, plant_id: str, technology: str
    ) -> dict[str, float]:
        """
        Extract historical production data for a specific plant and technology.

        This method searches the 'Steel production by plant' sheet for production
        values matching the given plant ID and technology. It returns a dictionary
        mapping years to production volumes.

        Args:
            production_df: DataFrame from 'Steel production by plant' sheet.
                          Expected to have columns like:
                          - 'Plant ID': Identifier matching the plant
                          - '{technology} {product} production {year} (ttpa)': Production values
            plant_id: The Plant ID to search for (e.g., 'P100000121145')
            technology: Technology code to extract data for. One of:
                       - 'BOF': Basic Oxygen Furnace (steel)
                       - 'EAF': Electric Arc Furnace (steel)
                       - 'BF': Blast Furnace (iron)
                       - 'DRI': Direct Reduced Iron
                       - 'OHF': Open Hearth Furnace (steel)

        Returns:
            Dictionary mapping year strings to production volumes in tonnes per annum.
            Returns empty dict if:
            - production_df is empty
            - technology is not recognized
            - no matching plant_id found
            - all production values are null/invalid

        Note:
            This differs from the OLD implementation which used a complex join and
            distribution logic. The NEW implementation reads production data directly
            from the Excel sheet without splitting among furnace groups.

            Special values like 'unknown', 'n/a', '>0' are filtered out.
        """
        if production_df.empty:
            return {}

        # Technology to column pattern mapping
        tech_column_patterns = {
            "BOF": "BOF steel production",
            "EAF": "EAF steel production",
            "BF": "BF production",
            "DRI": "DRI production",
            "OHF": "OHF steel production",
        }

        pattern = tech_column_patterns.get(technology)
        if not pattern:
            return {}

        # Filter for this plant
        plant_rows = production_df[production_df["Plant ID"] == plant_id]
        if plant_rows.empty:
            return {}

        historical_production = {}
        for year in [2019, 2020, 2021, 2022]:
            col_name = f"{pattern} {year} (ttpa)"
            if col_name in production_df.columns:
                # Get all values for this column from matching plant rows
                values = plant_rows[col_name]
                # Find the first non-null value (there might be multiple rows for the same plant)
                for value in values:
                    if pd.notna(value) and str(value).lower() not in ["unknown", "n/a", ">0"]:
                        try:
                            historical_production[str(year)] = float(value)
                            break  # Use the first valid value found
                        except (ValueError, TypeError):
                            pass

        return historical_production

    def read_plants(
        self,
        dynamic_feedstocks_dict: Optional[dict] = None,
        current_date: Optional[date] = None,
        gravity_distances_pkl_path: Optional[Path] = None,
        geocoder_coordinates: Optional[list[Coordinate]] = None,
        simulation_start_year: int = 2025,
        regional_fopex: dict[str, float] = {},
    ) -> tuple[list, dict]:
        """
        Extract plant data from the 'Iron and steel plants' sheet and create Plant domain objects.

        This is the main entry point for reading steel plant data from the master Excel file.
        It processes the raw Excel data and transforms it into domain objects (Plant, FurnaceGroup,
        Technology) while handling coordinate parsing, capacity extraction, historical production,
        and lifecycle calculations.

        The method reads from multiple sheets:
        - 'Iron and steel plants': Primary plant and furnace group data
        - 'Steel production by plant': Historical production data (optional)
        - 'Bill of Materials': Dynamic business cases for technologies (if not provided)

        Process:
            1. Read plant data from Excel sheet
            2. For each row with valid Plant ID and coordinates:
               a. Parse location (lat/lon) and derive ISO3 country code
               b. Split 'Main production equipment' into individual technologies
               c. For each technology (BF, BOF, EAF, DRI, ESF, MOE):
                  - Create Technology object with dynamic business cases
                  - Extract capacity (handling technology-specific columns)
                  - Extract historical production (from separate sheet)
                  - Calculate lifecycle (start year, current cycle, end year)
                  - Create FurnaceGroup with all metadata
               d. Create RawPlantData with all furnace groups
            3. Aggregate plants (handle duplicate Plant IDs)
            4. Validate no duplicate furnace_group_ids
            5. Return aggregated plants and metadata

        Args:
            dynamic_feedstocks_dict: Dictionary mapping technology names to lists of dynamic
                                   business cases. If None, reads from 'Bill of Materials' sheet.
                                   Example: {'BF': [...], 'BOF': [...], 'EAF': [...]}
            current_date: Reference date for determining plant operating status. If None,
                         uses today's date. Currently not used in filtering (filtering
                         happens at runtime via active_statuses config).
            gravity_distances_pkl_path: Path to pickle file with pre-computed gravity model
                                       distances. Currently not used (kept for future enhancement).
            geocoder_coordinates: List of Coordinate objects for initializing reverse geocoder.
                                If provided, enables coordinate-based ISO3 derivation. If None,
                                falls back to country name mapping.
            simulation_start_year: Year representing the start of simulation. Used for:
                                  - Calculating plant age (simulation_start_year - start_year)
                                  - Setting lifetime.current in FurnaceGroup
                                  - Determining current renovation cycle
                                  Defaults to 2025.
            regional_fopex: Dictionary mapping region codes to fixed operational expenses.
                           Applied to all plants in the region. Defaults to empty dict.

        Returns:
            tuple[list[Plant], dict[str, dict]]:
                - List of Plant domain objects with aggregated furnace groups
                - Dictionary mapping furnace_group_id (str) to FurnaceGroupMetadata dict:
                  {
                      'commissioning_year': Year | None,
                      'age_at_reference_year': int | None,
                      'last_renovation_year': Year | None,
                      'age_source': str ('exact' | 'imputed'),
                      'source_sheet': str,
                      'source_row': int,
                      'validation_warnings': list[str]
                  }

        Raises:
            ValueError: If 'Iron and steel plants' sheet is not found in Excel file
            Exception: For other errors during plant data extraction

        Notes:
            **Differences from OLD implementation:**

            1. **Plant Filtering**: OLD implementation used `filter_inactive_plants()` to
               remove plants with status 'retired', 'idled', 'mouthballed' or past
               retirement/idled dates. NEW implementation reads ALL plants and relies on
               runtime filtering via `active_statuses` config parameter. This allows
               simulation to control which plants are active.

            2. **Utilization Calculation**: OLD implementation calculated utilization by
               dividing historical production by nominal capacity, then filled missing
               values with regional averages. NEW implementation reads historical production
               but does NOT calculate utilization - this may not be used in simulation.

            3. **Production Distribution**: OLD implementation used complex logic to split
               plant-level production among furnace groups proportionally to capacity
               (`split_production_among_furnace_groups`). NEW implementation reads production
               directly from Excel without distribution.

            4. **Capacity Imputation**: OLD implementation filled missing capacity values
               with global average per technology. NEW implementation skips furnace groups
               with capacity <= 0 (no imputation).

            5. **Date Imputation**: OLD implementation had sophisticated logic for missing
               start dates based on operating status, retirement dates, announcement dates,
               and plant age. NEW implementation uses simple fallback to 2020 for all
               missing dates. See `_parse_date()` for details.

            6. **Coordinate Validation**: OLD implementation kept plants with invalid
               coordinates (set lat/lon to NaN) and logged comprehensive warnings. NEW
               implementation silently skips plants with missing/invalid coordinates using
               `continue` statements (lines 441-452).

            7. **Equipment Split**: OLD implementation split equipment on regex pattern
               `r"[;,\\s]+"` (semicolon, comma, or whitespace). NEW implementation only
               splits on semicolon `;`. If Excel uses comma/space-separated equipment,
               they won't be split correctly.

            8. **OHF Handling**: Both implementations handle OHF (Open Hearth Furnace).
               NEW implementation explicitly replaces OHF with EAF and combines their
               capacities (lines 482-520). OLD implementation handled it through column
               naming patterns.

            **Data Quality**: The method performs several validations:
            - Skips rows with missing Plant ID
            - Skips rows with missing/invalid coordinates
            - Skips furnace groups with capacity <= 0
            - Validates commissioning year (warns if > simulation_start_year)
            - Validates age at reference (warns if negative)
            - Ensures no duplicate furnace_group_ids across all plants

            **Aggregation**: Plants with the same Plant ID are aggregated - their furnace
            groups are combined into a single Plant object. The aggregation service
            handles metadata remapping to ensure furnace_group_ids remain unique.
        """
        from ...domain.models import Location, Technology, FurnaceGroup, PointInTime, TimeFrame, Year, Volumes
        from ..dataprocessing.excel_reader import read_dynamic_business_cases
        from .plant_aggregation import PlantAggregationService, RawPlantData
        from .plant_metadata import (
            FurnaceGroupMetadata,
            validate_commissioning_year,
            validate_age_at_reference,
        )

        sheet_name = "Iron and steel plants"

        try:
            # Check if sheet exists
            if not self._excel_file:
                self._excel_file = pd.ExcelFile(self.excel_path)

            if sheet_name not in self._excel_file.sheet_names:
                raise ValueError(f"Sheet '{sheet_name}' not found in Excel file")

            # Read the plant sheet
            plant_df = pd.read_excel(self._excel_file, sheet_name=sheet_name)

            # Track canonical metadata for each furnace group
            raw_canonical_metadata: dict[str, FurnaceGroupMetadata] = {}

            # Read dynamic business cases if not provided
            if dynamic_feedstocks_dict is None:
                logger.info("Reading dynamic business cases from Bill of Materials sheet")
                dynamic_feedstocks_dict = read_dynamic_business_cases(
                    str(self.excel_path), excel_sheet="Bill of Materials"
                )

            # Load gravity distances if path provided
            # if gravity_distances_pkl_path and gravity_distances_pkl_path.exists():
            #     logger.info(f"Loading gravity distances from {gravity_distances_pkl_path}")
            #     with open(gravity_distances_pkl_path, "rb") as f:
            #         gravity_distances = pickle.load(f)

            # Use current date if not provided
            if current_date is None:
                current_date = date.today()

            # Initialize reverse geocoder if coordinates provided
            if geocoder_coordinates:
                logger.info("Initializing reverse geocoder with provided coordinates")
                # Make a dummy call to initialize the geocoder
                try:
                    derive_iso3(0, 0, coordinates=geocoder_coordinates)
                except Exception:
                    # The dummy call might fail but the geocoder should be initialized
                    pass

            # Read historical production data
            try:
                production_df = self._read_steel_production_sheet()
            except Exception as e:
                logger.warning(f"Could not read historical production: {e}")
                production_df = pd.DataFrame()
            # Create raw plant data objects
            raw_plants = []

            for row_idx, row in plant_df.iterrows():
                # Skip if critical fields are missing
                if pd.isna(row.get("Plant ID")):
                    continue

                plant_id = str(row["Plant ID"])
                # Excel row number (add 2: 1 for header, 1 for 1-based indexing)
                excel_row = cast(int, row_idx) + 2

                # Parse coordinates - Excel has "Coordinates" column with "lat, lon" format
                coordinates = row.get("Coordinates")
                if pd.isna(coordinates):
                    continue

                # Create location
                try:
                    # Split comma-separated coordinates
                    lat_str, lon_str = str(coordinates).split(",")
                    lat = float(lat_str.strip())
                    lon = float(lon_str.strip())
                except (ValueError, AttributeError, TypeError):
                    # Skip plant if coordinates are not valid
                    continue

                # Try coordinate-based ISO3 derivation, fall back to country name if not initialized
                try:
                    iso3 = derive_iso3(lat, lon)
                except ValueError as e:
                    if "coordinates must be provided" in str(e):
                        # Reverse geocoder not initialized, fall back to country name mapping
                        logger.debug(
                            f"Reverse geocoder not initialized, using country name mapping for "
                            f"plant {row.get('Plant ID')}"
                        )
                        iso3 = self._get_iso3_from_country(str(row.get("Country", "")))
                    else:
                        raise
                location = Location(
                    lat=lat,
                    lon=lon,
                    country=iso3,
                    region="unknown",  # Will be set based on mappings
                    iso3=iso3,
                )

                # Create furnace groups from equipment string
                furnace_groups = []
                equipment_str = str(row.get("Main production equipment", ""))
                if equipment_str and equipment_str != "nan":
                    equipments = [e.strip() for e in equipment_str.split(";")]

                    # Check if OHF exists and replace with EAF
                    if "OHF" in equipments:
                        equipments.remove("OHF")
                        if "EAF" not in equipments:
                            equipments.append("EAF")

                    for idx, equipment in enumerate(equipments):
                        if equipment in ["BF", "BOF", "EAF", "DRI", "ESF", "MOE"]:
                            # Get dynamic business cases for this technology
                            tech_business_cases = dynamic_feedstocks_dict.get(
                                equipment, dynamic_feedstocks_dict.get(equipment.lower(), [])
                            )

                            # Create technology with dynamic business cases
                            technology = Technology(
                                name=equipment,
                                product="iron" if equipment in ["BF", "DRI", "ESF"] else "steel",
                                technology_readiness_level=None,
                                process_emissions=None,
                                dynamic_business_case=tech_business_cases,
                            )

                            # Determine capacity based on technology type
                            if technology.product == "iron":
                                capacity_value = (
                                    row.get(f"Nominal {equipment} capacity (ttpa)")
                                    or row.get("Nominal iron capacity (ttpa)")
                                    or 0
                                )
                                # Parse capacity, handling special values
                                capacity = self._parse_capacity(capacity_value)
                            else:
                                # For EAF, combine EAF and OHF capacities
                                if equipment == "EAF":
                                    eaf_capacity = row.get("Nominal EAF steel capacity (ttpa)") or 0
                                    ohf_capacity = row.get("Nominal OHF steel capacity (ttpa)") or 0
                                    # Parse both capacities to handle special values
                                    eaf_parsed = self._parse_capacity(eaf_capacity)
                                    ohf_parsed = self._parse_capacity(ohf_capacity)
                                    capacity = eaf_parsed + ohf_parsed
                                else:
                                    capacity_value = (
                                        row.get(f"Nominal {equipment} steel capacity (ttpa)")
                                        or row.get("Nominal crude steel capacity (ttpa)")
                                        or 0
                                    )
                                    # Parse capacity, handling special values
                                    capacity = self._parse_capacity(capacity_value)

                            if capacity <= 0:
                                continue

                            # Extract historical production
                            historical_production_raw = {}
                            if not production_df.empty:
                                historical_production_raw = self._extract_historical_production(
                                    production_df, plant_id=row["Plant ID"], technology=equipment
                                )

                            # Convert historical production from dict[str, float] to dict[Year, Volumes]
                            historical_production = {}
                            for year_str, volume in historical_production_raw.items():
                                try:
                                    historical_production[Year(int(year_str))] = Volumes(volume)
                                except (ValueError, TypeError):
                                    pass

                            # Create furnace group
                            start_date = self._parse_date(row.get("Start date"))
                            start_year = Year(start_date.year if start_date else 2020)

                            # Calculate the TimeFrame based on current renovation cycle
                            plant_age = simulation_start_year - start_year
                            if plant_age <= 0:
                                # Future plant or brand new - full cycle ahead
                                lifetime_start = start_year
                                lifetime_end = Year(start_year + PLANT_LIFETIME)
                            else:
                                # Calculate position in current renovation cycle
                                years_in_current_cycle = plant_age % PLANT_LIFETIME
                                if years_in_current_cycle == 0:
                                    # Exactly at renovation boundary - needs renovation
                                    lifetime_start = Year(simulation_start_year)
                                    lifetime_end = Year(simulation_start_year + PLANT_LIFETIME)
                                else:
                                    # Mid-cycle - calculate cycle boundaries
                                    cycles_completed = plant_age // PLANT_LIFETIME
                                    lifetime_start = Year(start_year + (cycles_completed * PLANT_LIFETIME))
                                    # End year is simulation start + remaining years in cycle
                                    remaining_years = PLANT_LIFETIME - years_in_current_cycle
                                    lifetime_end = Year(simulation_start_year + remaining_years)

                            furnace_group_id = f"temp_{plant_id}_{equipment}_{idx}_{excel_row}"
                            furnace_group = FurnaceGroup(
                                furnace_group_id=furnace_group_id,  # Temporary ID (unique per row), will be replaced by aggregator
                                capacity=Volumes(KT_TO_T * capacity),
                                status=str(row.get("Capacity operating status", "operating")),
                                last_renovation_date=start_date,
                                technology=technology,
                                historical_production=historical_production,
                                utilization_rate=0.0,
                                lifetime=PointInTime(
                                    current=Year(simulation_start_year),
                                    time_frame=TimeFrame(start=lifetime_start, end=lifetime_end),
                                    plant_lifetime=PLANT_LIFETIME,
                                ),
                            )
                            furnace_groups.append(furnace_group)

                            # Capture canonical metadata for this furnace group
                            commissioning_year = start_year if start_date else None
                            age_at_reference = None
                            if not commissioning_year:
                                # If no commissioning year, calculate age at simulation start
                                age_at_reference = plant_age if plant_age > 0 else 0

                            # Validate metadata
                            warnings = validate_commissioning_year(commissioning_year, furnace_group_id)
                            warnings.extend(
                                validate_age_at_reference(age_at_reference, simulation_start_year, furnace_group_id)
                            )

                            if warnings:
                                logger.warning(
                                    f"Age data issues for {furnace_group_id} ({sheet_name}:{excel_row}): {warnings}"
                                )

                            # Store metadata with temporary ID
                            raw_canonical_metadata[furnace_group_id] = FurnaceGroupMetadata(
                                commissioning_year=commissioning_year,
                                age_at_reference_year=age_at_reference,
                                last_renovation_year=start_year if start_date else None,
                                age_source="exact" if commissioning_year else "imputed",
                                source_sheet=sheet_name,
                                source_row=excel_row,
                                validation_warnings=warnings,
                            )

                # Only create raw plant data if it has furnace groups
                if furnace_groups:
                    # Create raw plant data (no aggregation yet)
                    raw_plant = RawPlantData(
                        plant_id=plant_id,
                        location=location,
                        furnace_groups=furnace_groups,
                        power_source=str(row.get("Power source", "unknown")),
                        soe_status=str(row.get("SOE Status", "unknown")),
                        parent_gem_id=str(row.get("Parent GEM ID", "")),
                        workforce_size=self._parse_workforce_size(row.get("Workforce size")),
                        technology_fopex=regional_fopex,
                    )
                    raw_plants.append(raw_plant)

            # Use aggregation service to handle duplicate Plant IDs and remap metadata
            aggregator = PlantAggregationService()
            plants, final_canonical_metadata = aggregator.aggregate_plants_with_metadata(
                raw_plants, raw_canonical_metadata
            )

            # Validate that all furnace group IDs are unique
            aggregator.validate_no_duplicate_furnace_group_ids(plants)

            logger.info(
                f"Successfully created {len(plants)} plants from master Excel "
                f"(from {len(raw_plants)} raw records) with metadata for {len(final_canonical_metadata)} furnace groups"
            )
            return plants, final_canonical_metadata

        except Exception as e:
            logger.error(f"Failed to extract plant data: {e}")
            raise

    def _get_iso3_from_country(self, country_name: str) -> str:
        """Convert country name to ISO3 code."""
        # Handle empty or invalid input
        if not country_name or not country_name.strip():
            return "XXX"

        # Handle special cases
        country_mappings = {
            "USA": "USA",
            "Germany": "DEU",
            "Japan": "JPN",
            "Türkiye": "TUR",
            "Russia": "RUS",
            "South Korea": "KOR",
            "UK": "GBR",
            "Ivory Coast": "CIV",
            "Democratic Republic of the Congo": "COD",
        }

        if country_name in country_mappings:
            return country_mappings[country_name]

        try:
            results = pycountry.countries.search_fuzzy(country_name)
            if results and len(results) > 0:
                country = results[0]
                return getattr(country, "alpha_3", "XXX")
            return "XXX"
        except (LookupError, AttributeError):
            logger.warning(f"Could not find ISO3 for country: {country_name}")
            return "XXX"

    def _parse_capacity(self, value: Any) -> float:
        """
        Parse capacity value from Excel, handling special cases and non-numeric values.

        The GEM dataset contains various capacity formats that need special handling:
        - Numeric values (float/int): Used as-is
        - '>0' or '>X': Treated as X + 0.1 (small positive value greater than threshold)
        - '<X': Treated as X - 0.1 (value less than threshold, minimum 0)
        - NaN/None/empty: Treated as 0
        - Other strings: Attempted float conversion, defaults to 0 on failure

        Args:
            value: Raw capacity value from Excel cell. Can be:
                  - float/int: Direct numeric value
                  - str: Numeric string or special indicator ('>0', '<100', etc.)
                  - NaN/None: Missing value

        Returns:
            Parsed capacity as float (tonnes per annum). Returns 0.0 for invalid values.

        Examples:
            >>> _parse_capacity(1500.0)
            1500.0
            >>> _parse_capacity('>0')
            0.1
            >>> _parse_capacity('<100')
            99.9
            >>> _parse_capacity('>50')
            50.1
            >>> _parse_capacity('unknown')
            0.0
            >>> _parse_capacity(None)
            0.0

        Note:
            The OLD implementation used `treat_non_numeric_values()` which converted
            '>0' and 'unknown' to NaN. The NEW implementation provides more nuanced
            handling by treating '>0' as a small positive capacity rather than missing.
        """
        if pd.isna(value) or value is None:
            return 0.0

        if isinstance(value, (int, float)):
            return float(value)

        # Handle string values
        str_value = str(value).strip()

        # Handle special cases
        if str_value.startswith(">"):
            # For ">0", treat as a small positive value
            try:
                return float(str_value[1:]) + 0.1
            except (ValueError, TypeError):
                return 0.1

        if str_value.startswith("<"):
            # For "<100", use the value minus a small amount
            try:
                return max(0.0, float(str_value[1:]) - 0.1)
            except (ValueError, TypeError):
                return 0.0

        # Try to parse as float
        try:
            return float(str_value)
        except (ValueError, TypeError):
            return 0.0

    def _parse_workforce_size(self, value: Any) -> int:
        """Parse workforce size value, handling non-numeric values."""
        if pd.isna(value) or value is None:
            return 0

        if isinstance(value, (int, float)):
            return int(value)

        # Handle string values
        str_value = str(value).strip().lower()
        if str_value in ["unknown", "n/a", "-", ""]:
            return 0

        # Try to parse as integer
        try:
            return int(float(str_value))
        except (ValueError, TypeError):
            return 0

    def _parse_date(self, date_value) -> date | None:
        """
        Parse date values from Excel, handling multiple formats and edge cases.

        The GEM dataset contains dates in various formats that require careful handling:
        - Python date/datetime objects: Converted to date
        - Year-only strings: '2015' → datetime(2015, 1, 1).date()
        - Date strings: Various formats like 'YYYY-MM-DD', 'DD/MM/YYYY', etc.
        - Numeric years: 2015 → datetime(2015, 1, 1).date()
        - Excel serial dates: Small integers (1-1900) treated as days since 1899-12-30
        - Invalid/empty values: Return None

        Args:
            date_value: Raw date value from Excel cell. Can be:
                       - datetime/date object: Python datetime types
                       - str: Year ('2015'), formatted date ('2015-01-01'), or empty
                       - int/float: Year (1900-2100) or Excel serial date (1-1900)
                       - NaN/None: Missing value

        Returns:
            Parsed date as Python date object, or None if value is invalid/missing.

        Examples:
            >>> _parse_date('2015')
            datetime.date(2015, 1, 1)
            >>> _parse_date('2015-03-15')
            datetime.date(2015, 3, 15)
            >>> _parse_date(2015)
            datetime.date(2015, 1, 1)
            >>> _parse_date(100)  # Excel serial date
            datetime.date(1900, 4, 9)
            >>> _parse_date(None)
            None
            >>> _parse_date('')
            None

        Note:
            The OLD implementation had limited date parsing in `filter_relevant_dates()`.
            The NEW implementation:
            - Prioritizes year interpretation (1900-2100) over Excel serial dates
            - Handles Excel serial dates only for small numbers (1-1900)
            - Rejects unrealistic years (<1900)
            - Tries multiple common date formats before falling back to pandas

            For missing start dates, the OLD implementation used complex imputation
            based on operating status, retirement dates, and plant age. The NEW
            implementation uses a simple fallback to 2020 (see read_plants line 550).
        """
        if pd.isna(date_value):
            return None

        try:
            # Check datetime before date since datetime is a subclass of date
            if isinstance(date_value, datetime):
                return date_value.date()
            elif isinstance(date_value, date):
                return date_value
            elif isinstance(date_value, str):
                # Handle empty strings
                if not date_value.strip():
                    return None
                # Try to parse year only
                if len(date_value) == 4 and date_value.isdigit():
                    year = int(date_value)
                    # Reject unrealistic years
                    if year < 1900:
                        return None
                    return datetime(year, 1, 1).date()
                else:
                    # Try different date formats explicitly to avoid warnings
                    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"]:
                        try:
                            return datetime.strptime(date_value, fmt).date()
                        except ValueError:
                            continue
                    # If no format matched, let pandas try
                    return pd.to_datetime(date_value).date()
            elif isinstance(date_value, (int, float)):
                year = int(date_value)

                # First check if it's a reasonable year (1900-2100)
                # This takes precedence for common year values
                if 1900 <= year <= 2100:
                    return datetime(year, 1, 1).date()

                # Otherwise, check if it could be an Excel serial date
                # Excel dates: 1 = 1900-01-01 (though Excel incorrectly treats 1900 as leap year)
                # We only treat small numbers as Excel dates (up to 1900)
                if 1 <= date_value <= 1900:
                    # Convert Excel serial date to datetime
                    # Excel epoch is 1899-12-30 (to account for Excel's leap year bug)
                    excel_epoch = datetime(1899, 12, 30)
                    try:
                        result_date = excel_epoch + pd.Timedelta(days=int(date_value))
                        # Only return if the result is a reasonable date
                        if result_date.year >= 1900:
                            return result_date.date()
                    except (ValueError, OverflowError):
                        pass

                # Unrealistic value, return None
                return None
            else:
                # Unknown type, return None instead of the value itself
                return None
        except (ValueError, TypeError, AttributeError):
            return None

    def read_technologies_config(self) -> ExtractionResult:
        """
        Extract technology configuration from the 'Techno-economic details' sheet.

        Returns:
            ExtractionResult with path to generated technologies.json file
        """
        sheet_name = "Techno-economic details"

        try:
            # Check if sheet exists
            if not self._excel_file:
                self._excel_file = pd.ExcelFile(self.excel_path)

            if sheet_name not in self._excel_file.sheet_names:
                return ExtractionResult(
                    success=False,
                    errors=[
                        ValidationError(
                            sheet_name=sheet_name,
                            error_type="MISSING_SHEET",
                            message=f"Sheet '{sheet_name}' not found in Excel file",
                        )
                    ],
                )

            # Read the sheet with improved handling
            df = self._excel_file.parse(
                sheet_name,
                dtype="object",  # Force strings to avoid float artifacts
                keep_default_na=False,  # Don't interpret "NA", "NULL" etc. as NaN
            )

            # Validate required columns before processing
            required = {"Technology"}
            missing = sorted(required - set(df.columns))
            if missing:
                return ExtractionResult(
                    success=False,
                    errors=[
                        ValidationError(
                            sheet_name=sheet_name,
                            error_type="MISSING_COLUMNS",
                            message=f"Required columns missing: {', '.join(missing)}",
                        )
                    ],
                )

            # Import the extraction function
            from .technology_extractor import extract_technologies, write_json_atomic

            # Extract technologies
            config_dict = extract_technologies(df, self.excel_path)

            # Create output JSON path with consistent structure
            dest_path = self.output_dir / "data" / "fixtures" / "technologies.json"
            write_json_atomic(config_dict, dest_path)

            return ExtractionResult(success=True, file_path=dest_path)

        except Exception as e:
            logger.exception("Technology extraction failed")
            return ExtractionResult(
                success=False,
                errors=[
                    ValidationError(
                        sheet_name=sheet_name,
                        error_type="EXTRACTION_ERROR",
                        message=str(e),
                    )
                ],
            )

    def read_bom(self) -> ExtractionResult:
        """
        Extract Bill of Materials data from the 'Bill of Materials' sheet.

        Returns:
            ExtractionResult with path to generated file
        """
        sheet_name = "Bill of Materials"

        try:
            if not self._excel_file:
                self._excel_file = pd.ExcelFile(self.excel_path)

            if sheet_name not in self._excel_file.sheet_names:
                return ExtractionResult(
                    success=False,
                    errors=[
                        ValidationError(
                            sheet_name=sheet_name,
                            error_type="MISSING_SHEET",
                            message=f"Sheet '{sheet_name}' not found in Excel file",
                        )
                    ],
                )

            # For now, save as Excel file to maintain compatibility
            output_path = self.output_dir / "BOM_ghg_system_boundary.xlsx"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy just the BOM sheet to a new Excel file
            with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                df = pd.read_excel(self._excel_file, sheet_name=sheet_name)
                df.to_excel(writer, sheet_name=sheet_name, index=False)

            logger.info(f"Successfully extracted Bill of Materials to {output_path}")

            return ExtractionResult(success=True, file_path=output_path)

        except Exception as e:
            logger.error(f"Failed to extract BOM data: {e}")
            return ExtractionResult(
                success=False,
                errors=[
                    ValidationError(
                        sheet_name=sheet_name,
                        error_type="EXTRACTION_ERROR",
                        message=str(e),
                    )
                ],
            )

    def extract_all_data(self) -> dict[str, ExtractionResult]:
        """
        Extract all supported data from the master Excel file.

        Returns:
            Dictionary mapping config field names to extraction results
        """
        results = {}

        # Extract high priority data
        # Note: read_plants() now returns list[Plant], not ExtractionResult
        # This method should probably be refactored to not include plants
        results["tech_switches_csv_path"] = self.read_tech_switches()
        results["railway_costs_json_path"] = self.read_railway_cost()
        results["new_business_cases_excel_path"] = self.read_bom()

        # TODO: Add other extractors as they are implemented
        # results['demand_center_xlsx_path'] = self.read_demand_centers()
        # results['carbon_costs_xlsx_path'] = self.read_carbon_costs()
        # results['input_costs_csv_path'] = self.read_input_costs()
        # results['mine_data_excel_path'] = self.read_mines()
        # results['tariff_excel_path'] = self.read_tariffs()

        return results

    def get_output_paths(self) -> dict[str, Path]:
        """
        Extract all data and return only the successfully created file paths.

        This method is used by SimulationConfig.from_master_excel() to get
        all prepared data file paths.

        Returns:
            Dictionary mapping SimulationConfig field names to file paths
        """
        all_results = self.extract_all_data()

        # Filter to only successful extractions and extract paths
        output_paths = {}
        for field_name, result in all_results.items():
            if result.success and result.file_path:
                output_paths[field_name] = result.file_path
            else:
                logger.warning(f"Failed to extract {field_name}: {result.errors}")

        return output_paths
