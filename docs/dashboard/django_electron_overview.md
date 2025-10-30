# Django Web Interface & Electron Standalone Application

## Overview

The Steel Model provides two deployment options:
1. **Django Web Application**: Browser-based interface for server deployments
2. **Electron Standalone App**: Desktop application with embedded Django server

Both options provide the same functionality through an intuitive web interface for configuring and running steel industry simulations.

:::{only} public
## What to Expect

- Configure simulations, launch runs, and review results from a consistent UI across both deployments.
- Monitor progress with real-time status updates and download generated reports.
- Use the Electron build for quick local exploration or the Django deployment for multi-user scenarios.

For deeper architectural details and build instructions, please refer to the internal documentation.
:::

:::{only} not public

## Django Web Interface

### Architecture

The Django application (`src/steeloweb/`) provides:
- **Model Configuration**: Set simulation parameters and scenarios
- **Data Management**: Upload and manage input data files
- **Simulation Execution**: Run simulations with progress tracking
- **Result Visualization**: Interactive charts and geographic maps
- **Worker Management**: [Parallel execution system](parallel_workers.md) for concurrent simulations

### Key Components

#### Models (`src/steeloweb/models.py`)
- `ModelRun`: Tracks individual simulation executions
- `DataPreparation`: Manages input data preparation
- `Worker`: Handles parallel task execution
- `MasterExcelFile`: Stores configuration spreadsheets
- `ResultImages`: Captures visualization outputs

#### Views & Templates
- **HTMX-powered UI**: Real-time updates without page refreshes
- **Bootstrap styling**: Responsive design for all screen sizes
- **Collapsible panels**: Clean interface with progressive disclosure
- **Progress indicators**: Live simulation status updates

#### Task Queue System
- **Django-tasks integration**: Asynchronous task processing
- **Database-backed queue**: Reliable task persistence
- **Worker pool management**: Dynamic scaling based on resources

### Accessing the Web Interface

#### Development Server
```bash
cd src/django
python manage.py runserver
# Access at http://localhost:8000/admin/
```

#### Production Deployment
- Configure with gunicorn/uwsgi
- Set up reverse proxy (nginx/Apache)
- Configure static file serving
- Set environment variables for production

## Electron Standalone Application

### Overview

The Electron app (`src/electron/`) wraps the Django application in a desktop container, providing:
- **Self-contained deployment**: No server required
- **Native desktop experience**: System tray, notifications
- **Offline capability**: Fully functional without internet
- **Automatic updates**: Built-in update mechanism

### Architecture

```
┌─────────────────────────────────┐
│     Electron Main Process       │
│  (src/electron/main.js)         │
│  - Window management            │
│  - Django server lifecycle      │
│  - System integration           │
└────────────┬────────────────────┘
             │
┌────────────▼────────────────────┐
│    Embedded Django Server       │
│  - Runs on random local port    │
│  - SQLite database              │
│  - Local file storage           │
└────────────┬────────────────────┘
             │
┌────────────▼────────────────────┐
│    Chromium Renderer           │
│  - Displays Django web UI      │
│  - Hardware acceleration       │
│  - Developer tools available   │
└─────────────────────────────────┘
```

### Building the Standalone App

#### Prerequisites
- Node.js 18+ and npm
- Python 3.11+ with pip
- Platform-specific build tools

#### Build Process
```bash
# Install dependencies
cd src/electron
npm install

# Build Django bundle
npm run build-django

# Package for current platform
npm run package

# Build for all platforms
npm run build-all
```

#### Output Artifacts
- **Windows**: `.exe` installer
- **macOS**: `.dmg` disk image
- **Linux**: `.AppImage` portable app

### Configuration

The Electron app stores configuration in platform-specific locations:
- **Windows**: `%APPDATA%/steel-iq/`
- **macOS**: `~/Library/Application Support/steel-iq/`
- **Linux**: `~/.config/steel-iq/`

## Feature Comparison

| Feature | Django Web | Electron App |
|---------|------------|--------------|
| Multi-user support | ✅ Yes | ❌ Single user |
| Remote access | ✅ Yes | ❌ Local only |
| Auto-updates | ❌ Manual | ✅ Automatic |
| Database | PostgreSQL/SQLite | SQLite only |
| File storage | Server filesystem | Local filesystem |
| Worker processes | ✅ Full support | ✅ Full support |
| Resource limits | Server-based | Desktop-based |

## Key Features

### Simulation Management
- **Create & Configure**: Set up new simulation runs with custom parameters
- **Data Preparation**: Upload and validate input data files
- **Execution Monitoring**: Real-time progress tracking
- **Result Analysis**: Interactive visualizations and data export

