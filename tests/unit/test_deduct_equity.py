"""Unit tests for PlantGroup.deduct_equity — the sole capex-debit mutation site."""

import logging

import pytest

from steelo.domain.models import PlantGroup


def _make_pg(balance: float = 0.0, gid: str = "pg1") -> PlantGroup:
    pg = PlantGroup(plant_group_id=gid, plants=[])
    pg.balance = balance
    return pg


def test_deduct_equity_decreases_balance_by_amount():
    """Calling deduct_equity reduces pg.balance by exactly amount."""
    pg = _make_pg(balance=1_000_000.0)
    pg.deduct_equity(250_000.0, reason="renovation")
    assert pg.balance == pytest.approx(750_000.0)


def test_deduct_equity_reason_has_no_behavioural_effect():
    """The reason argument is a trace label — it does not change the numeric outcome."""
    pg_a = _make_pg(balance=500.0)
    pg_b = _make_pg(balance=500.0)
    pg_a.deduct_equity(100.0, reason="renovation")
    pg_b.deduct_equity(100.0, reason="switch")
    assert pg_a.balance == pg_b.balance


def test_deduct_equity_allows_negative_balance():
    """Affordability is checked at call sites; deduct_equity itself does not gate."""
    pg = _make_pg(balance=100.0)
    pg.deduct_equity(500.0, reason="expansion")
    assert pg.balance == pytest.approx(-400.0)


def test_deduct_equity_emits_structured_log(caplog):
    """[DEDUCT EQUITY] log line carries reason, amount, plant_group_id, before/after."""
    pg = _make_pg(balance=1_000.0, gid="group_alpha")

    caplog.set_level(logging.INFO, logger="steelo.domain.models.deduct_equity")
    pg.deduct_equity(400.0, reason="switch")

    msgs = [r.getMessage() for r in caplog.records if "[DEDUCT EQUITY]" in r.getMessage()]
    assert len(msgs) == 1
    msg = msgs[0]
    assert "reason=switch" in msg
    assert "amount=400.00" in msg
    assert "plant_group_id=group_alpha" in msg
    assert "balance_before=1000.00" in msg
    assert "balance_after=600.00" in msg
