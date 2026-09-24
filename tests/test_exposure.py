import pandas as pd
import pytest

from src.exposure import build_timeline, prepare_executions, prepare_wallet, summarize

CFG = {
    "executions": {
        "time": "timestamp", "symbol": "symbol", "side": "side", "qty": "lastQty",
        "price": "lastPx", "exec_type": "execType", "order_type": "ordType",
        "trade_exec_types": ["Trade"],
    },
    "wallet": {
        "time": "transactTime", "balance": "walletBalance", "unit_divisor": 100_000_000,
        "currency": "currency", "currency_keep": ["XBt"],
    },
    "inverse_symbol_regex": r"^XBT(USD|[FGHJKMNQUVXZ]\d{2})$",
}


def run(execs, wallet):
    e, stats = prepare_executions(pd.DataFrame(execs), CFG)
    w = prepare_wallet(pd.DataFrame(wallet), CFG)
    return build_timeline(e, w), stats


def test_filters_and_basic_exposure():
    execs = {
        "timestamp": ["2019-01-01T01:00:00Z", "2019-01-01T02:00:00Z", "2019-01-01T03:00:00Z",
                      "2019-01-01T04:00:00Z", "2019-01-01T05:00:00Z"],
        "symbol": ["XBTUSD", "XBTUSD", "XBTUSD", "ETHUSD", "XBTUSD"],
        "side": ["Sell", "Buy", "Buy", "Buy", "Buy"],
        "lastQty": ["4000", "2000", "0", "10", "2000"],
        "lastPx": ["4000", "4000", "4000", "150", "4000"],
        "execType": ["Trade", "Trade", "Trade", "Trade", "Funding"],
        "ordType": ["Limit", "Market", "Limit", "Limit", ""],
    }
    wallet = {"transactTime": ["2019-01-01T00:00:00Z"], "walletBalance": ["100000000"], "currency": ["XBt"]}
    tl, stats = run(execs, wallet)
    assert list(tl["exposure"]) == pytest.approx([0.0, 0.5])  # 1배 숏 → 0.5배 숏
    assert list(tl["exposure_bucket"]) == [0.0, 0.5]
    assert stats["rows_non_trade"] == 1 and stats["rows_invalid"] == 1
    assert stats["excluded_symbols"] == {"ETHUSD": 1}
    assert "지정가 체결 비중: 50.0%" in summarize(tl, stats)


def test_unsettled_realized_pnl_counts_until_wallet_updates():
    execs = {
        "timestamp": ["2019-01-01T01:00:00Z", "2019-01-01T02:00:00Z", "2019-01-01T14:00:00Z"],
        "symbol": ["XBTUSD"] * 3,
        "side": ["Sell", "Buy", "Sell"],
        "lastQty": ["4000", "4000", "4000"],
        "lastPx": ["4000", "2000", "2000"],
        "execType": ["Trade"] * 3,
        "ordType": ["Limit"] * 3,
    }
    wallet = {
        "transactTime": ["2019-01-01T00:00:00Z", "2019-01-01T12:00:00Z", "2019-01-01T12:00:00Z"],
        "walletBalance": ["100000000", "200000000", "999"],
        "currency": ["XBt", "XBt", "USDt"],  # 다른 통화 기록은 걸러져야 함
    }
    tl, _ = run(execs, wallet)
    # 1) 1배 숏 → 0   2) 반값에 청산, 실현 +1 BTC(정산 전) → 지갑 2 BTC, 포지션 없음 → 1
    # 3) 정산 후 지갑 2 BTC, 4000계약 숏 @2000 = 1배 숏 → 0
    assert list(tl["realized_btc"]) == pytest.approx([0.0, 1.0, 0.0])
    assert list(tl["wallet_btc"]) == pytest.approx([1.0, 2.0, 2.0])
    assert list(tl["exposure"]) == pytest.approx([0.0, 1.0, 0.0], abs=1e-12)


def test_missing_column_gives_clear_error():
    with pytest.raises(SystemExit, match="config/columns.yaml"):
        prepare_executions(pd.DataFrame({"foo": [1]}), CFG)
