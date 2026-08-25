"""
Download capacity factors, static masks, and the ESA-CCI land-cover map from CDS.

Requires a CDS account (~/.cdsapirc) with the dataset licences accepted, and
the cdsapi package (uv sync --extra cds); see docs/cds-data-pipeline.md.

Capacity-factor requests are split one per technology per year (hourly global
0.25 deg data is large, and smaller requests queue, fail, and resume
independently). The static masks are time-invariant and tiny, so they are
fetched once in a single bundled request. CDS delivers multi-file results as a
zip, which is extracted next to the download unless extract=False.
"""

import logging
import zipfile
from pathlib import Path

from boa.cds.spec import (
    ALL_MONTHS,
    CDS_CF_VARIABLES,
    CDS_DATASET,
    CDS_RESOLUTION,
    CDS_TECH_SPEC,
    CDS_TEMPORAL_RESOLUTION,
    CDS_VERSION,
    LULC_DATASET,
    LULC_VERSION,
    LULC_YEAR,
    MASK_VARIABLES,
    cf_zip_name,
    lulc_nc_name,
    lulc_zip_name,
    masks_zip_name,
)

log = logging.getLogger(__name__)

CDSAPI_INSTALL_HINT = "cdsapi is not installed — run: uv sync --extra cds"


def cdsapi_available() -> bool:
    try:
        import cdsapi  # noqa: F401  # type: ignore[import-untyped]
    except ImportError:
        return False
    return True


def base_request(
    tech_spec: str = CDS_TECH_SPEC,
    resolution: str = CDS_RESOLUTION,
    temporal_resolution: str = CDS_TEMPORAL_RESOLUTION,
    version: str = CDS_VERSION,
) -> dict:
    return {
        "spatial_coverage": "global",
        "technological_specification": [tech_spec],
        "spatial_resolution": [resolution],
        "temporal_resolution": [temporal_resolution],
        "version": version,
        "file_format": "netcdf_4",
    }


def cf_request(variable: str, year: str, months: list[str], **base_kwargs) -> dict:
    return base_request(**base_kwargs) | {
        "variable": [variable],
        "year": [year],
        "month": months,
    }


def mask_request(year: str, month: str, **base_kwargs) -> dict:
    # Masks are time-invariant but the CDS form still wants a time selection.
    return base_request(**base_kwargs) | {
        "variable": MASK_VARIABLES,
        "year": [year],
        "month": [month],
    }


def lulc_request(year: int = LULC_YEAR, version: str = LULC_VERSION) -> dict:
    return {"variable": "all", "year": [str(year)], "version": [version]}


def download(
    dataset: str, request: dict, target: Path, dry_run: bool, extract: bool, extract_to: Path | None = None
) -> None:
    """Retrieve one CDS request to `target`, optionally extracting the zip.

    Zips are extracted into `extract_to` when given, else a sibling directory
    named after the zip stem. Skipped when the zip — or, for sibling
    extraction, the extracted directory — already exists, so zips can be
    deleted after extraction without triggering a re-download.
    """
    if dry_run:
        log.info(f"[dry-run] would retrieve {dataset} -> {target}")
        for key, value in request.items():
            log.info(f"  {key}: {value}")
        return
    if target.exists():
        log.info(f"{target} exists — skipping (delete it to re-download)")
        return
    sibling = target.with_suffix("")
    if extract and extract_to is None and sibling.is_dir() and any(sibling.iterdir()):
        log.info(f"{sibling}/ exists — skipping (delete it to re-download)")
        return
    import cdsapi  # type: ignore[import-untyped]

    client = cdsapi.Client()
    log.info(f"Retrieving {dataset} -> {target}")
    client.retrieve(dataset, request).download(str(target))
    if extract and zipfile.is_zipfile(target):
        out_dir = extract_to if extract_to is not None else target.with_suffix("")
        log.info(f"Extracting {target.name} -> {out_dir}/")
        with zipfile.ZipFile(target) as zf:
            zf.extractall(out_dir)


def download_capacity_factors(
    out_dir: Path,
    techs: list[str],
    years: list[str],
    months: list[str] | None = None,
    tech_spec: str = CDS_TECH_SPEC,
    resolution: str = CDS_RESOLUTION,
    temporal_resolution: str = CDS_TEMPORAL_RESOLUTION,
    version: str = CDS_VERSION,
    masks: bool = False,
    extract: bool = True,
    dry_run: bool = False,
) -> None:
    """One request per (technology, year); optionally the bundled static masks."""
    months = months or ALL_MONTHS
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    base_kwargs = dict(
        tech_spec=tech_spec, resolution=resolution, temporal_resolution=temporal_resolution, version=version
    )
    # Distinguish partial-month downloads so they don't collide with full-year files.
    month_tag = "" if months == ALL_MONTHS else "_m" + "-".join(months)
    for tech in techs:
        variable = CDS_CF_VARIABLES[tech]
        for year in years:
            target = out_dir / cf_zip_name(tech, year, month_tag, tech_spec, resolution)
            download(CDS_DATASET, cf_request(variable, year, months, **base_kwargs), target, dry_run, extract)

    if masks:
        target = out_dir / masks_zip_name(tech_spec, resolution)
        download(CDS_DATASET, mask_request(years[0], months[0], **base_kwargs), target, dry_run, extract)


def download_lulc(out_dir: Path, year: int = LULC_YEAR, version: str = LULC_VERSION, dry_run: bool = False) -> Path:
    """Fetch the ESA-CCI land-cover map (~2.35 GB zip) and extract the NetCDF.

    Returns the expected NetCDF path; raises if extraction did not produce it.
    """
    nc_path = out_dir / lulc_nc_name(year, version)
    if nc_path.exists():
        log.info(f"{nc_path} exists — skipping (delete it to re-download)")
        return nc_path
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / lulc_zip_name(year, version)
    # Extract into out_dir itself: the zip holds the single top-level NetCDF.
    download(LULC_DATASET, lulc_request(year, version), target, dry_run, extract=True, extract_to=out_dir)
    if not dry_run and not nc_path.exists():
        raise FileNotFoundError(f"{target.name} extracted, but expected {nc_path.name} was not in it")
    return nc_path
