"""Tests for the decision-tree evaluator: tagging, ratios, utilisation gate, penalties."""

import pytest

from steelo.capacity_policy import CapacityPolicyConfig, CapacityPolicyRecorder, TreeEvaluator
from steelo.capacity_policy.inputs import RegionRow, TechnologyRow


def region(geo_key: str, region_name: str | None = None, type: str | None = None) -> RegionRow:
    """Build a province row; key rows carry their cluster as region_name."""
    return RegionRow(geo_key=geo_key, region_name=region_name, type=type)


def classification(
    technology: str,
    reductant: str | None = None,
    is_emission_intense: bool | None = False,
    product: str = "iron",
) -> TechnologyRow:
    """Build a classification row (switching_to blank)."""
    return TechnologyRow(
        technology=technology,
        product=product,
        reductant=reductant,
        is_emission_intense=is_emission_intense,
        switching_to=None,
        swap_ratio=None,
    )


def override(technology: str, switching_to: str, swap_ratio: float, reductant: str | None = None) -> TechnologyRow:
    """Build an override row (flag blank, ratio set)."""
    return TechnologyRow(
        technology=technology,
        product=None,
        reductant=reductant,
        is_emission_intense=None,
        switching_to=switching_to,
        swap_ratio=swap_ratio,
    )


REGIONS = [
    region("CHN:CN-HE", "Jing-Jin-Ji", "key"),
    region("CHN:CN-TJ", "Jing-Jin-Ji", "key"),
    region("CHN:CN-SH", "Yangtze River Delta", "key"),
    region("CHN:CN-QH", "Qinghai", "exempt"),
    region("CHN:CN-GD", "Guangdong"),
]

TECHNOLOGIES = [
    classification("BF", is_emission_intense=True),
    classification("BOF", is_emission_intense=True, product="steel"),
    classification("EAF", is_emission_intense=False, product="steel"),
    classification("BF+CCS", is_emission_intense=False),
    # DRI is reductant-split: a flagless blank-reductant delegation row plus per-reductant rows.
    classification("DRI", reductant=None, is_emission_intense=None),
    classification("DRI", reductant="Coal", is_emission_intense=True),
    classification("DRI", reductant="Hydrogen", is_emission_intense=False),
    classification("BF_CHARCOAL", is_emission_intense=None),
]


def make_evaluator(
    technologies: list[TechnologyRow] | None = None,
    config: CapacityPolicyConfig | None = None,
    recorder: CapacityPolicyRecorder | None = None,
) -> TreeEvaluator:
    return TreeEvaluator(
        REGIONS,
        TECHNOLOGIES if technologies is None else technologies,
        config or CapacityPolicyConfig(),
        recorder=recorder,
    )


def permitted(
    evaluator: TreeEvaluator,
    old_technology: str = "BF",
    old_reductant: str | None = None,
    new_technology: str = "BF+CCS",
    new_reductant: str | None = None,
    capacity_mt: float = 3.0,
    geo_key: str = "CHN:CN-GD",
    historical_utilization: dict[int, float] | None = None,
    year: int = 2030,
    furnace_group_id: str | None = None,
    product: str = "steel",
) -> float | None:
    """Call permitted_capacity with defaults suited to single-branch tests."""
    return evaluator.permitted_capacity(
        old_technology=old_technology,
        old_reductant=old_reductant,
        new_technology=new_technology,
        new_reductant=new_reductant,
        capacity_mt=capacity_mt,
        geo_key=geo_key,
        product=product,
        historical_utilization=historical_utilization,
        year=year,
        furnace_group_id=furnace_group_id,
    )


