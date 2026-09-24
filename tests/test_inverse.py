import math

import pytest

from src.inverse import InversePosition, bucketize, exposure_ratio

P = 10_000.0


def ratio(wallet_btc, qty, entry=P, price=P, sym="XBTUSD"):
    pos = InversePosition()
    if qty:
        pos.apply_fill(qty, entry)
    return exposure_ratio(wallet_btc, {sym: pos}, {sym: price}, ref_price=price)


def test_no_position_is_one_x_long():
    assert ratio(1.0, 0) == pytest.approx(1.0)


def test_one_x_short_is_usd_neutral_even_after_price_moves():
    assert ratio(1.0, -P) == pytest.approx(0.0)
    assert ratio(1.0, -P, price=2 * P) == pytest.approx(0.0, abs=1e-12)
    assert ratio(1.0, -P, price=0.5 * P) == pytest.approx(0.0, abs=1e-12)


def test_half_short_is_half_long():
    assert ratio(1.0, -0.5 * P) == pytest.approx(0.5)


def test_one_x_long_position_is_two_x():
    assert ratio(1.0, P) == pytest.approx(2.0)


def test_inverse_average_entry_is_harmonic():
    pos = InversePosition()
    pos.apply_fill(10_000, 10_000)
    pos.apply_fill(10_000, 20_000)
    assert pos.avg_entry == pytest.approx(20_000 / 1.5)


def test_realized_pnl_long_and_short():
    long = InversePosition()
    long.apply_fill(10_000, 10_000)
    assert long.apply_fill(-10_000, 20_000) == pytest.approx(0.5)
    short = InversePosition()
    short.apply_fill(-10_000, 10_000)
    assert short.apply_fill(10_000, 5_000) == pytest.approx(1.0)
    assert not long.is_open and not short.is_open


def test_flip_opens_new_position_at_fill_price():
    pos = InversePosition()
    pos.apply_fill(100, 10_000)
    realized = pos.apply_fill(-300, 11_000)
    assert realized == pytest.approx(100 * (1 / 10_000 - 1 / 11_000))
    assert pos.qty == pytest.approx(-200)
    assert pos.avg_entry == pytest.approx(11_000)


def test_zero_or_unknown_equity_gives_nan():
    assert math.isnan(exposure_ratio(0.0, {}, {}, ref_price=P))
    assert math.isnan(exposure_ratio(math.nan, {}, {}, ref_price=P))


@pytest.mark.parametrize(
    "r,expected", [(0.1, 0.0), (0.3, 0.5), (0.74, 0.5), (0.76, 1.0), (-0.6, -0.5), (9.0, 3.0)]
)
def test_bucketize(r, expected):
    assert bucketize(r) == expected
