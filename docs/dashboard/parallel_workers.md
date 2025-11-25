# Parallel Worker Management System

## Overview

The Steel Model Django application includes a powerful parallel worker management system that enables concurrent execution of multiple simulations. This dramatically improves throughput by utilizing available system resources instead of processing simulations sequentially.

## Key Features

- **Concurrent Execution**: Run up to 4 simulations simultaneously (configurable)
- **Resource Management**: Automatic memory and CPU-aware worker spawning
- **Real-time Monitoring**: Live status updates via HTMX-powered UI
- **Graceful Degradation**: System automatically adjusts to resource constraints
- **Cross-platform Support**: Works on Windows, macOS, and Linux
- **Zero Configuration**: Works out-of-the-box with sensible defaults

## User Interface

### Accessing Worker Management

The worker management interface is integrated into the main Django admin panel:

1. Navigate to the Django admin interface (`/admin/`)
2. Look for the **Worker Status** section in the navigation
3. Click to expand the collapsible worker management panel

### Worker Status Panel

The worker status panel provides real-time information about:

#### System Overview
- **Active Workers**: Current number of running worker processes
- **Maximum Capacity**: Maximum workers based on system resources
- **Memory Usage**: Visual progress bar showing system memory utilization
- **Job Queue**: Number of pending and running tasks

#### Individual Worker Cards
Each active worker displays:
- **State Badge**: Color-coded status indicator
  - 🟢 Green (RUNNING): Processing tasks normally
  - 🔵 Blue (STARTING): Initializing worker process
  - 🟡 Yellow (DRAINING): Completing current task before shutdown
  - 🔴 Red (FAILED): Worker encountered an error
- **Process Information**: Worker ID and system PID
- **Resource Usage**: Real-time memory and CPU utilization
- **Heartbeat Status**: Time since last worker update
- **Status Indicators**:
  - ⚠️ Dead process warning
  - 🕐 Stalled worker indicator (no update for 3+ minutes)
  - ⏳ Draining in progress

### Worker Controls

#### Adding Workers
- Click **Add Worker** to spawn a new worker process
- **Memory Warning**: If system memory is insufficient, a confirmation dialog will appear:
  - Warns about potential system instability
  - Lists risks (crashes, slowdowns, data loss)
  - Allows proceeding at your own risk
  - User must explicitly confirm to proceed
- Button remains clickable even when system reports no capacity
- This ensures the app remains usable on low-memory systems

#### Removing Workers
- **Drain Worker**: Gracefully shutdown after current task completes
- **Per-worker Controls**: Individual drain/abort buttons for each worker
- **Abort** (🔴): Force terminate with confirmation (loses current work)

#### Automatic Refresh
- Status updates every 5 seconds when panel is expanded
- Updates pause when panel is collapsed to save resources
- Manual refresh available via button

## How It Works

### Worker Lifecycle

1. **Spawning**: User clicks "Add Worker" → System checks resources → Creates worker record → Launches process
2. **Handshake**: Worker validates security token → Transitions to RUNNING state
3. **Processing**: Worker claims tasks atomically → Executes simulations → Updates heartbeat
4. **Shutdown**: Graceful drain or force abort → Completes/terminates work → Marks as DEAD

### Resource Management

The system automatically determines safe worker counts based on:

- **Memory**: Each simulation requires ~8GB peak memory
- **CPU Cores**: Limited to physical cores (not hyperthreads)
- **Platform Constraints**: Special handling for macOS memory pressure
- **Hard Cap**: Maximum 4 workers by default (configurable)

### Task Queue

Workers process tasks from a shared queue using:
- **Atomic claiming**: Prevents double-processing of tasks
- **Priority ordering**: Higher priority tasks processed first
- **FIFO within priority**: First-in-first-out for same priority
- **Automatic retry**: Failed tasks can be retried

## Configuration

### Environment Variables

Workers can be configured via environment variables:

```bash
# Maximum number of workers (default: 4)
WORKER_MAX_COUNT=4

# Task execution timeout in seconds (default: 172800 = 48 hours)
# Simulations commonly take 14+ hours on Windows
WORKER_TASK_TIMEOUT=172800

# Worker startup timeout (default: 30)
WORKER_STARTUP_TIMEOUT=30

# Heartbeat interval in seconds (default: 30)
WORKER_HEARTBEAT_INTERVAL=30

# Log directory path (default: system temp)
WORKER_LOG_DIR=/var/log/steel-iq/workers

# Queue name to process (default: "default")
WORKER_QUEUE_NAME=default
```

### Deployment Scenarios

#### Small Systems (16-32GB RAM)
```bash
WORKER_MAX_COUNT=2
WORKER_TASK_TIMEOUT=86400  # 24 hours for complex simulations
```

#### Large Systems (64GB+ RAM)
```bash
WORKER_MAX_COUNT=8
WORKER_TASK_TIMEOUT=172800  # 48 hours for very complex simulations
```

#### High-Reliability Environments
```bash
WORKER_STARTUP_TIMEOUT=60     # More time for slow storage
WORKER_CLEANUP_AGE=300        # Keep failed workers longer for debugging
```

## Monitoring & Troubleshooting

### Worker States

| State | Description | Action Required |
|-------|-------------|-----------------|
| STARTING | Worker launching, performing handshake | Wait up to 30 seconds |
| RUNNING | Processing tasks normally | None |
| DRAINING | Completing current task before exit | Wait for completion |
| FAILED | Worker crashed or failed to start | Check logs, may need cleanup |
| DEAD | Worker terminated cleanly | Can be cleaned up |

