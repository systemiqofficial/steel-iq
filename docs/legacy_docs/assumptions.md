# Assumptions Record

```
Status: Proposed
Date: 2025-02-11
This document summarizes all assumptions, tuneable parameters, and scenarios the model is based on and the reasoning behind our choices by module. A template is provided below with what you should ideally include, but feel free to just drop in dirty notes for now and use it as a collective notebook. 
```

## Template: Example module name
- Example assumption: Assumption description. Why we make this assumption on the logic (i.e., rationale)? How does it affect the model (e.g., % of cases affected or expected impact of neglected dynamics)?
- Example default value: Parameter description and name in global_variables. Why we set a certain default parameter (i.e., underlying data or expert opinion)? How does it affect the model (e.g., model sensitivity to this parameter)?
- Example scenario configuration: Scenario description and official name / source. Why is/are this/these scenario(s) representative? How different are the model results in different scenarios?

## Plant Agent Module
### GEM plant data preprocessing
- Furnace groups (same plant, technology, and start date) are the smallest unit relevant to the model. 
- Filter by operation status: Inactive steel plants are not relevant for the model. Only operating, construction, operating pre-retirement, and announced steel plants with non-existing or future idled and retired dates included.  
- Anonymisation: We removed plant name, parent name, region, country etc. but kept the lat/lon coordinates. 
- Lat/lon coordinates corrected manually when missing (2 plants).
- Renovation time: The year of the last renovation is calculated based on the start date of the plant (if available) and the average renovation cycle length (set to 20 years). If the start date is not available, the last renovation is randomly chosen among the last 20 years. Renovations are assumed to happen at the beginning of each year. 
- Unknown and other technologies: Kept as separate categories for now and separated by iron and steel.
- Start date missing values: Missing: 20%. Fillna approach: (i) age of the plant, if available (2%) and (ii) a random year between 2000 and 2013, otherwise (18%). Reasoning: Construction boom in this time period (production doubled), specially in China. 66% of plants with missing start dates are in the Asia Pacific region (China 32%, India 20%, Iran 11%).
- Capacity and production missing values: (i) "unknown" and ">0" do not carry any information and are converted to Nan for simplicity. (ii) Missing: 12% capacity data; >90% production data (7% after filling in with regional averages from WS and USGSA). Fillna approach: (a) regional averages from WS and USGSA (for production only), if available and (b) the global average per technology.
- Utilisation calculation: The total production of a plant is split among the furnace groups with the same technology in the same plant proportionally to the furnace group's capacity. This applies only if the furnace group is operational in the year of interest and its capacity is >0, otherwise, its utilisation (= production/capacity) is set to 0. If this calculation yields a production higher than capacity for a certain furnace group, we cap its utilisation at 1. 
- Binary certifications: It only matters if a plant has some certification or not (yes/no). Which certification it has (ISO 14001, ISO 50001, ResponsibleSteel) is irrelevant for the model. 
- Irrelevant data: Detailed information about the production process and equipment (e.g., main production process, detailed production equipment, iron ore source, met coal source) is not passed to the PAM. 
### Decision-making
- Underutilised is evaluated a lower threshold \def 0.2
- Current furnace groups are initiated with cost of debt 0.05 
- Current furnace groups are set with equity share 0.02
- If optimal_tech_npv <0 we return None aka do nothing 
- Assuming unchanged production in strategy eval.
- Cost of stranding current asset is subtracted from NPV where tech!=current tech - should this be distributed on payoff period? 
- Always seeking to maximimise NPV - no randomness
- New_furnace_npv uses 0.05 debt cost - should inherit
- Expansion – MVP-fix expand if unit_production_cost < expected market price / 1.5
- New furnace name generated as plant + random number 
- New furnaces are spawned with assumption of 0.7 utilisation rate, with no impact on other furnaces


## Geospatial Module
### Cost of RE
- Simplified country-level LCOE calculation done for MVP. Baseload power optimization at pixel level for testable model. 
- Considered RE technologies: power and onshore wind.
### Country-level LCOE calculation
- SSP scenario selection: 
    - SSP1-2.6: Sustainability; low emissions in line with 2°C target; low adaptation and mitigation challenges
    - SSP2-4.5: Middle of the Road; moderate emissions; medium adaptation and mitigation challenges
    - SSP3-baseline: Regional Rivalry; high emissions from no global climate policy (BAU); high adaptation and mitigation challenges
- Fill missing cost of capital values (about 20%), historical RE capacity, and capacity factors with the global average.
- The gradient of the SSP capacity projections is used to project the historical capacity data until 2100 (linear interpolation to get yearly resolution). The absolute capacity values form SSP are considered inaccurate and not used. 
- Learning curve: cases with zero initial capacity lead to a division by zero error and are replaced by a small epsilon to avoid it. 
- Deterioration rate of 0.5%/y and 1%/y assumed for solar PV and onshore wind turbines, respectively.
- Lifetime of solar PV assumed to be 30 years according to IRENA https://energycentral.com/system/files/ece/nodes/451407/irena_end_of_life_management_solar_pv.pdf p. 11; other sources say 25 years.
- Lifetime of onshore wind turbines assumed to be 25 years according to IRENA https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2017/Jun/IRENA_Leveraging_for_Onshore_Wind_Executive_Summary_2017.pdf p.20; other sources say 25 years.
- Learning rates for solar PV assumed to be 0.334, 0.234, and 0.134 in low cost, average cost, and high cost scenarios, respectively. Source: Shell project. 
- Learning rates for onshore wind turbines assumed to be 0.226, 0.146, and 0.066 in low cost, average cost, and high cost scenarios, respectively. Source: Shell project. 
    

#### Baseload power optimization
- We currently minimize for the installation CAPEX (solar, wind, and battery combined). Goal: replace by LCOE-based minimization.
- Isolated plants need to cover all their energy requirements themselves, fulfilling their baseload demand at least 95% of the time. Reducing coverage from 100% to 95% has a huge impact on required capacity and, therefore, installation costs. 
- Global average solar PV and onshore wind CAPEX assumed to be 758 USD/kW and 1154 USD/kW, respectively, according to IRENA https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2024/Sep/IRENA_Renewable_power_generation_costs_in_2023.pdf, p. 88 and p. 64
- Global average battery CAPEX assumed to be 200 USD/kWh according to IRENA https://www.irena.org/publications/2017/Oct/Electricity-storage-and-renewables-costs-and-markets
- Baseload demand assumed to be 1230 MW from Rafal's calculations
- CAPEX assumed to be fixed in time for MVP. Replace by learning curve post-MVP. 
