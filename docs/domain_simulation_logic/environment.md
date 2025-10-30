# Environment Module Documentation

## Overview

The **Environment** class represents the macro-scale context in our agent-based simulation model. It is responsible for tracking system-wide variables and aggregating data from various agents (such as furnace groups, plants, and demand centers). The Environment module encapsulates key economic and operational dynamics including production capacity, cost curves, market price prediction, and investment evaluation. In effect, it provides the “big picture” in which individual agents interact, ensuring that local decisions (like those of a furnace group or plant) contribute to and are affected by the global state of the system.

## Key Responsibilities

- **Macro-scale Tracking:**  
  Manages high-level system properties such as total production capacity, cost curves for steel and iron, and the overall market price prediction.

- **Cost Calculations:**  
  Generates cost curves based on individual furnace group production costs and cumulative capacities, then extracts prices that reflect current demand levels.

- **Investment Evaluation:**  
  Computes net present values (NPVs) for new furnace investments, helping determine the optimal technology based on various financial parameters.

- **Regional Capacity Management:**  
  Aggregates production capacities across regions, updates initial capacity baselines, and calculates capex reduction ratios to reflect economies of scale or technological improvements.

- **Demand Management:**  
  Initializes and aggregates demand data from demand centers to determine the current production requirement for the simulation year.

## Detailed Functionality

### Initialization (`__init__`)

- **Setup of Initial Capex Values:**  
  The constructor imports capex values for greenfield and brownfield investments, sets up technology switching capex, and initializes variables such as initial capacities.
  
- **Time Management:**  
  Establishes the simulation's starting year by creating a `Year` instance based on a constant (e.g., `SIMULATION_START_YEAR`).

- **Cost of Capital:**  
  Calls a dedicated method to load cost of capital data for industrial assets from an external JSON configuration file.

### Industrial Asset Cost of Capital

- **`initiate_industrial_asset_cost_of_capital`:**  
  Loads and parses a JSON file containing cost-of-equity data by country. The resulting dictionary maps each country's code to its respective cost of capital. This parameter is critical for financial calculations such as NPV assessments.

### Cost Curve Generation

- **`_generate_cost_dict`:**  
  Iterates over a list of furnace groups and compiles a dictionary of active units (i.e., those with nonzero capacity and an active status) that maps each furnace group ID to its capacity and unit production cost.

- **`generate_cost_curve`:**  
  Uses the cost dictionary to create a cost curve by:
  1. Sorting the furnace groups by unit production cost.
  2. Calculating the cumulative production capacity.
  3. Mapping the cumulative capacity to the corresponding production cost.
  
  The curve, stored in `steel_cost_curve`, is a key component for market price prediction.

- **`update_cost_curve`:**  
  Filters furnace groups based on a specified product type (defaulting to "steel") and regenerates the cost curve accordingly.

- **`extract_price_from_costcurve`:**  
  Given a production demand, this method finds and returns the first production cost from the cost curve where the cumulative capacity meets or exceeds that demand. If the cost curve is empty or demand exceeds maximum capacity, it raises an error.

### Market Price Prediction

- **`_predict_new_market_price`:**  
  This method estimates the new market price after a potential addition of a new furnace group. It temporarily adds the new unit to the existing cost dictionary, re-sorts the assets, and then calculates the cumulative capacity until it meets the required demand. The corresponding production cost serves as the predicted market price.

### Investment Evaluation: New Furnace NPV Calculation

- **`new_furnace_npv`:**  
  Evaluates the net present value (NPV) for new furnace investments. Key features include:
  - **Parameterization:**  
    Accepts financial parameters such as cost of debt, equity share, lifetime, and expected utilisation rate.
  - **Scenario Handling:**  
    Differentiates between minimum viable product (MVP) scenarios and more detailed calculations.
  - **Integration with External Calculations:**  
    Utilizes an external function (`calculate_npv_full`) to compute NPV for each technology option, and then selects the technology with the highest NPV.

### Regional Capacity Management

- **`update_regional_capacity`:**  
  Aggregates production capacities for steel and iron by region. It iterates over plant objects, accumulating capacities by technology and region (using ISO country codes). It also sets baseline initial capacities for future comparisons.

- **`update_steel_capex_reduction_ratio` and `update_iron_capex_reduction_ratio`:**  
  For each region, these methods calculate capex reduction ratios based on the change from initial to current capacities. This reduction ratio reflects the benefits of scaling and efficiency gains over time.

### Demand Management

- **`initiate_demand_dicts`:**  
  Initializes a dictionary for demand, mapping each demand center's unique identifier to its yearly demand profile. This setup is essential for tracking and aggregating demand across the simulation.

- **`calculate_demand`:**  
  Computes the total current demand for the simulation year by summing up the demand from all demand centers. The result is stored as `current_demand`, which is then used in pricing and investment decisions.

## Conclusion

The Environment class is a pivotal component in our agent-based simulation model. By encapsulating both economic (cost, investment, and price prediction) and operational (capacity aggregation and demand management) aspects, it enables a dynamic interplay between micro-level agent actions and macro-level system outcomes. This integration facilitates more realistic simulations, allowing for adaptive responses to evolving market conditions and technological changes.