### Common Issues

#### No Workers Can Be Spawned (Memory Warning)
- **Cause**: System reports insufficient memory for safe worker operation
- **Indicator**: "Insufficient Memory Warning" dialog appears when clicking "Add Worker"
- **Solutions**:
  1. **Recommended**: Close other applications to free up memory
  2. **Alternative**: Click "Start Anyway" to force worker creation (at your own risk)
  3. **Long-term**: Reduce `WORKER_MAX_COUNT` or upgrade system RAM
- **Risk of Forcing**: Simulation may crash, system may become slow/unresponsive, other apps may be affected

#### Worker Shows as Stalled
- **Indicator**: 🕐 Clock icon appears
- **Cause**: No heartbeat for 3+ minutes
- **Solution**: Worker may be processing intensive task, wait or abort if stuck

#### Worker Failed to Start
- **Cause**: Database connection issues or configuration errors
- **Solution**: Check worker logs in `WORKER_LOG_DIR`

### Log Files

Each worker writes to an individual log file:
- Location: `WORKER_LOG_DIR` or system temp directory
- Format: `worker_[worker_id].log`
- Rotation: Automatic at 10MB, keeps 3 backups
- Contents: Startup info, task processing, errors

### Performance Metrics

- **Startup Time**: 1-2 seconds per worker
- **Memory Overhead**: ~50MB per idle worker
- **Task Claiming**: <10ms latency
- **Heartbeat Updates**: Every 30 seconds
- **UI Refresh**: 5-second intervals

## API Endpoints

For programmatic access, the following endpoints are available:

### Status Endpoints (GET)
- `/api/workers/status/` - JSON status of all workers
- `/htmx/workers/status/` - HTML fragment for UI updates

### Control Endpoints (POST)
- `/api/workers/add/` - Spawn a new worker
  - Optional parameter: `{"force": true}` to bypass memory checks
  - Query parameter (HTMX): `?force=true`
  - JSON body (API): `{"force": true}`
- `/api/workers/drain/` - Gracefully drain a worker
- `/api/workers/<id>/abort/` - Force terminate specific worker
- `/workers/tick/` - Trigger state transition checks

### Response Format

```json
{
  "workers": [
    {
      "worker_id": "abc123",
      "pid": 12345,
      "state": "RUNNING",
      "heartbeat": "2024-01-15T10:30:00Z",
      "memory": 524288000,
      "cpu": 15.2
    }
  ],
  "limits": {
    "admissible": 4,
    "active": 2,
    "can_add": true
  },
  "memory": {
    "total": 17179869184,
    "available": 8589934592,
    "percent": 50.0
  }
}
```

## Security Considerations

### Process Isolation
- Each worker runs in a separate process with its own memory space
- Workers cannot interfere with each other's tasks
- Process groups prevent orphaned child processes

### Authentication & Authorization
- Worker management requires Django admin permissions
- Launch tokens prevent unauthorized process registration
- PID reuse protection prevents accidental process termination

### Resource Limits
- Hard caps prevent system overload
- Conservative memory allocation prevents OOM crashes
- Automatic adjustment to memory pressure (macOS)

## Platform-Specific Notes

### Windows
- Process groups used for clean termination
- Extra memory guard (1GB) for system overhead
- Task timeouts not enforced (no SIGALRM support)

### macOS
- Memory pressure detection via `memory_pressure` command
- Reduces capacity by 50% under memory pressure
- Full timeout support via SIGALRM

### Linux
- Standard POSIX process management
- Session-based process isolation
- Full feature support including timeouts

## Best Practices

### Optimal Worker Count
- **Development**: 1-2 workers to leave resources for other tasks
- **Production**: Match physical CPU cores, respecting memory limits
- **Batch Processing**: Maximum workers for overnight runs

### Task Design
- Keep individual tasks under 1 hour when possible
- Use task priorities for important simulations
- Monitor failed tasks and investigate causes

### System Maintenance
- Regularly clean up old dead/failed workers
- Monitor log file sizes and rotate as needed
- Check worker performance metrics for degradation

## Integration with Electron App

When running in the Electron standalone application:

1. **Worker Management**: Handled by embedded Django server via HTTP API
2. **Startup Behavior**:
   - No workers are auto-created on app launch
   - UI checks for workers on first page load
   - If memory is insufficient, shows confirmation dialog
   - User can choose to start worker or run without workers
3. **Process Lifecycle**: Workers tied to Electron app lifetime
4. **Logs**: Stored in app data directory
5. **Configuration**: Via app preferences or environment variables

## Frequently Asked Questions

**Q: How many workers should I run?**
A: Start with 2 workers and increase based on system performance. The system will warn you if resources are insufficient.

**Q: What happens if I force-start a worker with low memory?**
A: The worker will start, but you risk system instability, crashes, and data loss. Only do this if you understand the risks and have no alternative.

**Q: Can I run workers on multiple machines?**
A: Currently workers run locally only. Distributed processing is a planned future enhancement.

**Q: What happens if a worker crashes?**
A: The system automatically detects dead workers and marks them as FAILED. Tasks can be retried by other workers.

**Q: How do I debug a failed simulation?**
A: Check the worker log file for the failed worker. The last 1000 characters are also captured in the database.

**Q: Can I change worker configuration without restarting?**
A: Environment variables are read at worker spawn time. Running workers continue with their original configuration.

## Technical Architecture

For developers and system administrators, the parallel simulations implementation includes:

- Database schema and models
- Process management implementation
- SQLite compatibility measures
- Testing infrastructure
- Performance characteristics
- Future enhancement roadmap