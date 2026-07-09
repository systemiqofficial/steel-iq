import cdsapi

# https://confluence.ecmwf.int/spaces/CKB/pages/621041351/Global+climate+and+energy+indicators+from+1950+to+present+derived+from+reanalysis+Product+User+Guide+PUG

# ANCI_ADM0-mask_C3S2LOT1_025d_v1.00.nc - Country-level mask used for spatial aggregation of model outputs
# ANCI_POPW-coeff_C3S2LOT1_025d_v1.00.nc - Gridded population used for weighting in CDD/HDD/EDD
# ANCI_SPVL-mask_C3S2LOT1_025d_v1.00.nc - Sea exclusion mask used to limit the SPV model domain
# ANCI_SPVM-mask_C3S2LOT1_025d_v1.00.nc - Combined exclusion layers for SPV modelling
# ANCI_WPLM-mask_C3S2LOT1_025d_v1.00.nc - Land mask used to restrict offshore wind modelling
# ANCI_WPM-mask_C3S2LOT1_025d_v1.00.nc - Combined exclusion layers for wind power modelling
# ANCI_WPSM-mask_C3S2LOT1_025d_v1.00.nc - Sea mask used to restrict the onshore wind power model domain

dataset = "sis-energy-global-reanalysis"
request = {
    "spatial_coverage": "global",
    "variable": [
        "solar_photovoltaic_generation_capacity_factor",
        "wind_power_offshore_capacity_factor",
        "wind_power_onshore_capacity_factor",
        # "offshore_wind_sea_mask",
        # "population_weighting_coefficients",
        # "solar_pv_exclusion_mask",
        # "solar_pv_land_mask",
        # "wind_power_exclusion_mask",
        # "wind_power_land_mask"
    ],
    "technological_specification": ["ic8hh105", "ic3_3hh84"],
    "spatial_resolution": ["0_25_degree"],
    "temporal_resolution": ["1_hour"],
    "year": ["2025"],
    "month": ["01"],
    "version": "1_00",
    "file_format": "netcdf_4",
}
target = f"copernicus/{dataset}_{request['year'][0]}_{request['month'][0]}_SPF_WOF_WON.zip"
client = cdsapi.Client()
client.retrieve(dataset, request).download(target)