class TestOnClose:
    def test_key_province_credit_carries_cluster_tag(self):
        """A closure in a key province deposits a credit tagged with the cluster name."""
        credit = make_evaluator().on_close(
            geo_key="CHN:CN-HE", capacity_mt=2.0, owner_id="E1", product="iron", year=2030
        )
        assert credit.region_tag == "Jing-Jin-Ji"
        assert credit.amount_mt == 2.0
        assert credit.owner_id == "E1"
        assert credit.product == "iron"
        assert credit.vintage_year == 2030

    def test_cluster_members_share_the_tag(self):
        """Member provinces of one cluster produce identically tagged credits."""
        evaluator = make_evaluator()
        hebei = evaluator.on_close(geo_key="CHN:CN-HE", capacity_mt=1.0, owner_id=None, product="iron", year=2030)
        tianjin = evaluator.on_close(geo_key="CHN:CN-TJ", capacity_mt=1.0, owner_id=None, product="iron", year=2030)
        assert hebei.region_tag == tianjin.region_tag == "Jing-Jin-Ji"

    @pytest.mark.parametrize("geo_key", ["CHN:CN-GD", "CHN:CN-QH", "CHN"])
    def test_non_key_locations_deposit_untagged(self, geo_key):
        """Non-key and exempt provinces, and a bare country key, deposit untagged credits."""
        credit = make_evaluator().on_close(geo_key=geo_key, capacity_mt=1.0, owner_id=None, product="steel", year=2030)
        assert credit.region_tag is None

    def test_key_row_without_cluster_name_rejected(self):
        """A key province with no cluster name cannot tag anything and fails at construction."""
        with pytest.raises(ValueError, match="no cluster name"):
            TreeEvaluator([region("CHN:CN-HE", None, "key")], TECHNOLOGIES, CapacityPolicyConfig())


class TestPermittedCapacityRatio:
    def test_intense_to_intense_is_penalised(self):
        """Replacing an emission-intense route with another shrinks by the default ratio."""
        assert permitted(make_evaluator(), new_technology="BOF") == pytest.approx(2.0)

    def test_non_intense_old_route_is_one_to_one(self):
        """Replacing a route that is not emission-intense is 1:1."""
        assert permitted(make_evaluator(), old_technology="EAF", new_technology="BOF") == pytest.approx(3.0)

    def test_non_intense_target_is_one_to_one(self):
        """A target that is not emission-intense is 1:1 even from an emission-intense route."""
        assert permitted(make_evaluator(), new_technology="BF+CCS") == pytest.approx(3.0)

    def test_coal_capture_route_resolves_one_to_one_as_new_side(self):
        """Coal-plus-capture is not emission-intense, so building it off an intense route is 1:1.

        Pins the intended data effect of the single flag: the four
        coal-plus-capture keys author ``is_emission_intense`` FALSE with a blank
        deep-abatement column, so the derivation refused them as a new side at
        commit 12 and resolves them now.
        """
        rows = TECHNOLOGIES + [classification("DRI+CCS", reductant="Coal", is_emission_intense=False)]
        assert permitted(
            make_evaluator(technologies=rows), new_technology="DRI+CCS", new_reductant="Coal"
        ) == pytest.approx(3.0)

    def test_exempt_province_is_one_to_one_regardless(self):
        """In an exempt province even intense-to-intense replacement is 1:1."""
        assert permitted(make_evaluator(), new_technology="BOF", geo_key="CHN:CN-QH") == pytest.approx(3.0)

    def test_wildcard_override_wins_over_derivation(self):
        """A * -> EAF override pins the ratio the flag would not derive."""
        rows = TECHNOLOGIES + [override("*", "EAF", 1.0)]
        evaluator = make_evaluator(technologies=rows)
        assert permitted(evaluator, new_technology="EAF") == pytest.approx(3.0)

    def test_reductant_specific_classification_resolves(self):
        """The reductant decides the classification of a reductant-split technology."""
        evaluator = make_evaluator()
        assert permitted(evaluator, new_technology="DRI", new_reductant="Hydrogen") == pytest.approx(3.0)
        assert permitted(evaluator, new_technology="DRI", new_reductant="Coal") == pytest.approx(2.0)

    def test_configured_ratio_is_used(self):
        """The penalised ratio comes from the config, not a constant."""
        evaluator = make_evaluator(config=CapacityPolicyConfig(replacement_ratio=2.0))
        assert permitted(evaluator, new_technology="BOF") == pytest.approx(1.5)

    def test_unauthored_flags_raise(self):
        """An unauthored classification refuses rather than defaulting to 1:1."""
        with pytest.raises(ValueError, match="Swap ratio undecided"):
            permitted(make_evaluator(), old_technology="BF_CHARCOAL", new_technology="BOF")

    def test_unknown_technology_raises(self):
        """A route with no classification row at all refuses."""
        with pytest.raises(ValueError, match="No classification row"):
            permitted(make_evaluator(), old_technology="MOE")

    def test_split_technology_without_authored_reductant_raises(self):
        """A reductant-split technology has no blanket row to fall back to."""
        with pytest.raises(ValueError, match="No classification row"):
            permitted(make_evaluator(), new_technology="DRI", new_reductant="Natural gas")


