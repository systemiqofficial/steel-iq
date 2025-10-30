# ============================================================================
# OLD CSV-BASED PREPROCESSING IMPLEMENTATION - COMMENTED OUT 2025-10-09
# ============================================================================
#
# This file contains the original CSV-based preprocessing pipeline for GEM
# (Global Energy Monitor) steel plant data. It has been replaced by the
# Excel-based preprocessing in master_excel_reader.py.
#
# REASON FOR COMMENTING OUT:
# After team meeting (2025-10-09), we decided to simplify the approach:
# - Historical production data and utilization are probably NOT used in simulation
# - Plant status filtering happens at runtime via `active_statuses` config
# - The NEW Excel-based implementation (master_excel_reader.py) is working in production
#
# THIS CODE IS PRESERVED FOR REFERENCE ONLY:
# - In case we want to review the logic later
# - Potentially useful if we decide to transfer some logic to the new Excel reader
# - Documents what the old implementation did
#
# DO NOT USE THIS CODE. See master_excel_reader.py for active implementation.
#
# For details on the migration decision, see:
# specs/2025-10-09_gem_data_preprocessing.md (Section 13)
#
# ============================================================================

# Stub functions to prevent ImportError in tests and notebooks
# These raise clear exceptions explaining that the code has been commented out

_DEPRECATION_MESSAGE = """
This function has been commented out as of 2025-10-09.

Reason: After team meeting, we decided to simplify the GEM data preprocessing approach.
The old CSV-based preprocessing logic has been replaced by the Excel-based implementation
in master_excel_reader.py.

The commented-out code is preserved for reference only in case we want to review the logic later.

What to do:
- If you're running old tests: Skip or update them (see tests/unit/test_raw_plant_data_processing.py)
- If you're using this in a notebook: Switch to master_excel_reader.py
- If you need this logic: See the commented code below or specs/2025-10-09_gem_data_preprocessing.md

For active implementation, use: src/steelo/adapters/dataprocessing/master_excel_reader.py
"""


def _raise_deprecated(func_name: str):
    """Helper to raise a clear deprecation error."""
    raise NotImplementedError(f"{func_name}() is no longer available.\n{_DEPRECATION_MESSAGE}")


# Stub functions (in alphabetical order, matching imports in test file)
def add_historical_production_data(*args, **kwargs):
    _raise_deprecated("add_historical_production_data")


def allocate_unnamed_tech_to_steel_or_iron(*args, **kwargs):
    _raise_deprecated("allocate_unnamed_tech_to_steel_or_iron")


def anonimise_plant_data(*args, **kwargs):
    _raise_deprecated("anonimise_plant_data")


def calculate_last_renovation_year(*args, **kwargs):
    _raise_deprecated("calculate_last_renovation_year")


def calculate_regional_average_utilisation(*args, **kwargs):
    _raise_deprecated("calculate_regional_average_utilisation")


def calculate_utilisation(*args, **kwargs):
    _raise_deprecated("calculate_utilisation")


def check_column_group_capped_at_1(*args, **kwargs):
    _raise_deprecated("check_column_group_capped_at_1")


def convert_certifications_to_binary(*args, **kwargs):
    _raise_deprecated("convert_certifications_to_binary")


def execute_preprocessing(*args, **kwargs):
    _raise_deprecated("execute_preprocessing")


def extract_furnace_capacity(*args, **kwargs):
    _raise_deprecated("extract_furnace_capacity")


def extract_items(*args, **kwargs):
    _raise_deprecated("extract_items")


def extract_plant_production_history(*args, **kwargs):
    _raise_deprecated("extract_plant_production_history")


def fill_missing_utlisation_values(*args, **kwargs):
    _raise_deprecated("fill_missing_utlisation_values")


def fill_missing_values_with_global_avg_per_tech(*args, **kwargs):
    _raise_deprecated("fill_missing_values_with_global_avg_per_tech")


def filter_inactive_plants(*args, **kwargs):
    _raise_deprecated("filter_inactive_plants")


def filter_relevant_dates(*args, **kwargs):
    _raise_deprecated("filter_relevant_dates")


def impute_missing_dates(*args, **kwargs):
    _raise_deprecated("impute_missing_dates")


def make_steel_product_df(*args, **kwargs):
    _raise_deprecated("make_steel_product_df")


def rename_capacity_cols(*args, **kwargs):
    _raise_deprecated("rename_capacity_cols")


def set_unique_furnace_id(*args, **kwargs):
    _raise_deprecated("set_unique_furnace_id")


def split_into_plant_and_furnace_group(*args, **kwargs):
    _raise_deprecated("split_into_plant_and_furnace_group")


def split_plants_into_furnace_groups(*args, **kwargs):
    _raise_deprecated("split_plants_into_furnace_groups")


def split_production_among_furnace_groups(*args, **kwargs):
    _raise_deprecated("split_production_among_furnace_groups")


def split_spatial_coordinates(*args, **kwargs):
    _raise_deprecated("split_spatial_coordinates")


def treat_non_numeric_values(*args, **kwargs):
    _raise_deprecated("treat_non_numeric_values")


# ============================================================================
# COMMENTED OUT CODE (PRESERVED FOR REFERENCE)
# ============================================================================

