# Architecture Decision Records

This section contains Architecture Decision Records (ADRs) documenting key technical decisions in the Steel Model project.

## ADR Overview

### 001: Project Infrastructure
Decisions on Python version, package management (uv), virtual environments, code formatting (ruff), and source organization.

### 002: Repository Pattern
Adoption of the Repository Pattern to abstract data access and enable flexible integration of both proprietary fine-grained and public aggregated datasets.

### 003: Configuration
Configuration management approach for the project.

### 004: Project Layout
Simplified directory structure organizing the codebase into adapters, domain logic, service layer, economic models, and entrypoints. See [Architecture Overview](../architecture.md) for the full system architecture.

### 005: Event-Driven Architecture
Implementation of event-driven architecture with Message Bus, Unit of Work, Commands, Events, and Handlers.

### 006: Data Input Strategy
Centralized data preparation architecture transforming master Excel into validated JSON repositories. See [Data Management Overview](../../data_management/overview_data_management.md#architecture-decision-data-input-strategy) for full details.

## Full ADR List

```{toctree}
:maxdepth: 1

001_project_infrastructure
002_repository
003_configuration
004_project_layout
005_event_driven_architecture
```