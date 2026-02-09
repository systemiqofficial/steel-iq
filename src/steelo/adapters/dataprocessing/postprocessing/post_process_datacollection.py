import pandas as pd
import pickle
from pathlib import Path

import logging

# Define standard cost_breakdown columns that should always be present
# This ensures consistent CSV output across all simulations
STANDARD_COST_BREAKDOWN_COLUMNS = [
    "cost_breakdown - coal",
    "cost_breakdown - coking coal",
    "cost_breakdown - coke",
    "cost_breakdown - pci",
    "cost_breakdown - bio-pci",
    "cost_breakdown - hydrogen",
    "cost_breakdown - natural gas",
    "cost_breakdown - electricity",
    "cost_breakdown - burnt lime",
    "cost_breakdown - burnt dolomite",
    "cost_breakdown - olivine",
    "cost_breakdown - bf gas",
    "cost_breakdown - cog",
    "cost_breakdown - bof gas",
    # "cost_breakdown - fixed opex",
    # "cost_breakdown - carbon cost",
    # "cost_breakdown - debt share",
    # "cost_breakdown - material cost",
]

STRUCTURAL_FEED_COLUMNS = {"commands", "materials", "energy", "cost_breakdown"}


def _normalize_reductant(value):
    """Convert reductant labels to a reporting-friendly form (spaces -> underscores)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return ""
    return text.replace(" ", "_")


def _filter_effectively_empty_frames(frames: list[pd.DataFrame]) -> list[pd.DataFrame]:
    """Drop frames that pandas considers empty (all rows NA) to avoid concat warnings."""
    cleaned: list[pd.DataFrame] = []
    for frame in frames:
        if frame is None or frame.empty:
            continue
        # Remove columns that are all-NA so pandas does not treat them specially during concat
        non_na_columns = frame.notna().any(axis=0)
        for column in STRUCTURAL_FEED_COLUMNS:
            if column in frame.columns and not non_na_columns.get(column, False):
                non_na_columns[column] = True
        frame = frame.loc[:, non_na_columns]
        if frame.empty:
            continue
        if frame.dropna(how="all").empty:
            continue
        cleaned.append(frame)
    return cleaned


def extract_and_process_stored_dataCollection(
    commands: dict,
    data_dir: Path,
    output_path: Path,
    store: bool = True,
) -> pd.DataFrame | str:
    """
    Extract and process stored pickle-files storing the collected data from the simulation.
    This function is a placeholder and should be replaced with actual implementation.
    """
    logging.info(f"extract_and_process_stored_dataCollection: Received commands for {len(commands)} years")
    if commands:
        sample_year = list(commands.keys())[0]
        sample_commands = commands[sample_year]
        logging.info(f"Sample year {sample_year}: {len(sample_commands)} commands")
        if sample_commands:
            logging.info(f"Sample commands: {list(sample_commands.items())[:3]}")

    # Track which furnace groups we've seen in previous years to detect new additions
    seen_furnace_groups: set[str] = set()
    # Track AddFurnaceGroup commands by plant_id to match them when furnaces become operational
    add_furnace_commands: dict[str, str] = {}  # plant_id -> command_name

    all_furnaces = []
    for year in list(commands.keys()):
        allocation_file = data_dir / f"datacollection_post_allocation_{str(year)}.pkl"
        if not allocation_file.exists():
            logging.warning(f"Warning: Allocation file for year {year} not found, skipping...")
            continue
        with open(allocation_file, "rb") as f:
            data = pickle.load(f)
        df = pd.DataFrame(data).T
        furnaces = []
        feedstock_proccess = []
        for plant_id, row in df.iterrows():
            plant = pd.DataFrame(row["furnace_groups"])
            if plant.empty:
                continue
            # Build list of columns to select - include optional columns only if they exist
            fg_cols_to_select = [
                "furnace_group_id",
                "technology",
                "product",
                "chosen_reductant",
                "capacity",
                "production",
                "unit_vopex",
                "unit_fopex",
                "unit_production_cost",
                "debt_repayment_for_current_year",
                "historic_balance",
            ]
            if "unit_debt_repayment" in plant.columns:
                fg_cols_to_select.append("unit_debt_repayment")
            # Add optional columns if they exist in the DataFrame
            if "unit_secondary_output_costs" in plant.columns:
                fg_cols_to_select.append("unit_secondary_output_costs")
            if "unit_carbon_cost" in plant.columns:
                fg_cols_to_select.append("unit_carbon_cost")
            # Add emissions columns dynamically
            fg_cols_to_select += plant.columns[plant.columns.str.startswith("emissions_")].tolist()

            furnaces.append(plant[fg_cols_to_select])
            # Include cost_breakdown if it exists in the plant DataFrame
            cols_to_select = ["furnace_group_id", "materials", "energy"]
            if "cost_breakdown" in plant.columns:
                cols_to_select.append("cost_breakdown")
            feedstock_proccess.append(plant[cols_to_select])

        # Filter out empty/all-NA DataFrames to avoid pandas FutureWarning on concat
        furnaces = _filter_effectively_empty_frames(furnaces)
        feedstock_proccess = _filter_effectively_empty_frames(feedstock_proccess)

        furnace_df = pd.concat(furnaces, axis=0).reset_index(drop=True) if furnaces else pd.DataFrame()
        # print(furnace_df)
        feedstock_df = (
            pd.concat(feedstock_proccess, axis=0).reset_index(drop=True) if feedstock_proccess else pd.DataFrame()
        )
        # print(feedstock_df)
        records = []
        for _aux, row in feedstock_df.iterrows():
            fg = row["furnace_group_id"]
            mats = row.get("materials", {})
            if not isinstance(mats, dict):
                mats = {}
            cost_break = row.get("cost_breakdown", {})
            if not isinstance(cost_break, dict):
                cost_break = {}

            # get every feedstock that appears in either dict
            for feed in set(mats):
                m = mats.get(feed, {})
                cb = cost_break.get(feed, {})
                records_dict = {
                    "furnace_group_id": fg,
                    "feedstock": feed,
                    "demand": m.get("demand"),
                    "total_cost - material and allocation": m.get("total_cost"),
                    "unit_cost - material and alloction": m.get("unit_cost"),
                }
                for process_cost in cost_break.get(feed, {}):
                    if process_cost in [
                        "total_cost",
                        "unit_cost",
                        "demand",
                        "product_volume",
                        "total_material_cost",
                        "unit_material_cost",
                    ]:
                        continue
                    records_dict[f"cost_breakdown - {process_cost}"] = cb.get(process_cost, 0)

                records.append(records_dict)
        long_df = pd.DataFrame.from_records(records)
        long_df.rename(
            columns={
                "cost_breakdown - fluxes": "cost_breakdown - burnt lime",
                "cost_breakdown - burnt_lime": "cost_breakdown - burnt lime",
                "cost_breakdown - burnt lime": "cost_breakdown - burnt lime",
                "cost_breakdown - lime": "cost_breakdown - burnt lime",
            },
            inplace=True,
        )
        if not long_df.empty and all(col in long_df.columns for col in ["furnace_group_id", "feedstock"]):
            long_df = (
                long_df.set_index(["furnace_group_id", "feedstock"])
                .T.groupby(level=0)
                .sum()
                .T.reset_index()[long_df.columns]
                .copy()
            )

        if not long_df.empty:
            # Ensure all standard cost_breakdown columns exist
            for col in STANDARD_COST_BREAKDOWN_COLUMNS:
                if col not in long_df.columns:
                    long_df[col] = None  # Use None for missing values (mypy compatible)

            full_furnace_df = furnace_df.merge(long_df, on="furnace_group_id", how="left").drop_duplicates().copy()
        else:
            # If no feedstock data, just use furnace_df and add empty cost_breakdown columns
            full_furnace_df = furnace_df.copy()
            for col in STANDARD_COST_BREAKDOWN_COLUMNS:
                full_furnace_df[col] = None
        full_furnace_df["plant_id"] = full_furnace_df["furnace_group_id"].apply(lambda x: x.split("_")[0])
        full_furnace_df = (
            df[["location", "balance"]]
            .reset_index()
            .rename(columns={"index": "plant_id"})
            .merge(full_furnace_df, on="plant_id", how="right")
            .copy()
        )
        full_furnace_df["year"] = year

        # Map commands to their string representation (class name)
        # Always add the commands column, even if empty, to ensure consistent DataFrame structure
        commands_dict = {}
        if commands:
            # Get all furnace group IDs that exist in the data for this year
            existing_fg_ids = set(full_furnace_df["furnace_group_id"].unique())

            # First, collect AddFurnaceGroup commands for tracking
            for fg_id, cmd in commands.get(year, {}).items():
                cmd_name = cmd.__class__.__name__ if cmd else None
                if cmd_name == "AddFurnaceGroup":
                    # Extract plant_id from fg_id (e.g., P100000120835_new_furnace -> P100000120835)
                    fg_id_str = str(fg_id)
                    if "_new_furnace" in fg_id_str:
                        plant_id = fg_id_str.replace("_new_furnace", "")
                        add_furnace_commands[plant_id] = cmd_name
                        logging.debug(f"Tracked AddFurnaceGroup for plant {plant_id}")

            # Process commands for this year
            for fg_id, cmd in commands.get(year, {}).items():
                cmd_name = cmd.__class__.__name__ if cmd else None

                # Skip AddFurnaceGroup - we'll handle it when the furnace becomes operational
                if cmd_name == "AddFurnaceGroup":
                    continue

                # Direct mapping for regular furnace group IDs (not new_furnace)
                fg_id_str = str(fg_id)
                if "_new_furnace" not in fg_id_str:
                    commands_dict[fg_id] = cmd_name

            # Detect newly operational furnace groups (appearing for the first time)
            new_furnace_groups = existing_fg_ids - seen_furnace_groups
            for fg_id in new_furnace_groups:
                # Extract plant_id from furnace_group_id (e.g., P100000120835_2 -> P100000120835)
                plant_id = str(fg_id).split("_")[0] if isinstance(fg_id, str) else ""

                # If we have a tracked AddFurnaceGroup command for this plant, assign it
                if plant_id in add_furnace_commands:
                    commands_dict[fg_id] = add_furnace_commands[plant_id]
                    logging.info(f"Year {year}: Assigned AddFurnaceGroup to {fg_id}")

            # Update seen furnace groups for next iteration
            seen_furnace_groups.update(existing_fg_ids)

            # Debug logging
            logging.info(f"Year {year}: Found {len(commands_dict)} commands to assign")
            if commands_dict:
                logging.info(f"Year {year}: Sample commands: {list(commands_dict.items())[:5]}")

        full_furnace_df["commands"] = full_furnace_df["furnace_group_id"].map(commands_dict)
        all_furnaces.append(full_furnace_df)

    # Combine all furnaces data
    all_furnaces = _filter_effectively_empty_frames(all_furnaces)
    if all_furnaces:
        final_df = pd.concat(all_furnaces).sort_values(by="year").reset_index(drop=True)

        # Ensure all standard cost_breakdown columns are present in the final DataFrame
        # This handles cases where a column might be missing across all years
        for col in STANDARD_COST_BREAKDOWN_COLUMNS:
            if col not in final_df.columns:
                # Find appropriate position (before 'year' column)
                if "year" in final_df.columns:
                    year_idx = final_df.columns.get_loc("year")
                    # Ensure year_idx is an integer for insert
                    if isinstance(year_idx, int):
                        final_df.insert(year_idx, col, None)  # Use None instead of pd.NA for compatibility
                    else:
                        final_df[col] = None
                else:
                    final_df[col] = None

        if "chosen_reductant" in final_df.columns:
            final_df["chosen_reductant"] = final_df["chosen_reductant"].apply(_normalize_reductant)
    else:
        final_df = pd.DataFrame()

    # Build deterministic column order:
    # [core 0-3] + [year, commands] + [remaining non-CB] + [priority CB] + [sorted CB]
    CB_PREFIX = "cost_breakdown - "
    PRIORITY_CB = [
        "cost_breakdown - demand_share_pct",
        "cost_breakdown - material cost (incl. transport and tariffs)",
    ]

    all_cols = list(final_df.columns)
    cb_cols = [c for c in all_cols if c.startswith(CB_PREFIX)]
    non_cb_cols = [c for c in all_cols if not c.startswith(CB_PREFIX) and c not in ("year", "commands")]

    # Insert year and commands at positions 4-5 within the non-CB group
    ordered = non_cb_cols[:4] + ["year", "commands"] + non_cb_cols[4:]

    # Cost breakdown: priority columns first, then the rest alphabetically
    priority = [c for c in PRIORITY_CB if c in cb_cols]
    remaining_cb = sorted(c for c in cb_cols if c not in PRIORITY_CB)
    ordered += priority + remaining_cb

    final_df = final_df[ordered]

    if store:
        final_df.to_csv(output_path, index=False)
        return str(output_path)
    else:
        return final_df