# # TODO: Check if used or logic mimicked in other files
#
# import pandas as pd
# import numpy as np
# import re
# import logging
#
# from pathlib import Path
#
# from pandas import DataFrame
#
# from steelo.domain import Year
# from steelo.domain.constants import MT_TO_T, KT_TO_T
#
# # Default data years - these should eventually be passed as parameters
# STEEL_PLANT_GEM_DATA_YEAR = 2025
# DEFAULT_SIMULATION_START_YEAR = 2025
# DEFAULT_PRODUCTION_GEM_DATA_YEARS = list(range(2019, 2023))
#
# logging.basicConfig(level=logging.INFO)
#
#
# # ----------------------------------------------------- FUNCTION DEF -----------------------------------------------------
# def load_data(
#     steel_plants_csv: Path | None = None,
#     hist_production_csv: Path | None = None,
#     reg_hist_production_iron_csv: Path | None = None,
#     reg_hist_production_steel_csv: Path | None = None,
# ) -> tuple[DataFrame, DataFrame, DataFrame, DataFrame]:
#     """
#     Load the steel plant and historical production data (per plant and regional averages) from csv files.
#     """
#     # All paths must be provided - no fallback to settings
#     if not steel_plants_csv:
#         raise ValueError("steel_plants_csv path must be provided")
#     if not hist_production_csv:
#         raise ValueError("hist_production_csv path must be provided")
#     if not reg_hist_production_iron_csv:
#         raise ValueError("reg_hist_production_iron_csv path must be provided")
#     if not reg_hist_production_steel_csv:
#         raise ValueError("reg_hist_production_steel_csv path must be provided")
#
#     logging.info("Loading steel plant data from csv file.")
#
#     plant_data = pd.read_csv(steel_plants_csv)
#     production_data = pd.read_csv(hist_production_csv, header=1)
#     regional_iron_production_data = pd.read_csv(reg_hist_production_iron_csv)
#     regional_steel_production_data = pd.read_csv(reg_hist_production_steel_csv)
#     return plant_data, production_data, regional_iron_production_data, regional_steel_production_data
#
#
# def anonimise_plant_data(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Anonimise the plant data by dropping all columns which include any information that could be used to identify the plant (except for its lat-lon coordinates).
#     Note: Consider some spatial aggregation of the data later on, since it is easy to identify a plant based on its location.
#     """
#     logging.info("Anonimising plant data.")
#     columns_to_drop = [
#         col
#         for col in data.columns
#         if any(
#             keyword in col.lower()
#             for keyword in [
#                 "plant name",  # All columns including the plant name (4)
#                 "owner",  # All columns including the owner (4)
#                 "address",  # All columns including the address (2)
#             ]
#         )
#     ] + [
#         "Parent",
#         "Owner PermID",
#         "Municipality",
#         "Subnational unit (province/state)",
#         "GEM wiki page",
#     ]
#     data_anon = data.drop(columns=columns_to_drop)
#     logging.debug(f"Removing columns: {columns_to_drop}")
#     return data_anon
#
#
# def split_spatial_coordinates(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Check if there are any plants with missing or invalid coordinates and split them into latitude and longitude columns. Manually added
#     missing coordinates for plants 'P100000121145' and 'P100000121251' in the csv file using Google Maps.
#     """
#     logging.info("Extracting latitude and longitude.")
#
#     def parse_coords(coord):
#         if isinstance(coord, str) and "," in coord:
#             parts = coord.split(",")
#             if len(parts) == 2:
#                 try:
#                     lat, lon = float(parts[0]), float(parts[1])
#                     return lat, lon
#                 except ValueError:
#                     pass
#         return None, None
#
#     latitudes, longitudes = [], []
#     invalid_ids = []
#
#     for i, row in data.iterrows():
#         lat, lon = parse_coords(row["Coordinates"])
#         if lat is None or lon is None:
#             invalid_ids.append((row["Plant ID"], row["Coordinates"]))
#         latitudes.append(lat)
#         longitudes.append(lon)
#
#     if invalid_ids:
#         logging.warning(
#             "Invalid coordinates found for Plant IDs:\n%s", "\n".join(f"{pid:>8} {coord}" for pid, coord in invalid_ids)
#         )
#
#     data = data.copy()
#     data["Latitude"] = latitudes
#     data["Longitude"] = longitudes
#     return data.drop(columns=["Coordinates", "Coordinate accuracy"])
#
#
# def filter_inactive_plants(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Remove inactive plants (retired, idled, mouthballed).
#     """
#     logging.info("Filtering inactive plants.")
#     # Filter based on operating status
#     data_active = data[
#         data["Capacity operating status"].isin(["announced", "construction", "operating", "operating pre-retirement"])
#     ]
#     logging.debug(f"There are {len(data_active)} active plants out of a total of {len(data)} plants.")
#
#     # Refine filter by dropping rows with past retired or idled dates
#     # Retired date: Date that iron/steel plant was permanently closed, either after being disassembled or after a period of inactivity (after 5 years of "mothballed")
#     # Idled date: Date that iron/steel plant stops iron or steel production, but has not been disassembled or permanently closed
#     # TODO Hannah: Consider filtering out at the time of plant age == average retirement age
#     current_date = pd.to_datetime("today").date()
#     data_active_date = data_active[
#         ~((data_active["Retired date"] <= str(current_date)) | (data_active["Idled date"] <= str(current_date)))
#     ].reset_index(drop=True)
#     logging.debug(
#         f"There are {len(data_active_date)} plants with non-past retirement nor idled dates out of {len(data_active)} active plants."
#     )
#     return data_active_date
#
#
# def split_plants_into_furnace_groups(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Split the plants into furnace groups based on their technology (main production equipment).
#     """
#     logging.info("Splitting plants into furnace groups based on technology and plant.")
#     data_furnace = (
#         data.assign(**{"Main production equipment": data["Main production equipment"].str.split(r"[;,\s]+")})
#         .explode("Main production equipment")
#         .reset_index(drop=True)
#     )
#     data_furnace = data_furnace.rename(columns={"Main production equipment": "Technology"})
#     data_furnace.drop(
#         [
#             "Nominal crude steel capacity (ttpa)",
#             "Nominal iron capacity (ttpa)",
#         ],
#         axis=1,
#         inplace=True,
#     )
#     logging.debug(f"Number of furnace groups: {len(data_furnace)}")
#     return data_furnace
#
#
# def set_unique_furnace_id(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Create a unique furnace ID by adding a number to the plant ID  if it is repeated and set it as the Dataframe index.
#     The first furnace group id is the plant id, so labelling starts with the second furnace group: P000000000001,
#     P000000000001_2, P000000000001_3, etc.
#     """
#     logging.info("Setting unique furnace ID.")
#     counts = data.groupby("Plant ID").cumcount() + 1
#     id_append = counts.astype(str).where(counts > 1).apply(lambda x: f"_{x}" if not pd.isna(x) else "")
#     data["Furnace ID"] = data["Plant ID"] + id_append
#     data.set_index("Furnace ID", inplace=True)
#     # TODO: Remove duplicate furnace groups with the same plant ID, technology, and start date but different operating
#     # status (e.g., P100000120035_3 and P100000120035_5).
#     # The capacity data is sometimes missing in one of the duplicates; need to make sure to pick the one with non-nan data.
#     return data
#
#
# def rename_capacity_cols(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Rename the capacity columns to have consistent names across all products and technologies.
#     """
#     logging.info("Renaming capacity columns for consistency.")
#     for product in ["iron", "steel"]:
#         data.rename(
#             columns={f"Other/unspecified {product} capacity (ttpa)": f"Nominal other {product} capacity (ttpa)"},
#             inplace=True,
#         )
#         data.rename(
#             columns={
#                 "Nominal BF capacity (ttpa)": "Nominal BF iron capacity (ttpa)",
#             },
#             inplace=True,
#         )
#         data.rename(
#             columns={
#                 "Nominal DRI capacity (ttpa)": "Nominal DRI iron capacity (ttpa)",
#             },
#             inplace=True,
#         )
#     return data
#
#
# def treat_non_numeric_values(df: pd.DataFrame, cols: list) -> pd.DataFrame:
#     """
#     Convert non-numeric values ">0" and "unknown" to NaN and numeric strings to float in the specified columns.
#     """
#     # TODO: Decide on how to handle ">0" in the data. Currently, converted to Nan for simplicity (information loss!).
#     for col in cols:
#         df.replace({col: [">0", "unknown"]}, np.nan, inplace=True)
#         df[col] = df[col].apply(lambda x: float(x) if isinstance(x, str) and x.replace(".", "", 1).isdigit() else x)
#     return df
#
#
# def allocate_unnamed_tech_to_steel_or_iron(df_row: pd.Series) -> pd.Series:
#     """
#     Allocate unnamed (other or unknown) technology to steel or iron based its nominal capacity.
#     """
#     if df_row.Technology == "other" or df_row.Technology == "unknown":
#         if df_row["Nominal iron capacity (ttpa)"] > 0 and (
#             df_row["Nominal steel capacity (ttpa)"] == 0 or np.isnan(df_row["Nominal steel capacity (ttpa)"])
#         ):
#             df_row["Technology"] = df_row["Technology"] + " iron"
#         elif df_row["Nominal steel capacity (ttpa)"] > 0 and (
#             df_row["Nominal iron capacity (ttpa)"] == 0 or np.isnan(df_row["Nominal iron capacity (ttpa)"])
#         ):
#             df_row["Technology"] = df_row["Technology"] + " steel"
#         if df_row["Nominal iron capacity (ttpa)"] > 0 and df_row["Nominal steel capacity (ttpa)"] > 0:
#             logging.warning(
#                 f"Both iron and steel capacities are non-zero for plant {df_row['Plant ID']} and technology {df_row['Technology']}."
#             )
#             # TODO: Decide how to handle these cases. Currently, the technology is kept as general unknown/other. Ideally, the technology
#             # should be split into two rows for iron and steel.
#     return df_row
#
#
# def fill_missing_values_with_global_avg_per_tech(df: pd.DataFrame, col: str) -> pd.DataFrame:
#     """
#     Fill in missing values in the capacity column with the global average per technology.
#     """
#     for tech in df["Technology"].unique():
#         # Get technology specific rows
#         tech_rows = df[df["Technology"] == tech]
#         # Calculate global capacity average per technology, excluding NaN values
#         cap_avg = tech_rows[col].mean()
#         # Fill in missing values
#         df.loc[df["Technology"] == tech, col] = df.loc[df["Technology"] == tech, col].fillna(cap_avg)
#     return df
#
#
# def assign_capacity_to_furnace_group(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Choose the capacity corresponding to the technology and assign it to each furnace group.
#     """
#     logging.info("Assigning iron and steel production capacity to each furnace group.")
#     for product in ["iron", "steel"]:
#         data[f"Nominal {product} capacity (ttpa)"] = data.apply(
#             lambda row: row.get(f"Nominal {row['Technology']} {product} capacity (ttpa)", 0), axis=1
#         )
#         # Add exception for the case when the technology is unknown
#         data[f"Nominal {product} capacity (ttpa)"] = data.apply(
#             lambda row: (
#                 row.get(f"Nominal other {product} capacity (ttpa)", 0)
#                 if row["Technology"] == "unknown"
#                 else row[f"Nominal {product} capacity (ttpa)"]
#             ),
#             axis=1,
#         )
#     return data
#
#
# def extract_furnace_capacity(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Extract the technology used for a certain furnace group (same plant, same technology) and assign the according iron and steel production capacity.
#     """
#     logging.info("Extracting steel production technology for each furnace group.")
#
#     # For each furnace group, set a unique ID and store the iron and steel-making capacity
#     data_furnace = split_plants_into_furnace_groups(data)
#     data_furnace_uid = set_unique_furnace_id(data_furnace)
#     data_furnace_nn = rename_capacity_cols(data_furnace_uid)
#     data_furnace_cap = assign_capacity_to_furnace_group(data_furnace_nn)
#
#     # Drop all unnecessary capacity columns and all other info about the production process and equipment
#     capacity_cols_to_drop = [
#         col
#         for col in data_furnace_cap.columns
#         if "capacity" in col.lower()
#         and col not in ["Nominal steel capacity (ttpa)", "Nominal iron capacity (ttpa)", "Capacity operating status"]
#     ]
#     other_cols_to_drop = [
#         "Main production process",
#         "Detailed production equipment",  # Relevant information included in "Technology" column
#         "Iron ore source",
#         "Met coal source",
#     ]  # Assumed to be irrelevant for the model according to Rafal, take inputs as a black box for now
#     cols_to_drop = capacity_cols_to_drop + other_cols_to_drop
#     data_furnace_filtered = data_furnace_cap.drop(columns=cols_to_drop)
#     logging.debug(f"Removing columns: {cols_to_drop}")
#
#     # Correct non-numeric values and aggregate steel and iron nominal capacities since already clear from the Technology column
#     iron_steel_capacity_cols = ["Nominal iron capacity (ttpa)", "Nominal steel capacity (ttpa)"]
#     furnace_group_data_numeric = treat_non_numeric_values(data_furnace_filtered, iron_steel_capacity_cols)
#     furnace_group_data_numeric = furnace_group_data_numeric.apply(
#         lambda row: allocate_unnamed_tech_to_steel_or_iron(row), axis=1
#     )
#     # TODO: There are 6 FGs where the technology is unknown/other and the capacity is non-zero for both steel and iron. Currenty sumed up.
#     # Decide how to handle these cases; linked to the to do in allocate_unnamed_tech_to_steel_or_iron().
#     furnace_group_data_numeric["Nominal capacity (ttpa)"] = furnace_group_data_numeric[iron_steel_capacity_cols].sum(
#         1, min_count=1
#     )  # ensure that the sum of two Nans is Nan
#     furnace_group_data_numeric.drop(columns=iron_steel_capacity_cols, inplace=True)
#
#     # Fill in missing capacity values with the global average per technology
#     furnace_group_data_filled = fill_missing_values_with_global_avg_per_tech(
#         furnace_group_data_numeric, "Nominal capacity (ttpa)"
#     )
#     return furnace_group_data_filled
#
#
# def extract_items(data: str) -> list:
#     """
#     Extracts items from a string containing multiple items, clean up, and convert to lower case.
#     """
#     # Split by multiple delimiters ([",", ";", " and "]) only if not between parentheses and if "and" is between whitespaces
#     split_data = re.split(r",(?![^(]*\))|;(?![^(]*\))|\s+and\s+(?![^(]*\))", data)
#     # Remove whitespaces and empty strings
#     split_data_full = [item.strip() for item in split_data if (item not in ["", " "]) and (item.strip() != "")]
#     # Convert to lowercase and remove duplicates
#     split_data_clean = list(set([item.lower() for item in split_data_full]))
#     return split_data_clean
#
#
# def make_steel_product_df(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Creates a binary DataFrame with the final steel products (e.g., hotrolled) (columns) produced by each steel plant (rows).
#     """
#     # TODO: Aggregate final steel products per plant into categories
#     logging.info("Creating a DataFrame with the steel products produced by each plant.")
#     data.fillna({"Steel products": ""}, inplace=True)
#     steel_prod_lists = data["Steel products"].apply(extract_items)
#     steel_prod_df = steel_prod_lists.apply(pd.Series).set_index(data["Plant ID"])
#     steel_prod_dummies = steel_prod_df.stack().str.get_dummies().groupby(level=0).sum()
#     steel_prod_dummies_nodup = (
#         steel_prod_dummies.T.groupby(steel_prod_dummies.columns.str.replace("s$", "", regex=True)).sum().T
#     )  # Remove plural forms
#     steel_prod_bindummies = steel_prod_dummies_nodup.map(lambda x: 1 if x > 0 else 0)  # Convert all values to binary
#     logging.debug(
#         f"Number of plants producing each steel product:\n{steel_prod_bindummies.sum(axis=0).sort_values(ascending=False)}"
#     )
#     return steel_prod_bindummies
#
#
# def zip_year_value(g) -> dict:
#     return dict(zip(g["year"], g["value"]))
#
#
# type GroupByResult = pd.DataFrame | pd.Series
#
#
# def extract_plant_production_history(raw_production_data: pd.DataFrame) -> GroupByResult:
#     """
#     Extracts the production for both iron and steel by plant and technology for each year.
#     Returns a DataFrame with: (1) rows for each plant ID and technology and (2) columns for each product (iron, steel) and year.
#     """
#     logging.info("Extracting production data for each plant.")
#     data = raw_production_data.set_index("Plant ID")
#
#     # Extract production per plant, technology, and year
#     # TODO: Solve for case in which the technology is unknown by making a new row and assigning Technology to unknown (e.g., steel production for plant P100000121188 in 2022). Currently, those cases are ignored.
#     data = data.stack().reset_index().rename(columns={0: "value"})
#     data[["Production type", "year"]] = data["level_1"].str.extract(r"(?i)(.*) production (\d{4})")
#     data = data[~data["Production type"].isin(["Crude steel", "Iron"])]
#     data.rename(columns={"Production type": "Technology"}, inplace=True)
#     data["Technology"] = (
#         data["Technology"].str.replace("EAF steel", "EAF").replace("BOF steel", "BOF").replace("OHF steel", "OHF")
#     )
#     # TODO: Can't we directly aggregate steel and iron production?
#     data["Iron production (ttpa)"] = data.apply(
#         lambda row: row["value"] if row["Technology"] in ["BF", "DRI", "OHF"] else np.nan, axis=1
#     )
#     data["Steel production (ttpa)"] = data.apply(
#         lambda row: row["value"] if row["Technology"] in ["BOF", "EAF"] else np.nan, axis=1
#     )
#
#     # Put into a clean format
#     data.drop(columns=["level_1", "value"], inplace=True)
#     data.dropna(
#         subset=["Technology", "year", "Iron production (ttpa)", "Steel production (ttpa)"], how="all", inplace=True
#     )
#
#     # Correct non-numeric values
#     data_numeric = treat_non_numeric_values(data, ["Iron production (ttpa)", "Steel production (ttpa)"])
#
#     # Aggregate steel and iron historical production since already clear from the Technology column
#     data_numeric["Production new"] = data_numeric[["Iron production (ttpa)", "Steel production (ttpa)"]].sum(
#         axis=1, min_count=1
#     )  # ensure that the sum of two Nans is Nan
#     data_numeric = (
#         data_numeric.groupby(["Plant ID", "Technology", "year"])["Production new"]
#         .sum(min_count=1)  # ensure that the sum of two Nans is Nan
#         .unstack("year", fill_value=None)
#         .reset_index()
#     )
#     data_numeric.columns.name = ""  # Remove the year column name
#     return data_numeric
#
#
# def calculate_last_renovation_year(
#     start_date: str, avg_renovation_cycle: int = 20, simulation_start_year: int = DEFAULT_SIMULATION_START_YEAR
# ) -> Year | float:
#     """
#     Calculates the year of the last renovation based on the start date of the plant and the average renovation cycle.
#     Renovations are assumed to happen at the beginning of each year and months are truncated.
#     """
#
#     start_year = pd.to_datetime(start_date, errors="coerce").year
#     if (
#         start_year <= simulation_start_year
#     ):  # To avoid dates in the future getting a last rennovation year that is in the past
#         age = simulation_start_year - start_year
#
#         years_since_last_renovation = age % avg_renovation_cycle
#
#         last_renovation_year = int(simulation_start_year - years_since_last_renovation)
#
#         return Year(last_renovation_year)
#     else:
#         try:
#             return Year(int(start_year))
#         except ValueError:
#             # FIXME this is probably a bug
#             return start_year  # start_year is nan
#
#
# def impute_missing_dates(
#     data: pd.DataFrame, avg_renovation_cycle: int = 20, simulation_start_year: int = DEFAULT_SIMULATION_START_YEAR
# ) -> pd.Series:
#     """
#     Impute missing 'Start date' values based on "Capacity operating status".
#
#     Rules:
#       1. For operating plants with missing start dates, assign a start year by subtracting a random offset
#          (between 0 and avg_renovation_cycle) from START_YEAR.
#       2. For pre-retirement plants:
#          a. Convert 'Retired Date' and 'Pre-retirement Announcement Date' to years.
#          b. Compute the 90% confidence interval (CI) of the difference between retirement and announcement years.
#          c. For rows with a missing retirement year, add a random offset (within the CI) to the announcement year.
#          d. If the retirement year is still missing, sample from the available retirement years.
#          e. Set the start year as 20 years before the (imputed) retirement year.
#       3. Plants under construction or announced are left unchanged if a start date is present.
#
#     Parameters:
#       data : DataFrame containing columns: "Start date", "Capacity operating status", "Retired Date", and "Pre-retirement Announcement Date".
#       avg_renovation_cycle : Average renovation cycle in years, default=20
#
#     Returns:
#           The "Start date" column after imputing missing values.
#     """
#     df = data.copy()
#
#     # Convert 'Start date' to datetime
#     df["Start date"] = pd.to_datetime(df["Start date"], errors="coerce")
#
#     # Select rows with missing start dates
#     missing_dates = df[df["Start date"].isna()].copy()
#     logging.info("Handling missing 'Start date' values.")
#
#     # --- Impute for operating plants ---
#     operating_mask = missing_dates["Capacity operating status"] == "operating"
#     operating_missing = missing_dates[operating_mask]
#     if not operating_missing.empty:
#         # Sample a random offset between 0 and avg_renovation_cycle and subtract from START_YEAR
#         random_offsets = np.random.randint(low=0, high=avg_renovation_cycle, size=operating_missing.shape[0])
#         imputed_years = simulation_start_year - random_offsets
#         df.loc[operating_missing.index, "Start date"] = pd.Series(
#             pd.to_datetime(imputed_years.astype(str)), index=operating_missing.index
#         )
#
#     # --- Impute for pre-retirement plants ---
#     pre_retirement_mask = missing_dates["Capacity operating status"] == "operating pre-retirement"
#     pre_retirement_missing = missing_dates[pre_retirement_mask]
#     if not pre_retirement_missing.empty:
#         # Convert retirement and announcement dates to year values
#         retire_year = pd.to_datetime(pre_retirement_missing["Retired date"], errors="coerce").dt.year
#         announce_year = pd.to_datetime(
#             pre_retirement_missing["Pre-retirement announcement date"], errors="coerce"
#         ).dt.year
#
#         # Compute the 90% confidence interval of the difference between retirement and announcement years
#         diff_years = (retire_year - announce_year).dropna()
#         if not diff_years.empty:
#             ci_lower, ci_upper = diff_years.quantile([0.05, 0.95]).round().astype(int).to_list()
#
#             # For rows with missing retirement year, infer from announcement year with a random offset in the CI
#             missing_retire = retire_year.isna()
#             if missing_retire.any():
#                 random_offsets = np.random.randint(
#                     low=ci_lower, high=ci_upper, size=announce_year[missing_retire].shape[0]
#                 )
#                 retire_year = retire_year.copy()
#                 retire_year.loc[missing_retire] = announce_year[missing_retire] + random_offsets
#
#             # For any remaining missing retirement years, sample from the available retire_year values
#             if retire_year.isna().any():
#                 sampled_years = retire_year.sample(frac=1, weights=pd.notna(retire_year).astype(float), replace=True)  # type: ignore[arg-type]
#                 sampled_years.index = retire_year.index
#                 retire_year.fillna(sampled_years, inplace=True)
#
#             # Impute 'Start date' as 20 years before the (imputed) retirement year
#             imputed_start_year = (retire_year - 20).astype(int)
#             df.loc[pre_retirement_missing.index, "Start date"] = pd.to_datetime(imputed_start_year.astype(str))
#
#     # Return the imputed 'Start date' column
#     return df["Start date"]
#
#
# def filter_relevant_dates(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Adds the last renovation year to each furnace group data and drops all irrelevant date columns.
#     Adds dates where missing, e.g., for the start date.
#     operating under the following ruleset:
#
#     ### Rulset
#         1. First we set the data for plants with dates, and assume that renovation occured as scheduled every 20 years ( this is what is being done in calculate last rennovation year)
#         2. missing operating data will be sampled from current distribution of under 20 year old furnaces (surely more intelligent methods are available)
#
#         ### For announced and construction furnaces
#         1. Announced: If Start date is present, we keep it. If not throw it
#         2. For construction furnaces we simply take the start date present
#         3. If missing start date we also leave it out for now (it seems that they have researched thigns an examples show that something has happened so that those steel plants are not yet operational)
#     """
#     logging.info(
#         "Filling in missing start dates, calculating the last renovation year per furnace group, and removing all irrelevant dates."
#     )
#
#     data["Last renovation year"] = data["Start date"].apply(calculate_last_renovation_year).values
#     data["Start date"] = impute_missing_dates(data).copy()
#     data["Start date"] = pd.to_datetime(data["Start date"].values, errors="coerce").year.astype("Int64")
#
#     # Remove missing rows with missing start dates
#
#     data = data.rename(columns={"Start date": "Start year"})
#     # Fill in missing start dates using plant age if available
#     data["Start year"] = data.apply(
#         lambda row: (
#             STEEL_PLANT_GEM_DATA_YEAR - int(float(row["Plant age (years)"]))
#             if pd.isna(row["Start year"]) and pd.notna(pd.to_numeric(row["Plant age (years)"], errors="coerce"))
#             else row["Start year"]
#         ),
#         axis=1,
#     )
#     # Else, with random years between 2000 and 2013 (most likely period since worldwide production doubled during that time period)
#     # TODO: @Rafal to refine based on OECD data
#     data["Start year"] = (
#         data["Start year"].apply(lambda x: x if pd.notna(x) else np.random.randint(2000, 2014)).astype("Int64")
#     )
#     data.drop(
#         columns=[
#             "Announced date",
#             "Construction date",
#             "Pre-retirement announcement date",
#             "Idled date",
#             "Retired date",  # TODO: I think we might need the retired year?
#             "Plant age (years)",
#         ],
#         inplace=True,
#     )
#     return data
#
#
# def read_technology_lcop_data(path: Path) -> dict:
#     """
#     Reads technology LCOP (Levelized Cost of Production) data from a CSV file.
#     The CSV file is expected to have two columns where the first column contains the keys and the second column contains the values.
#
#     Returns:
#         dict: A dictionary where the keys are from the first column and the values are from the second column of the CSV file.
#     """
#     if not path:
#         raise ValueError("technology_lcop_csv path must be provided")
#     data = pd.read_csv(path)
#     return dict(zip(data.iloc[:, 0], data.iloc[:, 1]))
#
#
# def convert_certifications_to_binary(data: pd.DataFrame) -> pd.DataFrame:
#     """
#     Combine the three certification-related columns into one and convert it into a binary value. 0: no certification, 1: any valid certification among ISO 14001, ISO 50001, and ResponsibleSteel.
#     """
#     # TODO Check with Rafal whether more detail is needed (e.g., date, type of certification)
#     logging.info("Converting certification columns to binary.")
#     cf_cols = ["ISO 14001", "ISO 50001", "ResponsibleSteel Certification"]
#     data_bin = data.copy()
#     for col in cf_cols:
#         data_bin[col] = data[col].apply(
#             lambda x: 0 if ((x == "unknown") | (x == "expired") | (not isinstance(x, str) and np.isnan(x))) else 1
#         )
#     data_bin["Certified"] = data_bin[cf_cols].sum(axis=1).apply(lambda x: 1 if x > 0 else 0)
#     data_bin.drop(cf_cols, axis=1, inplace=True)
#     return data_bin
#
#
# def calculate_utilisation(df: pd.DataFrame, production_gem_data_years: list[int]) -> pd.DataFrame:
#     """
#     Calculate the utilisation of each furnace group in each year by dividing the production by the nominal capacity.
#     """
#     for year in production_gem_data_years:
#         df[f"Utilisation {year}"] = df.apply(
#             lambda row: (
#                 float(row[str(year)]) / float(row["Nominal capacity (ttpa)"])
#                 if row["Nominal capacity (ttpa)"] != 0
#                 else 0
#             ),  # Set utilisation to 0 if capacity is 0
#             axis=1,
#         )
#         df.drop(columns=str(year), inplace=True)
#     return df
#
#
# def split_production_among_furnace_groups(
#     furnace_group_row: pd.Series, production_df: pd.DataFrame, production_gem_data_years: list[int]
# ) -> pd.Series:
#     """
#     Split the production of a plant among the furnace groups with the same technology in the same plant proportionally to the furnace group's capacity.
#     Make sure that the furnace group is operational in the year of interest, otherwise, set its production to zero.
#     """
#     furnace_group = furnace_group_row.copy()
#     for year in production_gem_data_years:
#         start_year = pd.to_numeric(furnace_group["Start year"], errors="coerce")
#         if year >= start_year:  # Make sure production is only allocated to years when the plant was operational
#             # Calculate the total capacity of the repeated technologies available in the same plant on that year
#             sibling_furnace_groups = production_df[
#                 (production_df["Plant ID"] == furnace_group["Plant ID"])
#                 & (production_df["Technology"] == furnace_group["Technology"])
#             ]
#             sibling_furnace_groups_active = sibling_furnace_groups[
#                 sibling_furnace_groups["Start year"].astype(int) <= year
#             ]
#             sibling_capacities = sibling_furnace_groups_active["Nominal capacity (ttpa)"]
#             total_capacity = np.sum(sibling_capacities)
#             # Calculate the production that should be allocated to the repeated technologies
#             if total_capacity > 0:
#                 furnace_group[str(year)] = (
#                     furnace_group[str(year)] * furnace_group["Nominal capacity (ttpa)"] / total_capacity
#                 )
#             else:
#                 furnace_group[str(year)] = 0
#         else:
#             furnace_group[str(year)] = 0
#
#         # If production is higher than capacity, set production to capacity
#         if furnace_group[str(year)] > furnace_group["Nominal capacity (ttpa)"]:
#             furnace_group[str(year)] = furnace_group["Nominal capacity (ttpa)"]
#     return furnace_group
#
#
# def calculate_regional_average_utilisation(
#     capacity_df: pd.DataFrame,
#     reg_prod_iron: pd.DataFrame,
#     reg_prod_steel: pd.DataFrame,
#     country_ws_region_map: dict,
#     production_gem_data_years: list[int],
# ) -> tuple[pd.DataFrame, pd.DataFrame]:
#     """
#     Calculate the average utilisation of each technology in each country and WS region by dividing the total production by the aggregated nominal capacity of all furnace groups.
#     Assumptions: Set utilisation to 0 if capacity is 0. Cap utlisation at 1 manually if larger than 1 (happens only for complete GEM data).
#     """
#     # Calculate total capacity per technology per country and WS region
#     capacity_df["WS Region"] = capacity_df["Country"].map(country_ws_region_map)
#     regional_tot_capacity = capacity_df.groupby(["Country", "Technology"])["Nominal capacity (ttpa)"].sum().to_dict()
#     regional_tot_capacity.update(
#         capacity_df.groupby(["WS Region", "Technology"])["Nominal capacity (ttpa)"].sum().to_dict()
#     )
#     # Calculate average utilisation per technology per country and WS region
#     for year in production_gem_data_years:
#         # Set utilisation to 0 if capacity is 0
#         reg_prod_iron[f"Utilisation {year}"] = reg_prod_iron.apply(
#             lambda row: (
#                 row[f"{year}"] / regional_tot_capacity.get((row["Country"], row["Technology"]), 1)
#                 if regional_tot_capacity.get((row["Country"], row["Technology"]), 1) != 0
#                 else 0
#             ),
#             axis=1,
#         )
#         # Cap utlisation at 1
#         reg_prod_iron[f"Utilisation {year}"] = reg_prod_iron[f"Utilisation {year}"].clip(upper=1)
#     for year in production_gem_data_years:
#         reg_prod_steel[f"{year}"] = reg_prod_steel[f"{year}"] * MT_TO_T  # Convert from Miot to t
#         reg_prod_iron[f"{year}"] = reg_prod_iron[f"{year}"] * KT_TO_T  # Convert from kt to t
#         # Set utilisation to 0 if capacity is 0
#         try:
#             reg_prod_steel[f"Utilisation {year}"] = reg_prod_steel.apply(
#                 lambda row: (
#                     row[f"{year}"] / regional_tot_capacity.get((row["WS Region"], row["Technology"]), 1)
#                     if regional_tot_capacity.get((row["WS Region"], row["Technology"]), 1) != 0
#                     else 0
#                 ),
#                 axis=1,
#             )
#             # Cap utlisation at 1
#             reg_prod_steel[f"Utilisation {year}"] = reg_prod_steel[f"Utilisation {year}"].clip(upper=1)
#         except KeyError:
#             # FIXME jochen - just to make the test pass again
#             reg_prod_steel[f"Utilisation {year}"] = 0
#     return reg_prod_iron, reg_prod_steel
#
#
# def fill_missing_utlisation_values(
#     df_missing_vals: pd.DataFrame,
#     reg_avg_iron: pd.DataFrame,
#     reg_avg_steel: pd.DataFrame,
#     production_gem_data_years: list[int] | None = None,
# ) -> pd.DataFrame:
#     """
#     Fill in missing historical utilisation values with regional (and global, if regional are not available) averages per technology from World Steel and USGSA.
#     """
#     if production_gem_data_years is None:
#         production_gem_data_years = DEFAULT_PRODUCTION_GEM_DATA_YEARS
#     df_filled = df_missing_vals.copy()
#     for year in production_gem_data_years:
#         logging.info(f"Filling missing values in {year}")
#         # Fill in missing values with the regional average per technology using the new regional data
#         for row in df_filled.iterrows():
#             if pd.isna(row[1][f"Utilisation {year}"]):
#                 if row[1]["Technology"] in reg_avg_iron["Technology"].unique():
#                     filtered_iron = reg_avg_iron[
#                         (reg_avg_iron["Technology"] == row[1]["Technology"])
#                         & (reg_avg_iron["Country"] == row[1]["Country"])
#                     ]
#                     if not filtered_iron.empty:
#                         df_filled.at[row[0], f"Utilisation {year}"] = filtered_iron[f"Utilisation {year}"].values[0]  # type: ignore[index]
#                 elif row[1]["Technology"] in reg_avg_steel["Technology"].unique():
#                     filtered_steel = reg_avg_steel[
#                         (reg_avg_steel["Technology"] == row[1]["Technology"])
#                         # FIXME jochen - just to make the test pass again
#                         # & (reg_avg_steel["WS Region"] == row[1]["WS Region"])
#                     ]
#                     if not filtered_steel.empty:
#                         df_filled.at[row[0], f"Utilisation {year}"] = filtered_steel[f"Utilisation {year}"].values[0]  # type: ignore[index]
#         # Fill in remaining missing values with the global average per technology
#         df_filled = fill_missing_values_with_global_avg_per_tech(df_filled, f"Utilisation {year}")
#     df_filled.drop(columns=["Country", "WS Region", "Region"], inplace=True)
#     return df_filled
#
#
# def check_column_group_capped_at_1(df: pd.DataFrame, cols: list, var_name: str) -> None:
#     """
#     Check that a group of columns has values which do not exceed 1. Return a warning and the rows for which at least one of the columns is higher than one
#     if it is not the case.
#     """
#     num_rows_higher_1 = (df[cols].max(axis=1) > 1).sum()
#     if num_rows_higher_1 > 0:
#         logging.warning(f"{var_name} is >1 for {num_rows_higher_1} rows.")
#         logging.info(df[df[cols].max(axis=1) > 1])
#
#
# def add_historical_production_data(
#     plant_furnace_data: pd.DataFrame,
#     hist_production_data: pd.DataFrame,
#     reg_iron_production_data: pd.DataFrame,
#     reg_steel_production_data: pd.DataFrame,
#     gem_country_ws_region_map: dict[str, str],
#     production_gem_data_years: list[int] | None = None,
# ) -> pd.DataFrame:
#     """
#     Add historical production data to the plant and furnace group data.
#     """
#     fg_prod_data = pd.merge(
#         plant_furnace_data.reset_index(),  # Make Furnace ID a column
#         hist_production_data,
#         on=["Plant ID", "Technology"],
#         how="left",  # keep all furnace groups
#     ).set_index("Furnace ID")
#     if production_gem_data_years is None:
#         production_gem_data_years = DEFAULT_PRODUCTION_GEM_DATA_YEARS
#     fg_prod_data_corrected = fg_prod_data.apply(
#         lambda row: split_production_among_furnace_groups(row, fg_prod_data, production_gem_data_years), axis=1
#     )
#     reg_avg_utilisation_iron, reg_avg_utilisation_steel = calculate_regional_average_utilisation(
#         fg_prod_data_corrected,
#         reg_iron_production_data,
#         reg_steel_production_data,
#         gem_country_ws_region_map,
#         production_gem_data_years,
#     )
#     fg_utilisation_data = calculate_utilisation(fg_prod_data_corrected, production_gem_data_years)
#     fg_utilisation_data_filled = fill_missing_utlisation_values(
#         fg_utilisation_data, reg_avg_utilisation_iron, reg_avg_utilisation_steel, production_gem_data_years
#     )
#     check_column_group_capped_at_1(
#         fg_utilisation_data_filled, [f"Utilisation {year}" for year in production_gem_data_years], "Utilisation"
#     )
#     return fg_utilisation_data_filled
#
#
# def split_into_plant_and_furnace_group(
#     plant_furnace_data: pd.DataFrame,
#     # steel_products_data: pd.DataFrame,
#     production_gem_data_years: list[int] | None = None,
# ) -> tuple[pd.DataFrame, pd.DataFrame]:
#     """
#     Split the data two dataframes (plant and furnace group data) and save them to file.
#     """
#     logging.info("Splitting the data into plant and furnace group dataframes and saving them to file.")
#     # TODO: Add category of steel product
#     plant_df_w_duplicates = plant_furnace_data[
#         [
#             "Plant ID",
#             "Latitude",
#             "Longitude",
#             "Power source",
#             "SOE Status",
#             "Parent GEM ID",
#             "Workforce size",
#             "Certified",
#         ]
#     ].set_index("Plant ID")
#     plant_df = plant_df_w_duplicates[~plant_df_w_duplicates.index.duplicated(keep="first")]
#     furnace_group_df = plant_furnace_data[
#         [
#             "Plant ID",
#             "Capacity operating status",
#             "Start year",
#             "Last renovation year",
#             "Technology",
#             "Nominal capacity (ttpa)",
#         ]
#         + [f"Utilisation {year}" for year in (production_gem_data_years or DEFAULT_PRODUCTION_GEM_DATA_YEARS)]
#     ]
#     # TODO @Jochen: Save to json
#     return plant_df, furnace_group_df
#
#
# # ----------------------------------------------------- WRAPPER -----------------------------------------------------
# def execute_preprocessing(
#     gem_country_ws_region_map: dict[str, str],
#     steel_plants_csv: Path | None = None,
#     hist_production_csv: Path | None = None,
#     reg_hist_production_iron_csv: Path | None = None,
#     reg_hist_production_steel_csv: Path | None = None,
#     simulation_start_year: int = DEFAULT_SIMULATION_START_YEAR,
#     production_gem_data_years: list[int] | None = None,
# ) -> tuple[pd.DataFrame, pd.DataFrame]:
#     """
#     Execute the preprocessing steps in the correct order.
#     """
#     logging.info("Executing all preprocessing steps.")
#     raw_plant_data, raw_production_data, raw_regional_iron_production_data, raw_regional_steel_production_data = (
#         load_data(
#             steel_plants_csv=steel_plants_csv,
#             hist_production_csv=hist_production_csv,
#             reg_hist_production_iron_csv=reg_hist_production_iron_csv,
#             reg_hist_production_steel_csv=reg_hist_production_steel_csv,
#         )
#     )
#     hist_production_data = extract_plant_production_history(raw_production_data)
#     anon_data = anonimise_plant_data(raw_plant_data)
#     anon_data_latlon = split_spatial_coordinates(anon_data)
#     anon_data_latlon_active = filter_inactive_plants(anon_data_latlon)
#     anon_data_latlon_active_fc = extract_furnace_capacity(anon_data_latlon_active)
#     anon_data_latlon_active_fc_cdates = filter_relevant_dates(anon_data_latlon_active_fc)
#     anon_data_latlon_active_fc_cdates_bincf = convert_certifications_to_binary(anon_data_latlon_active_fc_cdates)
#     assert isinstance(hist_production_data, pd.DataFrame)
#     anon_data_latlon_active_fc_cdates_bincf_hist = add_historical_production_data(
#         anon_data_latlon_active_fc_cdates_bincf,
#         hist_production_data,
#         raw_regional_iron_production_data,
#         raw_regional_steel_production_data,
#         gem_country_ws_region_map,
#         production_gem_data_years,
#     )
#     # steel_products_data = make_steel_product_df(anon_data_latlon_active_fc_cdates_bincf_hist)
#     return split_into_plant_and_furnace_group(
#         anon_data_latlon_active_fc_cdates_bincf_hist,
#         # steel_products_data
#         production_gem_data_years,
#     )
#
#
# if __name__ == "__main__":
#     # Note: This main block is for testing only. In production, pass gem_country_ws_region_map
#     logging.warning("This script requires gem_country_ws_region_map to be passed as a parameter.")
#     logging.warning("In production, use Environment.country_mappings to provide the mappings.")
