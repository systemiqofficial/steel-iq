"""
Recreation Configuration System

Provides modular control over the data recreation process, allowing
fine-grained control over which files to recreate, validation, and progress tracking.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union
import logging

logger = logging.getLogger(__name__)


@dataclass
class RecreationConfig:
    """Configuration for controlling the data recreation process."""

    # Control which files to recreate
    files_to_recreate: Optional[list[str]] = None  # None means recreate all
    skip_existing: bool = False  # Skip recreation if file already exists
    force_recreation: bool = False  # Force recreation even if file exists

    # Validation settings
    validate_after_creation: bool = True  # Validate each file after creation
    strict_validation: bool = False  # Treat validation warnings as errors

    # Progress tracking
    progress_callback: Optional[Callable[[str, int], None]] = None  # (message, percent)
    verbose: bool = True  # Print progress messages to console

    # Error handling
    continue_on_error: bool = False  # Continue with other files if one fails
    max_retries: int = 1  # Number of times to retry failed recreations

    # Performance
    parallel_recreation: bool = False  # Recreate files in parallel (future feature)

    def should_recreate_file(self, filename: str, file_path: Path) -> bool:
        """
        Determine if a file should be recreated based on configuration.

        Args:
            filename: Name of the file (e.g., "plants.json")
            file_path: Full path to the file

        Returns:
            True if file should be recreated, False otherwise
        """
        # Check if file is in the recreation list
        if self.files_to_recreate is not None and filename not in self.files_to_recreate:
            return False

        # Check force recreation
        if self.force_recreation:
            return True

        # Check skip existing
        if self.skip_existing and file_path.exists():
            return False

        return True

    def report_progress(self, message: str, percent: int = 0):
        """Report progress if callback is configured."""
        if self.progress_callback:
            self.progress_callback(message, percent)
        if self.verbose:
            logger.info(f"[{percent}%] {message}")


@dataclass
class FileRecreationSpec:
    """Specification for recreating a single file."""

    filename: str  # Output filename (e.g., "plants.json")
    recreate_function: Union[str, Callable[..., Any]]  # Function name or callable for recreation
    source_type: str  # "master-excel", "core-archive", or "derived"
    dependencies: list[str] = field(default_factory=list)  # Required input files
    master_excel_sheet: Optional[str] = None  # Sheet name if from master Excel
    description: str = ""  # Human-readable description

    # Validation
    validator_function: Optional[Callable[[Path], bool]] = None
    required_fields: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Validate the specification."""
        if self.source_type not in ["master-excel", "core-archive", "derived"]:
            raise ValueError(f"Invalid source_type: {self.source_type}")

        if self.source_type == "master-excel" and not self.master_excel_sheet:
            raise ValueError("master_excel_sheet required for master-excel source type")


