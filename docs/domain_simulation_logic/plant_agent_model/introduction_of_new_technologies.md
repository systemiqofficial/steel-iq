# Introduction of New Technologies

## Overview

This document describes how new technologies are introduced into the steel industry simulation and how their growth is constrained by realistic buildout limits. The model balances economic incentives for technology adoption with real-world constraints (e.g., manufacturing capacity, work force).

The introduction of new technologies is governed by:

1. **Activation controls**: Technologies become available based on activation year settings
2. **Three introduction mechanisms**: New plants, expansions, and technology switches
3. **Probabilistic filters**: Announcement and construction probabilities add realism
4. **Time lags**: Consideration time, announcement time, and construction time delay deployment
5. **Buildout limits**: Annual capacity constraints prevent unrealistic transition speeds

Time lags and buildout limits ensure realistic transition dynamics by:

1. **Preventing overnight shifts**: Industry cannot switch all capacity instantly
2. **Representing systemic constraints**: Limited manufacturing capacity and skilled workforce for new technologies
3. **Modeling infrastructure buildout**: Hydrogen, CCS, renewables take time to deploy

These mechanisms and limits are critical for modeling **transition pathways** rather than just **equilibrium outcomes**.

## Supply Chain Constraints Represented

### Manufacturing Capacity
Real-world limits on:
- Furnace construction capacity (only so many firms can build furnaces)
- Specialized equipment (electric arc furnace electrodes, refractory bricks)
- Engineering and installation workforce

### Infrastructure Buildout
Limits reflect time to build:
- Hydrogen production and transport infrastructure (e.g., for DRI)
- Carbon capture and storage networks (e.g., for BF+CCS or BF+CCU)
- Renewable electricity generation (e.g., for electric furnaces)

### Learning Curve
Early adopters face:
- Higher costs (economies of scale not yet achieved)
- Operational challenges (workforce training, process optimization)
- Supply chain immaturity (few suppliers, high prices)

Limits prevent the model from assuming these resolve instantly.

## Technology Activation and Availability

Allowed technologies per year are set via the following two parameters on the dashboard:
- **Activation year:** used to specify when technologies currently in development will become available for production (e.g., MOE)
- **End year:** represents bans (e.g., on incumbent, polluting technologies like BF)

## Mechanisms for Introducing New Technologies

There are three different ways to bring new technologies into the system:

### 1. New Plant Opening

Business opportunities are evaluated for technologies that will be allowed at the time construction begins. The earliest possible start of construction is calculated as the current simulation year plus a consideration period (default: 3 years) plus an announcement period (fixed at 1 year). This timeline can be significantly extended by probabilistic filters such as the probability of announcement and probability of construction, as well as by capacity constraints. New plant openings are allocated a share of the maximum annual new capacity (default: 40%), and opportunities that fail to pass these filters remain pending and are reconsidered in the following year.

See [New Plant Opening](../geospatial_model/new_plant_opening.md) for detailed documentation on the business opportunity evaluation process and parameter configuration (capacity limits, probabilities, and time lags).

### 2. Expansion of Existing Furnace Groups

Happens for technologies which are allowed in the current year. Probability and capacity filters are applied here as well (60% of the maximum new capacity per year is used to expand existing plants, configurable).

See [Plant Group Expansion](plant_expansions.md) for detailed documentation on the expansion evaluation process.

### 3. Switching Technology in Existing Furnace Groups

It is only possible to switch to technologies which are allowed in the current year, for which the switch from the current technology is allowed (see "Allowed tech switches" matrix in the Master Excel file). Probability and capacity filters are also applied here.

See [Furnace Group Strategy](furnace_group_strategy.md) for detailed documentation on the technology switching decision process.

### Construction Time Lag

After each of these decisions, there is still a further time lag before the technology becomes operational (construction time; defaults to 4 years).


## Technology Buildout Limits

Technology buildout limits constrain how fast new capacity can be added to the steel industry in the simulation. These limits prevent unrealistic "technology shock" scenarios where the entire industry switches overnight, reflecting real-world constraints on supply chains, workforce, and manufacturing capacity.

