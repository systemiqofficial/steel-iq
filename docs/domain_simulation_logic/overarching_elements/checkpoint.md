# Checkpoint System

## Overview

The checkpoint system provides the ability to save and restore simulation state at regular intervals. This feature is critical for:

- **Crash recovery**: Resume long-running simulations after unexpected failures
- **Debugging**: Start debugging from a specific year without re-running the entire simulation
- **Performance**: Avoid re-computing early years when testing changes that only affect later years
- **Analysis**: Save state at key points for detailed analysis

## Key Features

### Automatic Checkpointing

By default, checkpoints are automatically saved every 5 years during simulation. This happens in the `finalise_iteration` handler after each year completes:

```python
# Automatic checkpoint every 5 years
if env.year % 5 == 0:
    checkpoint_system.save_checkpoint(env.year, env, uow)
```

### Manual Checkpointing

You can also trigger checkpoints manually by raising a `SaveCheckpoint` event:

```python
from steelo.service_layer.checkpoint import SaveCheckpoint
from steelo.domain import Year

# Save checkpoint for current year
bus.handle(SaveCheckpoint(year=Year(2030)))
```

### What Gets Saved

Each checkpoint includes:

1. **Environment State**
   - Current year
   - Cost curves for products
   - Average bill of materials (BOMs)
   - Dynamic feedstocks data
   - Capacity additions and limits
   - Cost of capital by region
   - Regional capex data
   - Carbon costs

2. **Repository State**
   - All plants with their furnace groups
   - Demand centers
   - Suppliers
   - Trade tariffs
   - Plant groups

3. **Metadata**
   - Checkpoint year
   - Timestamp
   - Simulation ID
   - Checkpoint version

## File Structure

Checkpoints are saved in the `checkpoints/` directory (configurable) with the following naming convention:

```
checkpoints/
├── checkpoint_year_2025_sim_20240115_143022.pkl
├── checkpoint_year_2025_sim_20240115_143022_metadata.json
├── checkpoint_year_2030_sim_20240115_143022.pkl
├── checkpoint_year_2030_sim_20240115_143022_metadata.json
└── ...
```

- `.pkl` files contain the serialized simulation state
- `_metadata.json` files contain human-readable checkpoint information

## Usage Examples

### Basic Checkpoint Operations

```python
from steelo.service_layer.checkpoint import SimulationCheckpoint

# Create checkpoint system
checkpoint_system = SimulationCheckpoint("checkpoints")

# Save checkpoint
checkpoint_system.save_checkpoint(Year(2025), env, uow)

# Load checkpoint
checkpoint_data = checkpoint_system.load_checkpoint(Year(2025))
if checkpoint_data:
    # Restore state (implementation depends on your needs)
    restored_year = checkpoint_data['year']
    restored_env_state = checkpoint_data['environment_state']
    restored_repo_state = checkpoint_data['repository_state']

# List available checkpoints
checkpoints = checkpoint_system.list_checkpoints()
for checkpoint in checkpoints:
    print(f"Year: {checkpoint.year}, Saved at: {checkpoint.timestamp}")

# Clean old checkpoints (keep last 10)
checkpoint_system.clean_old_checkpoints(keep_last_n=10)
```

### Resuming from Checkpoint

To resume a simulation from a checkpoint, you would typically:

1. Load the checkpoint for the desired year
2. Restore the environment and repository state
3. Continue simulation from the next year

```python
# Example: Resume from year 2030
checkpoint_data = checkpoint_system.load_checkpoint(Year(2030))
if checkpoint_data:
    # Restore environment state
    env.year = checkpoint_data['environment_state']['year']
    env.cost_curve = checkpoint_data['environment_state']['cost_curve']
    # ... restore other environment attributes
    
    # Restore repository state
    # This would require deserialization logic for domain objects
    
    # Continue simulation from year 2031
    start_year = env.year + 1
```

## Configuration

### Checkpoint Frequency

The default checkpoint frequency (every 5 years) can be modified in `handlers.py`:

```python
# Save checkpoint every N years
if env.year % N == 0:
    checkpoint_system.save_checkpoint(env.year, env, uow)
```

### Checkpoint Directory

The checkpoint directory defaults to `"checkpoints"` but can be configured when bootstrapping the application:

```python
from steelo.bootstrap import bootstrap

# Configure with custom checkpoint directory
bus = bootstrap(checkpoint_dir="/path/to/custom/checkpoint/dir")

# Use default checkpoint directory ("checkpoints/")
bus = bootstrap()
```

The checkpoint system is now injected as a dependency into handlers that need it, following the dependency injection pattern used throughout the application.

### Checkpoint Retention

Old checkpoints can be automatically cleaned up to save disk space:

```python
# Keep only the last 20 checkpoints
checkpoint_system.clean_old_checkpoints(keep_last_n=20)
```

## Error Handling

The checkpoint system includes robust error handling:

- **Save failures**: Logged as warnings but don't interrupt simulation
- **Load failures**: Return `None` if checkpoint can't be loaded
- **Missing checkpoints**: Handled gracefully with appropriate warnings

## Performance Considerations

- **Disk space**: Each checkpoint can be several MB to GB depending on simulation size
- **Save time**: Checkpoint saves are typically fast (<1 second) but may increase with simulation complexity
- **Frequency trade-off**: More frequent checkpoints provide better recovery granularity but use more disk space

## Future Enhancements

Potential improvements to the checkpoint system:

1. **Incremental checkpoints**: Save only changes since last checkpoint
2. **Compression**: Compress checkpoint files to save disk space
3. **Cloud storage**: Support for saving checkpoints to S3/cloud storage
4. **Parallel checkpointing**: Save checkpoints in background thread
5. **Checkpoint validation**: Verify checkpoint integrity on save/load
6. **Selective state**: Configure which parts of state to include in checkpoints

## Technical Details

### Serialization

The checkpoint system uses Python's `pickle` module for serialization with the highest protocol version for best performance. Metadata is saved as JSON for human readability.

### Compatibility

Checkpoints include a version number to handle future changes to the checkpoint format. Currently version "1.0".

### Thread Safety

The current implementation is not thread-safe. If running multiple simulations in parallel, each should use a separate checkpoint directory or include a unique simulation ID.