#!/usr/bin/env python3
"""
v17.0 vs v17.1 파라미터 비교 백테스트

여러 심볼과 시장 구간에서 v17.0(원래) 파라미터와 v17.1(새) 파라미터를
BacktestEngine에 각각 먹여서 결과를 비교한다.

주의: position_monitor 동적 관리(Stage 1/2)는 엔진에 없으므로
이 백테스트는 순수 진입 품질 + 초기 TP/SL 효과만 본다.
Stage 1/2 변경의 실제 효과는 배포 후 로그로 검증한다.
"""
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from data.fetcher import fetch_ohlcv, get_exchange
from backtest.engine import BacktestEngine, BacktestConfig
from strategy.final_strategy import OPTIMAL_PARAMS
from config.indicator_params import PROFIT_OPTIMIZATION


V17_0_PARAMS = dict(OPTIMAL_PARAMS)
V17_0_PARAMS.update({
    "strategy_type": "momentum_scalper_v17.0",
    "sl_atr_mult": 1.5,
    "min_score": 3.0,
    "min_sl_pct": 0.015,
})
V17_0_VOL_SL = {"extreme": 1.8, "high": 1.6, "normal": 1.5, "low": 1.2}

V17_1_PARAMS = dict(OPTIMAL_PARAMS)  # v17.1 현재값


def _run_with_params(df, params, vol_sl_override=None, symbol="BTCUSDT"):
    """파라미터 세트로 백테스트 실행"""
    from strategy import final_strategy as fs

    saved_params = fs.OPTIMAL_PARAMS
    saved_vol_sl = PROFIT_OPTIMIZATION["vol_sl_scale"]
    try:
        fs.OPTIMAL_PARAMS = params
        if vol_sl_override:
            PROFIT_OPTIMIZATION["vol_sl_scale"] = vol_sl_override
        cfg = BacktestConfig(
            initial_capital=10000.0,
            fee_rate=0.0006,
            slippage_rate=0.0005,
            leverage=5,
            exit_type="fixed_tp_sl",
            max_bars=36,
            use_signal_leverage=True,
            bars_per_day=288,  # 5min: 288 bars/day
        )
        engine = BacktestEngine(cfg)
        return engine.run(df, strategy_name=params["strategy_type"], symbol=symbol)
    finally:
        fs.OPTIMAL_PARAMS = saved_params
        PROFIT_OPTIMIZATION["vol_sl_scale"] = saved_vol_sl


def fetch_period(symbol: str, days_ago_start: int, days_ago_end: int, timeframe="5m"):
    """과거 구간 데이터 fetch (5m 기준)"""
    exchange = get_exchange()
    df = fetch_ohlcv(symbol, timeframe, since_days=days_ago_start, exchange=exchange)
    if df.empty:
        return df
    cutoff_end = datetime.utcnow() - timedelta(days=days_ago_end)
    df = df[df["timestamp"] <= cutoff_end].reset_index(drop=True)
    return df


def summarize(tag, r):
    wr = r.win_rate
    pf = r.profit_factor
    rr = r.avg_pnl_ratio
    return (f"{tag:12s} | trades {r.total_trades:4d} | "
            f"WR {wr:5.1f}% | PF {pf:5.2f} | R:R {rr:5.2f} | "
            f"MDD {r.max_drawdown_pct:5.1f}% | ret {r.total_return_pct:+7.1f}%")


def main():
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
    # 5m data: 구간 x 심볼
    # (라벨, 시작일전, 끝일전)
    periods = [
        ("P1_최근30일",   30, 0),
        ("P2_30~60일전",  60, 30),
        ("P3_60~90일전",  90, 60),
    ]

    print("=" * 100)
    print("v17.0 vs v17.1 파라미터 비교 백테스트 (5분봉)")
    print("=" * 100)

    agg_v170 = []
    agg_v171 = []

    for period_name, start_days, end_days in periods:
        print(f"\n## {period_name}")
        print("-" * 100)
        for sym in symbols:
            try:
                df = fetch_period(sym, start_days, end_days)
                if df.empty or len(df) < 500:
                    print(f"  {sym:10s} | 데이터 부족 ({len(df)})")
                    continue

                r_v170 = _run_with_params(df, V17_0_PARAMS, V17_0_VOL_SL, symbol=sym)
                r_v171 = _run_with_params(df, V17_1_PARAMS, symbol=sym)

                print(f"  [{sym}] 캔들 {len(df)}")
                print(f"    {summarize('v17.0', r_v170)}")
                print(f"    {summarize('v17.1', r_v171)}")

                agg_v170.append((period_name, sym, r_v170))
                agg_v171.append((period_name, sym, r_v171))
            except Exception as e:
                print(f"  {sym:10s} | 오류: {e}")
            time.sleep(0.3)

    # 집계
    print("\n" + "=" * 100)
    print("종합 집계")
    print("=" * 100)

    def agg(rows, label):
        if not rows:
            return
        total_ret = sum(r.total_return_pct for _, _, r in rows)
        avg_wr = sum(r.win_rate for _, _, r in rows) / len(rows)
        avg_pf = sum(r.profit_factor for _, _, r in rows) / len(rows)
        avg_rr = sum(r.avg_pnl_ratio for _, _, r in rows) / len(rows)
        avg_mdd = sum(r.max_drawdown_pct for _, _, r in rows) / len(rows)
        total_trades = sum(r.total_trades for _, _, r in rows)
        print(f"{label:8s} | 총 trades {total_trades:4d} | "
              f"avg WR {avg_wr:5.1f}% | avg PF {avg_pf:5.2f} | "
              f"avg R:R {avg_rr:5.2f} | avg MDD {avg_mdd:5.1f}% | "
              f"합산 ret {total_ret:+7.1f}%")

    agg(agg_v170, "v17.0")
    agg(agg_v171, "v17.1")


if __name__ == "__main__":
    main()
