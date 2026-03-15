#!/usr/bin/env python3
"""
Multi-Period Backtest Runner v5.0 — Compound Growth Machine
- 15-minute candles with 4H cross-validation
- Maker-only fees (0.02%) + minimal slippage (limit orders)
- Leverage 20x default (signal can push to 25x)
- Period 1: Recent 6 months (2025-09 ~ 2026-03)
- Period 2: 2 years ago 6 months (2023-09 ~ 2024-03)
- Period 3: 3 years ago 6 months (2022-09 ~ 2023-03)

Note: 6 months of 15min data = ~17,500 candles per period
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
                      timeframe: str = "15m") -> "pd.DataFrame":
    """Fetch OHLCV data for a specific date range (15-minute candles)"""
    import pandas as pd

    exchange = get_exchange()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # Calculate days from start_date to now
    days_ago = (datetime.utcnow() - start_dt).days + 1

    logger.info(f"[{symbol}] Fetching {timeframe} data: {start_date} ~ {end_date} ({days_ago} days ago)...")

    df = fetch_ohlcv(symbol, timeframe, since_days=days_ago, exchange=exchange)

    if df.empty:
        return df

    # Filter to the requested range
    df = df[(df["timestamp"] >= start_date) & (df["timestamp"] <= end_date)]
    df = df.reset_index(drop=True)

    logger.info(f"[{symbol}] {len(df)} candles in range ({timeframe})")
    return df


def run_backtest_period(symbol: str, df, period_name: str,
                        config: BacktestConfig = None) -> dict:
    """Run backtest for a single period"""
    if config is None:
        config = BacktestConfig(
            initial_capital=10000.0,
            fee_rate=0.0002,           # Maker fee only (limit orders)
            slippage_rate=0.0001,      # Minimal slippage (limit orders)
            leverage=20,               # Default 20x (overridden by signal)
            exit_type="fixed_tp_sl",
            max_bars=0,                # No time exit -- only TP/SL
            use_signal_leverage=True,
        )

    engine = BacktestEngine(config)
    result = engine.run(
        df,
        strategy_name=f"CompoundGrowth_v5_{period_name}",
        symbol=symbol,
    )

    return {
        "period": period_name,
        "symbol": symbol,
        "result": result,
        "candles": len(df),
        "date_range": f"{df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]}",
    }


def print_result(info: dict):
    """Print backtest result summary with compound growth metrics"""
    r = info["result"]
    logger.info(f"\n{'=' * 60}")
    logger.info(f"Period: {info['period']}")
    logger.info(f"Symbol: {info['symbol']}")
    logger.info(f"Range:  {info['date_range']}")
    logger.info(f"Candles: {info['candles']}")
    logger.info(f"-" * 40)
    logger.info(f"  Total Return:   {r.total_return_pct:>10.1f}%")
    logger.info(f"  Final Capital:  ${r.final_capital:>12,.2f}")
    logger.info(f"  Sharpe Ratio:   {r.sharpe_ratio:>10.2f}")
    logger.info(f"  Max Drawdown:   {r.max_drawdown_pct:>10.1f}%")
    logger.info(f"  Win Rate:       {r.win_rate:>10.1f}%")
    logger.info(f"  Profit Factor:  {r.profit_factor:>10.2f}")
    logger.info(f"  Avg RR:         {r.avg_pnl_ratio:>10.2f}")
    logger.info(f"  Total Trades:   {r.total_trades:>10d}")
    logger.info(f"  Winning:        {r.winning_trades:>10d}")
    logger.info(f"  Losing:         {r.losing_trades:>10d}")
    logger.info(f"  Avg Bars Held:  {r.avg_bars_held:>10.1f}")

    # Compound growth projection
    if r.total_trades > 0:
        # Period is ~6 months; annualize the return
        period_return = r.total_return_pct / 100.0
        annual_return = (1 + period_return) ** 2 - 1  # 6mo -> 12mo
        logger.info(f"-" * 40)
        logger.info(f"  COMPOUND GROWTH PROJECTION:")
        logger.info(f"  6-Month Return:  {r.total_return_pct:>10.1f}%")
        logger.info(f"  Annualized:      {annual_return * 100:>10.1f}%")
        logger.info(f"  $10K -> 6mo:     ${r.final_capital:>12,.2f}")
        logger.info(f"  $10K -> 1yr:     ${10000 * (1 + annual_return):>12,.2f}")
        # Per-trade stats
        avg_pnl_pct = r.total_return_pct / r.total_trades if r.total_trades > 0 else 0
        logger.info(f"  Avg PnL/Trade:   {avg_pnl_pct:>10.2f}%")
        trades_per_year = r.total_trades * 2  # 6mo -> 12mo estimate
        logger.info(f"  Trades/Year:     {trades_per_year:>10d} (est.)")

    logger.info(f"{'=' * 60}")

    # Print individual trades (limit to first 50 for readability)
    if r.trades:
        display_trades = r.trades[:50]
        logger.info(f"\n  Trade Details ({len(display_trades)} of {len(r.trades)}):")
        for t in display_trades:
            marker = "+" if t.pnl > 0 else "-"
            logger.info(
                f"    {marker} {t.side:5s} {t.entry_time[:16]} -> {t.exit_time[:16]} | "
                f"Entry: {t.entry_price:.2f} Exit: {t.exit_price:.2f} | "
                f"Lev: {t.leverage}x | PnL: {t.pnl:+.2f} ({t.pnl_pct:+.1f}%) | "
                f"{t.exit_reason}"
            )
        if len(r.trades) > 50:
            logger.info(f"    ... and {len(r.trades) - 50} more trades")


def main():
    logger.info("=" * 60)
    logger.info("Compound Growth Machine v5.0 -- Backtest")
    logger.info("Timeframe: 15min (with 4H resampling)")
    logger.info("Leverage: 20-25x | Risk: 0.2% per trade")
    logger.info("Fees: 0.02% maker | Slippage: 0.01%")
    logger.info("=" * 60)

    symbol = "BTCUSDT"

    # === Period definitions (6 months each to manage data volume) ===
    periods = [
        ("Period1_Recent_6mo", "2025-09-14", "2026-03-14"),
        ("Period2_2yr_ago_6mo", "2023-09-14", "2024-03-14"),
        ("Period3_3yr_ago_6mo", "2022-09-14", "2023-03-14"),
    ]

    all_results = []

    for period_name, start, end in periods:
        logger.info(f"\n{'#' * 60}")
        logger.info(f"# {period_name}: {start} ~ {end}")
        logger.info(f"{'#' * 60}")

        df = fetch_period_data(symbol, start, end, timeframe="15m")
        if df.empty or len(df) < 500:
            logger.error(f"  Insufficient data for {period_name} (need >= 500 candles)")
            continue

        info = run_backtest_period(symbol, df, period_name)
        print_result(info)
        all_results.append(info)

    # === Summary ===
    if all_results:
        logger.info(f"\n{'#' * 60}")
        logger.info(f"# SUMMARY -- Compound Growth Machine v5.0")
        logger.info(f"{'#' * 60}")
        for info in all_results:
            r = info["result"]
            annual_return = ((1 + r.total_return_pct / 100) ** 2 - 1) * 100
            logger.info(
                f"  {info['period']:25s} | "
                f"Return: {r.total_return_pct:>8.1f}% | "
                f"Annual: {annual_return:>10.1f}% | "
                f"MDD: {r.max_drawdown_pct:>5.1f}% | "
                f"WR: {r.win_rate:>5.1f}% | "
                f"PF: {r.profit_factor:>5.2f} | "
                f"Trades: {r.total_trades:>3d} | "
                f"Sharpe: {r.sharpe_ratio:>5.2f}"
            )

        # Overall average
        avg_return = sum(i["result"].total_return_pct for i in all_results) / len(all_results)
        avg_wr = sum(i["result"].win_rate for i in all_results) / len(all_results)
        avg_annual = ((1 + avg_return / 100) ** 2 - 1) * 100
        logger.info(f"-" * 60)
        logger.info(f"  AVERAGE:  Return: {avg_return:>8.1f}% | "
                     f"Annual: {avg_annual:>10.1f}% | "
                     f"WR: {avg_wr:>5.1f}%")

    return all_results


if __name__ == "__main__":
    main()
