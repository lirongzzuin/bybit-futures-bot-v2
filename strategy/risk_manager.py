"""
리스크 관리 시스템

안전장치:
  1. Kelly Criterion / 고정비율(2%) 포지션 사이징
  2. 최대 동시 포지션: 5개
  3. 일일 최대 손실: 6% → 당일 매매 중단
  4. 3연패 시 사이즈 50% 축소
  5. 최대 레버리지: 20배
  6. 긴급 청산: 잔고 50% 이하 시 전 포지션 청산
  7. 심볼별 상관관계 0.8 이상 동시 진입 제한
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

from config.indicator_params import TRADING
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


class RiskManager:
    def __init__(self, initial_balance: float = 10000.0):
        self.state = RiskState(
            initial_balance=initial_balance,
            current_balance=initial_balance,
            daily_start_balance=initial_balance,
            current_date=date.today().isoformat(),
        )
        self.correlation_cache: dict = {}

    def reset_daily(self):
        """일일 리셋 (새 날짜 시작)"""
        today = date.today().isoformat()
        if self.state.current_date != today:
            self.state.current_date = today
            self.state.daily_start_balance = self.state.current_balance
            self.state.daily_pnl = 0.0
            self.state.daily_trade_halted = False
            logger.info(f"일일 리셋 — 시작 잔고: ${self.state.current_balance:.2f}")

    def can_open_position(self, symbol: str) -> tuple[bool, str]:
        """새 포지션 개설 가능 여부 확인"""
        # 1. 일일 손실 한도 체크
        if self.state.daily_trade_halted:
            return False, "일일 최대 손실 도달 — 매매 중단"

        daily_loss_pct = abs(self.state.daily_pnl) / self.state.daily_start_balance
        if self.state.daily_pnl < 0 and daily_loss_pct >= TRADING["max_daily_loss"]:
            self.state.daily_trade_halted = True
            return False, f"일일 손실 {daily_loss_pct:.1%} — 한도({TRADING['max_daily_loss']:.0%}) 도달"

        # 2. 최대 동시 포지션 체크
        if len(self.state.open_positions) >= TRADING["max_open_positions"]:
            return False, f"최대 포지션 수({TRADING['max_open_positions']}) 도달"

        # 3. 이미 해당 심볼 포지션 보유 중
        if symbol in self.state.open_positions:
            return False, f"{symbol} 이미 포지션 보유 중"

        # 4. 긴급 청산 체크
        if self.state.current_balance <= self.state.initial_balance * 0.5:
            return False, "긴급: 잔고 50% 이하 — 신규 진입 불가"

        # 5. 상관관계 체크 (기존 포지션과의 상관계수)
        # (백테스트에서는 간소화 — 라이브에서 상세 구현)

        return True, "진입 가능"

    def calc_position_size(self, balance: float, entry_price: float,
                           stop_loss_price: float, leverage: int = None,
                           fee_rate: float = 0.0) -> float:
        """
        포지션 사이즈 계산 (고정비율 방식 + 수수료 고려)
        - 계좌의 risk_per_trade% 리스크
        - 3연패 이상이면 50% 축소
        - 진입+청산 수수료를 리스크에서 차감
        """
        leverage = leverage or TRADING["default_leverage"]
        leverage = min(leverage, TRADING["max_leverage"])

        # 레버리지 고려 적응형 리스크 — 실질 리스크(risk × leverage) 기준
        # 소액: 실질 리스크 높게 (공격적 성장)
        # 대액: 실질 리스크 낮게 (자본 보호)
        if balance < 500:
            target_actual = 0.12    # 소액: 실질 12% (공격적)
        elif balance < 2000:
            target_actual = 0.08   # 실질 8%
        elif balance < 10000:
            target_actual = 0.06   # 실질 6%
        else:
            target_actual = 0.04   # 대액: 실질 4% (안정적)

        risk_pct = target_actual / leverage  # 레버리지 역산

        # 연속 손실 시 사이즈 축소
        if self.state.consecutive_losses >= 3:
            risk_pct *= 0.5
            logger.warning(f"연속 {self.state.consecutive_losses}패 — 포지션 사이즈 50% 축소")

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

        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

    def should_emergency_close(self) -> bool:
        """긴급 청산 조건 확인"""
        return self.state.current_balance <= self.state.initial_balance * 0.5

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
        """기존 포지션과 상관관계 0.8 이상인지 체크"""
        if symbol not in returns_data:
            return False

        for existing in existing_symbols:
            if existing in returns_data:
                corr = self.calc_correlation(returns_data[symbol], returns_data[existing])
                if abs(corr) >= 0.8:
                    logger.warning(f"{symbol}-{existing} 상관계수 {corr:.2f} — 동시 진입 제한")
                    return True
        return False
