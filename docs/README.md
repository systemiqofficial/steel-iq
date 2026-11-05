# Steel Model Documentation

Welcome to the Steel Model documentation. This guide will help you navigate the documentation structure.

## Documentation Overview

**New to Steel Model?** Start with the [User Guide Overview](user_guide/overview_user_guide) for installation, configuration, and running simulations.

**Interested in the logic behind the model?** Check the [Domain Logic](domain_simulation_logic/overview_simulation) to understand how the different modules of the model interact and the principles they follow.

## Quick Navigation

:::{only} not public
- **[User Guide](user_guide/overview_user_guide)** - Installation, CLI commands, configuration, and running simulations
- **[Domain Logic](domain_simulation_logic/overview_simulation)** - Plant Agent Model, Trade Model, and Geospatial Model
- **<a href="dashboard/overview_dashboard.html">Dashboard & UI</a>** - Web interface and Electron desktop application
- **<a href="data_management/overview_data_management.html">Data Management</a>** - Data packages, preparation, and management
- **[Architecture](architecture/overview_architecture)** - System design and architecture decision records
- **<a href="development/overview_development.html">Development Guide</a>** - Development tools, code standards, and open improvement areas
:::

:::{only} not public

```{toctree}
:maxdepth: 1
:caption: Getting Started

user_guide/overview_user_guide
```

```{toctree}
:maxdepth: 1
:caption: Model Logic

domain_simulation_logic/overview_simulation
```

```{toctree}
:maxdepth: 1
:caption: Developer Docs

architecture/overview_architecture
data_management/overview_data_management
dashboard/overview_dashboard
development/overview_development
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