# Define all file recreation specifications
FILE_RECREATION_SPECS = {
    "country_mappings.json": FileRecreationSpec(
        filename="country_mappings.json",
        recreate_function="recreate_country_mappings_data",
        source_type="master-excel",
        master_excel_sheet="Country mapping",
        dependencies=[],
        description="Country mapping data from master Excel",
    ),
    "plants.json": FileRecreationSpec(
        filename="plants.json",
        recreate_function="recreate_plants_data",
        source_type="master-excel",
        master_excel_sheet="Iron and steel plants",
        dependencies=[],
        description="Plant data from master Excel",
    ),
    "plant_groups.json": FileRecreationSpec(
        filename="plant_groups.json",
        recreate_function="recreate_plant_groups_data",
        source_type="derived",
        dependencies=["plants.json"],
        description="Plant groupings derived from plants data",
    ),
    "demand_centers.json": FileRecreationSpec(
        filename="demand_centers.json",
        recreate_function="recreate_demand_center_data",
        source_type="master-excel",
        master_excel_sheet="Demand and scrap availability",
        dependencies=["gravity_distances_dict.pkl", "countries.csv"],
        description="Demand centers from master Excel",
    ),
    "suppliers.json": FileRecreationSpec(
        filename="suppliers.json",
        recreate_function="recreate_mines_and_scrap_as_suppliers_data",
        source_type="master-excel",
        master_excel_sheet="Iron ore mines",
        dependencies=["countries.csv"],
        description="Mine and scrap suppliers from master Excel",
    ),
    "tariffs.json": FileRecreationSpec(
        filename="tariffs.json",
        recreate_function="recreate_tariffs_data",
        source_type="master-excel",
        master_excel_sheet="Tariffs",
        dependencies=[],
        description="Trade tariffs from master Excel",
    ),
    "subsidies.json": FileRecreationSpec(
        filename="subsidies.json",
        recreate_function="recreate_subsidy_data",
        source_type="master-excel",
        master_excel_sheet="Subsidies",
        dependencies=[],
        description="Subsidy data from master Excel",
    ),
    "carbon_costs.json": FileRecreationSpec(
        filename="carbon_costs.json",
        recreate_function="recreate_carbon_costs_data",
        source_type="master-excel",
        master_excel_sheet="Carbon costs",
        dependencies=[],
        description="Carbon cost time series from master Excel",
    ),
    "capex.json": FileRecreationSpec(
        filename="capex.json",
        recreate_function="recreate_capex_data",
        source_type="master-excel",
        master_excel_sheet="Tech CAPEX",
        dependencies=[],
        description="Technology CAPEX data from master Excel",
    ),
    "cost_of_capital.json": FileRecreationSpec(
        filename="cost_of_capital.json",
        recreate_function="recreate_cost_of_capital_data",
        source_type="master-excel",
        master_excel_sheet="Regional cost of capital",
        dependencies=[],
        description="Regional cost of capital from master Excel",
    ),
    "input_costs.json": FileRecreationSpec(
        filename="input_costs.json",
        recreate_function="recreate_input_costs_data",
        source_type="master-excel",
        master_excel_sheet="Input costs",
        dependencies=[],
        description="Regional input costs from master Excel",
    ),
    "primary_feedstocks.json": FileRecreationSpec(
        filename="primary_feedstocks.json",
        recreate_function="recreate_primary_feedstock_data",
        source_type="master-excel",
        master_excel_sheet="Bill of Materials",
        dependencies=[],
        description="Primary feedstock data from master Excel",
    ),
    "region_emissivity.json": FileRecreationSpec(
        filename="region_emissivity.json",
        recreate_function="recreate_region_emissivity_data",
        source_type="master-excel",
        master_excel_sheet="Power grid emissivity",
        dependencies=[],
        description="Regional emission factors from master Excel",
    ),
    "tech_switches_allowed.csv": FileRecreationSpec(
        filename="tech_switches_allowed.csv",
        recreate_function="recreate_tech_switches_data",
        source_type="master-excel",
        master_excel_sheet="Allowed tech switches",
        dependencies=[],
        description="Technology switching matrix from master Excel",
    ),
    "legal_process_connectors.json": FileRecreationSpec(
        filename="legal_process_connectors.json",
        recreate_function="recreate_legal_process_connectors_data",
        source_type="master-excel",
        master_excel_sheet="Legal Process connectors",
        dependencies=[],
        description="Legal Process connectors data from master Excel",
    ),
    "hydrogen_efficiency.json": FileRecreationSpec(
        filename="hydrogen_efficiency.json",
        recreate_function="recreate_hydrogen_efficiency_data",
        source_type="master-excel",
        master_excel_sheet="Hydrogen efficiency",
        dependencies=[],
        description="Hydrogen efficiency data from master Excel",
    ),
    "hydrogen_capex_opex.json": FileRecreationSpec(
        filename="hydrogen_capex_opex.json",
        recreate_function="recreate_hydrogen_capex_opex_data",
        source_type="master-excel",
        master_excel_sheet="Hydrogen CAPEX_OPEX component",
        dependencies=[],
        description="Hydrogen CAPEX/OPEX component data from master Excel",
    ),
    "transport_emissions.json": FileRecreationSpec(
        filename="transport_emissions.json",
        recreate_function="recreate_transport_emissions_data",
        source_type="master-excel",
        master_excel_sheet="Transport emissions",
        dependencies=[],
        description="Transport emission factors from master Excel",
    ),
    "biomass_availability.json": FileRecreationSpec(
        filename="biomass_availability.json",
        recreate_function="recreate_biomass_availability_data",
        source_type="master-excel",
        master_excel_sheet="Biomass availability",
        dependencies=[],
        description="Biomass availability constraints from master Excel",
    ),
    "technology_emission_factors.json": FileRecreationSpec(
        filename="technology_emission_factors.json",
        recreate_function="recreate_technology_emission_factors_data",
        source_type="master-excel",
        master_excel_sheet="Technology emission factors",
        dependencies=[],
        description="Technology emission factors from master Excel",
    ),
    "fopex.json": FileRecreationSpec(
        filename="fopex.json",
        recreate_function="recreate_fopex_data",
        source_type="master-excel",
        master_excel_sheet="Fixed OPEX",
        dependencies=[],
        description="Fixed Operating Expenditure (FOPEX) data from master Excel",
    ),
    "carbon_border_mechanisms.json": FileRecreationSpec(
        filename="carbon_border_mechanisms.json",
        recreate_function="recreate_carbon_border_mechanisms_data",
        source_type="master-excel",
        master_excel_sheet="CBAM",
        dependencies=[],
        description="Carbon border adjustment mechanisms from master Excel",
    ),
    "fallback_material_costs.json": FileRecreationSpec(
        filename="fallback_material_costs.json",
        recreate_function="recreate_fallback_material_costs",
        source_type="master-excel",
        master_excel_sheet="Fallback material cost",
        dependencies=[],
        description="Fallback material costs from master Excel",
    ),
}


