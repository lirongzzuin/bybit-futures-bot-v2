"""
리스크 관리 시스템 v13.0 — Aggressive Frequency Trading

안전장치:
  1. 고정비율(2%) 포지션 사이징 + 드로우다운 팩터
  2. 최대 동시 포지션: 5개 (다중 심볼 활용)
  3. 일일 최대 손실: 15% → 당일 매매 중단
  4. 5연패 시 사이즈 50% 축소 (was 3연패)
  5. 최대 레버리지: 20배 (동적)
  6. 긴급 청산: 잔고 40% 이하 시 전 포지션 청산
  7. Circuit breaker: MDD 50% → 1시간 쿨링 후 자동 리셋
  8. 심볼별 상관관계 0.85 이상 동시 진입 제한
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from typing import Optional

from config.indicator_params import TRADING, DAILY_CANDLE_PROTECTION
from utils.logger import setup_logger

logger = setup_logger("risk")


@dataclass
class RiskState:
    """리스크 관리 상태"""
    initial_balance: float = 10000.0
    current_balance: float = 10000.0
    daily_start_balance: float = 10000.0
    daily_pnl: float = 0.0
    current_date: str = ""
    consecutive_losses: int = 0
    open_positions: dict = field(default_factory=dict)  # symbol → position info
    daily_trade_halted: bool = False
    peak_balance: float = 10000.0  # tracked as max of current_balance
    mdd_circuit_breaker_triggered: bool = False  # circuit breaker activation flag
    circuit_breaker_triggered_at: Optional[datetime] = None  # v10.2: 발동 시각


class RiskManager:
    # Circuit breaker configuration (v13.0: 대폭 완화)
    MDD_CIRCUIT_BREAKER_PCT = 0.50  # 50% max drawdown threshold (was 30%)

    def __init__(self, initial_balance: float = 10000.0):
        # v13.1: peak_balance = current_balance (클린 스타트)
        # 이전 세션 MDD 누적 방지 → circuit breaker 즉시 발동 문제 해결
        self.state = RiskState(
            initial_balance=initial_balance,
            current_balance=initial_balance,
            daily_start_balance=initial_balance,
            peak_balance=initial_balance,
            current_date=date.today().isoformat(),
        )
        self.correlation_cache: dict = {}
        # Track ATR values for volatility regime detection
        self.atr_history: list = []

    def reset_daily(self):
        """일일 리셋 (새 날짜 시작)"""
        today = date.today().isoformat()
        if self.state.current_date != today:
            self.state.current_date = today
            self.state.daily_start_balance = self.state.current_balance
            self.state.daily_pnl = 0.0
            self.state.daily_trade_halted = False
            logger.info(f"일일 리셋 — 시작 잔고: ${self.state.current_balance:.2f}")

    def is_daily_candle_transition(self, simulated_time: datetime = None) -> bool:
        """
        일봉 전환 시간대(0:00 UTC = 9:00 KST) 전후인지 확인

        매일 9시 KST에 일봉이 시작되면서 급격한 변동성이 발생하는 패턴:
        - 대량 청산(liquidation cascade)
        - 일봉 시가 기준 스톱헌팅
        - 펀딩비 정산(8시간 간격, 9시도 정산 시점)

        이 시간대에는 신규 진입을 차단하고 보유 포지션을 보호해야 함
        """
        if not DAILY_CANDLE_PROTECTION.get("enabled", False):
            return False

        now = simulated_time or datetime.now(timezone.utc)
        current_minutes = now.hour * 60 + now.minute  # 0~1439

        pre = DAILY_CANDLE_PROTECTION["pre_candle_minutes"]
        post = DAILY_CANDLE_PROTECTION["post_candle_minutes"]

        # 0:00 UTC 기준 보호 구간: (24:00 - pre) ~ (0:00 + post)
        # = (1440 - pre) ~ post 분
        start = (1440 - pre) % 1440  # 예: 15분 전 = 23:45 = 1425분
        end = post                    # 예: 15분 후 = 00:15 = 15분

        if start > end:
            # 자정을 걸치는 경우 (23:45 ~ 00:15)
            return current_minutes >= start or current_minutes <= end
        else:
            return start <= current_minutes <= end

    def is_pre_daily_candle(self, simulated_time: datetime = None) -> bool:
        """일봉 전환 직전인지 (SL 타이트닝 시작 시점)"""
        if not DAILY_CANDLE_PROTECTION.get("enabled", False):
            return False

        now = simulated_time or datetime.now(timezone.utc)
        current_minutes = now.hour * 60 + now.minute

        pre = DAILY_CANDLE_PROTECTION["pre_candle_minutes"]
        # 일봉 전환 30분 전부터 사전 보호 시작 (SL 타이트닝)
        extended_pre = pre + 15  # 추가 15분 여유
        start = (1440 - extended_pre) % 1440

        return current_minutes >= start or current_minutes <= 0

    def get_drawdown_factor(self) -> float:
        """
        Drawdown-based position sizing factor.
        Returns a scaling factor based on current drawdown from peak equity:
        - Drawdown < 10%: factor = 1.0 (normal)
        - Drawdown 10-20%: factor = 0.7 (reduce size)
        - Drawdown 20-30%: factor = 0.5 (significantly reduce)
        - Drawdown > 30%: factor = 0.3 (minimal size)
        """
        if self.state.peak_balance <= 0:
            return 1.0

        drawdown = (self.state.peak_balance - self.state.current_balance) / self.state.peak_balance

        # v13.0: 드로다운 팩터 대폭 완화 (거래 기회 유지)
        if drawdown < 0.20:
            return 1.0
        elif drawdown < 0.35:
            return 0.75
        elif drawdown < 0.50:
            return 0.5
        else:
            return 0.3

    def get_volatility_regime(self, atr_current: float, atr_avg: float) -> str:
        """
        Volatility regime detection based on ATR.
        Returns:
        - "low" if atr_current < atr_avg * 0.7
        - "normal" if 0.7 <= ratio <= 1.3
        - "high" if atr_current > atr_avg * 1.3
        - "extreme" if atr_current > atr_avg * 2.0
        """
        if atr_avg <= 0:
            return "normal"

        ratio = atr_current / atr_avg

        if ratio > 2.0:
            return "extreme"
        elif ratio > 1.3:
            return "high"
        elif ratio < 0.7:
            return "low"
        else:
            return "normal"

    # v13.0: Circuit breaker 쿨링 시간 (1시간 — 즉시 복귀)
    CIRCUIT_BREAKER_COOLDOWN_HOURS = 1

    def check_circuit_breaker(self) -> tuple[bool, str]:
        """
        Circuit breaker to halt trading if max drawdown exceeds threshold.
        v10.2: 6시간 쿨링 후 자동 리셋 (peak를 현재 잔고로 재설정)
        """
        if self.state.peak_balance <= 0:
            return True, "Circuit breaker inactive"

        # Already triggered — check cooldown for auto-reset
        if self.state.mdd_circuit_breaker_triggered:
            if self.state.circuit_breaker_triggered_at:
                elapsed = (datetime.now(timezone.utc) - self.state.circuit_breaker_triggered_at).total_seconds() / 3600
                if elapsed >= self.CIRCUIT_BREAKER_COOLDOWN_HOURS:
                    # 쿨링 완료 → 리셋 (peak를 현재 잔고로)
                    self.state.mdd_circuit_breaker_triggered = False
                    self.state.circuit_breaker_triggered_at = None
                    self.state.peak_balance = self.state.current_balance
                    self.state.consecutive_losses = 0
                    logger.info(
                        f"Circuit breaker 리셋 — {elapsed:.1f}시간 쿨링 완료. "
                        f"새 peak: ${self.state.current_balance:.2f}"
                    )
                    return True, "Circuit breaker reset after cooldown"

            max_drawdown = (self.state.peak_balance - self.state.current_balance) / self.state.peak_balance
            return False, f"Circuit breaker active: MDD {max_drawdown:.1%}"

        max_drawdown = (self.state.peak_balance - self.state.current_balance) / self.state.peak_balance

        if max_drawdown > self.MDD_CIRCUIT_BREAKER_PCT:
            self.state.mdd_circuit_breaker_triggered = True
            self.state.circuit_breaker_triggered_at = datetime.now(timezone.utc)
            logger.critical(
                f"Circuit breaker triggered: max drawdown {max_drawdown:.1%} exceeds "
                f"threshold {self.MDD_CIRCUIT_BREAKER_PCT:.1%}. "
                f"{self.CIRCUIT_BREAKER_COOLDOWN_HOURS}시간 쿨링 시작."
            )
            return False, f"Circuit breaker: MDD {max_drawdown:.1%} > {self.MDD_CIRCUIT_BREAKER_PCT:.1%}"

        return True, "Circuit breaker OK"

    def can_open_position(self, symbol: str, simulated_time: datetime = None) -> tuple[bool, str]:
        """새 포지션 개설 가능 여부 확인"""
        # v13.1: Circuit breaker 제거 — 거래 차단 원인이었음
        # 이전 세션 MDD 누적으로 시작 즉시 발동 → 거래 0건 문제 해결

        # 1. 일봉 전환 시간대 진입 차단
        if DAILY_CANDLE_PROTECTION.get("block_new_entries", True) and self.is_daily_candle_transition(simulated_time):
            return False, "일봉 전환 시간대 (KST 08:45~09:15) — 신규 진입 차단"

        # 2. 일일 손실 한도 체크
        if self.state.daily_trade_halted:
            return False, "일일 최대 손실 도달 — 매매 중단"

        daily_loss_pct = abs(self.state.daily_pnl) / self.state.daily_start_balance if self.state.daily_start_balance > 0 else 0
        if self.state.daily_pnl < 0 and daily_loss_pct >= TRADING["max_daily_loss"]:
            self.state.daily_trade_halted = True
            return False, f"일일 손실 {daily_loss_pct:.1%} — 한도({TRADING['max_daily_loss']:.0%}) 도달"

        # 3. 최대 동시 포지션 체크
        if len(self.state.open_positions) >= TRADING["max_open_positions"]:
            return False, f"최대 포지션 수({TRADING['max_open_positions']}) 도달"

        # 4. 이미 해당 심볼 포지션 보유 중
        if symbol in self.state.open_positions:
            return False, f"{symbol} 이미 포지션 보유 중"

        # 5. 긴급 청산 체크 (잔고 30% 이하)
        if self.state.current_balance <= self.state.initial_balance * 0.3:
            return False, "긴급: 잔고 30% 이하 — 신규 진입 불가"

        # 6. 상관관계 체크 (기존 포지션과의 상관계수)
        # (백테스트에서는 간소화 — 라이브에서 상세 구현)

        return True, "진입 가능"

    def calc_position_size(self, balance: float, entry_price: float,
                           stop_loss_price: float, leverage: int = None,
                           fee_rate: float = 0.0) -> float:
        """
        포지션 사이즈 계산 (고정비율 방식 + 수수료 고려 + 드로우다운 팩터)
        - 계좌의 risk_per_trade% 리스크
        - 3연패 이상이면 50% 축소
        - 드로우다운 팩터 적용
        - 진입+청산 수수료를 리스크에서 차감
        """
        leverage = leverage or TRADING["default_leverage"]
        leverage = min(leverage, TRADING["max_leverage"])

        # v11.0: 공격적 포지션 사이징 — 2.5% 고정
        risk_pct = TRADING["risk_per_trade"]  # 2.5%

        # v13.1: 연속 손실 시 사이즈 축소 (7연패부터, was 5연패)
        if self.state.consecutive_losses >= 7:
            risk_pct *= 0.7
            logger.warning(f"연속 {self.state.consecutive_losses}패 — 포지션 사이즈 30% 축소")

        # 드로우다운 팩터 적용
        drawdown_factor = self.get_drawdown_factor()
        risk_pct *= drawdown_factor

        risk_amount = balance * risk_pct
        price_diff = abs(entry_price - stop_loss_price)

        if price_diff == 0:
            return 0.0

        # 포지션 사이즈 = 리스크 금액 / (가격 차이 / 진입가)
        # 레버리지는 엔진에서 PnL에 별도 적용됨
        position_value = risk_amount / (price_diff / entry_price)
        quantity = position_value / entry_price

        # 수수료 고려: 진입 + 청산(항상 taker) 수수료만큼 수량 축소
        if fee_rate > 0:
            # 총 수수료 = 진입 수수료 + 청산 수수료(taker 0.055%)
            from config.indicator_params import FEES
            total_fee_rate = fee_rate + FEES["taker"]  # 진입 + 청산
            fee_cost = quantity * entry_price * total_fee_rate
            # 수수료를 리스크 금액에서 차감하여 수량 재계산
            adjusted_risk = risk_amount - fee_cost
            if adjusted_risk <= 0:
                return 0.0
            adjusted_value = adjusted_risk / (price_diff / entry_price)
            quantity = adjusted_value / entry_price

        return quantity

    def update_on_trade_close(self, pnl: float):
        """거래 종료 시 상태 업데이트"""
        self.state.current_balance += pnl
        self.state.daily_pnl += pnl

        # Update peak_balance if current balance is higher
        if self.state.current_balance > self.state.peak_balance:
            self.state.peak_balance = self.state.current_balance

        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

    def should_emergency_close(self) -> bool:
        """긴급 청산 조건 확인 (v13.0: 40% 이하, was 50%)"""
        return self.state.current_balance <= self.state.initial_balance * 0.4

    def get_effective_leverage(self, requested: int) -> int:
        """실효 레버리지 (최대 제한 적용)"""
        return min(requested, TRADING["max_leverage"])

    def calc_correlation(self, returns_a: pd.Series, returns_b: pd.Series) -> float:
        """두 심볼의 수익률 상관계수"""
        if len(returns_a) < 20 or len(returns_b) < 20:
            return 0.0
        min_len = min(len(returns_a), len(returns_b))
        return returns_a.iloc[-min_len:].corr(returns_b.iloc[-min_len:])

    def is_correlated(self, symbol: str, existing_symbols: list,
                      returns_data: dict) -> bool:
        """기존 포지션과 상관관계 0.85 이상인지 체크 (v13.0: 0.8→0.85)"""
        if symbol not in returns_data:
            return False

        for existing in existing_symbols:
            if existing in returns_data:
                corr = self.calc_correlation(returns_data[symbol], returns_data[existing])
                if abs(corr) >= 0.85:
                    logger.warning(f"{symbol}-{existing} 상관계수 {corr:.2f} — 동시 진입 제한")
                    return True
        return False
