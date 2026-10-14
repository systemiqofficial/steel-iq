# Steel Industry Decarbonization Modeling - Web UI Concept Document

## Project Overview

The Steel Industry Decarbonization Modeling platform is a Python-based web application designed to simulate and analyze potential pathways for reducing CO2 emissions in the global steel industry. The platform allows users to configure various parameters, input data files, and select different economic modeling approaches to project industry developments from 2025 to 2050.

## Core Functionality Requirements

### 1. Parameter Configuration
- Allow configuration of numerous simulation parameters:
  - Economic indicators (e.g., carbon prices, energy costs)
  - Technology adoption rates
  - Demand forecasts
  - Policy scenarios
  - Regional constraints

### 2. Data Input Mechanisms
- Excel file uploads for custom datasets
- JSON configuration files
- Predefined scenario selection
- Parameter adjustment through UI controls

### 3. Model Selection & Execution
- Support for multiple economic modeling approaches:
  - Agent-Based Modeling (ABM)
  - Stock and Flow models
  - Linear optimization models
- Simulation execution with progress tracking

### 4. Execution Feedback
- Real-time progress indicators
- Detailed execution stage reporting
- Estimated completion time
- Notifications for long-running simulations

### 5. Result Visualization
- Interactive charts and graphs
- Geographic mapping of results
- Comparison tools for multiple scenarios
- Trend analysis over the simulation timeframe

### 6. Data Export & Storage
- Export raw simulation data to Excel/CSV
- Store simulation configurations
- Archive and label results for future reference
- Compare different simulation runs

### 7. Simulation Management
- Save, load, and modify simulation configurations
- Create simulation variants from existing runs
- Tag and categorize simulations
- Share simulation results with other users

## Technical Constraints

### Performance Considerations
- Simulations may run for 20-60 minutes
- Potential for resource-intensive calculations
- Need for asynchronous processing

### Architecture Requirements
- Separation of UI from compute processes
- Ability to queue and manage multiple simulation runs
- Efficient data storage for large result sets
- Compatibility with the existing Python codebase

## User Experience Goals

### Clarity
- Users should easily understand parameter impacts
- Results should be presented in an accessible way
- Complex model details should be approachable

### Efficiency
- Streamlined workflow for setting up simulations
- Quick access to previously used configurations
- Batch processing capabilities

### Insight Generation
- Help users discover meaningful patterns
- Support for comparative analysis
- Aid in identifying effective decarbonization strategies

## Target Users

- Policy analysts and researchers
- Industry strategists
- Environmental scientists
- Economic modelers

This document serves as a reference point for designing and implementing the web user interface for the Steel Industry Decarbonization Modeling platform.