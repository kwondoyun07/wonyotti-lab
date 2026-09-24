from datetime import datetime, timezone

import pytest

from src.risk_engine import AccountState, Proposal, RiskRules, evaluate

RULES = RiskRules()  # 기본값 = config/risk_rules.yaml 초기값
NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def state(**kw):
    base = dict(now=NOW, equity=1000.0, current_exposure=0.0, day_pnl=0.0, realized_daily_vol=0.02)
    base.update(kw)
    return AccountState(**base)


def test_exposure_is_clipped():
    d = evaluate(Proposal(3.0, 0.9), state(), RULES)
    assert d.action == "execute" and d.target_exposure == pytest.approx(1.0)


def test_high_volatility_scales_down():
    d = evaluate(Proposal(1.0, 0.9), state(realized_daily_vol=0.06), RULES)
    assert d.target_exposure == pytest.approx(0.5)


def test_low_confidence_blocks_increase_but_allows_reduction():
    blocked = evaluate(Proposal(0.8, 0.3), state(current_exposure=0.2), RULES)
    assert blocked.action == "hold" and blocked.target_exposure == pytest.approx(0.2)
    allowed = evaluate(Proposal(0.3, 0.3), state(current_exposure=0.8), RULES)
    assert allowed.action == "execute" and allowed.target_exposure == pytest.approx(0.3)


def test_flip_is_not_risk_reduction():
    d = evaluate(Proposal(-0.5, 0.3), state(current_exposure=0.8), RULES)
    assert d.action == "hold"


def test_daily_loss_locks_until_next_utc_midnight():
    d = evaluate(Proposal(0.8, 0.95), state(day_pnl=-60.0), RULES)
    assert d.action == "hold"
    assert d.locked_until == datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)
    later = evaluate(Proposal(0.8, 0.95), state(now=datetime(2026, 9, 24, 20, tzinfo=timezone.utc),
                                                 locked_until=d.locked_until), RULES)
    assert later.action == "hold"
    next_day = evaluate(Proposal(0.8, 0.95), state(now=datetime(2026, 9, 25, 1, tzinfo=timezone.utc),
                                                    locked_until=d.locked_until), RULES)
    assert next_day.action == "execute"


def test_isolated_margin_cap_is_max_loss():
    assert evaluate(Proposal(0.5, 0.9), state(), RULES).isolated_margin_cap == pytest.approx(100.0)


def test_rules_load_from_yaml():
    assert RiskRules.load() == RiskRules()
