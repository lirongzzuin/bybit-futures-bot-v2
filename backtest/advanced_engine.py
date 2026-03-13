"""
고급 백테스트 엔진
- 동적 레버리지 지원
- 동적 TP/SL (ATR 배수)
- 트레일링 스탑
- 부분 청산
- 상세 성과 분석
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, Callable

from strategy.advanced_strategy import calc_atr


@dataclass
class AdvancedConfig:
    """백테스트 설정"""
    initial_capital: float = 10000.0
    fee_rate: float = 0.0006       # 0.06%
    slippage_rate: float = 0.0005  # 0.05%
    risk_per_trade: float = 0.02   # 계좌의 2%
    max_daily_loss: float = 0.06   # 6%
    use_trailing_stop: bool = True
    trailing_activation: float = 0.5  # TP의 50% 도달 시 트레일링 시작
    trailing_step: float = 0.5        # ATR의 0.5배 단위로 SL 올림
    max_consecutive_loss_reduce: int = 3  # N연패 후 사이즈 축소
    partial_close_at: float = 0.0  # TP의 N% 도달 시 50% 부분 청산 (0=비활성)
    max_hold_bars: int = 0         # 최대 보유 기간 (0=무제한)
    min_rr_ratio: float = 1.5      # 최소 리스크:리워드 비율


@dataclass
class Trade:
    """거래 기록"""
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
    strength: float
    bars_held: int
    tp_price: float
    sl_price: float


@dataclass
class BacktestMetrics:
    """성과 지표"""
    strategy_name: str
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_rr_ratio: float = 0.0
    total_trades: int = 0
    avg_bars_held: float = 0.0
    long_trades: int = 0
    short_trades: int = 0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_leverage: float = 0.0
    final_balance: float = 0.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    monthly_returns: list = field(default_factory=list)


class AdvancedBacktestEngine:
    """
    고급 백테스트 엔진
    - 동적 레버리지/TP/SL
    - 트레일링 스탑
    - 일일 손실 한도
    """

    def __init__(self, config: Optional[AdvancedConfig] = None):
        self.config = config or AdvancedConfig()

    def run(
        self,
        df: pd.DataFrame,
        strategy_name: str = "advanced",
        symbol: str = "BTCUSDT",
    ) -> BacktestMetrics:
        """
        백테스트 실행

        df에는 아래 컬럼이 필요:
          sig_direction, sig_strength, sig_leverage, sig_tp_mult, sig_sl_mult
        """
        cfg = self.config
        capital = cfg.initial_capital
        position = None  # {side, entry, qty, lev, tp, sl, trail_sl, idx, strength, partial_closed}
        trades: list[Trade] = []
        equity = [capital]

        atr = calc_atr(df).values
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        timestamps = df["timestamp"].values

        sig_dir = df["sig_direction"].values
        sig_str = df["sig_strength"].values
        sig_lev = df["sig_leverage"].values
        sig_tp = df["sig_tp_mult"].values
        sig_sl = df["sig_sl_mult"].values

        # 일일 손실 추적
        daily_pnl = 0.0
        daily_start = capital
        last_date = ""
        daily_halted = False
        consecutive_losses = 0

        for i in range(1, len(df)):
            current_atr = atr[i]
            if np.isnan(current_atr) or current_atr <= 0:
                equity.append(capital)
                continue

            c = close[i]
            h = high[i]
            l = low[i]
            ts = str(timestamps[i])[:10]

            # 일일 리셋
            if ts != last_date:
                daily_pnl = 0.0
                daily_start = capital
                daily_halted = False
                last_date = ts

            # ── 포지션 관리 ──
            if position is not None:
                side = position["side"]
                entry = position["entry"]
                tp = position["tp"]
                sl = position["sl"]
                trail_sl = position.get("trail_sl", sl)
                bars_held = i - position["idx"]

                # 트레일링 스탑 업데이트
                if cfg.use_trailing_stop:
                    if side == "long":
                        # TP 방향으로 50% 이상 이동 시 트레일링 시작
                        tp_progress = (h - entry) / (tp - entry) if tp != entry else 0
                        if tp_progress >= cfg.trailing_activation:
                            new_trail = h - current_atr * cfg.trailing_step
                            if new_trail > trail_sl:
                                trail_sl = new_trail
                                position["trail_sl"] = trail_sl
                    else:
                        tp_progress = (entry - l) / (entry - tp) if tp != entry else 0
                        if tp_progress >= cfg.trailing_activation:
                            new_trail = l + current_atr * cfg.trailing_step
                            if new_trail < trail_sl:
                                trail_sl = new_trail
                                position["trail_sl"] = trail_sl

                # 청산 조건 체크
                exit_price = None
                exit_reason = ""

                if side == "long":
                    if h >= tp:
                        exit_price = tp * (1 - cfg.slippage_rate)
                        exit_reason = "take_profit"
                    elif l <= trail_sl:
                        exit_price = trail_sl * (1 - cfg.slippage_rate)
                        exit_reason = "trailing_stop" if trail_sl > sl else "stop_loss"
                    elif l <= sl:
                        exit_price = sl * (1 - cfg.slippage_rate)
                        exit_reason = "stop_loss"
                else:
                    if l <= tp:
                        exit_price = tp * (1 + cfg.slippage_rate)
                        exit_reason = "take_profit"
                    elif h >= trail_sl:
                        exit_price = trail_sl * (1 + cfg.slippage_rate)
                        exit_reason = "trailing_stop" if trail_sl < sl else "stop_loss"
                    elif h >= sl:
                        exit_price = sl * (1 + cfg.slippage_rate)
                        exit_reason = "stop_loss"

                # 최대 보유 기간 초과
                if exit_price is None and cfg.max_hold_bars > 0 and bars_held >= cfg.max_hold_bars:
                    exit_price = c * (1 - cfg.slippage_rate if side == "long" else 1 + cfg.slippage_rate)
                    exit_reason = "time_exit"

                # 반대 시그널 (강한 시그널만)
                if exit_price is None and sig_str[i] > 0.5:
                    if (side == "long" and sig_dir[i] == -1) or (side == "short" and sig_dir[i] == 1):
                        exit_price = c
                        exit_reason = "reverse_signal"

                if exit_price is not None:
                    qty = position["qty"]
                    lev = position["lev"]

                    if side == "long":
                        raw_pnl = (exit_price - entry) / entry * qty * lev
                    else:
                        raw_pnl = (entry - exit_price) / entry * qty * lev

                    fee = (entry + exit_price) * (qty / entry) * cfg.fee_rate
                    net_pnl = raw_pnl - fee

                    capital += net_pnl
                    daily_pnl += net_pnl

                    if net_pnl < 0:
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0

                    trades.append(Trade(
                        entry_time=str(timestamps[position["idx"]]),
                        exit_time=str(timestamps[i]),
                        symbol=symbol, side=side,
                        entry_price=entry, exit_price=exit_price,
                        quantity=qty, leverage=lev,
                        pnl=net_pnl, pnl_pct=net_pnl / cfg.initial_capital * 100,
                        fee=fee, exit_reason=exit_reason,
                        strength=position["strength"],
                        bars_held=bars_held,
                        tp_price=tp, sl_price=sl,
                    ))

                    position = None

            # ── 새 포지션 진입 ──
            if position is None and sig_dir[i] != 0 and not daily_halted:
                # 일일 손실 한도 체크
                if daily_pnl < 0 and abs(daily_pnl) / daily_start >= cfg.max_daily_loss:
                    daily_halted = True
                    equity.append(capital)
                    continue

                direction = sig_dir[i]
                strength = sig_str[i]
                leverage = int(sig_lev[i])
                tp_mult = sig_tp[i]
                sl_mult = sig_sl[i]

                # RR 비율 체크
                if tp_mult / sl_mult < cfg.min_rr_ratio:
                    equity.append(capital)
                    continue

                side = "long" if direction == 1 else "short"

                # 진입가 (슬리피지)
                if side == "long":
                    entry = c * (1 + cfg.slippage_rate)
                    tp = entry + current_atr * tp_mult
                    sl = entry - current_atr * sl_mult
                else:
                    entry = c * (1 - cfg.slippage_rate)
                    tp = entry - current_atr * tp_mult
                    sl = entry + current_atr * sl_mult

                # 포지션 사이즈 (리스크 기반)
                risk_pct = cfg.risk_per_trade
                if consecutive_losses >= cfg.max_consecutive_loss_reduce:
                    risk_pct *= 0.5  # 연패 시 축소

                risk_amount = capital * risk_pct
                price_risk = abs(entry - sl)

                if price_risk <= 0:
                    equity.append(capital)
                    continue

                # qty = 진입에 사용할 USDT 금액
                qty = risk_amount / (price_risk / entry) / leverage * leverage
                qty = min(qty, capital * 0.95)  # 최대 95%

                if qty <= 0 or capital <= 100:
                    equity.append(capital)
                    continue

                position = {
                    "side": side, "entry": entry, "qty": qty,
                    "lev": leverage, "tp": tp, "sl": sl,
                    "trail_sl": sl, "idx": i, "strength": strength,
                }

            equity.append(capital)

        # 미청산 포지션 청산
        if position is not None:
            c = close[-1]
            side = position["side"]
            entry = position["entry"]
            qty = position["qty"]
            lev = position["lev"]

            if side == "long":
                raw_pnl = (c - entry) / entry * qty * lev
            else:
                raw_pnl = (entry - c) / entry * qty * lev

            fee = (entry + c) * (qty / entry) * cfg.fee_rate
            net_pnl = raw_pnl - fee
            capital += net_pnl

            trades.append(Trade(
                entry_time=str(timestamps[position["idx"]]),
                exit_time=str(timestamps[-1]),
                symbol=symbol, side=side,
                entry_price=entry, exit_price=c,
                quantity=qty, leverage=lev,
                pnl=net_pnl, pnl_pct=net_pnl / cfg.initial_capital * 100,
                fee=fee, exit_reason="end_of_data",
                strength=position["strength"],
                bars_held=len(df) - 1 - position["idx"],
                tp_price=position["tp"], sl_price=position["sl"],
            ))
            equity.append(capital)

        return self._calc_metrics(trades, equity, strategy_name, cfg)

    def _calc_metrics(self, trades: list[Trade], equity: list,
                      name: str, cfg: AdvancedConfig) -> BacktestMetrics:
        """상세 성과 지표 계산"""
        m = BacktestMetrics(strategy_name=name, trades=trades, equity_curve=equity)

        if not trades:
            m.final_balance = cfg.initial_capital
            return m

        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        m.total_trades = len(trades)
        m.final_balance = equity[-1]
        m.total_return_pct = (equity[-1] - cfg.initial_capital) / cfg.initial_capital * 100

        # CAGR (4시간봉 기준, 6봉/일, 365일/년)
        n_bars = len(equity)
        years = n_bars / (6 * 365)
        if years > 0 and equity[-1] > 0:
            m.cagr_pct = ((equity[-1] / cfg.initial_capital) ** (1 / years) - 1) * 100

        # 승률
        m.win_rate = len(wins) / len(trades) * 100

        # Profit Factor
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 1
        m.profit_factor = total_wins / total_losses if total_losses > 0 else 0

        # 평균 RR 비율
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 1
        m.avg_rr_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # MDD
        eq = np.array(equity)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak * 100
        m.max_drawdown_pct = abs(dd.min())

        # Calmar Ratio
        if m.max_drawdown_pct > 0:
            m.calmar_ratio = m.cagr_pct / m.max_drawdown_pct

        # Sharpe (연간화)
        returns = pd.Series(pnls) / cfg.initial_capital
        if returns.std() > 0:
            m.sharpe_ratio = returns.mean() / returns.std() * np.sqrt(6 * 365)

        # Sortino (하방 변동성만)
        neg_returns = returns[returns < 0]
        if len(neg_returns) > 0 and neg_returns.std() > 0:
            m.sortino_ratio = returns.mean() / neg_returns.std() * np.sqrt(6 * 365)

        # 롱/숏 분리
        long_trades = [t for t in trades if t.side == "long"]
        short_trades = [t for t in trades if t.side == "short"]
        m.long_trades = len(long_trades)
        m.short_trades = len(short_trades)
        m.long_win_rate = len([t for t in long_trades if t.pnl > 0]) / max(len(long_trades), 1) * 100
        m.short_win_rate = len([t for t in short_trades if t.pnl > 0]) / max(len(short_trades), 1) * 100

        # 연속 승/패
        streak = 0
        max_win_streak = 0
        max_loss_streak = 0
        for p in pnls:
            if p > 0:
                if streak > 0:
                    streak += 1
                else:
                    streak = 1
                max_win_streak = max(max_win_streak, streak)
            else:
                if streak < 0:
                    streak -= 1
                else:
                    streak = -1
                max_loss_streak = max(max_loss_streak, abs(streak))
        m.max_consecutive_wins = max_win_streak
        m.max_consecutive_losses = max_loss_streak

        # 평균 레버리지/보유기간
        m.avg_leverage = np.mean([t.leverage for t in trades])
        m.avg_bars_held = np.mean([t.bars_held for t in trades])

        return m


def print_metrics(m: BacktestMetrics, detailed: bool = False):
    """성과 지표 출력"""
    print(f"\n{'='*60}")
    print(f"  {m.strategy_name}")
    print(f"{'='*60}")
    print(f"  총수익률:     {m.total_return_pct:>10.1f}%")
    print(f"  최종잔고:     ${m.final_balance:>10.2f}")
    print(f"  CAGR:        {m.cagr_pct:>10.1f}%")
    print(f"  샤프비율:     {m.sharpe_ratio:>10.2f}")
    print(f"  소르티노:     {m.sortino_ratio:>10.2f}")
    print(f"  MDD:         {m.max_drawdown_pct:>10.1f}%")
    print(f"  칼마비율:     {m.calmar_ratio:>10.2f}")
    print(f"  승률:         {m.win_rate:>10.1f}%")
    print(f"  Profit Factor:{m.profit_factor:>10.2f}")
    print(f"  평균 RR:      {m.avg_rr_ratio:>10.2f}")
    print(f"  총거래:       {m.total_trades:>10d} (롱:{m.long_trades} 숏:{m.short_trades})")
    print(f"  롱승률/숏승률: {m.long_win_rate:.1f}% / {m.short_win_rate:.1f}%")
    print(f"  연속승/연속패: {m.max_consecutive_wins} / {m.max_consecutive_losses}")
    print(f"  평균레버리지:  {m.avg_leverage:>10.1f}x")
    print(f"  평균보유:      {m.avg_bars_held:>10.1f}봉")

    if detailed and m.trades:
        print(f"\n  최근 10거래:")
        for t in m.trades[-10:]:
            emoji = "🟢" if t.pnl > 0 else "🔴"
            print(f"    {emoji} {t.side:5s} lev:{t.leverage:>2d}x "
                  f"${t.entry_price:.0f}→${t.exit_price:.0f} "
                  f"pnl:{t.pnl:>+8.1f} ({t.exit_reason}) "
                  f"str:{t.strength:.2f} {t.bars_held}봉")