class TestUtilizationGate:
    def test_full_window_at_threshold_blocks(self):
        """Utilisation exactly at the floor for the whole window blocks."""
        history = {2028: 0.25, 2029: 0.25}
        assert permitted(make_evaluator(), historical_utilization=history) is None

    def test_low_utilization_window_blocks(self):
        """A fully recorded low window ([0.20, 0.22]) blocks the replacement."""
        history = {2028: 0.20, 2029: 0.22}
        assert permitted(make_evaluator(), historical_utilization=history) is None

    def test_window_minus_one_low_year_does_not_block(self):
        """One low recorded year cannot establish a two-year condition."""
        history = {2029: 0.10}
        assert permitted(make_evaluator(), historical_utilization=history) is not None

    def test_one_year_above_threshold_does_not_block(self):
        """A single year above the floor inside the window breaks the streak."""
        history = {2028: 0.20, 2029: 0.26}
        assert permitted(make_evaluator(), historical_utilization=history) is not None

    def test_gap_in_recorded_years_does_not_block(self):
        """Non-consecutive low years cannot establish the condition."""
        history = {2027: 0.20, 2029: 0.20}
        assert permitted(make_evaluator(), historical_utilization=history, year=2030) is not None
        # Fully recorded control: the same rates on consecutive years do block.
        assert permitted(make_evaluator(), historical_utilization={2028: 0.20, 2029: 0.20}, year=2030) is None

    @pytest.mark.parametrize("history", [None, {}])
    def test_missing_history_does_not_block(self, history):
        """No recorded history means the gate cannot bind."""
        assert permitted(make_evaluator(), historical_utilization=history) is not None

    def test_window_anchors_at_latest_recorded_year(self):
        """The window ends at the latest recorded year at or before the decision year."""
        history = {2027: 0.20, 2028: 0.20}
        assert permitted(make_evaluator(), historical_utilization=history, year=2030) is None

    def test_future_years_are_ignored(self):
        """Recorded years after the decision year do not participate."""
        history = {2028: 0.20, 2029: 0.20, 2031: 0.90}
        assert permitted(make_evaluator(), historical_utilization=history, year=2029) is None

    def test_wider_window_needs_more_low_years(self):
        """A three-year window is not established by two low years."""
        evaluator = make_evaluator(config=CapacityPolicyConfig(utilization_window_years=3))
        assert permitted(evaluator, historical_utilization={2028: 0.20, 2029: 0.20}) is not None
        assert permitted(evaluator, historical_utilization={2027: 0.20, 2028: 0.20, 2029: 0.20}) is None


def renovation(
    evaluator: TreeEvaluator,
    technology: str = "BF",
    reductant: str | None = None,
    capacity_mt: float = 3.0,
    geo_key: str = "CHN:CN-GD",
    historical_utilization: dict[int, float] | None = None,
    year: int = 2030,
    furnace_group_id: str | None = None,
    product: str = "steel",
) -> float | None:
    """Call permitted_renovation with defaults suited to single-branch tests."""
    return evaluator.permitted_renovation(
        technology=technology,
        reductant=reductant,
        capacity_mt=capacity_mt,
        geo_key=geo_key,
        product=product,
        historical_utilization=historical_utilization,
        year=year,
        furnace_group_id=furnace_group_id,
    )


