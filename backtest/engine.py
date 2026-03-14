"""
Backtest Engine v3.0
- Event-driven simulator with trailing stop support
- Dynamic leverage from signals
- Accurate fee + slippage modeling
- Compound growth (risk % of current capital)
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Callable, Optional

from strategy.position_manager import Position, ExitCondition, calc_atr
from strategy.risk_manager import RiskManager
from utils.logger import setup_logger

logger = setup_logger("backtest")


@dataclass
class BacktestConfig:
    """Backtest configuration"""
    initial_capital: float = 10000.0
    fee_rate: float = 0.0006       # 0.06% total (maker+taker avg)
    slippage_rate: float = 0.0005  # 0.05%
    leverage: int = 3              # Default, overridden by signal
    tp_sl_ratio: float = 3.0      # R:R ratio
    atr_trailing_mult: float = 2.5
    exit_type: str = "fixed_tp_sl"  # No trailing by default
    max_bars: int = 40             # Time-based exit
    use_signal_leverage: bool = True  # Use leverage from signal


@dataclass
class TradeRecord:
    """Trade record"""
    entry_time: str
    exit_time: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    leverage: int
    pnl: float
    pnl_pct: float
    fee: float
    exit_reason: str
    signal_grade: str
    bars_held: int


@dataclass
class BacktestResult:
    """Backtest result"""
    strategy_name: str
    exit_type: str
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_pnl_ratio: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_bars_held: float = 0.0
    final_capital: float = 0.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


class BacktestEngine:
    """Event-driven backtest engine with compound growth"""

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()

    def run(
        self,
        df: pd.DataFrame,
        strategy_name: str = "ATM_v3",
        symbol: str = "BTCUSDT",
    ) -> BacktestResult:
        """
        Run backtest using generate_signals from final_strategy

        The strategy generates sig_direction, sig_strength, sig_leverage,
        sig_tp_mult, sig_sl_mult columns.
        """
        from strategy.final_strategy import generate_signals as gen_sig

        cfg = self.config
        capital = cfg.initial_capital
        position: Optional[Position] = None
        trades: list[TradeRecord] = []
        equity_curve = [capital]

        # Generate signals
        sig_df = gen_sig(df)
        atr_series = calc_atr(df)

        risk_mgr = RiskManager(capital)
        last_date = None
        max_capital = capital

        for i in range(1, len(sig_df)):
            row = sig_df.iloc[i]
            current_price = row["close"]
            high_price = row["high"]
            low_price = row["low"]
            atr = atr_series.iloc[i] if not pd.isna(atr_series.iloc[i]) else 0

            # Daily reset
            current_date = str(row["timestamp"])[:10]
            if last_date is not None and current_date != last_date:
                risk_mgr.state.daily_pnl = 0.0
                risk_mgr.state.daily_start_balance = risk_mgr.state.current_balance
                risk_mgr.state.daily_trade_halted = False
            last_date = current_date

            # ── Manage existing position ──
            if position is not None:
                # Update trailing stop
                if "trailing" in cfg.exit_type and atr > 0:
                    mult = cfg.atr_trailing_mult
                    position = ExitCondition.update_trailing_stop(
                        position, current_price, atr, mult
                    )

                bars_held = i - position.entry_index

                # Check intra-bar SL/TP hit using high/low
                exit_price = None
                reason = ""

                if position.side == "long":
                    # SL hit (check low)
                    if low_price <= position.stop_loss:
                        exit_price = position.stop_loss
                        reason = "stop_loss"
                    # TP hit (check high)
                    elif high_price >= position.take_profit:
                        exit_price = position.take_profit
                        reason = "take_profit"
                    # Trailing stop hit
                    elif (position.trailing_stop is not None and
                          low_price <= position.trailing_stop):
                        exit_price = position.trailing_stop
                        reason = "trailing_stop"
                else:
                    # SL hit (check high)
                    if high_price >= position.stop_loss:
                        exit_price = position.stop_loss
                        reason = "stop_loss"
                    # TP hit (check low)
                    elif low_price <= position.take_profit:
                        exit_price = position.take_profit
                        reason = "take_profit"
                    # Trailing stop hit
                    elif (position.trailing_stop is not None and
                          high_price >= position.trailing_stop):
                        exit_price = position.trailing_stop
                        reason = "trailing_stop"

                # Reverse signal exit
                if exit_price is None:
                    sig_dir = int(row.get("sig_direction", 0))
                    if sig_dir != 0:
                        if (position.side == "long" and sig_dir == -1) or \
                           (position.side == "short" and sig_dir == 1):
                            exit_price = current_price
                            reason = "reverse_signal"

                # Time-based exit
                if exit_price is None and cfg.max_bars > 0 and bars_held >= cfg.max_bars:
                    if position.side == "long":
                        progress = (current_price - position.entry_price) / atr if atr > 0 else 0
                    else:
                        progress = (position.entry_price - current_price) / atr if atr > 0 else 0
                    if progress < 0.3:  # Less than 0.3 ATR profit after max_bars
                        exit_price = current_price
                        reason = "time_exit"

                # Execute exit
                if exit_price is not None:
                    # Apply slippage to exit
                    if position.side == "long":
                        exit_price *= (1 - cfg.slippage_rate)
                        raw_pnl = (exit_price - position.entry_price) * position.quantity
                    else:
                        exit_price *= (1 + cfg.slippage_rate)
                        raw_pnl = (position.entry_price - exit_price) * position.quantity

                    raw_pnl *= position.leverage

                    # Fees
                    entry_fee = position.entry_price * position.quantity * cfg.fee_rate
                    exit_fee = exit_price * position.quantity * cfg.fee_rate
                    total_fee = entry_fee + exit_fee
                    net_pnl = raw_pnl - total_fee

                    # Prevent capital going below zero
                    if capital + net_pnl < 0:
                        net_pnl = -capital * 0.99

                    pnl_pct = net_pnl / capital * 100
                    capital += net_pnl
                    risk_mgr.state.current_balance = capital
                    risk_mgr.update_on_trade_close(net_pnl)

                    trades.append(TradeRecord(
                        entry_time=str(df.iloc[position.entry_index]["timestamp"]),
                        exit_time=str(row["timestamp"]),
                        symbol=symbol,
                        side=position.side,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        leverage=position.leverage,
                        pnl=net_pnl,
                        pnl_pct=pnl_pct,
                        fee=total_fee,
                        exit_reason=reason,
                        signal_grade=position.signal_grade,
                        bars_held=bars_held,
                    ))

                    if symbol in risk_mgr.state.open_positions:
                        del risk_mgr.state.open_positions[symbol]
                    position = None

            # ── New entry ──
            sig_dir = int(row.get("sig_direction", 0))
            if position is None and sig_dir != 0 and atr > 0:
                side = "long" if sig_dir == 1 else "short"
                sig_strength = float(row.get("sig_strength", 0.5))
                sig_lev = int(row.get("sig_leverage", cfg.leverage))
                sig_tp_mult = float(row.get("sig_tp_mult", cfg.tp_sl_ratio))
                sig_sl_mult = float(row.get("sig_sl_mult", 2.0))

                leverage = sig_lev if cfg.use_signal_leverage else cfg.leverage

                # Risk check
                can_open, msg = risk_mgr.can_open_position(symbol)
                if not can_open:
                    equity_curve.append(capital)
                    continue

                # Entry price with slippage
                if side == "long":
                    entry_price = current_price * (1 + cfg.slippage_rate)
                    tp = entry_price + atr * sig_tp_mult
                    sl = entry_price - atr * sig_sl_mult
                else:
                    entry_price = current_price * (1 - cfg.slippage_rate)
                    tp = entry_price - atr * sig_tp_mult
                    sl = entry_price + atr * sig_sl_mult

                # Position size (compound: risk % of current capital)
                quantity = risk_mgr.calc_position_size(
                    capital, entry_price, sl, leverage
                )

                if quantity > 0:
                    position = Position(
                        symbol=symbol,
                        side=side,
                        entry_price=entry_price,
                        quantity=quantity,
                        leverage=leverage,
                        stop_loss=sl,
                        take_profit=tp,
                        entry_index=i,
                        highest_price=entry_price,
                        lowest_price=entry_price,
                        signal_grade=f"str_{sig_strength:.2f}",
                        strategy=strategy_name,
                    )
                    risk_mgr.state.open_positions[symbol] = True

            equity_curve.append(capital)
            max_capital = max(max_capital, capital)

        # ── Force close remaining position ──
        if position is not None:
            last_price = df.iloc[-1]["close"]
            if position.side == "long":
                raw_pnl = (last_price - position.entry_price) * position.quantity * position.leverage
            else:
                raw_pnl = (position.entry_price - last_price) * position.quantity * position.leverage

            fee = (position.entry_price + last_price) * position.quantity * cfg.fee_rate
            net_pnl = raw_pnl - fee
            capital += net_pnl

            trades.append(TradeRecord(
                entry_time=str(df.iloc[position.entry_index]["timestamp"]),
                exit_time=str(df.iloc[-1]["timestamp"]),
                symbol=symbol,
                side=position.side,
                entry_price=position.entry_price,
                exit_price=last_price,
                quantity=position.quantity,
                leverage=position.leverage,
                pnl=net_pnl,
                pnl_pct=net_pnl / cfg.initial_capital * 100,
                fee=fee,
                exit_reason="end_of_data",
                signal_grade=position.signal_grade,
                bars_held=len(df) - 1 - position.entry_index,
            ))
            equity_curve.append(capital)

        result = self._calc_metrics(trades, equity_curve, strategy_name, cfg)
        return result

    def _calc_metrics(self, trades: list[TradeRecord], equity_curve: list,
                      strategy_name: str, cfg: BacktestConfig) -> BacktestResult:
        """Calculate performance metrics"""
        result = BacktestResult(
            strategy_name=strategy_name,
            exit_type=cfg.exit_type,
            trades=trades,
            equity_curve=equity_curve,
            final_capital=equity_curve[-1] if equity_curve else cfg.initial_capital,
        )

        if not trades:
            return result

        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        result.total_trades = len(trades)
        result.winning_trades = len(wins)
        result.losing_trades = len(losses)

        result.total_return_pct = (equity_curve[-1] - cfg.initial_capital) / cfg.initial_capital * 100

        result.win_rate = len(wins) / len(trades) * 100 if trades else 0

        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 1
        result.avg_pnl_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        total_win = sum(wins) if wins else 0
        total_loss = abs(sum(losses)) if losses else 1
        result.profit_factor = total_win / total_loss if total_loss > 0 else 0

        # MDD
        eq = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        drawdown = (eq - peak) / np.where(peak > 0, peak, 1) * 100
        result.max_drawdown_pct = abs(drawdown.min())

        # Sharpe ratio (annualized, 4h bars = 6/day * 365)
        if len(pnls) > 1:
            returns = pd.Series(pnls) / cfg.initial_capital
            if returns.std() > 0:
                result.sharpe_ratio = returns.mean() / returns.std() * np.sqrt(6 * 365)
            else:
                result.sharpe_ratio = 0
        else:
            result.sharpe_ratio = 0

        result.avg_bars_held = np.mean([t.bars_held for t in trades])

        return result
