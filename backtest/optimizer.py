"""
백테스트 옵티마이저
- 12개 전략 × 여러 청산 조건 조합 전수 백테스트
- 멀티 심볼 검증
- 워크포워드 분석 (70/30 분할)
- 최종 전략 자동 선정 및 반영
"""
import os
import json
import numpy as np
import pandas as pd
from itertools import product
from typing import Optional

from backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from strategy.signal_generator import STRATEGIES, compute_indicators
from config.indicator_params import SYMBOLS
from utils.logger import setup_logger

logger = setup_logger("optimizer")


# 테스트할 청산 조건들
EXIT_CONFIGS = [
    # 고정 TP/SL 비율
    {"exit_type": "fixed", "tp_sl_ratio": 1.0, "atr_trailing_mult": 0, "label": "fixed_1:1"},
    {"exit_type": "fixed", "tp_sl_ratio": 1.5, "atr_trailing_mult": 0, "label": "fixed_1.5:1"},
    {"exit_type": "fixed", "tp_sl_ratio": 2.0, "atr_trailing_mult": 0, "label": "fixed_2:1"},
    {"exit_type": "fixed", "tp_sl_ratio": 3.0, "atr_trailing_mult": 0, "label": "fixed_3:1"},
    # ATR 트레일링 스탑
    {"exit_type": "trailing_atr_1x", "tp_sl_ratio": 3.0, "atr_trailing_mult": 1.0, "label": "trail_1x"},
    {"exit_type": "trailing_atr_1.5x", "tp_sl_ratio": 3.0, "atr_trailing_mult": 1.5, "label": "trail_1.5x"},
    {"exit_type": "trailing_atr_2x", "tp_sl_ratio": 3.0, "atr_trailing_mult": 2.0, "label": "trail_2x"},
    # 반대 신호 청산
    {"exit_type": "opposite_signal", "tp_sl_ratio": 3.0, "atr_trailing_mult": 1.5, "label": "opposite"},
    # Ichimoku 구름 진입 시
    {"exit_type": "ichimoku_kumo", "tp_sl_ratio": 3.0, "atr_trailing_mult": 1.5, "label": "kumo_exit"},
    # S/D 반대 영역
    {"exit_type": "sd_zone", "tp_sl_ratio": 3.0, "atr_trailing_mult": 1.5, "label": "sd_exit"},
    # 시간 기반 (20봉 = 약 3.3일)
    {"exit_type": "trailing_atr_1.5x", "tp_sl_ratio": 3.0, "atr_trailing_mult": 1.5, "max_bars": 20, "label": "time_20"},
]

# 레버리지 옵션
LEVERAGE_OPTIONS = [3, 5, 10]