class TestPermittedRenovation:
    """The gate-only ② path: a renovation is blocked or untouched, never shrunk."""

    def test_a_healthy_group_renovates_at_its_own_capacity(self):
        assert renovation(make_evaluator(), historical_utilization={2029: 0.9, 2030: 0.9}) == pytest.approx(3.0)

    @pytest.mark.parametrize("history", [None, {}, {2030: 0.1}])
    def test_insufficient_history_never_blocks(self, history):
        """Only a fully recorded low window binds; insufficient history never blocks."""
        assert renovation(make_evaluator(), historical_utilization=history) == pytest.approx(3.0)

    def test_a_low_window_blocks(self):
        assert renovation(make_evaluator(), historical_utilization={2029: 0.1, 2030: 0.1}) is None

    def test_an_unclassified_technology_is_never_looked_up(self):
        """No ratio means no classification: a route with no sheet row still renovates."""
        assert renovation(make_evaluator(), technology="MOE") == pytest.approx(3.0)

    def test_a_block_records_one_row_with_the_incumbent_on_both_sides(self):
        recorder = CapacityPolicyRecorder()
        evaluator = make_evaluator(recorder=recorder)

        renovation(
            evaluator,
            reductant="Coke+PCI",
            historical_utilization={2029: 0.1, 2030: 0.1},
            furnace_group_id="fg-9",
        )

        (row,) = recorder._gate_decisions
        assert row["decision"] == "blocked_utilization"
        assert row["furnace_group_id"] == "fg-9"
        assert row["old_technology"] == row["new_technology"] == "BF"
        assert row["old_reductant"] == row["new_reductant"] == "Coke+PCI"
        assert row["capacity_t"] == pytest.approx(3.0)
        assert row["ratio"] is None
        assert row["permitted_t"] is None

    def test_an_unblocked_renovation_records_nothing(self):
        """Nothing was decided, so nothing joins the policy-bite metric."""
        recorder = CapacityPolicyRecorder()

        renovation(make_evaluator(recorder=recorder), historical_utilization={2029: 0.9, 2030: 0.9})

        assert recorder._gate_decisions == []


class TestIncreaseBuildCapacity:
    """The non-consuming sizing half of ③, which the pre-NPV query calls."""

    def test_clean_route_builds_the_planned_amount(self):
        assert make_evaluator().increase_build_capacity(capacity_mt=2.0, technology="EAF", reductant=None) == 2.0

    def test_emission_intense_route_is_divided(self):
        assert make_evaluator().increase_build_capacity(
            capacity_mt=3.0, technology="BF", reductant=None
        ) == pytest.approx(2.0)

    def test_penalty_divisor_comes_from_config(self):
        evaluator = make_evaluator(config=CapacityPolicyConfig(emission_intense_penalty_divisor=3.0))
        assert evaluator.increase_build_capacity(capacity_mt=3.0, technology="BF", reductant=None) == pytest.approx(1.0)

    def test_unauthored_intense_flag_raises(self):
        with pytest.raises(ValueError, match="is_emission_intense unauthored"):
            make_evaluator().increase_build_capacity(capacity_mt=1.0, technology="BF_CHARCOAL", reductant=None)

    def test_unknown_technology_raises(self):
        with pytest.raises(ValueError, match="No classification row"):
            make_evaluator().increase_build_capacity(capacity_mt=1.0, technology="NOPE", reductant=None)

    @pytest.mark.parametrize(
        "technology, reductant",
        [("EAF", None), ("BF", None), ("DRI", "Coal"), ("DRI", "Hydrogen"), ("DRI", None)],
    )
    def test_the_gate_builds_exactly_what_the_query_sized(self, technology, reductant):
        """One arithmetic, two callers: the stage-11.5 equality check rests on this."""
        evaluator = make_evaluator()
        spec = evaluator.on_increase(
            geo_key="CHN:CN-GD", product="iron", capacity_mt=3.0, technology=technology, reductant=reductant
        )
        assert spec.build_mt == evaluator.increase_build_capacity(
            capacity_mt=3.0, technology=technology, reductant=reductant
        )


