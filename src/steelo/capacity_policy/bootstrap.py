"""Run-level activation for the China capacity policy.

:func:`configure_capacity_policy` owns the binding lifecycle: it always
unbinds first, refuses an enabled run without complete fixtures, and binds a
fresh evaluator, pool and recorder seeded from the opening credits.
See docs/domain_simulation_logic/capacity_replacement_policy_reference.md#binding-lifecycle.
"""

from __future__ import annotations

import logging
from dataclasses import fields
from typing import TYPE_CHECKING

from steelo.domain.constants import MT_TO_T

from .config import CapacityPolicyConfig
from .handlers import bind_capacity_policy, unbind_capacity_policy
from .inputs import OpeningCreditRow
from .pool import CapacityPool, SeedEntry
from .recorder import CapacityPolicyRecorder
from .tree import TreeEvaluator
from .validation import ValidationIssue, validate_opening_credits, validate_provinces, validate_technologies

if TYPE_CHECKING:
    from steelo.adapters.repositories.json_repository import JsonRepository

logger = logging.getLogger(__name__)

FIXTURE_NAMES = (
    "capacity_pool_provinces",
    "capacity_pool_technologies",
    "capacity_pool_opening_credits",
)


def configure_capacity_policy(
    config: CapacityPolicyConfig, repository_json: "JsonRepository | None", *, start_year: int
) -> None:
    """Bind the capacity policy for this run, or make sure it is unbound.

    Always unbinds first: binding is module-level state and simulations run
    successively in one process.

    Args:
        config: The run's ``capacity_policy`` scenario levers.
        repository_json: The run's fixture repositories, or None when a
            repository was injected directly (test runs without fixtures).
        start_year: First simulation year. Seeded vintages after it are
            clamped down to it with a warning, and seeded credits already past
            their shelf life at the start are purged.

    Raises:
        ValueError: With ``enabled=True``, when the run has no fixture
            repositories, when any capacity pool fixture is missing, when a
            classification fixture is empty, or when the promoted cross-row
            validation finds any issue, warnings included.
    """
    unbind_capacity_policy()
    if not config.enabled:
        return

    logger.info(
        "[CAPACITY POOL] config %s",
        " ".join(f"{field.name}={getattr(config, field.name)!r}" for field in fields(config)),
    )

    if repository_json is None:
        raise ValueError(
            "capacity_policy.enabled=True but the run has no fixture repositories "
            "(repository injected directly); the policy cannot run without the "
            "capacity pool fixtures"
        )

    repos = [getattr(repository_json, name) for name in FIXTURE_NAMES]
    problems = []
    for name, repo in zip(FIXTURE_NAMES, repos):
        if repo.path is None or not repo.path.exists():
            problems.append(f"{name}.json is missing")
        elif name != "capacity_pool_opening_credits" and not repo.list():
            problems.append(f"{name}.json is empty")
    if problems:
        raise ValueError(
            "capacity_policy.enabled=True but " + "; ".join(problems) + ". "
            "A run claiming policy-on with no data must refuse — prepare the "
            "'Capacity pool - …' sheets in the master input, or disable the policy."
        )

    province_rows, technology_rows, credit_rows = (repo.list() for repo in repos)
    if not credit_rows:
        logger.info(
            "[CAPACITY POOL] opening credits fixture is empty: zero-pool start, "
            "so every INCREASE is blocked until a Chinese retirement banks the first credit"
        )
    _validate_promoted(
        [
            *validate_provinces(province_rows, chinese_geo_keys=_chinese_geo_keys()),
            *validate_technologies(
                technology_rows,
                technology_roster={capex.technology_name for capex in repository_json.capex.list()},
                reductant_vocabulary={
                    feedstock.reductant
                    for feedstock in repository_json.primary_feedstocks.list()
                    if feedstock.reductant
                },
            ),
            *validate_opening_credits(
                credit_rows,
                technology_roster={capex.technology_name for capex in repository_json.capex.list()},
                chinese_geo_keys=_chinese_geo_keys(),
            ),
        ]
    )

    _warn_when_geo_unit_data_unavailable()
    _warn_on_unknown_seed_owners(credit_rows, repository_json)

    recorder = CapacityPolicyRecorder()
    evaluator = TreeEvaluator(province_rows, technology_rows, config, recorder=recorder)
    pool = CapacityPool(
        inter_company_swap_cutoff_year=config.inter_company_swap_cutoff_year,
        banked_credit_rule=config.banked_credit_rule,
        credit_validity_years=config.credit_validity_years,
    )
    future_vintages = sorted({row.vintage_year for row in credit_rows if row.vintage_year > start_year})
    if future_vintages:
        # A vintage after the start year would sit ahead of older runtime deposits
        # and quietly bend FIFO; the opening pool is state at t=0, so clamp it there
        logger.warning(
            "[CAPACITY POOL] %d opening credit row(s) carry a vintage_year after the start year %d "
            "(%s): clamping them to the start year",
            sum(1 for row in credit_rows if row.vintage_year > start_year),
            start_year,
            ", ".join(str(v) for v in future_vintages),
        )
    entries = [
        SeedEntry(
            amount_mt=row.capacity_mt * MT_TO_T,
            vintage_year=min(row.vintage_year, start_year),
            geo_key=row.geo_key,
            owner_id=row.plant_group_id,
            product=row.product,
        )
        for row in credit_rows
    ]
    sheet_total_mt = sum(row.capacity_mt for row in credit_rows)
    logger.info(
        "[CAPACITY POOL] seeding opening credits sheet_mt=%.3f -> tonnes=%.1f (x %g) entries=%d",
        sheet_total_mt,
        sheet_total_mt * MT_TO_T,
        MT_TO_T,
        len(entries),
    )
    pool.seed_from(entries, evaluator.key_regions)
    for entry in sorted(entries, key=lambda e: e.vintage_year):
        recorder.record_ledger(
            year=entry.vintage_year,
            operation="seed",
            amount_t=entry.amount_mt,
            region_tag=evaluator.key_regions.get(entry.geo_key),
            owner_id=entry.owner_id,
            product=entry.product,
            vintage_year=entry.vintage_year,
            geo_key=entry.geo_key,
        )
    recorder.record_expired(start_year, pool.purge_expired(start_year))
    recorder.record_expired(start_year, pool.purge_unowned(start_year), operation="expired_unowned")
    bind_capacity_policy(evaluator, pool, recorder)
    logger.info("[CAPACITY POOL] policy bound: deposits and all three gates are live for this run")


