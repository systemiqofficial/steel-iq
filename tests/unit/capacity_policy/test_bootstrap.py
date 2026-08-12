"""Tests for the run-level policy activation: lifecycle, promotion, seeding.

The binding is module-level state shared by successive simulations in one
process, so the load-bearing claims are that a disabled bootstrap actively
unbinds stale state, an enabled one builds a fresh pool every time, and the
sheet-authored Mt convert into the model tonnes the runtime flows.
"""

import logging
from types import SimpleNamespace

import pytest

from steelo.adapters.repositories.json_repository import (
    CapacityPoolOpeningCreditJsonRepository,
    CapacityPoolProvinceJsonRepository,
    CapacityPoolTechnologyJsonRepository,
)
from steelo.capacity_policy import CapacityPolicyConfig, CapacityPolicyRecorder, CapacityPool, TreeEvaluator
from steelo.capacity_policy import handlers as cp_handlers
from steelo.capacity_policy.bootstrap import configure_capacity_policy
from steelo.capacity_policy.inputs import OpeningCreditRow, RegionRow, TechnologyRow
from steelo.data.recreation_functions import chinese_capacity_pool_geo_keys

KEY_PROVINCE = "CHN:CN-HE"
NON_KEY_PROVINCE = "CHN:CN-GD"


@pytest.fixture(autouse=True)
def unbind_after_test():
    """Module-level binding must never leak between tests."""
    yield
    cp_handlers.unbind_capacity_policy()


@pytest.fixture
def propagating_policy_logs():
    """Force policy log propagation so caplog sees records.

    Any earlier test that ran ``bootstrap_simulation`` has applied the YAML
    logging config, which stops steelo records propagating to the root logger
    caplog listens on.
    """
    loggers = [
        logging.getLogger(name)
        for name in ("steelo.capacity_policy.bootstrap", "steelo.capacity_policy.pool", "steelo.capacity_policy.tree")
    ]
    saved = [(policy_logger, policy_logger.propagate) for policy_logger in loggers]
    for policy_logger in loggers:
        policy_logger.propagate = True
    yield
    for policy_logger, propagate in saved:
        policy_logger.propagate = propagate


def province_rows() -> list[RegionRow]:
    """Enumerate every Chinese unit, with one key province, as validation demands."""
    rows = [RegionRow(geo_key=KEY_PROVINCE, region_name="Jing-Jin-Ji", type="key", from_year=None)]
    rows += [
        RegionRow(geo_key=geo_key, region_name=None, type=None, from_year=None)
        for geo_key in sorted(chinese_capacity_pool_geo_keys())
        if geo_key != KEY_PROVINCE
    ]
    return rows


def technology_rows(*, unauthored_coal_flag: bool = False) -> list[TechnologyRow]:
    """A complete classification for the fake roster, authored in sheet spelling."""

    def classification(technology, product, reductant, intense):
        return TechnologyRow(
            technology=technology,
            product=product,
            reductant=reductant,
            is_emission_intense=intense,
            switching_to=None,
            swap_ratio=None,
        )

    return [
        classification("BF", "iron", None, True),
        classification("EAF", "steel", None, False),
        classification("DRI", "iron", None, None),  # delegation row
        classification("DRI", "iron", "Coal", None if unauthored_coal_flag else True),
        classification("DRI", "iron", "Natural gas", False),
    ]


def opening_credit_rows() -> list[OpeningCreditRow]:
    return [
        OpeningCreditRow(
            vintage_year=2020,
            capacity_mt=1.5,
            geo_key=KEY_PROVINCE,
            product="iron",
            technology="BF",
            plant_group_id="E1",
        ),
        OpeningCreditRow(
            vintage_year=2022,
            capacity_mt=0.75,
            geo_key=NON_KEY_PROVINCE,
            product="steel",
            technology=None,
            plant_group_id=None,
        ),
    ]


def fake_repository_json(
    tmp_path,
    *,
    provinces: list[RegionRow] | None = None,
    technologies: list[TechnologyRow] | None = None,
    credits: list[OpeningCreditRow] | None = None,
    write: tuple[str, ...] = ("provinces", "technologies", "credits"),
):
    """A duck-typed JsonRepository carrying real capacity pool repositories.

    Fixtures named in ``write`` are written to disk; the others get a path
    that does not exist, which is exactly how a preparation without the
    optional sheets presents.
    """
    province_repo = CapacityPoolProvinceJsonRepository(tmp_path / "capacity_pool_provinces.json")
    technology_repo = CapacityPoolTechnologyJsonRepository(tmp_path / "capacity_pool_technologies.json")
    credit_repo = CapacityPoolOpeningCreditJsonRepository(tmp_path / "capacity_pool_opening_credits.json")
    if "provinces" in write:
        province_repo.add_list(province_rows() if provinces is None else provinces)
    if "technologies" in write:
        technology_repo.add_list(technology_rows() if technologies is None else technologies)
    if "credits" in write:
        credit_repo.add_list(opening_credit_rows() if credits is None else credits)
    roster_items = [SimpleNamespace(technology_name=name) for name in ("BF", "EAF", "DRI")]
    feedstock_items = [SimpleNamespace(reductant=name) for name in ("coal", "natural_gas", "coke_pci")]
    return SimpleNamespace(
        capacity_pool_provinces=province_repo,
        capacity_pool_technologies=technology_repo,
        capacity_pool_opening_credits=credit_repo,
        capex=SimpleNamespace(list=lambda: list(roster_items)),
        primary_feedstocks=SimpleNamespace(list=lambda: list(feedstock_items)),
    )


