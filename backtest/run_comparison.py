#!/usr/bin/env python3
"""
v10.1 데이터 기반 개선 백테스트 비교
- v10.0 (기존) vs v10.1 (SL 확대 + 시그널 필터 강화 + 레버리지 축소)
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
    """
    합성 BTC 15분봉 데이터 생성 (다양한 시장 상황 포함)
    - 상승 추세, 하락 추세, 횡보, 고변동성, 저변동성 구간 혼재
    """
    np.random.seed(42)
    bars_per_day = 96  # 15min bars
    total_bars = days * bars_per_day

    timestamps = pd.date_range(
        start="2025-01-01", periods=total_bars, freq="15min"
    )

    prices = [50000.0]
    volumes = []

    # Create regime segments
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
                drift = 0.0003
                vol = 0.008
                vol_mult = 1.2
            elif regime_type == "downtrend":
                drift = -0.0003
                vol = 0.009
                vol_mult = 1.3
            elif regime_type == "sideways":
                drift = 0.0
                vol = 0.005
                vol_mult = 0.8
            elif regime_type == "high_vol":
                drift = np.random.choice([-0.0001, 0.0001])
                vol = 0.015
                vol_mult = 2.0
            else:  # low_vol
                drift = 0.0001
                vol = 0.003
                vol_mult = 0.5

            ret = drift + vol * np.random.randn()
            close = last_price * (1 + ret)

            # OHLC from close
            intra_vol = abs(ret) * 0.5 + 0.001
            high = close * (1 + abs(np.random.randn()) * intra_vol)
            low = close * (1 - abs(np.random.randn()) * intra_vol)
            open_price = last_price * (1 + np.random.randn() * 0.001)

            # Ensure OHLC consistency
            high = max(high, open_price, close)
            low = min(low, open_price, close)

            prices.append(close)
            volumes.append(max(100, np.random.lognormal(10, 1) * vol_mult))

    prices = prices[1:]  # remove seed

    df = pd.DataFrame({
        "timestamp": timestamps[:len(prices)],
        "open": [prices[max(0, i-1)] * (1 + np.random.randn() * 0.001) for i in range(len(prices))],
        "high": [max(prices[i], prices[max(0, i-1)]) * (1 + abs(np.random.randn()) * 0.002) for i in range(len(prices))],
        "low": [min(prices[i], prices[max(0, i-1)]) * (1 - abs(np.random.randn()) * 0.002) for i in range(len(prices))],
        "close": prices,
        "volume": volumes[:len(prices)],
    })

    # Fix OHLC consistency
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)

    return df


def run_v10_0_backtest(df):
    """v10.0 기존 전략 (SL 0.8x, 레버리지 10x)"""
    from config.indicator_params import PROFIT_OPTIMIZATION
    from strategy.final_strategy import OPTIMAL_PARAMS

    # 임시로 v10.0 설정 복원
    original_opt = PROFIT_OPTIMIZATION.copy()
    original_params = OPTIMAL_PARAMS.copy()

    PROFIT_OPTIMIZATION["vol_tp_scale"] = {
        "extreme": 3.0, "high": 2.5, "normal": 2.0, "low": 1.5
    }
    PROFIT_OPTIMIZATION["vol_sl_scale"] = {
        "extreme": 1.0, "high": 0.9, "normal": 0.8, "low": 0.7
    }
    PROFIT_OPTIMIZATION["adx_tp_bonus"] = {
        "threshold_1": 35, "bonus_1": 0.3, "threshold_2": 45, "bonus_2": 0.5
    }
    PROFIT_OPTIMIZATION["volume_spike_threshold"] = 1.5
    PROFIT_OPTIMIZATION["volume_spike_score"] = 0.5

    OPTIMAL_PARAMS["sl_atr_mult"] = 0.8
    OPTIMAL_PARAMS["leverage"] = 10
    OPTIMAL_PARAMS["tf_leverage"] = 10
    OPTIMAL_PARAMS["adx_min"] = 23
    OPTIMAL_PARAMS["rsi_pull_long_hi"] = 43
    OPTIMAL_PARAMS["rsi_pull_short_lo"] = 57
    OPTIMAL_PARAMS["cooldown"] = 6
    OPTIMAL_PARAMS["min_score"] = 2.0
    OPTIMAL_PARAMS["bb_atr_margin"] = 0.35

    config = BacktestConfig(
        initial_capital=10000.0,
        fee_rate=0.0006,
        slippage_rate=0.0005,
        leverage=10,
        exit_type="fixed_tp_sl",
        max_bars=48,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, strategy_name="v10.0_baseline", symbol="BTCUSDT")

    # 복원
    for k, v in original_opt.items():
        PROFIT_OPTIMIZATION[k] = v
    for k, v in original_params.items():
        OPTIMAL_PARAMS[k] = v

    return result


def run_v10_1_backtest(df):
    """v10.1 개선 전략 (SL 1.2x, 레버리지 7x, 필터 강화)"""
    config = BacktestConfig(
        initial_capital=10000.0,
        fee_rate=0.0006,
        slippage_rate=0.0005,
        leverage=7,
        exit_type="trailing",
        atr_trailing_mult=1.0,
        max_bars=72,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, strategy_name="v10.1_improved", symbol="BTCUSDT")
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

        # Exit reason breakdown
        reasons = {}
        for t in result.trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        print(f"  청산 사유:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}회")


def main():
    print("=" * 60)
    print("  v10.1 데이터 기반 개선 백테스트 비교")
    print("  합성 BTC 데이터 90일 (다양한 시장 레짐)")
    print("=" * 60)

    # Generate data
    print("\n데이터 생성 중...")
    df = generate_synthetic_btc_data(days=90)
    print(f"  총 {len(df)}개 15분봉 (약 {len(df)//96}일)")
    print(f"  가격 범위: ${df['close'].min():,.0f} ~ ${df['close'].max():,.0f}")

    # Run backtests
    print("\nv10.0 백테스트 실행 중...")
    v10_0_result = run_v10_0_backtest(df)

    print("v10.1 백테스트 실행 중...")
    v10_1_result = run_v10_1_backtest(df)

    # Print results
    print_result(v10_0_result, "v10.0 기존 (SL 0.8x / Lev 10x / fixed_tp_sl)")
    print_result(v10_1_result, "v10.1 개선 (SL 1.2x / Lev 7x / trailing + 필터 강화)")

    # Comparison
    print(f"\n{'='*60}")
    print(f"  비교 분석 (v10.0 → v10.1)")
    print(f"{'='*60}")

    ret_diff = v10_1_result.total_return_pct - v10_0_result.total_return_pct
    print(f"  수익률 차이:   {ret_diff:+.2f}%p")

    if v10_0_result.win_rate > 0:
        wr_diff = v10_1_result.win_rate - v10_0_result.win_rate
        print(f"  승률 차이:     {wr_diff:+.1f}%p")

    pf_diff = v10_1_result.profit_factor - v10_0_result.profit_factor
    print(f"  수익팩터 차이: {pf_diff:+.2f}")

    mdd_diff = v10_1_result.max_drawdown_pct - v10_0_result.max_drawdown_pct
    print(f"  MDD 차이:      {mdd_diff:+.2f}%p ({'악화' if mdd_diff > 0 else '개선'})")

    sharpe_diff = v10_1_result.sharpe_ratio - v10_0_result.sharpe_ratio
    print(f"  샤프 차이:     {sharpe_diff:+.2f}")

    # v10.1 exit reason analysis
    for label, result in [("v10.0", v10_0_result), ("v10.1", v10_1_result)]:
        if result.trades:
            tp_trades = [t for t in result.trades if t.exit_reason == "take_profit"]
            sl_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
            trail_trades = [t for t in result.trades if t.exit_reason == "trailing_stop"]
            print(f"\n  {label}: TP {len(tp_trades)}회, SL {len(sl_trades)}회, Trailing {len(trail_trades)}회")
            if tp_trades:
                print(f"    TP 평균 수익:    ${np.mean([t.pnl for t in tp_trades]):.2f}")
            if sl_trades:
                print(f"    SL 평균 손실:    ${np.mean([t.pnl for t in sl_trades]):.2f}")
            if trail_trades:
                print(f"    Trail 평균 PnL:  ${np.mean([t.pnl for t in trail_trades]):.2f}")

    print(f"\n✅ 백테스트 완료")

    # Return verdict
    mdd_improved = v10_1_result.max_drawdown_pct < v10_0_result.max_drawdown_pct
    pf_improved = v10_1_result.profit_factor > v10_0_result.profit_factor
    wr_improved = v10_1_result.win_rate > v10_0_result.win_rate

    improvements = sum([mdd_improved, pf_improved, wr_improved])
    if improvements >= 2:
        print(f"📈 v10.1이 v10.0 대비 {improvements}/3 지표 개선")
        return True
    elif mdd_improved:
        print(f"📊 v10.1이 MDD 개선됨 (생존률 향상), 추가 최적화 검토")
        return True
    else:
        print(f"⚠️ v10.1 개선 미흡 — 추가 조정 필요")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
