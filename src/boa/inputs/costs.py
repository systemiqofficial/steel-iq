import pandas as pd
import numpy as np
import xarray as xr
import logging
from pathlib import Path
from boa.config.settings import LIFETIMES
from boa.config.constants import KILO_TO_MEGA
from boa.geo.geospatial import CountryMappings


ALLOWED_TECHS = {"solar", "wind", "battery"}

# The RES CAPEX projections and RES OPEX sheets use descriptive labels
TECH_LABEL_MAP = {
    "Solar PV": "solar",
    "Onshore wind": "wind",
    "Battery": "battery",
}


def preprocess_renewable_energy_cost_data(
    code_df: pd.DataFrame,
    code_to_irena_region_map: dict[str, str],
    input_data_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Loads OPEX, cost of capital, and CAPEX projections; maps them to per-country values.

    Data sources:
        - CAPEX projections: regional time series 2024–2050 for solar, wind, and battery
          ("RES CAPEX projections" sheet). Replaces the IRENA-2022-baseline + learning-curve path.
        - OPEX: world-wide percentage of CAPEX, applied uniformly to every country.
        - Cost of capital: country-level WACC; missing values filled with the global max.

    The `iso3` index of both outputs has a hybrid value-set: a plain iso3 for most countries,
    and `<iso3>:<subregion>` (e.g. `CHN:CN-HB`, ISO 3166-2) for any country whose region carries
    a Subregion split in the RES CAPEX projections sheet.

    Returns:
        cost_per_country: DataFrame indexed by iso3 (hybrid) with OPEX and cost-of-capital columns.
        capex_per_country: DataFrame indexed by (iso3, Technology) with one column per year.
    """
    logging.info("Preprocessing renewable energy cost data")

    renewable_opex = pd.read_excel(input_data_path, sheet_name="RES OPEX")
    cost_of_capital = pd.read_excel(input_data_path, sheet_name="Cost of capital")
    capex_projections = pd.read_excel(input_data_path, sheet_name="RES CAPEX projections")

    if "Unit" in renewable_opex.columns:
        renewable_opex.drop(columns=["Unit"], inplace=True)
    capex_projections = capex_projections.drop(columns=[c for c in ["Unit", "Value"] if c in capex_projections.columns])
    # Subregion code column is optional; synthesize an all-NA one so downstream merge logic is uniform.
    if "Subregion code" not in capex_projections.columns:
        capex_projections["Subregion code"] = pd.NA

    # Region names in the CAPEX sheet can carry trailing whitespace (e.g. "EU + Schengen\xa0")
    # that would silently break the merge against Country mapping.
    capex_projections["irena_region"] = capex_projections["irena_region"].astype(str).str.strip()

    for sheet_name, df in (("RES CAPEX projections", capex_projections), ("RES OPEX", renewable_opex)):
        unknown = set(df["Technology"].unique()) - set(TECH_LABEL_MAP) - ALLOWED_TECHS
        if unknown:
            raise ValueError(
                f"Unknown technology label(s) in {sheet_name} sheet: {unknown}. "
                f"Expected one of {sorted(TECH_LABEL_MAP) + sorted(ALLOWED_TECHS)}."
            )
        df["Technology"] = df["Technology"].replace(TECH_LABEL_MAP)

    # OPEX: one global row per technology; pivot, then broadcast to every country
    opex_pivoted = renewable_opex.pivot(index="Region", columns="Technology", values="Opex")
    opex_pivoted.columns = [f"Opex {tech}" for tech in opex_pivoted.columns]
    global_opex = opex_pivoted.iloc[0]

    # Sheet is long-format: one row per (iso3, Tech). Hydrogen rows stay in the sheet for
    # visibility but are not consumed by the model.
    cost_of_capital_renewables_raw = cost_of_capital.loc[
        cost_of_capital["Tech"] == "Renewables",
        ["Code", "Cost of capital"],
    ]

    # Western Sahara must use ISO 3166-1 alpha-3 "ESH"; a prior revision of the sheet used
    # the non-standard "WES" and silently dropped out of the join against Country mapping.
    if "WES" in set(cost_of_capital_renewables_raw["Code"]):
        raise ValueError(
            "Western Sahara must use ISO-3 code 'ESH' in the Cost of capital sheet; found the non-standard 'WES'."
        )

    # One row per iso3 is required — duplicates would explode the join in unpredictable ways.
    dup_mask = cost_of_capital_renewables_raw["Code"].duplicated(keep=False)
    if dup_mask.any():
        dups = sorted(cost_of_capital_renewables_raw.loc[dup_mask, "Code"].unique())
        raise ValueError(f"Duplicate ISO-3 codes in Renewables rows of Cost of capital sheet: {dups}.")

    cost_of_capital_renewables = cost_of_capital_renewables_raw.rename(
        columns={"Code": "iso3", "Cost of capital": "Cost of capital (%)"}
    ).set_index("iso3")

    # Subregion codes ARE the cost-keys. Parse `iso3:rest` (or bare `iso3`) to derive owners.
    capex_projections["Subregion code"] = capex_projections["Subregion code"].astype("string")
    iso3_to_subregions: dict[str, list[str]] = {}
    for sub in capex_projections["Subregion code"].dropna().unique():
        iso3_to_subregions.setdefault(sub.split(":", 1)[0], []).append(sub)

    rows = []
    for iso3, region in code_to_irena_region_map.items():
        if not isinstance(region, str):
            continue
        if iso3 in iso3_to_subregions:
            for sub in iso3_to_subregions[iso3]:
                rows.append({"iso3": iso3, "Region": region, "cost_key": sub, "merge_key": sub})
            if iso3 not in iso3_to_subregions[iso3]:
                # Self-keyed with no explicit bare-iso3 row: un-authored provinces fall back to national (via region)
                rows.append({"iso3": iso3, "Region": region, "cost_key": iso3, "merge_key": region})
        else:
            rows.append({"iso3": iso3, "Region": region, "cost_key": iso3, "merge_key": region})
    cost_key_index = pd.DataFrame(rows)
    cost_key_index = cost_key_index[cost_key_index["iso3"].isin(code_df["iso3"])]

    # Per-key table: OPEX broadcast + WACC joined by iso3 (broadcasts across subregion keys).
    cost_per_country = cost_key_index.set_index("cost_key").sort_index()[["iso3"]]
    for col, val in global_opex.items():
        cost_per_country[col] = val
    cost_per_country = cost_per_country.join(cost_of_capital_renewables, on="iso3", how="left")
    cost_per_country["Cost of capital (%)"] = cost_per_country["Cost of capital (%)"].fillna(
        cost_per_country["Cost of capital (%)"].max()
    )
    cost_per_country = cost_per_country.drop(columns=["iso3"])
    cost_per_country.index.name = "iso3"

    # Unified merge key on the CAPEX side: Subregion code when populated, else the IRENA region.
    capex_projections["merge_key"] = capex_projections["Subregion code"].where(
        capex_projections["Subregion code"].notna(), capex_projections["irena_region"]
    )
    # Each (merge_key, Technology) must be unique — otherwise the join below explodes rows.
    dup_mask = capex_projections.duplicated(subset=["merge_key", "Technology"], keep=False)
    if dup_mask.any():
        dups = (
            capex_projections.loc[dup_mask, ["irena_region", "Subregion code", "Technology"]]
            .drop_duplicates()
            .to_dict(orient="records")
        )
        raise ValueError(
            f"Duplicate (Region/Subregion, Technology) rows in RES CAPEX projections: {dups}. "
            f"Ensure each row is uniquely identified by (Region+Subregion, Technology)."
        )
    # Per-(cost_key, technology) CAPEX cascade: exact key -> national iso3 -> IRENA region.
    # merge_key already spans sub-national keys, bare-iso3 overrides, and region names.
    year_cols = [c for c in capex_projections.columns if isinstance(c, (int, np.integer))]
    authored = capex_projections.set_index(["merge_key", "Technology"])[year_cols].apply(pd.to_numeric, errors="coerce")
    techs = list(capex_projections["Technology"].dropna().unique())
    capex_per_country, provenance = _resolve_capex_cascade(
        authored, list(cost_per_country.index), techs, code_to_irena_region_map
    )
    _log_capex_cascade(provenance, self_keyed=set(iso3_to_subregions))

    capex_per_country = capex_per_country.apply(lambda col: col.fillna(col.mean()), axis=0)

    return cost_per_country, capex_per_country


def _resolve_capex_cascade(
    authored: pd.DataFrame,
    cost_keys: list[str],
    techs: list[str],
    code_to_irena_region_map: dict[str, str],
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Resolve CAPEX per (cost_key, technology) by precedence:
        exact (cost_key, tech) -> national (iso3, tech) -> IRENA region (region, tech).

    A candidate is considered authored iff its row is not entirely NaN. Cells that resolve
    at no level are left NaN for the caller's column-mean terminal.

    Args:
        authored: year-column CAPEX indexed by (merge_key, Technology). merge_key spans
            sub-national keys, bare-iso3 overrides, AND region names, so one reindex per
            level drives the whole cascade. Must be unique per (merge_key, Technology).
        cost_keys: the full cost-key set (the cost dataset's iso3 dimension), in the order
            the caller will use to build the xarray iso3 coordinate.
        techs: technologies to resolve for every cost key.
        code_to_irena_region_map: iso3 -> IRENA region name.

    Returns:
        resolved: DataFrame over (cost_keys x techs), indexed by (iso3, Technology),
            year columns. Row order preserves cost_keys (see the xarray-alignment invariant).
        provenance: Series on the same index; values in
            {'exact', 'national', 'region', 'terminal'}.
    """
    year_cols = list(authored.columns)
    full_index = pd.MultiIndex.from_product([cost_keys, techs], names=["iso3", "Technology"])
    key_level = full_index.get_level_values("iso3")
    tech_level = full_index.get_level_values("Technology")
    iso3_level = [k.split(":", 1)[0] for k in key_level]
    region_level = [code_to_irena_region_map.get(i) for i in iso3_level]

    def _candidate(level_keys) -> pd.DataFrame:
        idx = pd.MultiIndex.from_arrays([level_keys, tech_level])
        return pd.DataFrame(authored.reindex(idx).to_numpy(), index=full_index, columns=year_cols)

    resolved = _candidate(key_level)  # exact
    provenance = pd.Series(index=full_index, dtype="object")
    provenance[~resolved.isna().all(axis=1)] = "exact"

    for level_keys, label in ((iso3_level, "national"), (region_level, "region")):
        candidate = _candidate(level_keys)
        take = resolved.isna().all(axis=1) & ~candidate.isna().all(axis=1)
        resolved.loc[take] = candidate.loc[take]
        provenance[take] = label

    provenance[provenance.isna()] = "terminal"
    return resolved, provenance


def _log_capex_cascade(provenance: pd.Series, self_keyed: set[str]) -> None:
    """
    One INFO summary line, plus per-key detail only for *self-keyed* cost keys that dropped
    below their exact level (an iso3 that appears in the Subregion column — the only case
    where inheriting below the key is surprising; ordinary non-subregion countries resolving
    at region level are the normal path and stay silent). Column-mean hits are WARNINGs.
    """
    counts = provenance.value_counts()
    logging.info(
        "CAPEX cascade: %d exact, %d national, %d region, %d column-mean.",
        int(counts.get("exact", 0)),
        int(counts.get("national", 0)),
        int(counts.get("region", 0)),
        int(counts.get("terminal", 0)),
    )
    for (cost_key, tech), level in provenance.items():
        iso3 = cost_key.split(":", 1)[0]
        if level in ("national", "region") and iso3 in self_keyed:
            logging.info("[CAPEX FALLBACK] %s %s inherited from %s level.", cost_key, tech, level)
        elif level == "terminal":
            logging.warning(
                "[CAPEX FALLBACK] %s %s has no authored value at any level; using column mean.",
                cost_key,
                tech,
            )


def _slice_capex_to_horizon(
    capex_per_country: pd.DataFrame,
    years: list[int],
) -> pd.DataFrame:
    """
    Slice CAPEX projections to the requested year window. If the horizon extends past the latest
    year in the sheet, hold that last value flat — with horizon == lifetime, future-year CAPEX
    isn't consumed by the LCOE calculation anyway.
    """
    available_years = sorted([c for c in capex_per_country.columns if isinstance(c, (int, np.integer))])
    if not available_years:
        raise ValueError("RES CAPEX projections sheet has no year columns.")

    min_available, max_available = available_years[0], available_years[-1]
    if min(years) < min_available:
        raise ValueError(
            f"investment_year={min(years)} is before the earliest year in RES CAPEX projections ({min_available})."
        )

    sliced = {y: capex_per_country.loc[:, min(y, max_available)] for y in years}
    return pd.DataFrame(sliced, index=capex_per_country.index)


def process_global_baseload_simulation_costs(
    investment_year: int,
    input_data_path: Path,
    cost_cache_dir: Path,
) -> tuple[xr.Dataset, int]:
    """
    Process inputs for the baseload simulation for all countries in the world. CAPEX is loaded
    directly from the 'RES CAPEX projections' sheet (per region, per tech, 2024-2050) and mapped to
    countries via IRENA regions. No learning curve is applied.

    The per-year result is cached as ``cost_of_renewables_<year>_investment_year.nc`` under
    ``cost_cache_dir`` (``PathConfig.cost_cache_dir``, i.e. ``costs/<set>/cache_costs/``)
    and reused on subsequent runs; the cache is shared across all baseloads/coverages/regions
    since costs depend only on year + the Excel inputs.

    Outputs:
        - projected_cost_per_country: xarray with CAPEX (solar/wind in USD/MW, battery in USD/MWh)
          on (iso3, year), plus per-country OPEX percentages and cost of capital.
        - investment_horizon: max of solar/wind/battery lifetimes (years).
    """

    investment_horizon = max(LIFETIMES["solar"], LIFETIMES["wind"], LIFETIMES["battery"])
    years = list(range(investment_year, investment_year + investment_horizon + 1))

    renewables_costs_file = cost_cache_dir / f"cost_of_renewables_{investment_year}_investment_year.nc"
    cost_cache_dir.mkdir(parents=True, exist_ok=True)

    needs_reprocess = True
    if renewables_costs_file.exists():
        cached = xr.open_dataset(renewables_costs_file)
        if "Capex battery" in cached.data_vars and cached.attrs.get("subregion_aware") == 1:
            logging.info(f"Loading cost of renewables data from {renewables_costs_file}. Skipping processing.")
            # Eager-load: downstream `.sel(iso3=...)` is called ~210k times / region-year; lazy xarray is ~3x slower per call.
            projected_cost_per_country = cached.load()
            cached.close()
            needs_reprocess = False
        else:
            logging.info(f"Cached cost file {renewables_costs_file} is stale; reprocessing.")
            cached.close()

    if needs_reprocess:
        logging.info("Processing cost of renewables data globally.")

        country_mappings = CountryMappings.from_excel(input_data_path)
        code_to_irena_region_map = {
            k: v for k, v in country_mappings.code_to_irena_region_map.items() if isinstance(v, str)
        }

        iso3_df = pd.DataFrame({"iso3": list(code_to_irena_region_map.keys())})
        cost_per_country, capex_per_country = preprocess_renewable_energy_cost_data(
            iso3_df, code_to_irena_region_map, input_data_path
        )

        capex_per_country.columns = capex_per_country.columns.astype(int)
        capex_horizon = _slice_capex_to_horizon(capex_per_country, years)

        # Solar/wind: USD/kW → USD/MW; battery: USD/kWh → USD/MWh. Same conversion factor.
        capex_solar = capex_horizon.xs("solar", level="Technology").loc[:, years].to_numpy() * KILO_TO_MEGA
        capex_wind = capex_horizon.xs("wind", level="Technology").loc[:, years].to_numpy() * KILO_TO_MEGA
        capex_battery = capex_horizon.xs("battery", level="Technology").loc[:, years].to_numpy() * KILO_TO_MEGA

        projected_cost_per_country = xr.Dataset(
            coords={
                "iso3": list(cost_per_country.index),
                "year": years,
            },
            data_vars={
                "Capex solar": (("iso3", "year"), capex_solar, {"units": "USD/MW"}),
                "Capex wind": (("iso3", "year"), capex_wind, {"units": "USD/MW"}),
                "Capex battery": (("iso3", "year"), capex_battery, {"units": "USD/MWh"}),
                "Opex solar": (("iso3",), cost_per_country["Opex solar"].values, {"units": "%"}),
                "Opex wind": (("iso3",), cost_per_country["Opex wind"].values, {"units": "%"}),
                "Opex battery": (("iso3",), cost_per_country["Opex battery"].values, {"units": "%"}),
                "Cost of capital": (("iso3",), cost_per_country["Cost of capital (%)"].values, {"units": "%"}),
            },
            attrs={"subregion_aware": 1},
        )

        projected_cost_per_country.to_netcdf(renewables_costs_file, mode="w", format="NETCDF4")

    return projected_cost_per_country, investment_horizon