def _warn_when_geo_unit_data_unavailable() -> None:
    """Warn when province derivation would silently degrade to country level.

    Without the admin-1 layer and geo hierarchy every greenfield site resolves
    to the bare ``CHN`` key and the greenfield side runs region-blind. Not a
    refusal: existing plants carry their own fixture-tagged geo units.
    """
    from steelo.adapters.geospatial.geo_unit_lookup import geo_unit_reference_data_available

    available, detail = geo_unit_reference_data_available()
    if not available:
        logger.warning(
            "[CAPACITY POOL] geo_unit reference data unavailable (%s): every greenfield site "
            "will resolve at country level and be treated as non-key — the policy's regional "
            "rules will not bind on the greenfield channel this run",
            detail,
        )


def _warn_on_unknown_seed_owners(credit_rows: list[OpeningCreditRow], repository_json: "JsonRepository") -> None:
    """Warn when a seeded owner id matches no plant group in the plants fixture.

    From the cutoff such credits can never be spent through the expansion
    path. An absent or empty plants fixture (injected test repositories)
    skips the check.
    """
    plants_repo = getattr(repository_json, "plants", None)
    if plants_repo is None:
        return
    known_owners = {plant.parent_gem_id for plant in plants_repo.all.values()}
    if not known_owners:
        return
    unknown = sorted(
        {
            row.plant_group_id
            for row in credit_rows
            if row.plant_group_id is not None and row.plant_group_id not in known_owners
        }
    )
    if unknown:
        logger.warning(
            "[CAPACITY POOL] %d seeded owner id(s) match no plant group in the plants fixture: %s "
            "— from the cutoff year no company can spend these credits through the expansion path",
            len(unknown),
            ", ".join(unknown),
        )


def _validate_promoted(issues: list[ValidationIssue]) -> None:
    """Raise on any validation issue, warnings included: an enabled run demands a complete authoring."""
    if not issues:
        return
    detail = "\n".join(f"[{issue.severity}] {issue.sheet}: {issue.message}" for issue in issues)
    raise ValueError(
        "capacity_policy.enabled=True but the capacity pool fixtures fail validation "
        "(warnings promoted to errors — an unauthored flag blocks a policy run):\n" + detail
    )


def _chinese_geo_keys() -> set[str]:
    """The Chinese first-order units, from the same construction data prep validates with."""
    from steelo.data.recreation_functions import chinese_capacity_pool_geo_keys

    return chinese_capacity_pool_geo_keys()