def bind_stale_policy() -> None:
    """Simulate a binding left behind by a previous enabled run."""
    evaluator = TreeEvaluator(
        [RegionRow(geo_key=KEY_PROVINCE, region_name="Jing-Jin-Ji", type="key", from_year=None)],
        [technology_rows()[0]],
        CapacityPolicyConfig(),
    )
    cp_handlers.bind_capacity_policy(evaluator, CapacityPool(), CapacityPolicyRecorder())


def test_disabled_configure_unbinds_stale_state(tmp_path):
    bind_stale_policy()
    assert cp_handlers.replace_capacity_hook() is not None

    configure_capacity_policy(CapacityPolicyConfig(enabled=False), fake_repository_json(tmp_path))

    assert cp_handlers.replace_capacity_hook() is None
    assert cp_handlers.expansion_capacity_hook() is None
    assert cp_handlers.greenfield_capacity_hook() is None


def test_disabled_configure_without_repositories_is_dormant():
    configure_capacity_policy(CapacityPolicyConfig(enabled=False), None)
    assert cp_handlers.replace_capacity_hook() is None


def test_enabled_binds_and_seeds_in_model_tonnes(tmp_path, caplog, propagating_policy_logs):
    caplog.set_level(logging.INFO)
    configure_capacity_policy(CapacityPolicyConfig(enabled=True), fake_repository_json(tmp_path))

    assert cp_handlers.replace_capacity_hook() is not None
    policy = cp_handlers._policy
    assert policy is not None
    assert policy.pool.total() == pytest.approx(2.25e6)
    oldest, newer = policy.pool.snapshot()
    assert oldest.vintage_year == 2020
    assert oldest.amount_mt == pytest.approx(1.5e6)
    assert oldest.region_tag == "Jing-Jin-Ji"
    assert oldest.owner_id == "E1"
    assert newer.region_tag is None
    assert newer.owner_id is None
    assert "sheet_mt=2.250 -> tonnes=2250000.0" in caplog.text


def test_enabled_logs_every_config_field(tmp_path, caplog, propagating_policy_logs):
    caplog.set_level(logging.INFO)
    from dataclasses import fields

    config = CapacityPolicyConfig(enabled=True)
    configure_capacity_policy(config, fake_repository_json(tmp_path))

    config_lines = [
        record.getMessage() for record in caplog.records if "[CAPACITY POOL] config " in record.getMessage()
    ]
    assert len(config_lines) == 1
    for field in fields(config):
        assert f"{field.name}=" in config_lines[0]


def test_enabled_promotes_validation_warnings_to_errors(tmp_path):
    repository = fake_repository_json(tmp_path, technologies=technology_rows(unauthored_coal_flag=True))
    with pytest.raises(ValueError, match="warnings promoted") as excinfo:
        configure_capacity_policy(CapacityPolicyConfig(enabled=True), repository)
    assert "unauthored" in str(excinfo.value)
    assert cp_handlers.replace_capacity_hook() is None


def test_reenabling_builds_a_fresh_pool(tmp_path):
    from steelo.capacity_policy import Credit

    configure_capacity_policy(CapacityPolicyConfig(enabled=True), fake_repository_json(tmp_path))
    first_policy = cp_handlers._policy
    assert first_policy is not None
    first_policy.pool.deposit(
        Credit(amount_mt=123.0, vintage_year=2030, region_tag=None, owner_id="E9", product="iron")
    )

    configure_capacity_policy(CapacityPolicyConfig(enabled=True), fake_repository_json(tmp_path))
    second_policy = cp_handlers._policy
    assert second_policy is not None
    assert second_policy is not first_policy
    assert second_policy.pool is not first_policy.pool
    assert second_policy.pool.total() == pytest.approx(2.25e6)


