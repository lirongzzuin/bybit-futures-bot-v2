#!/usr/bin/env python3
"""
v10.2 생존 최적화 백테스트 비교
- v10.0 (기존) vs v10.1 (SL 확대) vs v10.2 (생존 최적화)
- BTCUSDT 15분봉 합성 데이터로 검증
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Test data directory
os.environ['DATA_DIR'] = '/tmp/test_trading'
os.makedirs('/tmp/test_trading/logs', exist_ok=True)
os.makedirs('/tmp/test_trading/db', exist_ok=True)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from backtest.engine import BacktestEngine, BacktestConfig
from strategy.final_strategy import generate_signals, calc_atr, OPTIMAL_PARAMS


def generate_synthetic_btc_data(days=90, volatility="mixed"):
    """합성 BTC 15분봉 데이터 생성 (다양한 시장 상황 포함)"""
    np.random.seed(42)
    bars_per_day = 96
    total_bars = days * bars_per_day

    timestamps = pd.date_range(
        start="2025-01-01", periods=total_bars, freq="15min"
    )

    prices = [50000.0]
    volumes = []

    regimes = []
    bars_left = total_bars
    while bars_left > 0:
        regime_len = np.random.randint(bars_per_day * 2, bars_per_day * 10)
        regime_len = min(regime_len, bars_left)
        regime_type = np.random.choice(
            ["uptrend", "downtrend", "sideways", "high_vol", "low_vol"],
            p=[0.25, 0.20, 0.25, 0.15, 0.15]
        )
        regimes.append((regime_type, regime_len))
        bars_left -= regime_len

    for regime_type, regime_len in regimes:
        for _ in range(regime_len):
            last_price = prices[-1]
            if regime_type == "uptrend":
                drift, vol, vol_mult = 0.0003, 0.008, 1.2
            elif regime_type == "downtrend":
                drift, vol, vol_mult = -0.0003, 0.009, 1.3
            elif regime_type == "sideways":
                drift, vol, vol_mult = 0.0, 0.005, 0.8
            elif regime_type == "high_vol":
                drift, vol, vol_mult = np.random.choice([-0.0001, 0.0001]), 0.015, 2.0
            else:
                drift, vol, vol_mult = 0.0001, 0.003, 0.5

            ret = drift + vol * np.random.randn()
            close = last_price * (1 + ret)
            intra_vol = abs(ret) * 0.5 + 0.001
            high = close * (1 + abs(np.random.randn()) * intra_vol)
            low = close * (1 - abs(np.random.randn()) * intra_vol)
            open_price = last_price * (1 + np.random.randn() * 0.001)
            high = max(high, open_price, close)
            low = min(low, open_price, close)
            prices.append(close)
            volumes.append(max(100, np.random.lognormal(10, 1) * vol_mult))

    prices = prices[1:]

    df = pd.DataFrame({
        "timestamp": timestamps[:len(prices)],
        "open": [prices[max(0, i-1)] * (1 + np.random.randn() * 0.001) for i in range(len(prices))],
        "high": [max(prices[i], prices[max(0, i-1)]) * (1 + abs(np.random.randn()) * 0.002) for i in range(len(prices))],
        "low": [min(prices[i], prices[max(0, i-1)]) * (1 - abs(np.random.randn()) * 0.002) for i in range(len(prices))],
        "close": prices,
        "volume": volumes[:len(prices)],
    })
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


def run_v10_0_backtest(df):
    """v10.0 기존 전략 (SL 0.8x, 레버리지 10x)"""
    from config.indicator_params import PROFIT_OPTIMIZATION

    original_opt = {k: (v.copy() if isinstance(v, dict) else v) for k, v in PROFIT_OPTIMIZATION.items()}
    original_params = OPTIMAL_PARAMS.copy()

    PROFIT_OPTIMIZATION["vol_tp_scale"] = {"extreme": 3.0, "high": 2.5, "normal": 2.0, "low": 1.5}
    PROFIT_OPTIMIZATION["vol_sl_scale"] = {"extreme": 1.0, "high": 0.9, "normal": 0.8, "low": 0.7}
    PROFIT_OPTIMIZATION["adx_tp_bonus"] = {"threshold_1": 35, "bonus_1": 0.3, "threshold_2": 45, "bonus_2": 0.5}
    PROFIT_OPTIMIZATION["volume_spike_threshold"] = 1.5
    PROFIT_OPTIMIZATION["volume_spike_score"] = 0.5

    OPTIMAL_PARAMS["sl_atr_mult"] = 0.8
    OPTIMAL_PARAMS["tp_atr_mult"] = 2.0
    OPTIMAL_PARAMS["leverage"] = 10
    OPTIMAL_PARAMS["tf_leverage"] = 10
    OPTIMAL_PARAMS["adx_min"] = 23
    OPTIMAL_PARAMS["rsi_pull_long_hi"] = 43
    OPTIMAL_PARAMS["rsi_pull_short_lo"] = 57
    OPTIMAL_PARAMS["cooldown"] = 6
    OPTIMAL_PARAMS["min_score"] = 2.0
    OPTIMAL_PARAMS["bb_atr_margin"] = 0.35

    config = BacktestConfig(
        initial_capital=10000.0, leverage=10,
        exit_type="fixed_tp_sl", max_bars=48,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, strategy_name="v10.0_baseline", symbol="BTCUSDT")

    for k, v in original_opt.items():
        PROFIT_OPTIMIZATION[k] = v
    for k, v in original_params.items():
        OPTIMAL_PARAMS[k] = v
    return result


def run_v10_1_backtest(df):
    """v10.1 개선 전략 (SL 1.2x, 레버리지 7x)"""
    from config.indicator_params import PROFIT_OPTIMIZATION

    original_opt = {k: (v.copy() if isinstance(v, dict) else v) for k, v in PROFIT_OPTIMIZATION.items()}
    original_params = OPTIMAL_PARAMS.copy()

    PROFIT_OPTIMIZATION["vol_tp_scale"] = {"extreme": 2.5, "high": 2.2, "normal": 2.0, "low": 1.8}
    PROFIT_OPTIMIZATION["vol_sl_scale"] = {"extreme": 1.5, "high": 1.3, "normal": 1.2, "low": 1.0}
    PROFIT_OPTIMIZATION["adx_tp_bonus"] = {"threshold_1": 40, "bonus_1": 0.2, "threshold_2": 50, "bonus_2": 0.3}
    PROFIT_OPTIMIZATION["volume_spike_threshold"] = 2.0
    PROFIT_OPTIMIZATION["volume_spike_score"] = 0.3

    OPTIMAL_PARAMS["sl_atr_mult"] = 1.2
    OPTIMAL_PARAMS["tp_atr_mult"] = 2.0
    OPTIMAL_PARAMS["leverage"] = 7
    OPTIMAL_PARAMS["tf_leverage"] = 7
    OPTIMAL_PARAMS["adx_min"] = 25
    OPTIMAL_PARAMS["rsi_pull_long_hi"] = 40
    OPTIMAL_PARAMS["rsi_pull_short_lo"] = 60
    OPTIMAL_PARAMS["cooldown"] = 8
    OPTIMAL_PARAMS["min_score"] = 2.5
    OPTIMAL_PARAMS["bb_atr_margin"] = 0.2

    config = BacktestConfig(
        initial_capital=10000.0, leverage=7,
        exit_type="trailing", atr_trailing_mult=1.0, max_bars=72,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, strategy_name="v10.1_improved", symbol="BTCUSDT")

    for k, v in original_opt.items():
        PROFIT_OPTIMIZATION[k] = v
    for k, v in original_params.items():
        OPTIMAL_PARAMS[k] = v
    return result


def run_v10_2_backtest(df):
    """v10.2 생존 최적화 (SL 1.5x, 레버리지 5x, 필터 극강화)"""
    config = BacktestConfig(
        initial_capital=10000.0, leverage=5,
        exit_type="trailing", atr_trailing_mult=1.2, max_bars=96,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, strategy_name="v10.2_survival", symbol="BTCUSDT")
    return result


def print_result(result, label):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  총 수익률:     {result.total_return_pct:+.2f}%")
    print(f"  최종 자본:     ${result.final_capital:,.2f}")
    print(f"  총 거래:       {result.total_trades}회")
    print(f"  승률:          {result.win_rate:.1f}%")
    print(f"  수익 팩터:     {result.profit_factor:.2f}")
    print(f"  평균 PnL 비율: {result.avg_pnl_ratio:.2f}")
    print(f"  최대 낙폭:     {result.max_drawdown_pct:.2f}%")
    print(f"  샤프 비율:     {result.sharpe_ratio:.2f}")
    print(f"  평균 체류:     {result.avg_bars_held:.1f}바")

    if result.trades:
        wins = [t for t in result.trades if t.pnl > 0]
        losses = [t for t in result.trades if t.pnl <= 0]
        if wins:
            print(f"  평균 수익:     ${np.mean([t.pnl for t in wins]):.2f}")
        if losses:
            print(f"  평균 손실:     ${np.mean([t.pnl for t in losses]):.2f}")

        reasons = {}
        for t in result.trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        print(f"  청산 사유:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}회")


def main():
    print("=" * 60)
    print("  v10.2 생존 최적화 백테스트 비교")
    print("  합성 BTC 데이터 90일 (다양한 시장 레짐)")
    print("=" * 60)

    print("\n데이터 생성 중...")
    df = generate_synthetic_btc_data(days=90)
    print(f"  총 {len(df)}개 15분봉 (약 {len(df)//96}일)")
    print(f"  가격 범위: ${df['close'].min():,.0f} ~ ${df['close'].max():,.0f}")

    print("\nv10.0 백테스트 실행 중...")
    v10_0 = run_v10_0_backtest(df)

    print("v10.1 백테스트 실행 중...")
    v10_1 = run_v10_1_backtest(df)

    print("v10.2 백테스트 실행 중...")
    v10_2 = run_v10_2_backtest(df)

    print_result(v10_0, "v10.0 기존 (SL 0.8x / Lev 10x / fixed_tp_sl)")
    print_result(v10_1, "v10.1 개선 (SL 1.2x / Lev 7x / trailing)")
    print_result(v10_2, "v10.2 생존 (SL 1.5x / Lev 5x / ADX 28 / score 3.0)")

    # 3-way comparison table
    print(f"\n{'='*60}")
    print(f"  종합 비교 (v10.0 → v10.1 → v10.2)")
    print(f"{'='*60}")
    print(f"  {'지표':<16} {'v10.0':>10} {'v10.1':>10} {'v10.2':>10}")
    print(f"  {'-'*46}")
    print(f"  {'수익률':<16} {v10_0.total_return_pct:>+9.2f}% {v10_1.total_return_pct:>+9.2f}% {v10_2.total_return_pct:>+9.2f}%")
    print(f"  {'거래수':<16} {v10_0.total_trades:>10}  {v10_1.total_trades:>10}  {v10_2.total_trades:>10}")
    print(f"  {'승률':<16} {v10_0.win_rate:>9.1f}% {v10_1.win_rate:>9.1f}% {v10_2.win_rate:>9.1f}%")
    print(f"  {'MDD':<16} {v10_0.max_drawdown_pct:>9.2f}% {v10_1.max_drawdown_pct:>9.2f}% {v10_2.max_drawdown_pct:>9.2f}%")
    print(f"  {'수익팩터':<16} {v10_0.profit_factor:>10.2f} {v10_1.profit_factor:>10.2f} {v10_2.profit_factor:>10.2f}")
    print(f"  {'샤프비율':<16} {v10_0.sharpe_ratio:>10.2f} {v10_1.sharpe_ratio:>10.2f} {v10_2.sharpe_ratio:>10.2f}")

    # Verdict
    improvements = 0
    if v10_2.max_drawdown_pct < v10_1.max_drawdown_pct:
        improvements += 1
    if v10_2.win_rate > v10_1.win_rate:
        improvements += 1
    if v10_2.total_return_pct > v10_1.total_return_pct:
        improvements += 1
    if v10_2.profit_factor > v10_1.profit_factor:
        improvements += 1

    print(f"\nv10.2 vs v10.1: {improvements}/4 지표 개선")
    return improvements >= 2


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