def run_full_optimization(
    data: dict[str, pd.DataFrame],
    leverage_options: list[int] = None,
) -> dict:
    """
    전체 최적화 실행

    Args:
        data: {symbol: DataFrame} 딕셔너리 (지표 미계산 상태)
        leverage_options: 테스트할 레버리지 목록

    Returns:
        최적화 결과 딕셔너리
    """
    leverage_options = leverage_options or LEVERAGE_OPTIONS
    primary = SYMBOLS["primary"]

    if primary not in data:
        logger.error(f"{primary} 데이터 없음!")
        return {}

    # ── Step 1: 지표 계산 ──
    logger.info("=" * 60)
    logger.info("Step 1: 전 심볼 지표 계산")
    logger.info("=" * 60)

    indicator_data = {}
    for sym, df in data.items():
        logger.info(f"  [{sym}] 지표 계산 중...")
        indicator_data[sym] = compute_indicators(df)

    # ── Step 2: BTC에서 전략 × 청산 × 레버리지 전수 백테스트 ──
    logger.info("=" * 60)
    logger.info(f"Step 2: {primary} 전수 백테스트")
    logger.info("=" * 60)

    btc_df = indicator_data[primary]
    all_results: list[BacktestResult] = []

    total_combos = len(STRATEGIES) * len(EXIT_CONFIGS) * len(leverage_options)
    logger.info(f"  총 {total_combos}개 조합 테스트")

    combo_idx = 0
    for strat_name, strat_fn in STRATEGIES.items():
        for exit_cfg in EXIT_CONFIGS:
            for lev in leverage_options:
                combo_idx += 1
                label = f"{strat_name}_{exit_cfg['label']}_lev{lev}"

                config = BacktestConfig(
                    leverage=lev,
                    tp_sl_ratio=exit_cfg["tp_sl_ratio"],
                    atr_trailing_mult=exit_cfg.get("atr_trailing_mult", 1.5),
                    exit_type=exit_cfg["exit_type"],
                    max_bars=exit_cfg.get("max_bars", 0),
                )

                engine = BacktestEngine(config)
                result = engine.run(btc_df, strat_fn, label, primary)
                all_results.append(result)

                if combo_idx % 50 == 0:
                    logger.info(f"  진행: {combo_idx}/{total_combos}")

    # 결과 정렬 (샤프비율 × 수익률 가중)
    scored = []
    for r in all_results:
        # 거래 수 최소 10개 이상, MDD 50% 이하 필터
        if r.total_trades < 10 or r.max_drawdown_pct > 50:
            continue
        # 복합 스코어: 샤프 * 0.4 + 수익률 * 0.3 + 승률 * 0.2 + 손익비 * 0.1
        score = (
            r.sharpe_ratio * 0.4 +
            min(r.total_return_pct / 100, 5) * 0.3 +  # 수익률 cap
            r.win_rate / 100 * 0.2 +
            min(r.avg_pnl_ratio, 5) * 0.1
        )
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)

    # 상위 결과 출력
    logger.info("\n" + "=" * 100)
    logger.info(f"{'전략명':<40} {'수익률':>8} {'샤프':>6} {'MDD':>6} {'승률':>6} {'손익비':>6} {'거래수':>6}")
    logger.info("-" * 100)

    for score, r in scored[:20]:
        logger.info(
            f"{r.strategy_name:<40} {r.total_return_pct:>7.1f}% {r.sharpe_ratio:>6.2f} "
            f"{r.max_drawdown_pct:>5.1f}% {r.win_rate:>5.1f}% {r.avg_pnl_ratio:>6.2f} {r.total_trades:>6d}"
        )

    if not scored:
        logger.warning("유효한 전략 없음! 필터 기준 완화 필요")
        return {"top_strategies": [], "final_strategy": None}

    # ── Step 3: 상위 3개 전략 멀티 심볼 검증 ──
    logger.info("=" * 60)
    logger.info("Step 3: 멀티 심볼 검증 (상위 전략)")
    logger.info("=" * 60)

    top_3 = scored[:3]
    multi_results = {}

    for rank, (score, r) in enumerate(top_3, 1):
        # 전략명에서 원래 전략, 청산, 레버리지 파싱
        parts = r.strategy_name.rsplit("_lev", 1)
        lev = int(parts[1]) if len(parts) > 1 else 5
        strat_exit = parts[0].rsplit("_", 1)
        strat_name = strat_exit[0] if len(strat_exit) > 1 else parts[0]
        exit_label = strat_exit[1] if len(strat_exit) > 1 else "trail_1.5x"

        # 원래 전략 함수 찾기
        base_strat = None
        for sn in STRATEGIES:
            if strat_name.startswith(sn):
                base_strat = sn
                break

        if base_strat is None:
            continue

        strat_fn = STRATEGIES[base_strat]

        # 청산 설정 찾기
        exit_cfg = None
        for ec in EXIT_CONFIGS:
            if ec["label"] == exit_label:
                exit_cfg = ec
                break
        if exit_cfg is None:
            exit_cfg = EXIT_CONFIGS[5]  # trail_1.5x 기본값

        symbol_returns = {}
        for sym, sym_df in indicator_data.items():
            config = BacktestConfig(
                leverage=lev,
                tp_sl_ratio=exit_cfg["tp_sl_ratio"],
                atr_trailing_mult=exit_cfg.get("atr_trailing_mult", 1.5),
                exit_type=exit_cfg["exit_type"],
                max_bars=exit_cfg.get("max_bars", 0),
            )
            engine = BacktestEngine(config)
            sym_result = engine.run(sym_df, strat_fn, r.strategy_name, sym)
            symbol_returns[sym] = sym_result.total_return_pct

            logger.info(f"  #{rank} [{sym}] 수익률: {sym_result.total_return_pct:.1f}%, "
                        f"승률: {sym_result.win_rate:.1f}%, 거래수: {sym_result.total_trades}")

        # 편차 계산
        returns_list = list(symbol_returns.values())
        if len(returns_list) > 1:
            std = np.std(returns_list)
            mean = np.mean(returns_list)
            cv = std / abs(mean) * 100 if mean != 0 else float("inf")
        else:
            cv = 0

        multi_results[r.strategy_name] = {
            "rank": rank,
            "btc_score": score,
            "symbol_returns": symbol_returns,
            "mean_return": np.mean(returns_list),
            "std_return": np.std(returns_list),
            "cv": cv,
            "overfitting": cv > 30,  # 변동계수 30% 초과 = 과적합
            "btc_result": r,
            "base_strat": base_strat,
            "exit_cfg": exit_cfg,
            "leverage": lev,
        }

        if cv > 30:
            logger.warning(f"  ⚠ #{rank} 과적합 의심 (변동계수: {cv:.1f}%)")
        else:
            logger.info(f"  ✓ #{rank} 안정적 (변동계수: {cv:.1f}%)")

    # ── Step 4: 워크포워드 분석 ──
    logger.info("=" * 60)
    logger.info("Step 4: 워크포워드 분석 (70/30)")
    logger.info("=" * 60)

    valid_strategies = {k: v for k, v in multi_results.items() if not v["overfitting"]}

    if not valid_strategies:
        logger.warning("과적합 필터 후 유효 전략 없음 — 필터 완화하여 재시도")
        valid_strategies = multi_results  # 모두 사용

    walkforward_results = {}
    for name, info in valid_strategies.items():
        strat_fn = STRATEGIES[info["base_strat"]]
        exit_cfg = info["exit_cfg"]
        lev = info["leverage"]

        # 70/30 분할
        split_idx = int(len(btc_df) * 0.7)
        train_df = btc_df.iloc[:split_idx].copy()
        test_df = btc_df.iloc[split_idx:].copy()

        config = BacktestConfig(
            leverage=lev,
            tp_sl_ratio=exit_cfg["tp_sl_ratio"],
            atr_trailing_mult=exit_cfg.get("atr_trailing_mult", 1.5),
            exit_type=exit_cfg["exit_type"],
            max_bars=exit_cfg.get("max_bars", 0),
        )

        engine = BacktestEngine(config)
        train_result = engine.run(train_df, strat_fn, name + "_train", primary)
        test_result = engine.run(test_df, strat_fn, name + "_test", primary)

        # 학습 대비 테스트 성과 비율
        if train_result.total_return_pct > 0:
            performance_ratio = test_result.total_return_pct / train_result.total_return_pct
        else:
            performance_ratio = 0

        overfit = performance_ratio < 0.5

        walkforward_results[name] = {
            "train_return": train_result.total_return_pct,
            "test_return": test_result.total_return_pct,
            "performance_ratio": performance_ratio,
            "overfit": overfit,
        }

        status = "⚠ 과적합" if overfit else "✓ 통과"
        logger.info(
            f"  {name}: 학습 {train_result.total_return_pct:.1f}% → "
            f"테스트 {test_result.total_return_pct:.1f}% "
            f"(비율: {performance_ratio:.2f}) {status}"
        )

    # ── Step 5: 최종 전략 선정 ──
    logger.info("=" * 60)
    logger.info("Step 5: 최종 전략 선정")
    logger.info("=" * 60)

    final_candidates = {
        k: v for k, v in valid_strategies.items()
        if k in walkforward_results and not walkforward_results[k]["overfit"]
    }

    if not final_candidates:
        logger.warning("워크포워드 필터 후 유효 전략 없음 — 가장 안정적인 전략 선택")
        # 가장 변동계수 낮은 전략 선택
        final_candidates = dict(
            sorted(valid_strategies.items(), key=lambda x: x[1]["cv"])[:1]
        )

    # 최종: BTC 스코어 + 멀티심볼 안정성 + 워크포워드 비율 종합
    best_name = None
    best_score = -float("inf")

    for name, info in final_candidates.items():
        wf = walkforward_results.get(name, {})
        combined = (
            info["btc_score"] * 0.5 +
            (1 - info["cv"] / 100) * 0.3 +
            wf.get("performance_ratio", 0) * 0.2
        )
        if combined > best_score:
            best_score = combined
            best_name = name

    if best_name is None:
        best_name = list(multi_results.keys())[0]

    final = multi_results[best_name]
    logger.info(f"\n{'='*60}")
    logger.info(f"🏆 최종 선정 전략: {best_name}")
    logger.info(f"  기본 전략: {final['base_strat']}")
    logger.info(f"  청산 방식: {final['exit_cfg']['label']}")
    logger.info(f"  레버리지: {final['leverage']}x")
    logger.info(f"  BTC 수익률: {final['btc_result'].total_return_pct:.1f}%")
    logger.info(f"  평균 수익률(전 심볼): {final['mean_return']:.1f}%")
    logger.info(f"  승률: {final['btc_result'].win_rate:.1f}%")
    logger.info(f"  샤프비율: {final['btc_result'].sharpe_ratio:.2f}")
    logger.info(f"  MDD: {final['btc_result'].max_drawdown_pct:.1f}%")
    logger.info(f"{'='*60}")

    return {
        "all_results": [(s, r.strategy_name, r.total_return_pct, r.sharpe_ratio,
                         r.max_drawdown_pct, r.win_rate, r.avg_pnl_ratio, r.total_trades)
                        for s, r in scored[:20]],
        "multi_symbol": multi_results,
        "walkforward": walkforward_results,
        "final_strategy": {
            "name": best_name,
            "base_strategy": final["base_strat"],
            "exit_config": final["exit_cfg"],
            "leverage": final["leverage"],
            "btc_result": final["btc_result"],
        },
        "indicator_data": indicator_data,
    }


def apply_best_strategy(optimization_result: dict) -> dict:
    """
    최적화 결과를 signal_generator.py의 전역 변수에 반영
    (런타임 반영 — 파일 수정은 리포트에서 별도 처리)
    """
    import strategy.signal_generator as sg

    final = optimization_result.get("final_strategy")
    if not final:
        logger.warning("최적화 결과 없음")
        return {}

    sg.SELECTED_STRATEGY = final["base_strategy"]
    sg.SELECTED_EXIT = final["exit_config"]["exit_type"]
    sg.SELECTED_TP_SL_RATIO = final["exit_config"]["tp_sl_ratio"]
    sg.SELECTED_LEVERAGE = final["leverage"]

    logger.info(f"전략 반영 완료: {sg.SELECTED_STRATEGY}, "
                f"청산: {sg.SELECTED_EXIT}, TP/SL: {sg.SELECTED_TP_SL_RATIO}, "
                f"레버리지: {sg.SELECTED_LEVERAGE}x")

    return {
        "strategy": sg.SELECTED_STRATEGY,
        "exit": sg.SELECTED_EXIT,
        "tp_sl_ratio": sg.SELECTED_TP_SL_RATIO,
        "leverage": sg.SELECTED_LEVERAGE,
    }
