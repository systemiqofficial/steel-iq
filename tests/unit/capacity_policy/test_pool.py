"""Tests for the capacity pool: FIFO withdrawal over the filtered applicable pool."""

import pytest

from steelo.capacity_policy import CapacityPool, Credit, SeedEntry


def make_credit(
    amount_mt: float = 1.0,
    vintage_year: int = 2020,
    region_tag: str | None = None,
    owner_id: str | None = "owner-a",
    product: str = "steel",
) -> Credit:
    """Build a credit with defaults suited to single-filter tests."""
    return Credit(
        amount_mt=amount_mt,
        vintage_year=vintage_year,
        region_tag=region_tag,
        owner_id=owner_id,
        product=product,
    )


# Years either side of the default 2028 cutoff, so each test states which regime it runs in.
PRE_CUTOFF_YEAR = 2026
POST_CUTOFF_YEAR = 2029


def test_deposit_and_withdraw_consumes_credit():
    """A granted withdrawal consumes the deposited credit."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=2.0))

    result = pool.try_withdraw(2.0, region_tag=None, product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)

    assert result.granted is True
    assert result.blocked_reason is None
    assert result.attributed_owner_id is None
    assert pool.total() == 0.0


def test_deposit_rejects_non_positive_amount():
    """Zero or negative credits are bugs and fail loudly."""
    pool = CapacityPool()
    with pytest.raises(ValueError):
        pool.deposit(make_credit(amount_mt=0.0))


def test_unknown_banked_credit_rule_rejected():
    """An unrecognised banked-credit rule fails at construction, not at use."""
    with pytest.raises(ValueError):
        CapacityPool(banked_credit_rule="reasign")


def test_withdrawal_consumes_oldest_credits_first():
    """FIFO: the oldest credits are consumed first; newer ones remain."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=1.0, vintage_year=2018))
    pool.deposit(make_credit(amount_mt=1.0, vintage_year=2022))

    result = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)

    assert result.granted is True
    assert [c.vintage_year for c in result.credits_consumed] == [2018]
    assert [c.vintage_year for c in pool.snapshot()] == [2022]


def test_empty_pool_blocks_withdrawal():
    """An empty pool grants nothing and consumes nothing."""
    pool = CapacityPool()

    result = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)

    assert result.granted is False
    assert result.credits_consumed == ()
    assert result.blocked_reason == "insufficient_applicable_pool"


def test_insufficient_applicable_pool_blocks_and_consumes_nothing():
    """Grants are all-or-nothing: a short pool is left untouched."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=1.0))

    result = pool.try_withdraw(2.0, region_tag=None, product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)

    assert result.granted is False
    assert result.blocked_reason == "insufficient_applicable_pool"
    assert pool.total() == 1.0


def test_partial_consumption_leaves_credit_remainder_with_original_vintage():
    """A withdrawal smaller than the front credit leaves its remainder in place, in order."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=3.0, vintage_year=2018))
    pool.deposit(make_credit(amount_mt=1.0, vintage_year=2022))

    result = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)

    assert result.granted is True
    assert result.credits_consumed[0].amount_mt == 1.0
    remainder = pool.snapshot()[0]
    assert remainder.amount_mt == 2.0
    assert remainder.vintage_year == 2018
    assert [c.vintage_year for c in pool.snapshot()] == [2018, 2022]


def test_region_filter_requires_exact_tag():
    """A key-region withdrawal sees only credits carrying exactly that tag."""
    pool = CapacityPool()
    pool.deposit(make_credit(region_tag=None))
    pool.deposit(make_credit(region_tag="CHN:CN-SD"))
    pool.deposit(make_credit(region_tag="CHN:CN-HE"))

    result = pool.try_withdraw(1.0, region_tag="CHN:CN-HE", product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)

    assert result.granted is True
    assert result.credits_consumed[0].region_tag == "CHN:CN-HE"
    blocked = pool.try_withdraw(1.0, region_tag="CHN:CN-HE", product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)
    assert blocked.granted is False


def test_untagged_withdrawal_spends_any_credit():
    """region_tag=None makes both tagged and untagged credits applicable."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=1.0, region_tag="CHN:CN-HE"))
    pool.deposit(make_credit(amount_mt=1.0, region_tag=None))

    result = pool.try_withdraw(2.0, region_tag=None, product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)

    assert result.granted is True
    assert pool.total() == 0.0


def test_product_filter_separates_iron_and_steel():
    """An iron credit cannot fund a steel build."""
    pool = CapacityPool()
    pool.deposit(make_credit(product="iron"))

    result = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)

    assert result.granted is False
    assert pool.total() == 1.0


def test_owner_filter_inactive_before_cutoff():
    """Before the swap cutoff, another owner's credit is freely spendable."""
    pool = CapacityPool()
    pool.deposit(make_credit(owner_id="owner-a"))

    result = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-b", year=PRE_CUTOFF_YEAR)

    assert result.granted is True


