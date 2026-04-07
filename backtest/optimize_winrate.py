#!/usr/bin/env python3
"""
승률 60%+ 달성을 위한 파라미터 조합 탐색
- ADX, RSI, min_score, SL/TP, BB 마진 조합별 백테스트
- 승률 60% 이상 + 수익률 양수인 조합 찾기
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ['DATA_DIR'] = '/tmp/test_trading'
os.makedirs('/tmp/test_trading/logs', exist_ok=True)
os.makedirs('/tmp/test_trading/db', exist_ok=True)

import numpy as np
import pandas as pd
from itertools import product
from backtest.engine import BacktestEngine, BacktestConfig
from strategy.final_strategy import OPTIMAL_PARAMS, generate_signals
from config.indicator_params import PROFIT_OPTIMIZATION


def generate_market_data(days=90, seed=42):
    np.random.seed(seed)
    bars_per_day = 96
    total_bars = days * bars_per_day
    timestamps = pd.date_range(start="2025-01-01", periods=total_bars, freq="15min")
    prices = [50000.0]
    volumes = []
    regimes = []
    bars_left = total_bars
    while bars_left > 0:
        regime_len = np.random.randint(bars_per_day * 3, bars_per_day * 15)
        regime_len = min(regime_len, bars_left)
        rtype = np.random.choice(["bull", "bear", "sideways", "high_vol"],
                                  p=[0.30, 0.25, 0.25, 0.20])
        regimes.append((rtype, regime_len))
        bars_left -= regime_len

    cfgs = {
        "bull": (0.0005, 0.008, 1.5),
        "bear": (-0.0005, 0.009, 1.3),
        "sideways": (0.0, 0.004, 0.7),
        "high_vol": (0.0002, 0.018, 2.5),
    }
    for rtype, rlen in regimes:
        drift, vol, vm = cfgs[rtype]
        for _ in range(rlen):
            ret = drift + vol * np.random.randn()
            close = prices[-1] * (1 + ret)
            prices.append(close)
            volumes.append(max(100, np.random.lognormal(10, 1) * vm))

    prices = prices[1:]
    n = len(prices)
    df = pd.DataFrame({
        "timestamp": timestamps[:n],
        "open": [prices[max(0, i-1)] * (1 + np.random.randn() * 0.001) for i in range(n)],
        "high": [max(prices[i], prices[max(0, i-1)]) * (1 + abs(np.random.randn()) * 0.002) for i in range(n)],
        "low": [min(prices[i], prices[max(0, i-1)]) * (1 - abs(np.random.randn()) * 0.002) for i in range(n)],
        "close": prices,
        "volume": volumes[:n],
    })
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


def test_params(df, adx_min, rsi_hi, sl_mult, tp_mult, min_score, bb_margin):
    """특정 파라미터 조합으로 백테스트"""
    orig = OPTIMAL_PARAMS.copy()
    orig_opt = {k: (v.copy() if isinstance(v, dict) else v) for k, v in PROFIT_OPTIMIZATION.items()}

    OPTIMAL_PARAMS["adx_min"] = adx_min
    OPTIMAL_PARAMS["rsi_pull_long_hi"] = rsi_hi
    OPTIMAL_PARAMS["rsi_pull_short_lo"] = 100 - rsi_hi  # 대칭
    OPTIMAL_PARAMS["sl_atr_mult"] = sl_mult
    OPTIMAL_PARAMS["tp_atr_mult"] = tp_mult
    OPTIMAL_PARAMS["min_score"] = min_score
    OPTIMAL_PARAMS["bb_atr_margin"] = bb_margin

    # SL 적응 범위도 맞춤
    PROFIT_OPTIMIZATION["vol_sl_scale"]["normal"] = sl_mult
    PROFIT_OPTIMIZATION["vol_tp_scale"]["normal"] = tp_mult

    config = BacktestConfig(
        initial_capital=10000.0, leverage=10,
        exit_type="fixed_tp_sl", max_bars=48,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, strategy_name="optimize", symbol="BTCUSDT")

    for k, v in orig.items():
        OPTIMAL_PARAMS[k] = v
    for k, v in orig_opt.items():
        PROFIT_OPTIMIZATION[k] = v

    return result


def main():
    print("=" * 70)
    print("  승률 60%+ 파라미터 최적화 탐색")
    print("=" * 70)

    # 여러 시장에서 테스트
    seeds = [42, 123, 789]
    dfs = []
    for seed in seeds:
        dfs.append(generate_market_data(days=90, seed=seed))
    print(f"  {len(seeds)}개 시장 데이터 생성 완료")

    # 파라미터 조합
    param_grid = {
        "adx_min": [25, 30, 35],
        "rsi_hi": [35, 38, 40],        # RSI long upper (short lower = 100 - this)
        "sl_mult": [0.8, 1.0, 1.2],
        "tp_mult": [1.5, 2.0, 2.5],
        "min_score": [2.0, 2.5, 3.0],
        "bb_margin": [0.2, 0.35, 0.5],
    }

    total = 1
    for v in param_grid.values():
        total *= len(v)
    print(f"  총 {total}개 조합 탐색 중...\n")

    results = []
    count = 0
    for adx, rsi, sl, tp, ms, bb in product(
        param_grid["adx_min"], param_grid["rsi_hi"], param_grid["sl_mult"],
        param_grid["tp_mult"], param_grid["min_score"], param_grid["bb_margin"]
    ):
        count += 1
        if count % 100 == 0:
            print(f"  진행: {count}/{total}...")

        total_wr = 0
        total_ret = 0
        total_trades = 0
        valid = True

        for df in dfs:
            r = test_params(df, adx, rsi, sl, tp, ms, bb)
            if r.total_trades < 3:  # 최소 3거래
                valid = False
                break
            total_wr += r.win_rate
            total_ret += r.total_return_pct
            total_trades += r.total_trades

        if not valid:
            continue

        avg_wr = total_wr / len(seeds)
        avg_ret = total_ret / len(seeds)
        avg_trades = total_trades / len(seeds)

        results.append({
            "adx": adx, "rsi_hi": rsi, "sl": sl, "tp": tp,
            "min_score": ms, "bb": bb,
            "wr": avg_wr, "ret": avg_ret, "trades": avg_trades,
        })

    # 결과 정렬: 승률 60%+ AND 수익 양수 → 수익률 내림차순
    high_wr = [r for r in results if r["wr"] >= 60 and r["ret"] > 0]
    high_wr.sort(key=lambda x: x["ret"], reverse=True)

    print(f"\n{'='*70}")
    print(f"  승률 60%+ & 수익 양수 조합: {len(high_wr)}개 / {len(results)}개")
    print(f"{'='*70}")

    if high_wr:
        for i, r in enumerate(high_wr[:15]):
            rr = r["tp"] / r["sl"]
            print(f"  #{i+1} WR:{r['wr']:.0f}% Ret:{r['ret']:+.0f}% "
                  f"Trades:{r['trades']:.0f} R:R:{rr:.1f} | "
                  f"ADX≥{r['adx']} RSI{r['rsi_hi']} SL{r['sl']}x TP{r['tp']}x "
                  f"Score≥{r['min_score']} BB{r['bb']}")
    else:
        print("  ❌ 승률 60%+ 조합 없음. 50%+ 조합 확인:")
        decent = [r for r in results if r["wr"] >= 50 and r["ret"] > 0]
        decent.sort(key=lambda x: x["ret"], reverse=True)
        for i, r in enumerate(decent[:15]):
            rr = r["tp"] / r["sl"]
            print(f"  #{i+1} WR:{r['wr']:.0f}% Ret:{r['ret']:+.0f}% "
                  f"Trades:{r['trades']:.0f} R:R:{rr:.1f} | "
                  f"ADX≥{r['adx']} RSI{r['rsi_hi']} SL{r['sl']}x TP{r['tp']}x "
                  f"Score≥{r['min_score']} BB{r['bb']}")

    # 전체 결과 중 수익률 TOP 15 (승률 무관)
    all_sorted = sorted(results, key=lambda x: x["ret"], reverse=True)
    print(f"\n  수익률 TOP 15 (승률 무관):")
    for i, r in enumerate(all_sorted[:15]):
        rr = r["tp"] / r["sl"]
        print(f"  #{i+1} WR:{r['wr']:.0f}% Ret:{r['ret']:+.0f}% "
              f"Trades:{r['trades']:.0f} R:R:{rr:.1f} | "
              f"ADX≥{r['adx']} RSI{r['rsi_hi']} SL{r['sl']}x TP{r['tp']}x "
              f"Score≥{r['min_score']} BB{r['bb']}")


if __name__ == "__main__":
    main()
