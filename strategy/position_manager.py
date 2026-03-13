"""
포지션 관리자
- 진입/청산/손절/익절 로직
- ATR 기반 트레일링 스탑
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    """포지션 정보"""
    symbol: str
    side: str           # 'long' / 'short'
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: float
    take_profit: float
    entry_index: int    # 진입 봉 인덱스
    trailing_stop: Optional[float] = None
    highest_price: float = 0.0   # 롱: 최고가 추적
    lowest_price: float = 1e18   # 숏: 최저가 추적
    signal_grade: str = ""
    strategy: str = ""


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR (Average True Range) 계산"""
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs()
    ], axis=1).max(axis=1)

    return tr.rolling(period).mean()


class ExitCondition:
    """청산 조건 정의"""

    @staticmethod
    def fixed_tp_sl(entry_price: float, side: str, atr: float,
                    tp_ratio: float = 2.0) -> tuple[float, float]:
        """
        고정 TP/SL — ATR 기반
        Returns: (take_profit, stop_loss)
        """
        if side == "long":
            sl = entry_price - atr
            tp = entry_price + atr * tp_ratio
        else:
            sl = entry_price + atr
            tp = entry_price - atr * tp_ratio
        return tp, sl

    @staticmethod
    def update_trailing_stop(pos: Position, current_price: float,
                             atr: float, atr_mult: float = 1.5) -> Position:
        """트레일링 스탑 업데이트"""
        if pos.side == "long":
            pos.highest_price = max(pos.highest_price, current_price)
            new_trail = pos.highest_price - atr * atr_mult
            if pos.trailing_stop is None or new_trail > pos.trailing_stop:
                pos.trailing_stop = new_trail
        else:
            pos.lowest_price = min(pos.lowest_price, current_price)
            new_trail = pos.lowest_price + atr * atr_mult
            if pos.trailing_stop is None or new_trail < pos.trailing_stop:
                pos.trailing_stop = new_trail
        return pos

    @staticmethod
    def should_exit(pos: Position, current_row: pd.Series,
                    current_price: float, atr: float,
                    exit_type: str = "trailing_atr_1.5x",
                    max_bars: int = 0) -> tuple[bool, str]:
        """
        청산 여부 판단

        exit_type:
          - fixed_1:1, fixed_1.5:1, fixed_2:1, fixed_3:1
          - trailing_atr_1x, trailing_atr_1.5x, trailing_atr_2x
          - opposite_signal
          - ichimoku_kumo
          - sd_zone
          - time_N (N봉 후 강제)

        Returns: (should_exit, reason)
        """
        # TP/SL 체크 (항상)
        if pos.side == "long":
            if current_price >= pos.take_profit:
                return True, "take_profit"
            if current_price <= pos.stop_loss:
                return True, "stop_loss"
        else:
            if current_price <= pos.take_profit:
                return True, "take_profit"
            if current_price >= pos.stop_loss:
                return True, "stop_loss"

        # 트레일링 스탑 체크
        if pos.trailing_stop is not None:
            if pos.side == "long" and current_price <= pos.trailing_stop:
                return True, "trailing_stop"
            if pos.side == "short" and current_price >= pos.trailing_stop:
                return True, "trailing_stop"

        # 반대 신호 청산
        if "opposite" in exit_type:
            if pos.side == "long" and current_row.get("cmp_signal", 0) == -1:
                return True, "opposite_signal"
            if pos.side == "short" and current_row.get("cmp_signal", 0) == 1:
                return True, "opposite_signal"

        # Ichimoku 구름 진입 시 청산
        if "ichimoku" in exit_type:
            kumo_top = current_row.get("kumo_top", 0)
            kumo_bottom = current_row.get("kumo_bottom", 0)
            if pos.side == "long" and current_price < kumo_top:
                return True, "kumo_entry"
            if pos.side == "short" and current_price > kumo_bottom:
                return True, "kumo_entry"

        # S/D 반대 영역
        if "sd_zone" in exit_type:
            if pos.side == "long" and current_row.get("in_supply_zone", 0) == 1:
                return True, "supply_zone"
            if pos.side == "short" and current_row.get("in_demand_zone", 0) == 1:
                return True, "demand_zone"

        # 시간 기반 (max_bars 봉 후)
        if max_bars > 0:
            # entry_index는 외부에서 체크
            pass

        return False, ""