def test_owner_filter_active_from_cutoff_year():
    """From the cutoff year only the withdrawer's own credits count."""
    pool = CapacityPool()
    pool.deposit(make_credit(owner_id="owner-a", vintage_year=POST_CUTOFF_YEAR))

    blocked = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-b", year=POST_CUTOFF_YEAR)
    assert blocked.granted is False

    own = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-a", year=POST_CUTOFF_YEAR)
    assert own.granted is True


def test_owner_filter_disabled_when_cutoff_is_none():
    """inter_company_swap_cutoff_year=None disables the partition entirely."""
    pool = CapacityPool(inter_company_swap_cutoff_year=None)
    pool.deposit(make_credit(owner_id="owner-a", vintage_year=POST_CUTOFF_YEAR))

    result = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-b", year=POST_CUTOFF_YEAR)

    assert result.granted is True


def test_reassign_applies_owner_filter_to_pre_cutoff_credits():
    """reassign (default): pre-cutoff credits stay with their depositor at the boundary."""
    pool = CapacityPool(banked_credit_rule="reassign")
    pool.deposit(make_credit(owner_id="owner-a", vintage_year=PRE_CUTOFF_YEAR))

    other = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-b", year=POST_CUTOFF_YEAR)
    assert other.granted is False

    own = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-a", year=POST_CUTOFF_YEAR)
    assert own.granted is True


def test_persist_exempts_pre_cutoff_credits_from_owner_filter():
    """persist: pre-cutoff vintages stay freely spendable; post-cutoff ones do not."""
    pool = CapacityPool(banked_credit_rule="persist")
    pool.deposit(make_credit(amount_mt=1.0, owner_id="owner-a", vintage_year=PRE_CUTOFF_YEAR))
    pool.deposit(make_credit(amount_mt=1.0, owner_id="owner-a", vintage_year=POST_CUTOFF_YEAR))

    result = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-b", year=POST_CUTOFF_YEAR)
    assert result.granted is True
    assert result.credits_consumed[0].vintage_year == PRE_CUTOFF_YEAR

    blocked = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-b", year=POST_CUTOFF_YEAR)
    assert blocked.granted is False


def test_expire_drops_pre_cutoff_credits_at_the_boundary():
    """expire: pre-cutoff vintages become unusable from the cutoff, even by their owner."""
    pool = CapacityPool(banked_credit_rule="expire")
    pool.deposit(make_credit(owner_id="owner-a", vintage_year=PRE_CUTOFF_YEAR))

    before = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)
    assert before.granted is True

    pool.deposit(make_credit(owner_id="owner-a", vintage_year=PRE_CUTOFF_YEAR))
    after = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-a", year=POST_CUTOFF_YEAR)
    assert after.granted is False


def test_expire_boundary_vintage_is_post_cutoff():
    """A credit deposited in the cutoff year itself is not a pre-cutoff vintage."""
    pool = CapacityPool(banked_credit_rule="expire")
    pool.deposit(make_credit(owner_id="owner-a", vintage_year=2028))

    result = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-a", year=POST_CUTOFF_YEAR)

    assert result.granted is True


def test_unowned_credits_drawable_under_every_banked_credit_rule():
    """Unowned seeded credits stay freely drawable — even under expire."""
    for rule in ("reassign", "persist", "expire"):
        pool = CapacityPool(banked_credit_rule=rule)
        pool.deposit(make_credit(owner_id=None, vintage_year=PRE_CUTOFF_YEAR))

        result = pool.try_withdraw(1.0, region_tag=None, product="steel", owner_id="owner-b", year=POST_CUTOFF_YEAR)

        assert result.granted is True, f"unowned credit not drawable under {rule}"


def test_single_owner_draws_wholly_from_one_holder():
    """A single-owner withdrawal is served by the first holder able to cover it."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=1.0, vintage_year=2018, owner_id="owner-a"))
    pool.deposit(make_credit(amount_mt=1.5, vintage_year=2020, owner_id="owner-b"))

    result = pool.try_withdraw(
        1.5, region_tag=None, product="steel", owner_id="indi_CHN", year=PRE_CUTOFF_YEAR, single_owner=True
    )

    assert result.granted is True
    assert result.attributed_owner_id == "owner-b"
    assert all(c.owner_id == "owner-b" for c in result.credits_consumed)
    assert pool.total() == 1.0


def test_single_owner_blocked_when_no_holder_covers_the_amount():
    """An ample pool still blocks when no single holder covers the withdrawal."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=1.0, owner_id="owner-a"))
    pool.deposit(make_credit(amount_mt=1.5, owner_id="owner-b"))

    result = pool.try_withdraw(
        2.0, region_tag=None, product="steel", owner_id="indi_CHN", year=PRE_CUTOFF_YEAR, single_owner=True
    )

    assert result.granted is False
    assert result.blocked_reason == "no_single_owner_with_sufficient_credits"
    assert pool.total() == 2.5