def minimal_simulation_config(tmp_path):
    """A runnable SimulationConfig over an empty on-disk dataset.

    Mirrors the ``test_simulation_runner_factory`` scaffold: empty fixture
    files are enough for ``bootstrap_simulation`` to build its repositories,
    and the capacity pool fixture paths simply do not exist — exactly how a
    preparation without the optional sheets presents.
    """
    from steelo.domain import Year
    from steelo.simulation import SimulationConfig
    from steelo.simulation_types import get_default_technology_settings

    data_dir = tmp_path / "data"
    fixtures_dir = data_dir / "fixtures"
    fixtures_dir.mkdir(parents=True)
    (fixtures_dir / "tech_switches_allowed.csv").write_text(
        "Technology,BF-BOF,DRI-EAF,Scrap-EAF\nBF-BOF,YES,YES,YES\nDRI-EAF,NO,YES,YES\nScrap-EAF,NO,NO,YES\n"
    )
    for name in (
        "plants",
        "demand_centers",
        "suppliers",
        "plant_groups",
        "tariffs",
        "subsidies",
        "carbon_costs",
        "primary_feedstocks",
        "region_emissivity",
        "capex",
        "cost_of_capital",
    ):
        (fixtures_dir / f"{name}.json").write_text('{"root": []}')
    (fixtures_dir / "input_costs.json").write_text(
        '{"root": ['
        '{"iso3": "DEU", "year": 2025, "costs": {"electricity": 0.05}},'
        '{"iso3": "DEU", "year": 2026, "costs": {"electricity": 0.05}}'
        "]}"
    )
    (fixtures_dir / "legal_process_connectors.json").write_text("[]")
    (fixtures_dir / "country_mappings.json").write_text(
        '[{"country": "Germany", "iso2": "DE", "iso3": "DEU", "irena_name": "Germany",'
        ' "region_for_outputs": "Europe", "ssp_region": "EUR", "tiam-ucl_region": "Rest of World"}]'
    )
    (fixtures_dir / "hydrogen_efficiency.json").write_text(
        '[{"year": 2025, "efficiency": 0.05}, {"year": 2026, "efficiency": 0.05}]'
    )
    (fixtures_dir / "hydrogen_capex_opex.json").write_text(
        '[{"country_code": "DEU", "values": {"2025": 1.0, "2026": 1.0}}]'
    )
    (fixtures_dir / "transport_emissions.json").write_text("[]")
    (fixtures_dir / "biomass_availability.json").write_text("[]")
    (fixtures_dir / "fallback_material_costs.json").write_text(
        '[{"iso3": "DEU", "technology": "BF", "metric": "Unit material cost", "unit": "USD/t HM",'
        ' "costs_by_year": {"2025": 100.0}}]'
    )
    (data_dir / "railway_costs.json").write_text('{"root": []}')

    return SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2026),
        master_excel_path=tmp_path / "test.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
        data_dir=data_dir,
    )


def test_bootstrap_simulation_disabled_unbinds_stale_binding(tmp_path):
    """A disabled run through the real entry point must clear a previous run's binding."""
    from steelo.bootstrap import bootstrap_simulation

    config = minimal_simulation_config(tmp_path)
    assert config.capacity_policy.enabled is False
    bind_stale_policy()
    assert cp_handlers.replace_capacity_hook() is not None

    runner = bootstrap_simulation(config)

    assert runner is not None
    assert cp_handlers.replace_capacity_hook() is None
    assert cp_handlers.expansion_capacity_hook() is None
    assert cp_handlers.greenfield_capacity_hook() is None


def test_enabled_with_missing_fixtures_raises_naming_them(tmp_path):
    """Criterion 17: enabled=True with no capacity pool fixtures refuses, naming each one."""
    with pytest.raises(ValueError) as excinfo:
        configure_capacity_policy(CapacityPolicyConfig(enabled=True), fake_repository_json(tmp_path, write=()))
    message = str(excinfo.value)
    assert "capacity_pool_provinces.json is missing" in message
    assert "capacity_pool_technologies.json is missing" in message
    assert "capacity_pool_opening_credits.json is missing" in message
    assert cp_handlers.replace_capacity_hook() is None


def test_enabled_with_one_missing_fixture_names_only_it(tmp_path):
    repository = fake_repository_json(tmp_path, write=("provinces", "technologies"))
    with pytest.raises(ValueError) as excinfo:
        configure_capacity_policy(CapacityPolicyConfig(enabled=True), repository)
    message = str(excinfo.value)
    assert "capacity_pool_opening_credits.json is missing" in message
    assert "capacity_pool_provinces.json" not in message


def test_enabled_with_empty_fixture_raises_as_empty(tmp_path):
    """An empty fixture is refused distinctly from a missing one — never silent dormancy."""
    repository = fake_repository_json(tmp_path, credits=[])
    with pytest.raises(ValueError, match="capacity_pool_opening_credits.json is empty"):
        configure_capacity_policy(CapacityPolicyConfig(enabled=True), repository)


def test_enabled_with_injected_repository_raises():
    """A run without fixture repositories cannot claim policy-on."""
    with pytest.raises(ValueError, match="no fixture repositories"):
        configure_capacity_policy(CapacityPolicyConfig(enabled=True), None)
    assert cp_handlers.replace_capacity_hook() is None


def test_bootstrap_simulation_enabled_without_pool_fixtures_raises(tmp_path):
    """Criterion 17 through the real entry point: a prepared dataset without the
    optional pool sheets must refuse an enabled run at bootstrap."""
    from steelo.bootstrap import bootstrap_simulation

    config = minimal_simulation_config(tmp_path)
    config.capacity_policy.enabled = True

    with pytest.raises(ValueError, match="capacity_pool_provinces.json is missing"):
        bootstrap_simulation(config)
    assert cp_handlers.replace_capacity_hook() is None