### Parallel Processing
The [Parallel Worker Management System](parallel_workers.md) enables:
- **Concurrent Simulations**: Run multiple simulations simultaneously
- **Resource Optimization**: Automatic scaling based on available memory/CPU
- **Progress Monitoring**: Real-time status of all running simulations
- **Graceful Degradation**: System adapts to resource constraints

### Data Management
- **Master Excel Files**: Upload and manage configuration spreadsheets
- **Data Packages**: Import pre-configured data sets
- **Repository System**: Version control for simulation configurations
- **Result Archive**: Store and compare simulation outputs

### Storage Management

When you delete a simulation run from the list view, **both the database entry and all output files are automatically removed** to free up disk space.

#### What Gets Deleted
- **Database records**: ModelRun entry and associated metadata
- **Output directory**: Complete output folder with all results (`MEDIA_ROOT/model_outputs/run_{id}/`)
- **Result files**: All CSV files, plots, and visualizations
- **Trade module outputs**: Files in `TM/` subdirectory
- **Geographic data**: Files in `GEO/` subdirectory

#### Storage Location
Each simulation run creates an isolated output directory:
- **Path structure**: `MEDIA_ROOT/model_outputs/run_{id}/`
- **Typical size**: Several hundred MB to a few GB per simulation
- **Automatic cleanup**: No orphaned folders accumulate over time

#### Safety Features
- **Protection**: Cannot delete running or canceling simulations
- **Confirmation**: Deletion requires explicit user confirmation
- **Atomic operation**: Database and disk cleanup happen together

This automatic cleanup ensures efficient disk space usage, especially important for the Electron standalone app where storage is limited by the user's local disk.

### Visualization
- **Geographic Maps**: Spatial distribution of results
- **Time Series Charts**: Temporal evolution of key metrics
- **Technology Mix**: Breakdown of steel production methods
- **Cost Curves**: Economic analysis visualizations

## User Workflows

### Running a Simulation

1. **Access the Interface**
   - Django: Navigate to `http://server:port/admin/`
   - Electron: Launch the desktop application

2. **Prepare Data**
   - Upload Master Excel file or select existing
   - Validate data integrity
   - Configure simulation parameters

3. **Execute Simulation**
   - Click "Run Simulation"
   - Monitor progress in real-time
   - View worker status for parallel execution

4. **Analyze Results**
   - View generated maps and charts
   - Export data to CSV/Excel
   - Compare with previous runs

### Managing Workers

1. **Open Worker Panel**
   - Click "Worker Status" in navigation
   - Expand to see detailed information

2. **Scale Workers**
   - Click "Add Worker" to increase parallelism
   - Use "Drain Worker" for graceful shutdown
   - Monitor resource usage

3. **Troubleshoot Issues**
   - Check individual worker status
   - View worker logs for errors
   - Clean up failed workers

## System Requirements

### Django Web Application
- **Server**: Python 3.11+, 8GB RAM minimum
- **Client**: Modern web browser (Chrome, Firefox, Safari, Edge)
- **Network**: Stable connection for remote access

### Electron Standalone
- **Windows**: Windows 10/11, 8GB RAM, 2GB disk space
- **macOS**: macOS 10.15+, 8GB RAM, 2GB disk space
- **Linux**: Ubuntu 20.04+/equivalent, 8GB RAM, 2GB disk space

## Security Considerations

### Django Deployment
- Use HTTPS in production
- Configure CSRF protection
- Set secure session cookies
- Implement user authentication
- Regular security updates

### Electron Application
- Code signing for distribution
- Sandboxed renderer process
- Context isolation enabled
- Secure IPC communication
- Local-only Django server

## Troubleshooting

### Common Django Issues
- **Static files not loading**: Run `collectstatic` command
- **Database locked**: Check for concurrent access
- **Workers not starting**: Verify database migrations

### Common Electron Issues
- **App won't start**: Check port availability
- **Blank window**: Django server startup delay
- **High memory usage**: Limit concurrent workers

## Development Setup

### Django Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run development server
python manage.py runserver
```

### Electron Development
```bash
# Install dependencies
npm install

# Run in development mode
npm run dev

# Open developer tools
Ctrl+Shift+I (Windows/Linux)
Cmd+Option+I (macOS)
```

## Related Documentation

- [Parallel Worker Management](parallel_workers.md) - Detailed worker system documentation
- [User Stories](user_stories.md) - Use cases and requirements
- [Design Approaches](design_approaches.md) - UI/UX design decisions
- [Custom Repositories](custom_repositories.md) - Data management system
- [Architecture](../Architecture.md) - Overall system architecture

## Support & Resources

- **Issue Tracker**: Report bugs and request features
- **Documentation**: Comprehensive guides and API references
- **Community Forum**: Get help and share experiences
- **Development Guide**: Contributing to the project

:::
