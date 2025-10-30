# Steel Model Documentation

Welcome to the Steel Model documentation. This guide will help you navigate the documentation structure.

## Documentation Overview

**New to Steel Model?** Start with the [User Guide Overview](user_guide/overview_user_guide) for installation, configuration, and running simulations.

**Interested in the logic behind the model?** Check the [Domain Logic](domain_simulation_logic/overview_simulation) to understand how the different modules of the model interact and the principles they follow.

## Quick Navigation

:::{only} public
- **[User Guide](user_guide/overview_user_guide)** - Install, configure, and operate the Steel Model
- **[Model Overview](domain_simulation_logic/overview_simulation)** - Understand how simulations work conceptually
- **[Dashboards](dashboard/overview_dashboard)** - Explore the UI and monitoring tools
:::

:::{only} not public
- **[User Guide](user_guide/overview_user_guide)** - Installation, CLI commands, configuration, and running simulations
- **[Domain Logic](domain_simulation_logic/overview_simulation)** - Plant Agent Model, Trade Model, and Geospatial Model
- **[Dashboard & UI](dashboard/overview_dashboard)** - Web interface and Electron desktop application
- **[Data Management](data_management/overview_data_management)** - Data packages, preparation, and management
- **[Architecture](architecture/overview_architecture)** - System design and architecture decision records
- **[Development Guide](development/overview_development)** - Development tools, code standards, and open improvement areas
:::

:::{only} not public

```{toctree}
:maxdepth: 2
:caption: Getting Started

user_guide/overview_user_guide
user_guide/installation_guide
user_guide/running_simulations_locally
user_guide/configuration
user_guide/cli_commands
user_guide/commandline_entrypoints
user_guide/LOGGING_GUIDE
```

```{toctree}
:maxdepth: 2
:caption: Architecture

architecture/overview_architecture
architecture/adr/index
```

```{toctree}
:maxdepth: 2
:caption: Development

development/overview_development
development/just_commands
development/electron_app
development/style_guide_and_variable_naming
development/create_modelrun_native_validation
development/technical_debt
```

```{toctree}
:maxdepth: 2
:caption: Data Management

data_management/overview_data_management
data_management/adding_excel_data_howto
data_management/python_api
data_management/django_data_management
data_management/data_packages
data_management/cli_commands
```

```{toctree}
:maxdepth: 2
:caption: Dashboard & UI

dashboard/overview_dashboard
dashboard/django_electron_overview
dashboard/parallel_workers
dashboard/user_stories
dashboard/design_approaches
dashboard/custom_repositories
dashboard/concept
```

```{toctree}
:maxdepth: 2
:caption: Domain & Simulation Logic

domain_simulation_logic/overview_simulation
domain_simulation_logic/environment
domain_simulation_logic/checkpoint
domain_simulation_logic/plant_agent_model/overview_plant_agent_model
domain_simulation_logic/plant_agent_model/agent_definitions
domain_simulation_logic/plant_agent_model/plant_agents_model_orchestration
domain_simulation_logic/plant_agent_model/plant_agent_model_logic
domain_simulation_logic/plant_agent_model/market_price_calculation
domain_simulation_logic/plant_agent_model/furnace_group_strategy
domain_simulation_logic/plant_agent_model/plant_expansions
domain_simulation_logic/plant_agent_model/introduction_of_new_technologies
domain_simulation_logic/plant_agent_model/economic_considerations
domain_simulation_logic/plant_agent_model/debt_accumulation_impact
domain_simulation_logic/plant_agent_model/calculate_costs
domain_simulation_logic/plant_agent_model/trade_model_connector
domain_simulation_logic/geospatial_model/new_plant_opening
domain_simulation_logic/geospatial_model/priority_location_selection
domain_simulation_logic/geospatial_model/baseload_optimization_atlas
domain_simulation_logic/geospatial_model/overview_geospatial_model
domain_simulation_logic/trade_model/overview_trade_model
domain_simulation_logic/trade_model/trade_model_setup
```

```{toctree}
:maxdepth: 1
:caption: Legacy Documents

legacy_docs/backward_compatibility
legacy_docs/django_simulation_field_mapping
legacy_docs/master-input-migration-plan
legacy_docs/CHANGELOG
legacy_docs/Handover-Handout
legacy_docs/Handover-Slides
legacy_docs/assumptions
legacy_docs/quota_validation_error
```

```{toctree}
:hidden:

index
```

:::
