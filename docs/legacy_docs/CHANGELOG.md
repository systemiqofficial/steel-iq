0.1.3 - Unreleased
==================

### Features
- **Checkpoint System**: Added simulation checkpointing functionality for crash recovery and debugging
  - Automatic checkpoints saved every 5 years during simulation
  - Manual checkpoint triggering via `SaveCheckpoint` event
  - Checkpoint listing and cleanup utilities
  - Comprehensive state serialization including environment and repository data
  - Robust error handling that doesn't interrupt simulation flow
  - Full test coverage with unit and integration tests
  - Documentation in `docs/simulation_domain/checkpoint.md`

### Fixes
- Fixed KeyError for 'ESF' technology in region_capex by updating default capex dictionary when new technologies are activated
- Fixed various bill_of_materials access errors with defensive programming checks
- Added ESF, MOE, Prep Pellet, and Prep Coke to Technology._allowed_names validation

0.1.2 - 2024-12-20
==================

### Features
- Added a new command line entrypoint: `recreate_plants_sample_data` which recreates the sample JSON plants data
  from the default plants CSV file.
- New event-driven architecture

0.1.1 - 2024-12-05
==================

### Features

- Command line interface example using all architecture parts: `show_plants_on_map`
- Mypy type checking for the project
- Added json steel plants sample data to the project / packages fixture folder

0.1.0 - 2024-11-22
==================

### Initial Release

This is the first release of the model.