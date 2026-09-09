"""Run-level activation for the China capacity policy.

:func:`configure_capacity_policy` is called once per ``bootstrap_simulation``
and owns the binding lifecycle: it always unbinds first, so no evaluator or
pool state can survive from a previous simulation in the same process
(steeloweb, test suites), and a disabled run is guaranteed dormant even after
an enabled one. With ``enabled=True`` it refuses to run without the
classification fixtures — never silent dormancy — while an empty opening-credits
fixture is a legitimate zero-pool start, re-runs the cross-row sheet validation with
warnings promoted to errors, builds a fresh evaluator, pool and observability
recorder from the fixtures and config, seeds the pool (converting the sheet's
Mt into the model tonnes every runtime capacity flows in) and opens the ledger
with those seed rows, purges any seeded vintage already past its shelf life at
the start year, and makes the single ``bind_capacity_policy`` call that
activates the deposit handlers, all three decision gates and the CSV emission
together.
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

    Always unbinds first: binding is module-level state and simulations can run
    successively in one process, so a disabled bootstrap must actively clear
    any binding a previous enabled run left behind. An enabled bootstrap then
    builds a fresh evaluator and pool — pool state never survives into the
    next simulation.

    Args:
        config: The run's ``capacity_policy`` scenario levers.
        repository_json: The run's fixture repositories, or None when a
            repository was injected directly (test runs without fixtures).
        start_year: First simulation year. Seeded vintages after it are
            clamped down to it with a warning (the opening pool is state at
            t=0, and a future vintage would quietly bend FIFO), and seeded
            credits already past their shelf life at t=0 are purged — the
            run must not open with dead credit in it.

    Raises:
        ValueError: With ``enabled=True``, when any capacity pool fixture is
            missing, when either classification fixture is empty (named in the
            message), or when the promoted cross-row validation finds any issue
            — unauthored classification flags only warn at data preparation,
            but block a policy run. An empty opening-credits fixture does not
            raise: it is a zero-pool start.
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
            "(%s): scaling them down to the start year",
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
    """Warn loudly when province derivation would silently degrade to country level.

    ``derive_geo_unit_for_site`` returns None for every site when the admin-1
    layer or geo_hierarchy is absent, and ``compose_geo_key("CHN", None)`` is
    the bare country key the evaluator treats as non-key — so an enabled run
    without the reference data would apply the whole greenfield side of the
    policy region-blind (untagged deposits, unrestricted withdrawals, no
    exemption) without a word. Not a refusal: existing plants carry their own
    fixture-tagged geo_units and are unaffected, so the run may still be
    meaningful — but never silently.
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

    Post-cutoff, an ordinary withdrawal may only spend the withdrawer's own
    credits — credits owned by an id no company carries can then never be spent
    through the expansion path, which is indistinguishable from the policy
    binding unless it is said out loud. (The greenfield single-owner path can
    still select them; attribution falls back to ``indi_<iso3>``.) The owner
    universe is the plants fixture's ``parent_gem_id`` values, read from the
    raw rows without domain conversion; an absent or empty plants fixture
    (injected test repositories) skips the check.
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
    """Raise on any validation issue, warnings included.

    Warnings are content gaps that data preparation tolerates so fixtures can
    still be built from a workbook with ``TO AUTHOR`` cells; a run with the
    policy enabled demands a complete authoring, so they are promoted here.
    """
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
