#!/usr/bin/env python3
"""
Multi-Period Backtest Runner v3.0
- Period 1: Recent 1 year (2025-03 ~ 2026-03)
- Period 2: 3 years ago 1 year (2022-03 ~ 2023-03)
- Period 3: COVID era 1 year (2020-03 ~ 2021-03)
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from data.fetcher import fetch_ohlcv, get_exchange
from backtest.engine import BacktestEngine, BacktestConfig
from utils.logger import setup_logger

logger = setup_logger("backtest_runner")


def fetch_period_data(symbol: str, start_date: str, end_date: str,
                      timeframe: str = "4h") -> "pd.DataFrame":
    """Fetch OHLCV data for a specific date range"""
    import pandas as pd

    exchange = get_exchange()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # Calculate days from start_date to now
    days_ago = (datetime.utcnow() - start_dt).days + 1

    logger.info(f"[{symbol}] Fetching {start_date} ~ {end_date} ({days_ago} days ago)...")

    df = fetch_ohlcv(symbol, timeframe, since_days=days_ago, exchange=exchange)

    if df.empty:
        return df

    # Filter to the requested range
    df = df[(df["timestamp"] >= start_date) & (df["timestamp"] <= end_date)]
    df = df.reset_index(drop=True)

    logger.info(f"[{symbol}] {len(df)} candles in range")
    return df


def run_backtest_period(symbol: str, df, period_name: str,
                        config: BacktestConfig = None) -> dict:
    """Run backtest for a single period"""
    if config is None:
        config = BacktestConfig(
            initial_capital=10000.0,
            fee_rate=0.0006,
            slippage_rate=0.0005,
            leverage=3,
            exit_type="fixed_tp_sl",
            max_bars=60,
            use_signal_leverage=True,
        )

    engine = BacktestEngine(config)
    result = engine.run(df, strategy_name=f"ATM_v3_{period_name}", symbol=symbol)

    return {
        "period": period_name,
        "symbol": symbol,
        "result": result,
        "candles": len(df),
        "date_range": f"{df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]}",
    }


def print_result(info: dict):
    """Print backtest result summary"""
    r = info["result"]
    logger.info(f"\n{'='*60}")
    logger.info(f"Period: {info['period']}")
    logger.info(f"Symbol: {info['symbol']}")
    logger.info(f"Range:  {info['date_range']}")
    logger.info(f"Candles: {info['candles']}")
    logger.info(f"-" * 40)
    logger.info(f"  Total Return:   {r.total_return_pct:>8.1f}%")
    logger.info(f"  Final Capital:  ${r.final_capital:>10,.2f}")
    logger.info(f"  Sharpe Ratio:   {r.sharpe_ratio:>8.2f}")
    logger.info(f"  Max Drawdown:   {r.max_drawdown_pct:>8.1f}%")
    logger.info(f"  Win Rate:       {r.win_rate:>8.1f}%")
    logger.info(f"  Profit Factor:  {r.profit_factor:>8.2f}")
    logger.info(f"  Avg RR:         {r.avg_pnl_ratio:>8.2f}")
    logger.info(f"  Total Trades:   {r.total_trades:>8d}")
    logger.info(f"  Winning:        {r.winning_trades:>8d}")
    logger.info(f"  Losing:         {r.losing_trades:>8d}")
    logger.info(f"  Avg Bars Held:  {r.avg_bars_held:>8.1f}")
    logger.info(f"{'='*60}")

    # Print individual trades
    if r.trades:
        logger.info(f"\n  Trade Details:")
        for t in r.trades:
            emoji = "+" if t.pnl > 0 else "-"
            logger.info(
                f"    {emoji} {t.side:5s} {t.entry_time[:16]} -> {t.exit_time[:16]} | "
                f"Entry: {t.entry_price:.2f} Exit: {t.exit_price:.2f} | "
                f"Lev: {t.leverage}x | PnL: {t.pnl:+.2f} ({t.pnl_pct:+.1f}%) | "
                f"{t.exit_reason}"
            )


def main():
    logger.info("=" * 60)
    logger.info("Multi-Period Backtest — ATM Strategy v3.0")
    logger.info("=" * 60)

    symbol = "BTCUSDT"

    # === Period definitions ===
    periods = [
        ("Period1_Recent_1yr", "2025-03-14", "2026-03-14"),
        ("Period2_3yr_ago", "2022-03-14", "2023-03-14"),
        ("Period3_COVID", "2020-03-14", "2021-03-14"),
    ]

    all_results = []

    for period_name, start, end in periods:
        logger.info(f"\n{'#'*60}")
        logger.info(f"# {period_name}: {start} ~ {end}")
        logger.info(f"{'#'*60}")

        df = fetch_period_data(symbol, start, end)
        if df.empty or len(df) < 100:
            logger.error(f"  Insufficient data for {period_name}")
            continue

        info = run_backtest_period(symbol, df, period_name)
        print_result(info)
        all_results.append(info)

    # === Summary ===
    if all_results:
        logger.info(f"\n{'#'*60}")
        logger.info(f"# SUMMARY — All Periods")
        logger.info(f"{'#'*60}")
        for info in all_results:
            r = info["result"]
            logger.info(
                f"  {info['period']:25s} | "
                f"Return: {r.total_return_pct:>7.1f}% | "
                f"MDD: {r.max_drawdown_pct:>5.1f}% | "
                f"WR: {r.win_rate:>5.1f}% | "
                f"PF: {r.profit_factor:>5.2f} | "
                f"Trades: {r.total_trades:>3d} | "
                f"Sharpe: {r.sharpe_ratio:>5.2f}"
            )

    return all_results


if __name__ == "__main__":
    main()