class TestOnIncrease:
    def test_key_province_restricts_to_cluster(self):
        """A build in a key province may only spend that cluster's credits."""
        spec = make_evaluator().on_increase(
            geo_key="CHN:CN-SH", product="steel", capacity_mt=2.0, technology="EAF", reductant=None
        )
        assert spec.region_tag == "Yangtze River Delta"
        assert spec.product == "steel"

    def test_non_key_province_draws_from_any_credit(self):
        """Elsewhere the applicable pool is unrestricted by tag."""
        spec = make_evaluator().on_increase(
            geo_key="CHN:CN-GD", product="steel", capacity_mt=2.0, technology="EAF", reductant=None
        )
        assert spec.region_tag is None

    def test_clean_build_withdraws_and_builds_the_planned_amount(self):
        spec = make_evaluator().on_increase(
            geo_key="CHN:CN-GD", product="steel", capacity_mt=2.0, technology="EAF", reductant=None
        )
        assert spec.withdraw_mt == 2.0
        assert spec.build_mt == 2.0

    def test_emission_intense_build_withdraws_full_but_builds_divided(self):
        """The penalty removes the originally planned freed capacity: withdraw 3, build 2."""
        spec = make_evaluator().on_increase(
            geo_key="CHN:CN-GD", product="iron", capacity_mt=3.0, technology="BF", reductant=None
        )
        assert spec.withdraw_mt == 3.0
        assert spec.build_mt == pytest.approx(2.0)

    def test_penalty_divisor_comes_from_config(self):
        evaluator = make_evaluator(config=CapacityPolicyConfig(emission_intense_penalty_divisor=3.0))
        spec = evaluator.on_increase(
            geo_key="CHN:CN-GD", product="iron", capacity_mt=3.0, technology="BF", reductant=None
        )
        assert spec.build_mt == pytest.approx(1.0)

    def test_unauthored_intense_flag_raises(self):
        """An increase cannot be evaluated against an unauthored emission-intense flag."""
        with pytest.raises(ValueError, match="is_emission_intense unauthored"):
            make_evaluator().on_increase(
                geo_key="CHN:CN-GD", product="iron", capacity_mt=1.0, technology="BF_CHARCOAL", reductant=None
            )


class TestReductantNormalization:
    """Sheet rows author the workbook vocabulary; the runtime carries normalised keys."""

    def test_runtime_form_reductant_hits_sheet_form_row(self):
        """ "hydrogen" (runtime) must resolve the "Hydrogen" (sheet) DRI row: not intense, so 1:1."""
        assert permitted(make_evaluator(), new_technology="DRI", new_reductant="hydrogen") == pytest.approx(3.0)

    def test_sheet_form_reductant_still_resolves(self):
        assert permitted(make_evaluator(), new_technology="DRI", new_reductant="Hydrogen") == pytest.approx(3.0)

    def test_on_increase_normalises_reductant(self):
        """ "coal" (runtime) must resolve the "Coal" DRI row: emission-intense, so the build is penalised."""
        spec = make_evaluator().on_increase(
            geo_key="CHN:CN-GD", product="iron", capacity_mt=3.0, technology="DRI", reductant="coal"
        )
        assert spec.withdraw_mt == 3.0
        assert spec.build_mt == pytest.approx(2.0)


class TestConservativeFallback:
    """A split technology with no reductant hypothesis classifies as its worst case."""

    def test_replace_target_with_no_hypothesis_is_penalised(self):
        """DRI spans intense-coal and non-intense-hydrogen rows; unresolved, it is intense: 1.5:1."""
        assert permitted(make_evaluator(), new_technology="DRI", new_reductant=None) == pytest.approx(2.0)

    def test_increase_with_no_hypothesis_is_penalised(self):
        """Worst case is emission-intense (any variant is), so the build is divided."""
        spec = make_evaluator().on_increase(
            geo_key="CHN:CN-GD", product="iron", capacity_mt=3.0, technology="DRI", reductant=None
        )
        assert spec.withdraw_mt == 3.0
        assert spec.build_mt == pytest.approx(2.0)

    def test_fallback_is_logged(self, caplog):
        import logging

        tree_logger = logging.getLogger("steelo.capacity_policy.tree")
        original_propagate = tree_logger.propagate
        tree_logger.propagate = True  # the YAML logging config may have detached steelo logs from root
        try:
            with caplog.at_level(logging.INFO):
                permitted(make_evaluator(), new_technology="DRI", new_reductant=None)
        finally:
            tree_logger.propagate = original_propagate
        assert "decision=conservative_fallback technology=DRI" in caplog.text

    def test_fallback_refuses_unauthored_variant_flags(self):
        """The worst case may only rest on authored rows — a sheet gap still refuses."""
        rows = [
            classification("BF", is_emission_intense=True),
            classification("DRI", reductant=None, is_emission_intense=None),
            classification("DRI", reductant="Coal", is_emission_intense=None),
            classification("DRI", reductant="Hydrogen", is_emission_intense=False),
        ]
        with pytest.raises(ValueError, match="unauthored"):
            permitted(make_evaluator(rows), new_technology="DRI", new_reductant=None)

    def test_named_reductant_without_row_still_refuses(self):
        """The fallback covers the no-hypothesis case only, never a named sheet gap."""
        with pytest.raises(ValueError, match="No classification row"):
            permitted(make_evaluator(), new_technology="DRI", new_reductant="Natural gas")


