"""2단계: 체결·지갑내역 → 달러 기준 순노출 타임라인(학습 라벨).

    python -m src.exposure                  # config/columns.yaml 설정대로 전체 실행
    python -m src.exposure --limit 100000   # 시간순 앞쪽 일부만 빠르게 확인

전제: 1단계에서 원본을 parquet으로 변환했고(src.inspect_data convert),
config/columns.yaml의 컬럼 매핑이 실제 컬럼명과 맞아야 한다.

1차 버전의 근사:
- XBT 인버스 계약만 계산한다. ETHUSD(콴토) 등은 제외하고 목록만 출력한다.
- BitMEX 지갑 잔고에는 실현손익이 정산 시점에 들어간다. 정산 전 실현분은
  직전 지갑 기록 이후 누적분(pending)으로 더해 근사한다.
- 가격은 해당 체결가를 쓴다. 체결 사이 구간은 시장 데이터를 붙이는 3단계에서 채운다.
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

from .inverse import InversePosition, bucketize, exposure_ratio

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "columns.yaml"
DEFAULT_INVERSE = r"^XBT(USD|[FGHJKMNQUVXZ]\d{2})$"
EXEC_KEYS = ("time", "symbol", "side", "qty", "price", "exec_type", "order_type")


def load_config(path: Path = CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _check_columns(columns, mapping: dict, what: str) -> None:
    missing = [f"{k} -> '{v}'" for k, v in mapping.items() if v and v not in columns]
    if missing:
        raise SystemExit(
            f"[{what}] 설정한 컬럼이 파일에 없습니다: {', '.join(missing)}\n"
            f"  실제 컬럼: {list(columns)}\n"
            "  config/columns.yaml 매핑을 실제 컬럼명으로 고쳐주세요."
        )


def _read_parquet(path: Path, mapping: dict, what: str) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(
            f"[{what}] {path} 가 없습니다. 먼저 변환하세요:\n"
            "  python -m src.inspect_data convert <원본파일> " + str(path.relative_to(ROOT))
        )
    names = pq.read_schema(path).names
    _check_columns(names, mapping, what)
    wanted = sorted({v for v in mapping.values() if v})
    return pd.read_parquet(path, columns=wanted)


def prepare_executions(raw: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """원본 체결내역 → 시간순 정렬된 인버스 계약 체결 (signed_qty 포함)."""
    c = cfg["executions"]
    mapping = {k: c.get(k) for k in EXEC_KEYS}
    _check_columns(raw.columns, mapping, "executions")

    df = pd.DataFrame(
        {
            "time": pd.to_datetime(raw[c["time"]], utc=True, errors="coerce"),
            "symbol": raw[c["symbol"]].astype("string"),
            "side": raw[c["side"]].astype("string"),
            "qty": pd.to_numeric(raw[c["qty"]], errors="coerce"),
            "price": pd.to_numeric(raw[c["price"]], errors="coerce"),
        }
    )
    df["exec_type"] = raw[c["exec_type"]].astype("string") if c.get("exec_type") else "Trade"
    df["order_type"] = raw[c["order_type"]].astype("string") if c.get("order_type") else pd.NA

    stats: dict = {"rows_total": len(df)}
    is_trade = df["exec_type"].isin(set(c.get("trade_exec_types") or ["Trade"])).fillna(False)
    stats["rows_non_trade"] = int((~is_trade).sum())
    df = df[is_trade.to_numpy(dtype=bool)]

    valid = (
        df["time"].notna()
        & df["qty"].gt(0)
        & df["price"].gt(0)
        & df["side"].isin(["Buy", "Sell"]).fillna(False)
    )
    stats["rows_invalid"] = int((~valid).sum())
    df = df[valid.to_numpy(dtype=bool)]

    pattern = re.compile(cfg.get("inverse_symbol_regex") or DEFAULT_INVERSE)
    is_inverse = df["symbol"].map(lambda s: bool(pattern.match(str(s)))).to_numpy(dtype=bool)
    stats["excluded_symbols"] = df.loc[~is_inverse, "symbol"].value_counts().to_dict()
    df = df[is_inverse].copy()

    is_buy = (df["side"] == "Buy").to_numpy(dtype=bool)
    df["signed_qty"] = np.where(is_buy, df["qty"], -df["qty"])
    df = df.sort_values("time", kind="stable").reset_index(drop=True)
    stats["rows_used"] = len(df)
    return df, stats


def prepare_wallet(raw: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """원본 지갑내역 → 시간순 BTC 잔고."""
    w = cfg["wallet"]
    mapping = {k: w.get(k) for k in ("time", "balance", "currency")}
    _check_columns(raw.columns, mapping, "wallet")
    df = raw
    if w.get("currency") and w.get("currency_keep"):
        keep = df[w["currency"]].astype("string").isin(set(w["currency_keep"])).fillna(False)
        df = df[keep.to_numpy(dtype=bool)]
    divisor = float(w.get("unit_divisor") or 1)
    out = pd.DataFrame(
        {
            "wallet_time": pd.to_datetime(df[w["time"]], utc=True, errors="coerce"),
            "wallet_btc": pd.to_numeric(df[w["balance"]], errors="coerce") / divisor,
        }
    ).dropna()
    return out.sort_values("wallet_time", kind="stable").reset_index(drop=True)


def build_timeline(execs: pd.DataFrame, wallet: pd.DataFrame, bucket_step: float = 0.5) -> pd.DataFrame:
    """체결 하나마다 포지션을 갱신하고 그 직후의 달러 기준 노출 비율을 기록한다."""
    merged = pd.merge_asof(
        execs, wallet, left_on="time", right_on="wallet_time", direction="backward"
    )
    positions: dict[str, InversePosition] = {}
    last_price: dict[str, float] = {}
    pending_realized = 0.0  # 직전 지갑 기록 이후 실현됐지만 아직 정산 안 된 손익
    last_wallet_time = None

    pos_contracts, wallet_eff, upnls, realized_col, exposures = [], [], [], [], []
    for sym, sq, px, w_btc, w_time in zip(
        merged["symbol"], merged["signed_qty"], merged["price"],
        merged["wallet_btc"], merged["wallet_time"],
    ):
        if not ((pd.isna(w_time) and pd.isna(last_wallet_time)) or w_time == last_wallet_time):
            pending_realized = 0.0  # 새 지갑 기록 = 정산 반영 → 대기분 초기화
            last_wallet_time = w_time

        pos = positions.setdefault(sym, InversePosition())
        realized = pos.apply_fill(float(sq), float(px))
        pending_realized += realized
        last_price[sym] = float(px)

        w_eff = float(w_btc) + pending_realized if not pd.isna(w_btc) else math.nan
        open_pos = {s: p for s, p in positions.items() if p.is_open}
        pos_contracts.append(sum(p.qty for p in open_pos.values()))
        wallet_eff.append(w_eff)
        upnls.append(sum(p.upnl_btc(last_price[s]) for s, p in open_pos.items()))
        realized_col.append(realized)
        exposures.append(exposure_ratio(w_eff, open_pos, last_price, ref_price=float(px)))

    out = merged[["time", "symbol", "signed_qty", "price", "order_type"]].copy()
    out["pos_contracts"] = pos_contracts
    out["wallet_btc"] = wallet_eff
    out["upnl_btc"] = upnls
    out["realized_btc"] = realized_col
    out["exposure"] = exposures
    out["exposure_bucket"] = [bucketize(x, bucket_step) for x in exposures]
    return out


def summarize(tl: pd.DataFrame, stats: dict) -> str:
    lines = []
    if len(tl):
        lines.append(f"기간: {tl['time'].min()} ~ {tl['time'].max()}")
    lines.append(
        f"사용 체결 {stats['rows_used']:,}건 / 전체 {stats['rows_total']:,}건 "
        f"(Trade 아님 {stats['rows_non_trade']:,}, 이상값 {stats['rows_invalid']:,})"
    )
    if stats.get("excluded_symbols"):
        top = ", ".join(f"{k}:{v:,}" for k, v in list(stats["excluded_symbols"].items())[:10])
        lines.append(f"제외한 심볼(XBT 인버스 아님): {top}")
    if tl["order_type"].notna().any():
        limit_share = (tl["order_type"] == "Limit").mean()
        lines.append(f"지정가 체결 비중: {limit_share:.1%}")

    valid = tl.dropna(subset=["exposure"])
    if len(valid) > 1:
        seconds = (valid["time"].shift(-1) - valid["time"]).dt.total_seconds().fillna(0)
        dist = seconds.groupby(valid["exposure_bucket"]).sum()
        if dist.sum() > 0:
            dist = dist / dist.sum()
            lines.append("시간가중 노출 분포 (0 = 달러 중립, 1 = 포지션 없음/BTC 1배 롱):")
            for bucket, share in dist.sort_index().items():
                lines.append(f"  {bucket:+.1f}배 {share:6.1%} {'#' * int(round(share * 50))}")
        changes = int((valid["exposure_bucket"].diff().fillna(0) != 0).sum())
        lines.append(f"노출 칸이 바뀐 횟수: {changes:,}")
    missing_wallet = int(tl["exposure"].isna().sum())
    if missing_wallet:
        lines.append(f"노출 계산 불가(지갑 기록 이전이거나 자기자본 0 이하): {missing_wallet:,}건")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--limit", type=int, default=None, help="시간순 앞쪽 N건만 계산")
    ap.add_argument("--bucket-step", type=float, default=0.5)
    args = ap.parse_args()

    cfg = load_config(args.config)
    paths = cfg["paths"]
    raw_exec = _read_parquet(
        ROOT / paths["executions"], {k: cfg["executions"].get(k) for k in EXEC_KEYS}, "executions"
    )
    raw_wallet = _read_parquet(
        ROOT / paths["wallet"],
        {k: cfg["wallet"].get(k) for k in ("time", "balance", "currency")},
        "wallet",
    )

    execs, stats = prepare_executions(raw_exec, cfg)
    if args.limit:
        execs = execs.head(args.limit)
        stats["rows_used"] = len(execs)
    wallet = prepare_wallet(raw_wallet, cfg)
    if wallet.empty:
        raise SystemExit("지갑 잔고 기록이 비었습니다. wallet 매핑(time/balance/currency)을 확인하세요.")

    timeline = build_timeline(execs, wallet, args.bucket_step)
    out_path = ROOT / paths["output"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    timeline.to_parquet(out_path, index=False)

    print(summarize(timeline, stats))
    print(f"\n저장: {out_path.relative_to(ROOT)}  ({len(timeline):,}행)")
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(timeline.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
