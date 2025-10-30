# Style Guide

```
Status: Proposed
Date: 2024-11-18
```

## Overview

Consistent and descriptive naming conventions are essential for maintaining code readability and ensuring that future developers (including yourself) can easily understand and maintain the codebase. This guide outlines best practices for naming variables, functions, classes, constants, and other code elements, with examples specific to Python.

Note: We use PEP 8 as a general style guide. You can read more about PEP 8 [here](https://peps.python.org/pep-0008/).

## General Guidelines

1. **Descriptive Names**: Names should clearly indicate the purpose of the variable or function. Avoid using abbreviations unless they are common and well-understood (e.g., `avg` for average).
2. **Avoid Magic Numbers**: Always use meaningful names instead of hardcoding values. Magic numbers should be named using constants.
3. **Consistency**: Keep naming conventions consistent across the entire codebase.

## Variables

- Use **snake_case** for variable names: `investment_decision`, `capacity_factor`, `expected_production`
- Variable names should be descriptive and indicate their usage or context.

Examples:
```python
supply_capacity = 1000
energy_demand_cost = 50
```

## Functions

- Use **snake_case** for function names, and ensure the function name describes its purpose.
- Prefix boolean-returning functions with `is_` or `has_` to clarify their behavior.

Examples:
```python
def calculate_variable_opex(materials_demand_cost, energy_demand_cost):
    # Function logic
    pass

def is_valid_opex_input(input_data):
    # Function logic
    return True
```

## Classes

- Use **CamelCase** for class names, starting with an uppercase letter.
- Class names should represent objects or concepts.

Examples:
```python
class ProjectManager:
    def __init__(self, name, supply_capacity):
        self.name = name
        self.supply_capacity = supply_capacity
```

## Constants

- Use **UPPERCASE_SNAKE_CASE** for constants.
- Constant names should be descriptive of their value and usage.

Examples:
```python
cost_of_equity = 0.05
CAPACITY_MULTIPLIER = 1.25
```

## Parameters

- Ensure parameters in function signatures are named descriptively to represent their purpose.
- Optional parameters should indicate they are optional in the name or default value, where possible.

Examples:
```python
def calculate_fixed_opex(supply_capacity, capacity_factor, opex_data=None):
    pass
```

## Naming Conventions for Special Cases

- **Iterators**: Use common conventions like `i`, `j` for simple loops. If more descriptive names can add clarity, use them (e.g., `plant_index`).
- **Boolean Variables**: Use names that make their meaning clear (e.g., `is_valid`, `has_capacity`).
- **Temporary Variables**: Use concise but meaningful names for temporary values.

## Avoid Common Pitfalls

1. **Avoid ambiguous names**: Names like `data`, `temp`, `foo`, `bar` are not helpful.
2. **Avoid abbreviations**: Use full words unless the abbreviation is widely understood.
3. **Avoid excessive length**: Names should be descriptive but concise.

## Practical Examples

```python
# Correct
materials_demand_cost = {
    'steel': {'demand': 100, 'cost_per_unit': 50},
    'aluminum': {'demand': 150, 'cost_per_unit': 30}
}

def calculate_total_cost(materials_demand_cost):
    total_cost = sum(data['demand'] * data['cost_per_unit'] for data in materials_demand_cost.values())
    return total_cost

# Incorrect
x = {
    'steel': {'d': 100, 'c': 50},
    'al': {'d': 150, 'c': 30}
}

def calc_tc(x):
    tc = sum(v['d'] * v['c'] for v in x.values())
    return tc
```


## Dictionary of Commonly Used Parameter Names

- `materials_demand_cost`: Dictionary containing materials with 'demand' and 'cost_per_unit'.
- `energy_demand_cost`: Dictionary containing energy types with 'demand' and 'cost_per_unit'.
- `supply_capacity`: The capacity of supply for a given process or plant.
- `capacity_factor`: Factor describing the utilization of plant capacity.
- `investment_decision`: Type of investment, such as 'Greenfield' or 'Brownfield'.
- `source_tech`: The existing technology for retrofitting projects.
- `new_tech`: The new technology being implemented.
- `opex_data`: Data used to calculate operational expenditure.
- `capex_data`: Data used to calculate capital expenditure.
- `cost_of_equity`: The cost of equity for financial calculations.
- `expected_production`: The expected production output per year.
- `lifetime_remaining`: The remaining lifetime of an asset.
- `price`: Price per unit of the produced goods.
- `initial_investment`: Initial investment cost for a project.


## Conclusion

Well-thought-out naming conventions improve code readability and maintainability. The primary goal is clarity: names should communicate their purpose unambiguously to the reader. Following these guidelines helps ensure consistency, which is key to working effectively in a team or revisiting your own code after some time.










