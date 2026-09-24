"""BitMEX XBT 인버스 계약 수학.

XBT 인버스 계약(XBTUSD 무기한, XBTZ20 같은 분기 선물)은 1계약 = 1달러이고
증거금과 손익이 BTC로 정산된다. 그래서 BTC를 증거금으로 둔 계좌에서
'포지션 없음'은 달러 기준으로 BTC를 1배 들고 있는 상태이고, 1배 숏이 달러 중립이다.
워뇨띠가 "비트멕스에서는 1배 숏을 무포지션 기준점으로 잡는다"고 한 이유다.

이 프로젝트의 학습 라벨인 '노출 비율' = 달러 델타 / 달러 기준 자기자본
    0.0 -> 달러 중립 (BTC 증거금 + 1배 숏)
    0.5 -> BTC 0.5배 롱 (0.5배 숏. 그가 말한 '관망' 자세)
    1.0 -> BTC 1배 롱 (포지션 없음)
    2.0 -> BTC 2배 롱 (1배 롱 포지션)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EPS = 1e-9


@dataclass
class InversePosition:
    """인버스 심볼 하나의 포지션. qty는 계약 수(=달러), 롱 +, 숏 -."""

    qty: float = 0.0
    avg_entry: float = 0.0

    @property
    def is_open(self) -> bool:
        return abs(self.qty) > EPS

    def apply_fill(self, signed_qty: float, price: float) -> float:
        """체결을 반영하고, 이 체결로 실현된 손익(BTC)을 돌려준다."""
        if price <= 0:
            raise ValueError(f"가격은 양수여야 합니다: {price}")
        if abs(signed_qty) <= EPS:
            return 0.0

        if not self.is_open or (self.qty > 0) == (signed_qty > 0):
            # 같은 방향으로 늘리기. 인버스 계약의 평균 진입가는 조화평균:
            # avg = 총계약수 / Σ(계약수 / 가격)
            held = abs(self.qty) / self.avg_entry if self.is_open else 0.0
            self.qty += signed_qty
            self.avg_entry = abs(self.qty) / (held + abs(signed_qty) / price)
            return 0.0

        # 줄이기 · 청산 · 반대로 뒤집기
        closing = min(abs(signed_qty), abs(self.qty))
        sign = 1.0 if self.qty > 0 else -1.0
        realized = sign * closing * (1.0 / self.avg_entry - 1.0 / price)
        flipped = abs(signed_qty) > abs(self.qty) + EPS
        self.qty += signed_qty
        if not self.is_open:
            self.qty, self.avg_entry = 0.0, 0.0
        elif flipped:
            self.avg_entry = price  # 뒤집힌 새 포지션은 이번 체결가에서 시작
        return realized

    def upnl_btc(self, price: float) -> float:
        """미실현 손익(BTC)."""
        if not self.is_open:
            return 0.0
        return self.qty * (1.0 / self.avg_entry - 1.0 / price)

    def usd_delta(self, price: float) -> float:
        """포지션의 달러 델타: 가격이 x% 움직일 때 달러 가치가 (x% * 이 값)만큼 변한다."""
        if not self.is_open:
            return 0.0
        return self.qty * price / self.avg_entry


def exposure_ratio(
    wallet_btc: float,
    positions: dict[str, InversePosition],
    prices: dict[str, float],
    ref_price: float,
) -> float:
    """달러 기준 순노출 / 달러 기준 자기자본. 자기자본이 0 이하이거나 모르면 NaN."""
    open_positions = {s: p for s, p in positions.items() if p.is_open}
    upnl = sum(p.upnl_btc(prices[s]) for s, p in open_positions.items())
    equity_usd = (wallet_btc + upnl) * ref_price
    if not equity_usd > 0:
        return math.nan
    delta_usd = wallet_btc * ref_price + sum(
        p.usd_delta(prices[s]) for s, p in open_positions.items()
    )
    return delta_usd / equity_usd


def bucketize(ratio: float, step: float = 0.5, lo: float = -2.0, hi: float = 3.0) -> float:
    """노출 비율을 step 간격 칸으로 반올림한다 (분류 라벨용)."""
    if math.isnan(ratio):
        return ratio
    return min(max(round(ratio / step) * step, lo), hi)
