# Backward Compatibility with Legacy data/ and output/ Directories

## Overview

Starting with the caching implementation, Steel Model now uses a new directory structure under `$STEELO_HOME` (defaults to `~/.steelo`). To maintain backward compatibility with existing scripts and workflows that expect `data/` and `output/` directories in the project root, the system automatically creates symlinks.

## How It Works

### 1. Data Directory Symlinks

When you run data preparation (either through `run_simulation` or `steelo-data-prepare`), the system:

1. Prepares data in `$STEELO_HOME/preparation_cache/prep_<hash>/data/`
2. Creates a symlink at `$STEELO_HOME/data` pointing to the latest preparation
3. Creates a symlink at `<project_root>/data` pointing to `$STEELO_HOME/data`

### 2. Output Directory Symlinks  

When you run a simulation, the system:

1. Creates output in `$STEELO_HOME/output/sim_<timestamp>/`
2. Creates a symlink at `$STEELO_HOME/output/latest` pointing to the latest simulation
3. Creates a symlink at `$STEELO_HOME/output_latest` pointing to the latest simulation
4. Creates a symlink at `<project_root>/output` pointing to `$STEELO_HOME/output_latest`

### 3. Automatic Backup

If `data/` or `output/` already exist as real directories (not symlinks), they are automatically backed up:

- `data/` → `data_backup_<timestamp>/`
- `output/` → `output_backup_<timestamp>/`

The system keeps up to 5 backups and automatically removes older ones.

## Examples

### Running a Simulation

```bash
$ run_simulation --start-year 2025 --end-year 2030

[blue]Preparing data...[/blue]
Using cached preparation from: /Users/you/.steelo/preparation_cache/prep_a1b2c3d4/data
[green]Created data symlink:[/green] /Users/you/.steelo/data -> /var/folders/.../data
[blue]Running simulation in: /Users/you/.steelo/output/sim_20240726_143052[/blue]
[green]Created output symlink:[/green] /Users/you/.steelo/output_latest -> /Users/you/.steelo/output/sim_20240726_143052
[green]Created legacy symlinks in project root[/green]
[green]Simulation completed![/green]
```

After this, you can access:
- Latest data: `./data/fixtures/` (symlink)
- Latest output: `./output/plots/` (symlink)
- All simulations: `~/.steelo/output/`
- All cached data: `~/.steelo/preparation_cache/`

### Checking What the Symlinks Point To

```bash
$ ls -la data output
lrwxr-xr-x  1 user  staff  35 Jul 26 14:30 data -> /Users/you/.steelo/data
lrwxr-xr-x  1 user  staff  42 Jul 26 14:31 output -> /Users/you/.steelo/output_latest

$ ls -la ~/.steelo/
drwxr-xr-x  5 user  staff  160 Jul 26 14:30 .
drwxr-xr-x  3 user  staff   96 Jul 26 14:30 data -> preparation_cache/prep_a1b2c3d4/data
drwxr-xr-x  4 user  staff  128 Jul 26 14:31 output/
lrwxr-xr-x  1 user  staff   35 Jul 26 14:31 output_latest -> output/sim_20240726_143052
drwxr-xr-x  3 user  staff   96 Jul 26 14:30 preparation_cache/
```

## Benefits

1. **Backward Compatibility**: Existing scripts that reference `./data` or `./output` continue to work
2. **Caching**: Data preparation results are cached and reused
3. **History**: All simulation runs are preserved in timestamped directories
4. **Clean Project Root**: No more cluttered data/output directories in version control
5. **Automatic Cleanup**: Old backups are automatically removed

## Migration Guide

If you have existing `data/` or `output/` directories:

1. **Option 1**: Let the system handle it automatically
   - Run your simulation normally
   - Existing directories will be backed up to `*_backup_<timestamp>`
   - Symlinks will be created

2. **Option 2**: Manual migration
   ```bash
   # Move existing data
   mv data ~/.steelo/data_manual
   mv output ~/.steelo/output_manual
   
   # Run simulation to create symlinks
   run_simulation --start-year 2025 --end-year 2026
   ```

3. **Option 3**: Keep using old structure
   - Set `--no-cache` flag to disable caching
   - Manually specify paths in your scripts

## Environment Variables

- `STEELO_HOME`: Change the base directory (default: `~/.steelo`)
  ```bash
  export STEELO_HOME=/path/to/custom/steelo
  run_simulation
  ```

## Troubleshooting

### "Could not create symlink" Warning

This can happen if:
1. You don't have permissions to create symlinks
2. You're on a filesystem that doesn't support symlinks (rare)

The simulation will still complete successfully, but you'll need to access files directly from `$STEELO_HOME`.

### Finding Old Backups

```bash
ls -la *_backup_*
```

### Removing All Symlinks and Backups

```bash
# Remove symlinks
rm -f data output

# Remove backups (careful!)
rm -rf data_backup_* output_backup_*
```

## Technical Details

The symlink management is handled by `steelo.utils.symlink_manager` which provides:
- `create_symlink_with_backup()`: Creates symlinks with automatic backup
- `update_data_symlink()`: Updates the data symlink after preparation
- `update_output_symlink()`: Updates the output symlink after simulation
- `setup_legacy_symlinks()`: Creates project root symlinks for backward compatibility

Maximum of 5 backups are kept per directory to prevent disk space issues.