### Overall Annual New Capacity Limits

**Separate limits by product type:**

The new capacity limits are set separately for iron and steel, since the two products have:
- **Different market dynamics:** Steel and iron have independent demand drivers. Also, iron can be traded internationally more easily (as pellets, DRI, HBI), while steel production is often localized near final consumers.
- **Different technology transition speeds:** For steel, EAF adoption is primarily limited by scrap availability, while, for iron, DRI (with H2) adoption is limited by hydrogen infrastructure buildout.

```python
capacity_limit_steel = 100 * MT_TO_T  # Default: 100 Mt/year max steel capacity additions
capacity_limit_iron = 100 * MT_TO_T   # Default: 100 Mt/year max iron capacity additions
```

### New Plant vs Expansion Allocation

The overall limits can be filled by furnace group expansions (adding capacity at existing plants), technology switches (replacing existing capacity with different tech), and new plants (greenfield investments). The first two are evaluated simultaneously within the Plant Agent Model. Since the third option is considered only afterwards, while running the Geospatial Model, some new capacity is reserved for it in advance (default: 40%).

```python
new_capacity_share_from_new_plants = 0.4  # 40% of new capacity from greenfield
```

## Implementation Details

### Capacity Tracking

Cumulative counters track capacity added each year and are reset annually:

```python
# Initialize at start of PAM run
steel_capacity_added_this_year = 0.0
iron_capacity_added_this_year = 0.0

# For each expansion or switch command:
if product_type == "steel":
    steel_capacity_added_this_year += capacity
elif product_type == "iron":
    iron_capacity_added_this_year += capacity

# Before approving next expansion:
if steel_capacity_added_this_year + new_expansion > capacity_limit_steel:
    reject_expansion()  # Limit reached
```

### Checking Logic

New capacity additions are rejected if the cumulative new capacity in that year is above the new capacity limit. For instance, in the furnace group strategy evaluation (`Plant.evaluate_furnace_group_strategy`):

```python
# After calculating NPV for technology switch
if switch_approved:
    product = tech_to_product[new_technology]

    # Calculate current capacity usage
    total_installed = installed_capacity_in_year(product)
    new_plants_only = new_plant_capacity_in_year(product)
    expansions_and_switches = total_installed - new_plants_only

    # Check limit
    if product == "steel":
        limit = capacity_limit_steel
    else:
        limit = capacity_limit_iron

    if expansions_and_switches + furnace_capacity > limit:
        reject_switch()  # Would exceed limit
```

## Interaction with Other Model Components

### With Market Prices
- Limits prevent oversupply → Maintain high prices → Sustain profitability
- Without limits: All profitable plants expand → Oversupply → Price crash → Mass closures

### With Balance Sheets
- Limits spread investment over multiple years
- Plants accumulate balance while waiting for next expansion opportunity
- Creates more stable financial dynamics

### With Technology Transition
- Limits determine transition speed (e.g., 20 years to full DRI adoption)
- Allows early adopters to benefit from high margins before competition intensifies
- Models first-mover advantage and learning-by-doing effects

## Configuration Examples

### Conservative Scenario (Slow Transition)
```python
capacity_limit_steel = Volumes(200_000)    # 200 kt/year
capacity_limit_iron = Volumes(100_000)     # 100 kt/year
new_capacity_share_from_new_plants = 0.3   # 30% from greenfield

# Result: Gradual technology shift over 40+ years
```

### Aggressive Scenario (Fast Transition)
```python
capacity_limit_steel = Volumes(5_000_000)   # 5 Mt/year
capacity_limit_iron = Volumes(3_000_000)    # 3 Mt/year
new_capacity_share_from_new_plants = 0.7    # 70% from greenfield

# Result: Rapid buildout, complete transition in 10-15 years
```

---

## Related Documentation

- **[Economic Considerations](economic_considerations.md)**: How limits interact with other economic factors
- **[PlantAgentsModel Orchestration](plant_agents_model_orchestration.md)**: Where limits are checked in the simulation loop
- **[Plant Expansions](plant_expansions.md)**: Detailed expansion evaluation logic including capacity checks
