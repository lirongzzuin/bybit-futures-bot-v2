#!/usr/bin/env python3
"""
백테스트 실행기
- 데이터 다운로드 → 전략 최적화 → 리포트 생성
"""
import sys
import os

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(__file__))

from data.fetcher import fetch_all_symbols
from backtest.optimizer import run_full_optimization, apply_best_strategy
from backtest.report import generate_html_report
from utils.logger import setup_logger

logger = setup_logger("backtest_runner")


def main():
    logger.info("=" * 60)
    logger.info("암호화폐 선물 자동매매 백테스트 시작")
    logger.info("=" * 60)

    # ── 1. 데이터 수집 ──
    logger.info("\n📥 데이터 수집 중...")
    data = fetch_all_symbols(
        timeframe="4h",
        since_days=365,
        force_refresh=False,
    )

    if not data:
        logger.error("데이터 수집 실패!")
        return

    for sym, df in data.items():
        logger.info(f"  {sym}: {len(df)} 캔들 ({df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]})")

    # ── 2. 전략 최적화 ──
    logger.info("\n🔬 전략 최적화 실행...")
    result = run_full_optimization(data)

    if not result or not result.get("final_strategy"):
        logger.error("최적화 실패!")
        return

    # ── 3. 최적 전략 적용 ──
    logger.info("\n⚙️ 최적 전략 적용...")
    applied = apply_best_strategy(result)
    logger.info(f"  적용 완료: {applied}")

    # ── 4. 리포트 생성 ──
    logger.info("\n📊 리포트 생성...")
    report_path = generate_html_report(result)
    logger.info(f"  리포트: {report_path}")

    # ── 5. 결과 요약 ──
    final = result["final_strategy"]
    btc_r = final["btc_result"]

    logger.info("\n" + "=" * 60)
    logger.info("🏆 백테스트 최종 결과")
    logger.info("=" * 60)
    logger.info(f"  전략: {final['name']}")
    logger.info(f"  총수익률: {btc_r.total_return_pct:.1f}%")
    logger.info(f"  샤프비율: {btc_r.sharpe_ratio:.2f}")
    logger.info(f"  MDD: {btc_r.max_drawdown_pct:.1f}%")
    logger.info(f"  승률: {btc_r.win_rate:.1f}%")
    logger.info(f"  손익비: {btc_r.avg_pnl_ratio:.2f}")
    logger.info(f"  거래수: {btc_r.total_trades}")
    logger.info(f"  레버리지: {final['leverage']}x")
    logger.info("=" * 60)

    return result


if __name__ == "__main__":
    main()
