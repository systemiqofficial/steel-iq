# Dashboard & User Interface

This section documents the Steel Model's web interface and desktop application, including the Django web application, Electron standalone app, and the parallel worker management system.

:::{only} public
## Getting Started

The Steel Model UI comes in two flavors:

- **Django Web Application** for shared, server-hosted deployments.
- **Electron Standalone App** for self-contained desktop use.

Both experiences share the same workflows for configuring simulations, launching runs, and reviewing results. Start with the [Django & Electron Overview](django_electron_overview.md) for a tour of the interface.
:::

:::{only} not public
## Overview

The Steel Model provides two deployment options:
- **Django Web Application**: Browser-based interface for server deployments with multi-user support
- **Electron Standalone App**: Self-contained desktop application with embedded Django server

Both options provide an intuitive web interface for configuring and running steel industry simulations, with support for parallel execution, real-time progress tracking, and interactive result visualization.

## Getting Started

### [Django & Electron Overview](django_electron_overview.md)
Comprehensive guide to both deployment options, covering:
- Architecture and key components
- Installation and setup
- Building the Electron standalone app
- Feature comparison between Django and Electron
- User workflows and system requirements
- Security considerations and troubleshooting

## Implementation Details

### <a href="parallel_workers.html">Parallel Worker Management</a>
Detailed documentation of the parallel execution system:
- Worker architecture and lifecycle
- Resource management and scaling
- Task queue and scheduling
- Monitoring and troubleshooting

### <a href="custom_repositories.html">Custom Repositories</a>
Data management and repository system:
- Repository pattern implementation
- Data storage and retrieval
- Version control for configurations
- Custom data source integration

## Related Documentation

- <a href="../legacy_docs/index.html">Archive</a> - Early UI concept, user stories and design approaches
- [Architecture Overview](../architecture/overview_architecture.md) - Overall system architecture
- [Configuration Guide](../user_guide/configuration.md) - Configuration options

## Quick Links

- **Running the Django Server**: `cd src/django && python manage.py runserver`
- **Building Electron App**: `cd src/electron && npm run build-all`
- **Worker Management**: Access via the web interface navigation panel
- **Issue Reporting**: See project issue tracker for bugs and feature requests

:::


```{toctree}
:hidden:

django_electron_overview
parallel_workers
custom_repositories
```