def test_single_owner_prefers_holder_with_oldest_credit():
    """Among holders with enough, the one whose oldest credit comes first wins."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=2.0, vintage_year=2018, owner_id="owner-b"))
    pool.deposit(make_credit(amount_mt=2.0, vintage_year=2019, owner_id="owner-a"))

    result = pool.try_withdraw(
        2.0, region_tag=None, product="steel", owner_id="indi_CHN", year=PRE_CUTOFF_YEAR, single_owner=True
    )

    assert result.granted is True
    assert result.attributed_owner_id == "owner-b"


def test_single_owner_region_and_product_still_bind():
    """The single-owner rule replaces only the owner filter; region and product hold."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=2.0, owner_id="owner-a", region_tag="CHN:CN-HE", product="iron"))

    wrong_region = pool.try_withdraw(
        1.0, region_tag="CHN:CN-SD", product="iron", owner_id="indi_CHN", year=PRE_CUTOFF_YEAR, single_owner=True
    )
    assert wrong_region.granted is False

    wrong_product = pool.try_withdraw(
        1.0, region_tag="CHN:CN-HE", product="steel", owner_id="indi_CHN", year=PRE_CUTOFF_YEAR, single_owner=True
    )
    assert wrong_product.granted is False

    match = pool.try_withdraw(
        1.0, region_tag="CHN:CN-HE", product="iron", owner_id="indi_CHN", year=PRE_CUTOFF_YEAR, single_owner=True
    )
    assert match.granted is True


def test_single_owner_uniform_across_the_cutoff():
    """Greenfield may draw any single owner's credits post-cutoff — same rule as before it."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=2.0, owner_id="owner-a", vintage_year=POST_CUTOFF_YEAR))

    result = pool.try_withdraw(
        2.0, region_tag=None, product="steel", owner_id="indi_CHN", year=POST_CUTOFF_YEAR, single_owner=True
    )

    assert result.granted is True
    assert result.attributed_owner_id == "owner-a"


def test_single_owner_can_draw_wholly_from_the_unowned_pot():
    """The unowned pot is a holder like any other; drawing from it attributes to nobody."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=2.0, vintage_year=2018, owner_id=None))
    pool.deposit(make_credit(amount_mt=2.0, vintage_year=2020, owner_id="owner-a"))

    result = pool.try_withdraw(
        1.5, region_tag=None, product="steel", owner_id="indi_CHN", year=PRE_CUTOFF_YEAR, single_owner=True
    )

    assert result.granted is True
    assert result.attributed_owner_id is None
    assert all(c.owner_id is None for c in result.credits_consumed)


def test_single_owner_does_not_mix_owned_and_unowned_credits():
    """One holder means one holder: owned and unowned credits cannot combine."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=1.0, owner_id="owner-a"))
    pool.deposit(make_credit(amount_mt=1.0, owner_id=None))

    result = pool.try_withdraw(
        2.0, region_tag=None, product="steel", owner_id="indi_CHN", year=PRE_CUTOFF_YEAR, single_owner=True
    )

    assert result.granted is False
    assert result.blocked_reason == "no_single_owner_with_sufficient_credits"


def test_expansion_withdrawal_may_span_owners_before_cutoff():
    """Ordinary withdrawals are plain FIFO across owners before the cutoff."""
    pool = CapacityPool()
    pool.deposit(make_credit(amount_mt=1.0, vintage_year=2018, owner_id="owner-a"))
    pool.deposit(make_credit(amount_mt=1.0, vintage_year=2020, owner_id="owner-b"))

    result = pool.try_withdraw(2.0, region_tag=None, product="steel", owner_id="owner-a", year=PRE_CUTOFF_YEAR)

    assert result.granted is True
    assert result.attributed_owner_id is None
    assert {c.owner_id for c in result.credits_consumed} == {"owner-a", "owner-b"}


def test_seed_from_derives_cluster_tags_orders_by_vintage_and_counts_unowned(caplog):
    """Seeding tags key-province entries with their cluster name, age-orders the queue, and warns on blanks."""
    pool = CapacityPool()
    with caplog.at_level("WARNING", logger="steelo.capacity_policy.pool"):
        pool.seed_from(
            [
                SeedEntry(amount_mt=1.0, vintage_year=2021, geo_key="CHN:CN-SD", owner_id="owner-a", product="steel"),
                SeedEntry(amount_mt=2.0, vintage_year=2018, geo_key="CHN:CN-HE", owner_id="owner-a", product="steel"),
                SeedEntry(amount_mt=1.5, vintage_year=2020, geo_key="CHN:CN-TJ", owner_id="owner-b", product="steel"),
                SeedEntry(amount_mt=0.5, vintage_year=2019, geo_key="CHN", owner_id=None, product="iron"),
            ],
            key_regions={"CHN:CN-HE": "Jing-Jin-Ji", "CHN:CN-TJ": "Jing-Jin-Ji"},
        )

    assert [c.vintage_year for c in pool.snapshot()] == [2018, 2019, 2020, 2021]
    assert pool.total() == 5.0
    # Member provinces share the cluster tag, so a Hebei build can spend a Tianjin credit
    assert pool.total_by_tag() == {"Jing-Jin-Ji": 3.5, None: 1.5}
    assert "1 of 4 seeded credit(s) name no owner" in caplog.text
