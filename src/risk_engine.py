"""리스크 엔진: AI(학습 모델 · Jev)가 무엇을 제안하든 최종 거부권을 가진다.

노출은 모두 '달러 기준 순노출 / 자기자본'으로 표현한다.
    0 = 중립, 1 = 자기자본만큼 BTC 롱, -1 = 자기자본만큼 숏
규칙 수치는 config/risk_rules.yaml에서만 바꾼다.

거래소 API 키에는 입금 · 출금 · 이체 권한을 주지 않는다. 물렸을 때 돈을 더 넣어
물타기하는 길을 막아 둔 워뇨띠의 원칙을 코드가 아니라 권한으로 강제하는 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import yaml

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RiskRules:
    max_loss_per_position: float = 0.10
    max_net_exposure: float = 1.0
    min_net_exposure: float = -1.0
    target_daily_vol: float = 0.03
    daily_loss_limit: float = 0.05
    min_confidence: float = 0.6

    @classmethod
    def load(cls, path: Path | None = None) -> "RiskRules":
        path = path or ROOT / "config" / "risk_rules.yaml"
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: float(v) for k, v in data.items() if k in known})


@dataclass(frozen=True)
class Proposal:
    target_exposure: float  # 모델이 원하는 순노출
    confidence: float       # 0~1, 모델 · Jev 확신도


@dataclass(frozen=True)
class AccountState:
    now: datetime
    equity: float                  # 자기자본 (계좌 통화)
    current_exposure: float        # 현재 순노출
    day_pnl: float                 # 오늘(UTC) 손익 (계좌 통화)
    realized_daily_vol: float      # 최근 실현 일간 변동성 (0.04 = 4%)
    locked_until: datetime | None = None


@dataclass
class Decision:
    action: Literal["execute", "hold"]
    target_exposure: float
    isolated_margin_cap: float     # 이 포지션에 걸 수 있는 격리 증거금 상한 = 최대 손실
    locked_until: datetime | None
    reasons: list[str] = field(default_factory=list)


def next_utc_midnight(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def reduces_risk(current: float, target: float) -> bool:
    """같은 방향 안에서 크기만 줄이거나 0으로 가는 경우만 '위험 축소'로 본다."""
    if target == 0:
        return True
    return abs(target) <= abs(current) and (target > 0) == (current > 0)


def evaluate(proposal: Proposal, state: AccountState, rules: RiskRules) -> Decision:
    reasons: list[str] = []
    target = proposal.target_exposure
    locked_until = state.locked_until
    margin_cap = max(state.equity, 0.0) * rules.max_loss_per_position

    # 1) 하루 손실 한도 → 다음 UTC 0시까지 잠금
    if state.equity > 0 and state.day_pnl <= -rules.daily_loss_limit * state.equity:
        lock = next_utc_midnight(state.now)
        locked_until = max(locked_until, lock) if locked_until else lock
        reasons.append(f"오늘 손실 {state.day_pnl / state.equity:.1%}: 한도 {rules.daily_loss_limit:.0%} 도달")
    locked = locked_until is not None and state.now < locked_until

    # 2) 변동성이 클수록 비중 축소
    if rules.target_daily_vol > 0 and state.realized_daily_vol > rules.target_daily_vol:
        scale = rules.target_daily_vol / state.realized_daily_vol
        target *= scale
        reasons.append(
            f"변동성 {state.realized_daily_vol:.1%} > 목표 {rules.target_daily_vol:.1%}: 노출 x{scale:.2f}"
        )

    # 3) 순노출 상 · 하한
    clipped = min(max(target, rules.min_net_exposure), rules.max_net_exposure)
    if clipped != target:
        reasons.append(f"순노출 {target:+.2f} → 한도 {clipped:+.2f}")
        target = clipped

    # 4) 잠금 중이거나 확신도가 낮으면 위험을 줄이는 방향만 허용
    blockers = []
    if locked:
        blockers.append(f"잠금 중 ({locked_until:%Y-%m-%d %H:%M} UTC까지)")
    if proposal.confidence < rules.min_confidence:
        blockers.append(f"확신도 {proposal.confidence:.2f} < 기준 {rules.min_confidence:.2f}")
    if blockers and not reduces_risk(state.current_exposure, target):
        reasons += blockers + ["위험을 늘리는 주문이라 관망"]
        return Decision("hold", state.current_exposure, margin_cap, locked_until, reasons)

    return Decision("execute", target, margin_cap, locked_until, reasons)
