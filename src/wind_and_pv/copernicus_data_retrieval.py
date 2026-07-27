import cdsapi
import logging
import sys
import os

# https://confluence.ecmwf.int/spaces/CKB/pages/621041351/Global+climate+and+energy+indicators+from+1950+to+present+derived+from+reanalysis+Product+User+Guide+PUG

# ANCI_ADM0-mask_C3S2LOT1_025d_v1.00.nc - Country-level mask used for spatial aggregation of model outputs
# ANCI_POPW-coeff_C3S2LOT1_025d_v1.00.nc - Gridded population used for weighting in CDD/HDD/EDD
# ANCI_SPVL-mask_C3S2LOT1_025d_v1.00.nc - Sea exclusion mask used to limit the SPV model domain
# ANCI_SPVM-mask_C3S2LOT1_025d_v1.00.nc - Combined exclusion layers for SPV modelling
# ANCI_WPLM-mask_C3S2LOT1_025d_v1.00.nc - Land mask used to restrict offshore wind modelling
# ANCI_WPM-mask_C3S2LOT1_025d_v1.00.nc - Combined exclusion layers for wind power modelling
# ANCI_WPSM-mask_C3S2LOT1_025d_v1.00.nc - Sea mask used to restrict the onshore wind power model domain

logging.basicConfig(level=logging.INFO)


def download_capacity_factors_month(year: str, months: list[str], path: str = "data_copernicus"):
    """
    Download Copernicus data for solar and wind capacity factors.
    The data will be saved as a zip file in the current working directory.
    Note: Requests size for multiple months may be too large; consider downloading month-by-month if needed.
    Args:
        year (str): Year for which to download data (e.g., "2024").
        months (list[str]): List of month strings (e.g., ["01", "02", "03"]).

    """
    dataset = "sis-energy-global-reanalysis"
    request = {
        "spatial_coverage": "global",
        "variable": [
            "solar_photovoltaic_generation_capacity_factor",
            # "wind_power_offshore_capacity_factor",
            "wind_power_onshore_capacity_factor",
        ],
        "technological_specification": ["ic8hh105", "ic3_3hh84"],
        "spatial_resolution": ["0_25_degree"],
        "temporal_resolution": ["1_hour"],
        "year": [year],
        "month": months,
        "version": "1_00",
        "file_format": "netcdf_4",
    }
    client = cdsapi.Client()
    target = f"{path}/{dataset}_{year}_{'_'.join(months)}_SPF_WOF_WON.zip"
    if os.path.exists(target):
        logging.info(f"File {target} already exists. Skipping download.")
        return
    try:
        client.retrieve(dataset, request).download(target)
        logging.info(f"Downloaded Copernicus data for {year} months {months} to {target}")
    except Exception as e:
        logging.error(f"Failed to download Copernicus data for {year} months {months}: {e}")
        raise


def download_capacity_factors_year(years: list[str], path: str = "data_copernicus"):
    for year in years:
        for month in range(1, 13):
            month_str = f"{month:02d}"
            download_capacity_factors_month(year, [month_str], path=path)


def download_masks(path: str = "data_copernicus"):
    """
    Download Copernicus masks for solar and wind power modeling.
    The data will be saved as a zip file in the current working directory.
    """
    dataset = "sis-energy-global-reanalysis"
    request = {
        "spatial_coverage": "global",
        "variable": [
            "ANCI_ADM0-mask_C3S2LOT1_025d_v1_00",
            "ANCI_POPW-coeff_C3S2LOT1_025d_v1_00",
            "ANCI_SPVL-mask_C3S2LOT1_025d_v1_00",
            "ANCI_SPVM-mask_C3S2LOT1_025d_v1_00",
            "ANCI_WPLM-mask_C3S2LOT1_025d_v1_00",
            "ANCI_WPM-mask_C3S2LOT1_025d_v1_00",
            "ANCI_WPSM-mask_C3S2LOT1_025d_v1_00",
        ],
        "version": "1_00",
        "file_format": "netcdf_4",
    }
    client = cdsapi.Client()
    target = f"{path}/{dataset}_masks.zip"
    if os.path.exists(target):
        logging.info(f"File {target} already exists. Skipping download.")
        return
    try:
        client.retrieve(dataset, request).download(target)
        logging.info(f"Downloaded Copernicus masks to {target}")
    except Exception as e:
        logging.error(f"Failed to download Copernicus masks: {e}")
        raise


if __name__ == "__main__":
    if len(sys.argv) < 1:
        logging.error("Usage: python copernicus_data_retrieval.py [year1 year2 ...] or 'masks' to download masks")
        sys.exit(1)

    if sys.argv[1] == "masks":
        download_masks()

    else:
        years = sys.argv[1:]
        download_capacity_factors_year(years)
