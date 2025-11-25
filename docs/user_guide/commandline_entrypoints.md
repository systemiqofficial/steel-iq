# Data Preparation Commands

## steelo-data-prepare

This command prepares all data files needed for simulations, replacing the old `recreate_sample_data` functionality.

```shell
❯ steelo-data-prepare -h
usage: steelo-data-prepare [-h] [--output-dir OUTPUT_DIR]
                           [--cache-dir CACHE_DIR]
                           [--master-excel-file MASTER_EXCEL_FILE]
                           [--geo-version GEO_VERSION] [--skip-existing]
                           [--no-skip-existing] [--list-files] [--verbose]
                           [--force-refresh]

Prepare all data files for simulation.
```

## steelo-data-recreate

This command recreates JSON repositories from downloaded data packages.

```shell
❯ steelo-data-recreate -h
usage: steelo-data-recreate [-h] [--force-download] [--master-excel MASTER_EXCEL]
                            [--track-timing] [--list-packages]
                            [package_name] [output_dir]

Recreate JSON repositories from downloaded data packages (similar to recreate_sample_data).
```


Use the `download-binaries` and `download-latest` CLI helpers to fetch packaged Electron apps. Refer to the [CLI Commands](cli_commands.md) section for full usage examples and options.
