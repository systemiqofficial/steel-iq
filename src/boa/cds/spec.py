"""
CDS dataset mechanics: dataset ids, variable names, and file-naming helpers.

Single source of truth shared by the downloader and the converter, so the
folder names one writes and the other reads can never drift apart. Model
parameters (densities, LULC fractions) live in boa.config.settings instead.
"""

CDS_DATASET = "sis-energy-global-reanalysis"

# Technology key -> full CDS variable name (request), short filename slug, and
# variable name inside the delivered NetCDFs.
TECHS = ("solar", "wind")
CDS_CF_VARIABLES = {
    "solar": "solar_photovoltaic_generation_capacity_factor",
    "wind": "wind_power_onshore_capacity_factor",
}
CDS_SLUGS = {"solar": "solar_cf", "wind": "wind_onshore_cf"}
CDS_VARS = {"solar": "spv_cf", "wind": "won"}

MASK_VARIABLES = [
    "country_aggregation_mask",
    "country_aggregation_mask_for_energy_demand",
    "latitude_weighting_coefficients",
    "solar_pv_exclusion_mask",
    "solar_pv_land_mask",
    "sub_national_aggregation_mask",
    "wind_power_exclusion_mask",
    "wind_power_land_mask",
]

# Combined exclusion masks, per technology: delivered filename and the variable inside
# it. Both were read off the delivered files rather than the documentation, which names
# the wind variable `m_rest`; the shipped file uses `wp_mask`. Values are binary with
# 1 = excluded, so an availability factor is `1 - mask`.
EXCLUSION_MASK_FILES = {
    "pv": "ANCI_SPVM-mask_C3S2LOT1_025d_v1.00.nc",
    "wind": "ANCI_WPM-mask_C3S2LOT1_025d_v1.00.nc",
}
EXCLUSION_MASK_VARS = {"pv": "PVmask", "wind": "wp_mask"}

CDS_TECH_SPEC = "ic6hh135"
CDS_RESOLUTION = "0_25_degree"
CDS_TEMPORAL_RESOLUTION = "1_hour"
CDS_VERSION = "1_00"
ALL_MONTHS = [f"{m:02d}" for m in range(1, 13)]

# ESA-CCI land cover (used by boa_cds max-capacity), also served through CDS.
LULC_DATASET = "satellite-land-cover"
LULC_YEAR = 2022
LULC_VERSION = "v2_1_1"


def cf_zip_name(
    tech: str, year: int | str, month_tag: str = "", tech_spec: str = CDS_TECH_SPEC, resolution: str = CDS_RESOLUTION
) -> str:
    """Zip filename for one (technology, year) capacity-factor download."""
    return f"cds_{CDS_SLUGS[tech]}_{tech_spec}_{resolution}_{year}{month_tag}.zip"


def cf_extract_dir_name(
    tech: str, year: int | str, tech_spec: str = CDS_TECH_SPEC, resolution: str = CDS_RESOLUTION
) -> str:
    """Directory the full-year zip is extracted to (12 monthly NetCDFs)."""
    return cf_zip_name(tech, year, "", tech_spec, resolution)[: -len(".zip")]


def masks_zip_name(tech_spec: str = CDS_TECH_SPEC, resolution: str = CDS_RESOLUTION) -> str:
    return f"cds_masks_{tech_spec}_{resolution}.zip"


def masks_extract_dir_name(tech_spec: str = CDS_TECH_SPEC, resolution: str = CDS_RESOLUTION) -> str:
    """Directory the mask bundle is extracted to (the exclusion masks live here)."""
    return masks_zip_name(tech_spec, resolution)[: -len(".zip")]


def lulc_nc_name(year: int = LULC_YEAR, version: str = LULC_VERSION) -> str:
    """NetCDF filename inside the ESA-CCI land-cover delivery, e.g.
    C3S-LC-L4-LCCS-Map-300m-P1Y-2022-v2.1.1.nc."""
    dotted = version.lstrip("v").replace("_", ".")
    return f"C3S-LC-L4-LCCS-Map-300m-P1Y-{year}-v{dotted}.nc"


def lulc_zip_name(year: int = LULC_YEAR, version: str = LULC_VERSION) -> str:
    return f"esa_cci_lc_{year}_{version}.zip"
