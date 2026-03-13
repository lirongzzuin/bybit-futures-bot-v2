"""
반복 최적화 엔진
- 파라미터 그리드 탐색
- 각 라운드 결과 분석 → 다음 라운드 파라미터 범위 축소
- 멀티 심볼 교차 검증
- 워크포워드 테스트
"""
import numpy as np
import pandas as pd
from itertools import product
from typing import Optional

from strategy.signal_generator import compute_indicators
from strategy.advanced_strategy import generate_advanced_signal
from backtest.advanced_engine import AdvancedBacktestEngine, AdvancedConfig, BacktestMetrics, print_metrics


def run_optimization_round(
    data: dict[str, pd.DataFrame],
    param_grid: dict,
    engine_config: Optional[AdvancedConfig] = None,
    primary_symbol: str = "BTCUSDT",
    top_n: int = 10,
) -> list[tuple[float, dict, BacktestMetrics]]:
    """
    한 라운드 최적화 실행

    Args:
        data: {symbol: indicator_computed_df}
        param_grid: 탐색할 파라미터 딕셔너리
        engine_config: 백테스트 엔진 설정
        primary_symbol: 1차 테스트 심볼
        top_n: 상위 N개 결과 반환
    """
    config = engine_config or AdvancedConfig()
    engine = AdvancedBacktestEngine(config)
    btc = data[primary_symbol]

    # 파라미터 조합 생성
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(product(*values))

    print(f"  파라미터 조합: {len(combos)}개")

    results = []
    for combo in combos:
        params = dict(zip(keys, combo))

        # 시그널 생성
        df = generate_advanced_signal(btc.copy(), **params)

        # 백테스트
        label = "_".join(f"{k}={v}" for k, v in params.items())
        m = engine.run(df, label, primary_symbol)

        if m.total_trades < 15:  # 최소 거래 수
            continue

        # 종합 스코어
        score = calc_score(m)
        results.append((score, params, m))

    # 정렬
    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_n]


def calc_score(m: BacktestMetrics) -> float:
    """
    종합 스코어 (높을수록 좋음)

    균형 잡힌 전략을 선호:
    - 수익률만 높고 MDD 큰 것은 페널티
    - 거래 수 너무 적으면 페널티
    - 승률 + 손익비 균형
    """
    if m.total_trades < 10 or m.max_drawdown_pct > 70:
        return -999

    # 칼마비율 (수익/MDD) 가장 중요
    calmar = m.calmar_ratio if m.max_drawdown_pct > 0 else 0

    # 수익률 (cap at 500%)
    ret_score = min(m.total_return_pct / 100, 5)

    # 샤프
    sharpe = min(m.sharpe_ratio, 10)

    # 승률 × 손익비 시너지
    edge = (m.win_rate / 100) * m.avg_rr_ratio

    # Profit Factor
    pf = min(m.profit_factor, 5)

    # 거래 수 보너스 (30~100이 이상적)
    trade_bonus = min(m.total_trades / 50, 1.0) if m.total_trades < 50 else 1.0
    if m.total_trades > 200:
        trade_bonus *= 0.8  # 과다 거래 페널티

    # MDD 페널티 (40% 초과 시 감점)
    mdd_penalty = 1.0
    if m.max_drawdown_pct > 40:
        mdd_penalty = 0.5
    elif m.max_drawdown_pct > 30:
        mdd_penalty = 0.7

    score = (
        calmar * 2.0 +
        ret_score * 1.5 +
        sharpe * 1.0 +
        edge * 3.0 +
        pf * 1.0
    ) * trade_bonus * mdd_penalty

    return score


def multi_symbol_validation(
    data: dict[str, pd.DataFrame],
    params: dict,
    engine_config: Optional[AdvancedConfig] = None,
) -> dict:
    """멀티 심볼 교차 검증"""
    config = engine_config or AdvancedConfig()
    engine = AdvancedBacktestEngine(config)

    results = {}
    for sym, df in data.items():
        sig_df = generate_advanced_signal(df.copy(), **params)
        m = engine.run(sig_df, f"multi_{sym}", sym)
        results[sym] = {
            "return": m.total_return_pct,
            "sharpe": m.sharpe_ratio,
            "mdd": m.max_drawdown_pct,
            "win_rate": m.win_rate,
            "trades": m.total_trades,
            "pf": m.profit_factor,
            "metrics": m,
        }

    returns = [v["return"] for v in results.values()]
    mean_ret = np.mean(returns)
    std_ret = np.std(returns)
    profitable = sum(1 for r in returns if r > 0)

    return {
        "per_symbol": results,
        "mean_return": mean_ret,
        "std_return": std_ret,
        "cv": std_ret / abs(mean_ret) * 100 if mean_ret != 0 else 999,
        "profitable_symbols": profitable,
        "total_symbols": len(results),
    }


def walk_forward_test(
    df: pd.DataFrame,
    params: dict,
    engine_config: Optional[AdvancedConfig] = None,
    train_ratio: float = 0.7,
    symbol: str = "BTCUSDT",
) -> dict:
    """워크포워드 분석"""
    config = engine_config or AdvancedConfig()
    engine = AdvancedBacktestEngine(config)

    split = int(len(df) * train_ratio)
    train = df.iloc[:split].copy().reset_index(drop=True)
    test = df.iloc[split:].copy().reset_index(drop=True)

    train_sig = generate_advanced_signal(train.copy(), **params)
    test_sig = generate_advanced_signal(test.copy(), **params)

    train_m = engine.run(train_sig, "train", symbol)
    test_m = engine.run(test_sig, "test", symbol)

    ratio = test_m.total_return_pct / train_m.total_return_pct if train_m.total_return_pct != 0 else 0

    return {
        "train_return": train_m.total_return_pct,
        "test_return": test_m.total_return_pct,
        "train_sharpe": train_m.sharpe_ratio,
        "test_sharpe": test_m.sharpe_ratio,
        "train_trades": train_m.total_trades,
        "test_trades": test_m.total_trades,
        "ratio": ratio,
        "overfit": ratio < 0.3,
        "train_metrics": train_m,
        "test_metrics": test_m,
    }