def determine_file_source(filename: str) -> str:
    """Centralized function to determine file source for consistent tracking.

    Args:
        filename: Name of the file

    Returns:
        String describing the file source
    """
    # Check FILE_RECREATION_SPECS first
    spec = FILE_RECREATION_SPECS.get(filename)
    if spec:
        if spec.source_type == "master-excel":
            sheet = spec.master_excel_sheet or "Unknown sheet"
            return f"master-excel - {sheet}"
        elif spec.source_type == "derived":
            deps = spec.dependencies[:2]
            deps_str = ", ".join(deps)
            if len(spec.dependencies) > 2:
                deps_str += f" (+{len(spec.dependencies) - 2} more)"
            return f"derived from {deps_str}"
        else:
            return spec.source_type

    # Special cases for known files not in FILE_RECREATION_SPECS
    master_excel_files = {"master_input.xlsx"}
    if filename in master_excel_files:
        return "master-excel - Source File"

    # Geo data files
    geo_files = {"terrain_025_deg.nc", "wind_025_deg.nc", "pv_025_deg.nc", "countries_110m.zip"}
    if filename in geo_files:
        return "geo-data"

    # All other files are from core-data
    return "core-data"


class RecreationManager:
    """Manages the file recreation process with detailed control and reporting."""

    def __init__(self, config: RecreationConfig):
        self.config = config
        self.results: dict[str, Any] = {}

    def get_recreation_order(self) -> list[str]:
        """
        Determine the order to recreate files based on dependencies.

        Returns:
            List of filenames in dependency order
        """
        # Simple topological sort
        order = []
        visited = set()

        def visit(filename: str):
            if filename in visited:
                return
            visited.add(filename)

            spec = FILE_RECREATION_SPECS.get(filename)
            if spec:
                # Visit dependencies first
                for dep in spec.dependencies:
                    if dep.endswith(".json") and dep in FILE_RECREATION_SPECS:
                        visit(dep)
            order.append(filename)

        # Visit all files
        files = self.config.files_to_recreate or list(FILE_RECREATION_SPECS.keys())
        for filename in files:
            visit(filename)

        return order

    def validate_dependencies(self, spec: FileRecreationSpec, output_dir: Path) -> list[str]:
        """
        Check if all dependencies for a file exist.

        Returns:
            List of missing dependencies
        """
        missing = []
        for dep in spec.dependencies:
            dep_path = output_dir / dep
            if not dep_path.exists():
                missing.append(dep)
        return missing

    def get_recreation_summary(self) -> dict[str, Any]:
        """
        Get a summary of what will be recreated.

        Returns:
            Dictionary with recreation plan details
        """
        files = self.config.files_to_recreate or list(FILE_RECREATION_SPECS.keys())

        summary = {
            "total_files": len(files),
            "by_source": {
                "master-excel": 0,
                "core-archive": 0,
                "derived": 0,
            },
            "files": {},
        }

        for filename in files:
            spec = FILE_RECREATION_SPECS.get(filename)
            if spec:
                by_source = summary["by_source"]
                assert isinstance(by_source, dict)
                by_source[spec.source_type] += 1
                files_dict = summary["files"]
                assert isinstance(files_dict, dict)
                files_dict[filename] = {
                    "source": spec.source_type,
                    "sheet": spec.master_excel_sheet,
                    "dependencies": spec.dependencies,
                    "description": spec.description,
                }

        return summary
