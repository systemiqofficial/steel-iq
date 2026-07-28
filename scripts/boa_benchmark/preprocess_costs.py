"""One-time preprocessing: flatten `Renewable_Energy_Input_Data.xlsx` into a single
`flat_costs.csv` lookup table, one row per region, with the scalar CAPEX/OPEX/battery/
cost-of-capital values this benchmark uses (no multi-year learning-curve projection, no
historical-capacity correction -- see the plan for why). A per-region `cost_of_capital` IS
included (unlike the rest of the multi-year pipeline, this is a plain lookup, not a
projection), so the benchmark can run standalone off this one CSV without a separate CLI
parameter -- see `load_cost_of_capital` below for the one targeted read of
`master_input.xlsx` this requires.

`investment_horizon` is NOT in this file -- it stays a `boa_config.LIFETIMES`-derived
constant, since it isn't data pulled from either workbook.

Bug found in production, fixed here: `preprocess_storage_costs` in
`boa_input_preprocessing.py` never converts the "Storage costs" sheet's stated `USD/kWh`
to `USD/MWh` the way the CAPEX loader does for solar/wind (`x 1000`). We're benchmarking
the sampling methodology, not reproducing that bug, so the conversion is applied here.
"""

import argparse
from pathlib import Path

import pandas as pd

DEFAULT_INVESTMENT_YEAR = 2025
DEFAULT_MASTER_INPUT_PATH = Path.home() / ".steelo" / "data_cache" / "master-input-v2.0.0" / "master_input.xlsx"


def load_cost_of_capital(master_input_path: Path) -> pd.Series:
    """Per-IRENA-region average renewable WACC, read directly from `master_input.xlsx`'s
    "Country mapping" (ISO3 -> irena_region, the same 16-region taxonomy as
    `Renewable_Energy_Input_Data.xlsx`'s "Installation costs" sheet) and "Cost of capital"
    (ISO3 -> WACC - Renewables, Systemiq-internal, country level) sheets -- a plain pandas
    join + groupby mean, not the full `CountryMappingService`/`DataManager` pipeline.

    Regions with no mapped country (e.g. "International Marine Bunkers", a shipping-fuel
    bucket with no territory) get no row here; the caller fills those from the cross-region
    max, matching production's "missing WACC -> assume unfavorable conditions" convention
    (see `preprocess_renewable_energy_cost_data` in `boa_input_preprocessing.py`).
    """
    mapping = pd.read_excel(master_input_path, sheet_name="Country mapping")[["ISO 3-letter code", "irena_region"]]
    wacc = pd.read_excel(master_input_path, sheet_name="Cost of capital")[["ISO-3 Code", "WACC - Renewables"]]
    merged = mapping.merge(wacc, left_on="ISO 3-letter code", right_on="ISO-3 Code", how="inner")
    return merged.groupby("irena_region")["WACC - Renewables"].mean().rename("cost_of_capital")


def load_flat_costs(
    xlsx_path: Path, master_input_path: Path, investment_year: int = DEFAULT_INVESTMENT_YEAR
) -> pd.DataFrame:
    capex = pd.read_excel(xlsx_path, sheet_name="Installation costs")
    if "Unit" in capex.columns:
        capex = capex.drop(columns=["Unit"])
    # The source sheet has stray non-breaking spaces (\xa0) in some region names, which
    # would otherwise silently break exact-string region lookups (e.g. from sites.yaml).
    capex["Region"] = capex["Region"].str.replace("\xa0", " ", regex=False).str.strip()
    capex = capex.pivot(index="Region", columns="Technology", values="Capex").reset_index()
    # USD/kW -> USD/MW, matching process_global_baseload_simulation_costs's convention.
    capex["capex_solar_usd_per_mw"] = capex["solar"] * 1000
    capex["capex_wind_usd_per_mw"] = capex["wind"] * 1000
    capex = capex[["Region", "capex_solar_usd_per_mw", "capex_wind_usd_per_mw"]].rename(columns={"Region": "region"})

    opex = pd.read_excel(xlsx_path, sheet_name="Operational costs")
    if "Unit" in opex.columns:
        opex = opex.drop(columns=["Unit"])
    opex_by_tech = opex.set_index("Technology")["Opex"]
    opex_pct_solar = float(opex_by_tech["solar"])
    opex_pct_wind = float(opex_by_tech["wind"])
    opex_pct_battery = float(opex_by_tech["battery"])
    # Operational costs sheet only has "World" rows -- same OPEX % applies to every region.
    capex["opex_pct_solar"] = opex_pct_solar
    capex["opex_pct_wind"] = opex_pct_wind
    capex["opex_pct_battery"] = opex_pct_battery

    storage = pd.read_excel(xlsx_path, sheet_name="Storage costs")
    if "Unit" in storage.columns:
        storage = storage.drop(columns=["Unit"])
    storage = storage.set_index("Metric")
    # USD/kWh -> USD/MWh: the x1000 conversion production code is missing (see docstring).
    battery_cost_usd_per_mwh = float(storage.loc["Total installed unit cost", investment_year]) * 1000
    avg_implied_storage = float(storage.loc["Average implied storage", investment_year])
    capex["battery_cost_usd_per_mwh"] = battery_cost_usd_per_mwh
    capex["avg_implied_storage"] = avg_implied_storage
    capex["source_investment_year"] = investment_year

    cost_of_capital = load_cost_of_capital(master_input_path)
    capex = capex.merge(cost_of_capital, left_on="region", right_on="irena_region", how="left")
    missing = capex.loc[capex["cost_of_capital"].isna(), "region"].tolist()
    if missing:
        print(f"No mapped country for region(s) {missing}; filling cost_of_capital with cross-region max.")
        capex["cost_of_capital"] = capex["cost_of_capital"].fillna(cost_of_capital.max())

    return capex.sort_values("region").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx-path", type=Path, default=Path("data_copernicus/Renewable_Energy_Input_Data.xlsx"))
    parser.add_argument("--master-input-path", type=Path, default=DEFAULT_MASTER_INPUT_PATH)
    parser.add_argument("--investment-year", type=int, default=DEFAULT_INVESTMENT_YEAR)
    parser.add_argument("--out", type=Path, default=Path("scripts/boa_benchmark/preprocessed_data/flat_costs.csv"))
    args = parser.parse_args()

    df = load_flat_costs(args.xlsx_path, args.master_input_path, args.investment_year)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} regions to {args.out}")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
