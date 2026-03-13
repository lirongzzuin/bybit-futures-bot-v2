"""
백테스트 엔진
- 초기 자본: $10,000
- 수수료: 0.06% (메이커/테이커)
- 슬리피지: 0.05%
- 양방향 (롱/숏) 매매
- 성과 지표: 총수익률, 샤프비율, MDD, 승률, 손익비, 거래횟수
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
    """백테스트 설정"""
    initial_capital: float = 10000.0
    fee_rate: float = 0.0006       # 0.06%
    slippage_rate: float = 0.0005  # 0.05%
    leverage: int = 5
    tp_sl_ratio: float = 2.0      # TP:SL 비율
    atr_trailing_mult: float = 1.5
    exit_type: str = "trailing_atr_1.5x"
    max_bars: int = 0              # 시간 기반 청산 (0=비활성)


@dataclass
class TradeRecord:
    """거래 기록"""
    entry_time: str
    exit_time: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    fee: float
    exit_reason: str
    signal_grade: str
    bars_held: int


@dataclass
class BacktestResult:
    """백테스트 결과"""
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
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


class BacktestEngine:
    """
    이벤트 기반 백테스트 엔진
    - 봉 단위로 순회하며 시그널 → 진입/청산 시뮬레이션
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()

    def run(
        self,
        df: pd.DataFrame,
        signal_fn: Callable[[pd.DataFrame], pd.Series],
        strategy_name: str = "unknown",
        symbol: str = "BTCUSDT",
    ) -> BacktestResult:
        """
        백테스트 실행

        Args:
            df: 지표가 계산된 OHLCV DataFrame
            signal_fn: 시그널 생성 함수 (df → Series[1,-1,0])
            strategy_name: 전략 이름
            symbol: 심볼명
        """
        cfg = self.config
        capital = cfg.initial_capital
        position: Optional[Position] = None
        trades: list[TradeRecord] = []
        equity_curve = [capital]

        # ATR 계산
        atr_series = calc_atr(df)

        # 시그널 생성
        signals = signal_fn(df)

        risk_mgr = RiskManager(capital)
        last_date = None

        for i in range(1, len(df)):
            row = df.iloc[i]
            prev_row = df.iloc[i - 1]
            current_price = row["close"]
            atr = atr_series.iloc[i] if not pd.isna(atr_series.iloc[i]) else 0

            # 일일 리셋 (날짜 변경 감지)
            current_date = str(row["timestamp"])[:10]
            if last_date is not None and current_date != last_date:
                risk_mgr.state.daily_pnl = 0.0
                risk_mgr.state.daily_start_balance = risk_mgr.state.current_balance
                risk_mgr.state.daily_trade_halted = False
            last_date = current_date

            # ── 기존 포지션 관리 ──
            if position is not None:
                # 트레일링 스탑 업데이트
                if "trailing" in cfg.exit_type and atr > 0:
                    mult = cfg.atr_trailing_mult
                    position = ExitCondition.update_trailing_stop(position, current_price, atr, mult)

                # 청산 조건 체크
                bars_held = i - position.entry_index
                should_exit, reason = ExitCondition.should_exit(
                    position, row, current_price, atr, cfg.exit_type, cfg.max_bars
                )

                # 시간 기반 청산
                if cfg.max_bars > 0 and bars_held >= cfg.max_bars:
                    should_exit, reason = True, "time_exit"

                if should_exit:
                    # 슬리피지 적용
                    if position.side == "long":
                        exit_price = current_price * (1 - cfg.slippage_rate)
                        raw_pnl = (exit_price - position.entry_price) * position.quantity
                    else:
                        exit_price = current_price * (1 + cfg.slippage_rate)
                        raw_pnl = (position.entry_price - exit_price) * position.quantity

                    # 레버리지 적용
                    raw_pnl *= position.leverage

                    # 수수료 (진입 + 청산)
                    entry_fee = position.entry_price * position.quantity * cfg.fee_rate
                    exit_fee = exit_price * position.quantity * cfg.fee_rate
                    total_fee = entry_fee + exit_fee

                    net_pnl = raw_pnl - total_fee
                    pnl_pct = net_pnl / capital * 100

                    capital += net_pnl
                    risk_mgr.update_on_trade_close(net_pnl)

                    trades.append(TradeRecord(
                        entry_time=str(df.iloc[position.entry_index]["timestamp"]),
                        exit_time=str(row["timestamp"]),
                        symbol=symbol,
                        side=position.side,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        pnl=net_pnl,
                        pnl_pct=pnl_pct,
                        fee=total_fee,
                        exit_reason=reason,
                        signal_grade=position.signal_grade,
                        bars_held=bars_held,
                    ))

                    # 리스크 매니저에서 포지션 제거
                    if symbol in risk_mgr.state.open_positions:
                        del risk_mgr.state.open_positions[symbol]

                    position = None

            # ── 새 포지션 진입 ──
            if position is None and signals.iloc[i] != 0 and atr > 0:
                signal = signals.iloc[i]
                side = "long" if signal == 1 else "short"
                grade = df.iloc[i].get("cmp_grade", "")

                # 리스크 체크
                can_open, msg = risk_mgr.can_open_position(symbol)
                if not can_open:
                    equity_curve.append(capital)
                    continue

                # TP/SL 계산
                tp, sl = ExitCondition.fixed_tp_sl(
                    current_price, side, atr, cfg.tp_sl_ratio
                )

                # 슬리피지 적용한 진입가
                if side == "long":
                    entry_price = current_price * (1 + cfg.slippage_rate)
                else:
                    entry_price = current_price * (1 - cfg.slippage_rate)

                # 포지션 사이즈
                quantity = risk_mgr.calc_position_size(
                    capital, entry_price, sl, cfg.leverage
                )

                if quantity > 0:
                    position = Position(
                        symbol=symbol,
                        side=side,
                        entry_price=entry_price,
                        quantity=quantity,
                        leverage=cfg.leverage,
                        stop_loss=sl,
                        take_profit=tp,
                        entry_index=i,
                        highest_price=entry_price,
                        lowest_price=entry_price,
                        signal_grade=str(grade),
                        strategy=strategy_name,
                    )
                    risk_mgr.state.open_positions[symbol] = True

            equity_curve.append(capital)

        # ── 미청산 포지션 강제 청산 ──
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
                pnl=net_pnl,
                pnl_pct=net_pnl / cfg.initial_capital * 100,
                fee=fee,
                exit_reason="end_of_data",
                signal_grade=position.signal_grade,
                bars_held=len(df) - 1 - position.entry_index,
            ))
            equity_curve.append(capital)

        # ── 성과 지표 계산 ──
        result = self._calc_metrics(trades, equity_curve, strategy_name, cfg)
        return result

    def _calc_metrics(self, trades: list[TradeRecord], equity_curve: list,
                      strategy_name: str, cfg: BacktestConfig) -> BacktestResult:
        """성과 지표 계산"""
        result = BacktestResult(
            strategy_name=strategy_name,
            exit_type=cfg.exit_type,
            trades=trades,
            equity_curve=equity_curve,
        )

        if not trades:
            return result

        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        result.total_trades = len(trades)
        result.winning_trades = len(wins)
        result.losing_trades = len(losses)

        # 총수익률
        result.total_return_pct = (equity_curve[-1] - cfg.initial_capital) / cfg.initial_capital * 100

        # 승률
        result.win_rate = len(wins) / len(trades) * 100 if trades else 0

        # 손익비 (평균 이익 / 평균 손실)
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 1
        result.avg_pnl_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # Profit Factor
        total_win = sum(wins) if wins else 0
        total_loss = abs(sum(losses)) if losses else 1
        result.profit_factor = total_win / total_loss if total_loss > 0 else 0

        # MDD (Maximum Drawdown)
        eq = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        drawdown = (eq - peak) / peak * 100
        result.max_drawdown_pct = abs(drawdown.min())

        # 샤프비율 (연간화, 4시간봉 기준 = 6봉/일 * 365일)
        if len(pnls) > 1:
            returns = pd.Series(pnls) / cfg.initial_capital
            if returns.std() > 0:
                result.sharpe_ratio = returns.mean() / returns.std() * np.sqrt(6 * 365)
            else:
                result.sharpe_ratio = 0
        else:
            result.sharpe_ratio = 0

        # 평균 보유 기간
        result.avg_bars_held = np.mean([t.bars_held for t in trades])

        return result
