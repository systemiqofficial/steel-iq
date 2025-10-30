# Architecture Decision Record: Simplified Project Layout

```
Status: Proposed
Date: 2024-11-21
Updated: 2025-07-18
```

## Context

We are developing a steel decarbonization modeling package. The primary goals are to:

- Import and process datasets from Excel sheets.
- Run different economic models (e.g., Agent-Based Modeling and Stock and Flow models).
- Keep the architecture simple and maintainable.
- Facilitate unit testing over integration testing.

## Revised Directory Structure

```
src/
├── steelo/                   # Core simulation package
│   ├── adapters/            # External interfaces
│   │   ├── dataprocessing/  # Excel readers, preprocessing
│   │   ├── repositories/    # JSON & in-memory storage
│   │   └── geospatial.py    # GIS utilities
│   ├── domain/              # Business logic
│   │   ├── models.py        # Core entities (Plant, Technology, etc.)
│   │   ├── events.py        # Domain events
│   │   ├── commands.py      # Command pattern implementations
│   │   ├── trade_modelling/ # LP optimization for trade
│   │   └── mappings/        # Country/region mappings
│   ├── service_layer/       # Application services
│   │   ├── handlers.py      # Event/command handlers
│   │   ├── message_bus.py   # Event bus implementation
│   │   ├── checkpoint.py    # State persistence
│   │   └── unit_of_work.py  # Transaction management
│   ├── economic_models/     # Economic calculations
│   ├── entrypoints/         # CLI & dev tools
│   └── simulation.py        # Main simulation runner
├── steeloweb/               # Django web application
│   ├── models.py            # ModelRun, Repository entities
│   ├── views.py             # CRUD views for simulations
│   ├── tasks.py             # Background task processing
│   └── templates/           # HTML templates
├── django/                  # Django configuration
│   └── config/
│       └── settings/        # Base, local, production, test
├── electron/                # Desktop app wrapper
│   ├── main.js              # Electron main process
│   ├── build-django.js      # Django bundling script
│   └── package.json         # Node dependencies
├── network_optimisation/    # Hitchcock LP solver
├── data/fixtures/           # Default location for steelo-data-prepare
├── docs/                    # Documentation
└── tests/
    ├── unit/                # Domain logic tests
    ├── integration/         # Service layer tests
    ├── e2e/                 # CLI end-to-end tests
    ├── web/                 # Django view tests
    └── wind_and_pv/        # Renewable energy tests
```

### Testing Strategy

- **Supports a Proper Test Pyramid**: The structure facilitates writing unit tests for the core domain and services, with fewer integration and end-to-end tests.
- **Testability**: Components are decoupled and can be tested in isolation.

## Future Considerations

- **Monitor Complexity**: Regularly assess the application's complexity to determine if introducing patterns like the Message Bus or UoW becomes beneficial.
- **Prepare for Integration**: If new requirements necessitate more complex interactions (e.g., web interfaces, message queues), revisit architectural decisions.

