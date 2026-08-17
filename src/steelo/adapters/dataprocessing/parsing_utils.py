"""Shared parsing helpers for the GEM unit-level furnace ETL and reader
(build_furnace_units_sheet.py / MasterExcelReader.read_plants_from_furnace_units_sheet).

Ambiguous-value handling (parse_capacity) follows the OLD preprocessing pipeline's
convention in preprocessing/raw_plant_data_processing.py (treat_non_numeric_values):
indicators like ">0" or "unknown" are treated as missing (NaN) rather than
approximated to a nearby numeric value, since fabricating a number discards real
uncertainty already flagged by the source data.
"""

from datetime import date, datetime
from typing import Any

import pandas as pd

from steelo.domain.models import Year


def parse_capacity(value: Any) -> float:
    """Parse a capacity value, treating ambiguous indicators as missing.

    - Numeric values: used as-is.
    - '>X', '<X', 'unknown' (any casing): NaN — the source data is explicitly
      uncertain, so no value is fabricated (matches
      preprocessing/raw_plant_data_processing.py's treat_non_numeric_values).
    - NaN/None/unparseable strings: NaN.
    """
    if pd.isna(value) or value is None:
        return float("nan")

    if isinstance(value, (int, float)):
        return float(value)

    str_value = str(value).strip()

    if str_value.lower() == "unknown":
        return float("nan")

    if str_value.startswith(">") or str_value.startswith("<"):
        return float("nan")

    try:
        return float(str_value)
    except (ValueError, TypeError):
        return float("nan")


def parse_workforce_size(value: Any) -> int:
    """Parse a workforce size value, handling non-numeric values."""
    if pd.isna(value) or value is None:
        return 0

    if isinstance(value, (int, float)):
        return int(value)

    str_value = str(value).strip().lower()
    if str_value in ["unknown", "n/a", "-", ""]:
        return 0

    try:
        return int(float(str_value))
    except (ValueError, TypeError):
        return 0


def parse_date(date_value: Any) -> date | None:
    """Parse a date value from Excel, handling datetimes, year-only strings, common
    date formats, and small-int Excel serial dates. Returns None if unparseable.
    """
    if pd.isna(date_value):
        return None

    try:
        if isinstance(date_value, datetime):
            return date_value.date()
        elif isinstance(date_value, date):
            return date_value
        elif isinstance(date_value, str):
            if not date_value.strip():
                return None
            if len(date_value) == 4 and date_value.isdigit():
                year = int(date_value)
                if year < 1900:
                    return None
                return datetime(year, 1, 1).date()
            else:
                for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"]:
                    try:
                        return datetime.strptime(date_value, fmt).date()
                    except ValueError:
                        continue
                return pd.to_datetime(date_value).date()
        elif isinstance(date_value, (int, float)):
            year = int(date_value)

            if 1900 <= year <= 2100:
                return datetime(year, 1, 1).date()

            if 1 <= date_value <= 1900:
                excel_epoch = datetime(1899, 12, 30)
                try:
                    result_date = excel_epoch + pd.Timedelta(days=int(date_value))
                    if result_date.year >= 1900:
                        return result_date.date()
                except (ValueError, OverflowError):
                    pass

            return None
        else:
            return None
    except (ValueError, TypeError, AttributeError):
        return None


def compute_furnace_lifetime(start_year: "Year", simulation_start_year: int, plant_lifetime: int) -> tuple[Year, Year]:
    """Compute the (lifetime_start, lifetime_end) TimeFrame boundaries for a furnace
    group's current renovation cycle, given its start year and the simulation's
    reference year. Mirrors the calculation used in MasterExcelReader.read_plants().
    """
    plant_age = simulation_start_year - start_year
    if plant_age <= 0:
        lifetime_start = start_year
        lifetime_end = Year(start_year + plant_lifetime)
    else:
        years_in_current_cycle = plant_age % plant_lifetime
        if years_in_current_cycle == 0:
            lifetime_start = Year(simulation_start_year)
            lifetime_end = Year(simulation_start_year + plant_lifetime)
        else:
            cycles_completed = plant_age // plant_lifetime
            lifetime_start = Year(start_year + (cycles_completed * plant_lifetime))
            remaining_years = plant_lifetime - years_in_current_cycle
            lifetime_end = Year(simulation_start_year + remaining_years)

    return lifetime_start, lifetime_end