class TestRecordedGateDecisions:
    """One row per evaluation, 1:1 with the line the evaluator logs."""

    def test_a_ratio_evaluation_records_its_resolved_numbers(self):
        recorder = CapacityPolicyRecorder()
        evaluator = make_evaluator(recorder=recorder)

        permitted(evaluator, new_technology="DRI", new_reductant="Coal", furnace_group_id="fg-1")

        (row,) = recorder._gate_decisions
        assert row["decision"] == "ratio"
        assert row["furnace_group_id"] == "fg-1"
        assert row["geo_key"] == "CHN:CN-GD"
        assert row["ratio"] == pytest.approx(1.5)
        assert row["capacity_t"] == pytest.approx(3.0)
        assert row["permitted_t"] == pytest.approx(2.0)

    def test_a_utilisation_block_records_without_a_ratio(self):
        recorder = CapacityPolicyRecorder()
        evaluator = make_evaluator(recorder=recorder)

        permitted(evaluator, historical_utilization={2029: 0.1, 2030: 0.1}, furnace_group_id="fg-2")

        (row,) = recorder._gate_decisions
        assert row["decision"] == "blocked_utilization"
        assert row["ratio"] is None
        assert row["permitted_t"] is None

    def test_every_evaluation_records_exactly_one_row(self):
        recorder = CapacityPolicyRecorder()
        evaluator = make_evaluator(recorder=recorder)

        permitted(evaluator, new_technology="EAF")
        permitted(evaluator, new_technology="BF+CCS")
        permitted(evaluator, historical_utilization={2029: 0.1, 2030: 0.1})

        assert len(recorder._gate_decisions) == 3

    def test_a_refused_evaluation_records_nothing(self):
        """A sheet gap raises before either log line, so no row is written."""
        recorder = CapacityPolicyRecorder()
        evaluator = make_evaluator(recorder=recorder)

        with pytest.raises(ValueError):
            permitted(evaluator, new_technology="DRI", new_reductant="Natural gas")

        assert recorder._gate_decisions == []

    def test_the_fallback_flag_marks_only_the_unresolved_side(self):
        recorder = CapacityPolicyRecorder()
        evaluator = make_evaluator(recorder=recorder)

        permitted(evaluator, new_technology="DRI", new_reductant=None)

        (row,) = recorder._gate_decisions
        assert row["new_used_conservative_fallback"] is True
        assert row["old_used_conservative_fallback"] is False

    @pytest.mark.parametrize(
        "new_technology, new_reductant",
        [("DRI", "Coal"), ("BF+CCS", None)],
        ids=["named-reductant-row", "technology-wide-row"],
    )
    def test_an_authored_row_is_not_a_fallback(self, new_technology, new_reductant):
        recorder = CapacityPolicyRecorder()
        evaluator = make_evaluator(recorder=recorder)

        permitted(evaluator, new_technology=new_technology, new_reductant=new_reductant)

        (row,) = recorder._gate_decisions
        assert row["new_used_conservative_fallback"] is False

    def test_no_recorder_still_evaluates(self):
        assert permitted(make_evaluator()) == pytest.approx(3.0)
