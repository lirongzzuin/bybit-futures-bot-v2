#!/usr/bin/env python3
"""
v11.0 Aggressive Profit Runner — 다기간 백테스트 비교
- 상승장, 하락장, 횡보장, 고변동장, 혼합장 각각 테스트
- v10.2 (보수적) vs v11.0 (공격적) 비교
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ['DATA_DIR'] = '/tmp/test_trading'
os.makedirs('/tmp/test_trading/logs', exist_ok=True)
os.makedirs('/tmp/test_trading/db', exist_ok=True)

import pandas as pd
import numpy as np

from backtest.engine import BacktestEngine, BacktestConfig
from strategy.final_strategy import OPTIMAL_PARAMS


def generate_market_data(days=90, market_type="mixed", seed=42):
    """다양한 시장 상황별 합성 데이터 생성"""
    np.random.seed(seed)
    bars_per_day = 96
    total_bars = days * bars_per_day
    timestamps = pd.date_range(start="2025-01-01", periods=total_bars, freq="15min")

    prices = [50000.0]
    volumes = []

    regime_configs = {
        "bull": {"drift": 0.0005, "vol": 0.008, "vol_mult": 1.5},
        "bear": {"drift": -0.0005, "vol": 0.009, "vol_mult": 1.3},
        "sideways": {"drift": 0.0, "vol": 0.004, "vol_mult": 0.7},
        "high_vol": {"drift": 0.0002, "vol": 0.018, "vol_mult": 2.5},
        "mixed": None,  # special handling
    }

    if market_type == "mixed":
        regimes = []
        bars_left = total_bars
        while bars_left > 0:
            regime_len = np.random.randint(bars_per_day * 3, bars_per_day * 15)
            regime_len = min(regime_len, bars_left)
            rtype = np.random.choice(["bull", "bear", "sideways", "high_vol"],
                                      p=[0.30, 0.25, 0.25, 0.20])
            regimes.append((rtype, regime_len))
            bars_left -= regime_len
    else:
        regimes = [(market_type, total_bars)]

    for rtype, regime_len in regimes:
        cfg = regime_configs[rtype]
        for _ in range(regime_len):
            last_price = prices[-1]
            drift = cfg["drift"]
            vol = cfg["vol"]

            # 가끔 급등/급락 이벤트
            if np.random.random() < 0.002:
                drift += np.random.choice([-0.02, 0.02])

            ret = drift + vol * np.random.randn()
            close = last_price * (1 + ret)
            intra_vol = abs(ret) * 0.5 + 0.001
            high = close * (1 + abs(np.random.randn()) * intra_vol)
            low = close * (1 - abs(np.random.randn()) * intra_vol)
            open_price = last_price * (1 + np.random.randn() * 0.001)
            high = max(high, open_price, close)
            low = min(low, open_price, close)
            prices.append(close)
            volumes.append(max(100, np.random.lognormal(10, 1) * cfg["vol_mult"]))

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


def run_v10_2_backtest(df):
    """v10.2 보수적 전략"""
    from config.indicator_params import PROFIT_OPTIMIZATION
    original_opt = {k: (v.copy() if isinstance(v, dict) else v) for k, v in PROFIT_OPTIMIZATION.items()}
    original_params = OPTIMAL_PARAMS.copy()

    PROFIT_OPTIMIZATION["vol_tp_scale"] = {"extreme": 3.0, "high": 2.7, "normal": 2.5, "low": 2.0}
    PROFIT_OPTIMIZATION["vol_sl_scale"] = {"extreme": 2.0, "high": 1.7, "normal": 1.5, "low": 1.2}
    PROFIT_OPTIMIZATION["adx_tp_bonus"] = {"threshold_1": 45, "bonus_1": 0.2, "threshold_2": 55, "bonus_2": 0.3}
    PROFIT_OPTIMIZATION["volume_spike_threshold"] = 2.5
    OPTIMAL_PARAMS["sl_atr_mult"] = 1.5
    OPTIMAL_PARAMS["tp_atr_mult"] = 2.5
    OPTIMAL_PARAMS["leverage"] = 5
    OPTIMAL_PARAMS["tf_leverage"] = 5
    OPTIMAL_PARAMS["adx_min"] = 28
    OPTIMAL_PARAMS["rsi_pull_long_hi"] = 38
    OPTIMAL_PARAMS["rsi_pull_short_lo"] = 62
    OPTIMAL_PARAMS["cooldown"] = 10
    OPTIMAL_PARAMS["min_score"] = 3.0
    OPTIMAL_PARAMS["bb_atr_margin"] = 0.15

    config = BacktestConfig(
        initial_capital=10000.0, leverage=5,
        exit_type="trailing", atr_trailing_mult=1.2, max_bars=96,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, strategy_name="v10.2_conservative", symbol="BTCUSDT")

    for k, v in original_opt.items():
        PROFIT_OPTIMIZATION[k] = v
    for k, v in original_params.items():
        OPTIMAL_PARAMS[k] = v
    return result


def run_v11_0_backtest(df):
    """v11.0 공격적 전략 (현재 파라미터 그대로)"""
    config = BacktestConfig(
        initial_capital=10000.0, leverage=10,
        exit_type="trailing", atr_trailing_mult=0.8, max_bars=72,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, strategy_name="v11.0_aggressive", symbol="BTCUSDT")
    return result


def print_result(result, label):
    print(f"  {'─'*50}")
    print(f"  {label}")
    print(f"  수익률: {result.total_return_pct:+.1f}% | "
          f"거래: {result.total_trades}회 | "
          f"승률: {result.win_rate:.0f}% | "
          f"MDD: {result.max_drawdown_pct:.1f}% | "
          f"PF: {result.profit_factor:.2f}")
    if result.trades:
        wins = [t for t in result.trades if t.pnl > 0]
        losses = [t for t in result.trades if t.pnl <= 0]
        reasons = {}
        for t in result.trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        r_str = ", ".join(f"{k}:{v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1]))
        avg_win = np.mean([t.pnl for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl for t in losses]) if losses else 0
        print(f"  평균 수익: ${avg_win:.0f} | 평균 손실: ${avg_loss:.0f} | {r_str}")


def main():
    print("=" * 60)
    print("  v11.0 Aggressive Profit Runner — 다기간 백테스트")
    print("=" * 60)

    market_types = [
        ("bull", "상승장 (90일)", 42),
        ("bear", "하락장 (90일)", 42),
        ("sideways", "횡보장 (90일)", 42),
        ("high_vol", "고변동장 (90일)", 42),
        ("mixed", "혼합장 A (90일)", 42),
        ("mixed", "혼합장 B (90일)", 123),
        ("mixed", "혼합장 C (90일)", 789),
    ]

    total_v102 = {"return": 0, "trades": 0, "wins": 0}
    total_v110 = {"return": 0, "trades": 0, "wins": 0}

    for mtype, label, seed in market_types:
        print(f"\n{'='*60}")
        print(f"  [{label}]")
        df = generate_market_data(days=90, market_type=mtype, seed=seed)
        print(f"  데이터: {len(df)}개 15분봉 | 가격: ${df['close'].min():,.0f}~${df['close'].max():,.0f}")

        v102 = run_v10_2_backtest(df)
        v110 = run_v11_0_backtest(df)

        print_result(v102, "v10.2 보수적")
        print_result(v110, "v11.0 공격적")

        total_v102["return"] += v102.total_return_pct
        total_v102["trades"] += v102.total_trades
        total_v102["wins"] += v102.winning_trades
        total_v110["return"] += v110.total_return_pct
        total_v110["trades"] += v110.total_trades
        total_v110["wins"] += v110.winning_trades

    # 종합
    n = len(market_types)
    print(f"\n{'='*60}")
    print(f"  종합 비교 ({n}개 시장)")
    print(f"{'='*60}")
    v102_wr = total_v102["wins"] / max(total_v102["trades"], 1) * 100
    v110_wr = total_v110["wins"] / max(total_v110["trades"], 1) * 100
    print(f"  v10.2: 평균 수익률 {total_v102['return']/n:+.1f}% | 총 거래 {total_v102['trades']}회 | 승률 {v102_wr:.0f}%")
    print(f"  v11.0: 평균 수익률 {total_v110['return']/n:+.1f}% | 총 거래 {total_v110['trades']}회 | 승률 {v110_wr:.0f}%")
    print(f"  개선: {(total_v110['return']-total_v102['return'])/n:+.1f}%p 수익률 | {v110_wr-v102_wr:+.0f}%p 승률")


if __name__ == "__main__":
    main()
