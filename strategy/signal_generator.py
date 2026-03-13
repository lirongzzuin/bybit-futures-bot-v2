"""
3개 지표 종합 시그널 생성기
- Candle Master Plan + Ichimoku Cloud + Volume Analysis
- 백테스트 최적화 결과에 따라 최종 전략이 자동 반영됨
"""
import pandas as pd
import numpy as np
from typing import Optional

from indicators import candle_master_plan as cmp
from indicators import ichimoku as ichi
from indicators import volume_analysis as vol


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """3개 지표 모두 계산하여 df에 추가"""
    df = cmp.compute_all(df)
    df = ichi.compute_all(df)
    df = vol.compute_all(df)
    return df


# ─────────────────────────────────────────
# 전략 조합 정의 (백테스트 대상)
# ─────────────────────────────────────────

def strategy_1_gold_only(df: pd.DataFrame) -> pd.Series:
    """전략 1: CMP Gold 신호만 진입"""
    signal = pd.Series(0, index=df.index)
    signal[df["cmp_grade"] == "gold"] = df.loc[df["cmp_grade"] == "gold", "cmp_signal"]
    return signal


def strategy_2_gold_ichimoku(df: pd.DataFrame) -> pd.Series:
    """전략 2: Gold + Ichimoku 구름 위(롱)/아래(숏)"""
    signal = pd.Series(0, index=df.index)
    long_mask = (df["cmp_grade"] == "gold") & (df["cmp_signal"] == 1) & (df["close"] > df["kumo_top"])
    short_mask = (df["cmp_grade"] == "gold") & (df["cmp_signal"] == -1) & (df["close"] < df["kumo_bottom"])
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


def strategy_3_grade_volume(df: pd.DataFrame) -> pd.Series:
    """전략 3: Gold/Silver + Volume 급등 확인"""
    signal = pd.Series(0, index=df.index)
    grade_ok = df["cmp_grade"].isin(["gold", "silver"])
    long_mask = grade_ok & (df["cmp_signal"] == 1) & (df["vol_spike"] == 1)
    short_mask = grade_ok & (df["cmp_signal"] == -1) & (df["vol_spike"] == 1)
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


def strategy_4_ma_rsi_sd(df: pd.DataFrame) -> pd.Series:
    """전략 4: MA크로스 + RSI 반전 + S/D존 지지/저항"""
    signal = pd.Series(0, index=df.index)
    long_mask = (df["ma_bull"] == 1) & (df["rsi_bull"] == 1) & (df["sd_bull"] == 1)
    short_mask = (df["ma_bear"] == 1) & (df["rsi_bear"] == 1) & (df["sd_bear"] == 1)
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


def strategy_5_stoch_ichi(df: pd.DataFrame) -> pd.Series:
    """전략 5: 이중 스토캐스틱 과매도 + Ichimoku 기준선 위 반등"""
    signal = pd.Series(0, index=df.index)
    long_mask = (df["stoch_bull"] == 1) & (df["close"] > df["kijun"])
    short_mask = (df["stoch_bear"] == 1) & (df["close"] < df["kijun"])
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


def strategy_6_zigzag_vol_ichi(df: pd.DataFrame) -> pd.Series:
    """전략 6: ZigZag Breakout + Volume 급등 + Ichimoku TK 크로스"""
    signal = pd.Series(0, index=df.index)
    long_mask = (df["fib_breakout_bull"] == 1) & (df["vol_spike"] == 1) & (df["tk_cross_bull"] == 1)
    short_mask = (df["fib_breakout_bear"] == 1) & (df["vol_spike"] == 1) & (df["tk_cross_bear"] == 1)
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


def strategy_7_bronze_thin_kumo(df: pd.DataFrame) -> pd.Series:
    """전략 7: Bronze 이상 + Ichimoku 구름 얇은 구간 돌파"""
    signal = pd.Series(0, index=df.index)
    grade_ok = df["cmp_grade"].isin(["gold", "silver", "bronze"])
    thin_kumo = df["kumo_thickness"] < 1.0  # 구름 두께 1% 미만
    long_mask = grade_ok & (df["cmp_signal"] == 1) & thin_kumo & (df["close"] > df["kumo_top"])
    short_mask = grade_ok & (df["cmp_signal"] == -1) & thin_kumo & (df["close"] < df["kumo_bottom"])
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


def strategy_8_sd_rsi_div_vol(df: pd.DataFrame) -> pd.Series:
    """전략 8: S/D존 도달 + RSI 다이버전스 + Volume 확인"""
    signal = pd.Series(0, index=df.index)
    long_mask = (df["in_demand_zone"] == 1) & (df["rsi_div_bull"] == 1) & (df["vol_ratio"] > 1.2)
    short_mask = (df["in_supply_zone"] == 1) & (df["rsi_div_bear"] == 1) & (df["vol_ratio"] > 1.2)
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


def strategy_9_grade_chikou(df: pd.DataFrame) -> pd.Series:
    """전략 9: Gold/Silver + 후행스팬 가격 위(롱)/아래(숏)"""
    signal = pd.Series(0, index=df.index)
    grade_ok = df["cmp_grade"].isin(["gold", "silver"])
    past_close = df["close"].shift(26)
    long_mask = grade_ok & (df["cmp_signal"] == 1) & (df["close"] > past_close)
    short_mask = grade_ok & (df["cmp_signal"] == -1) & (df["close"] < past_close)
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


def strategy_10_full_consensus(df: pd.DataFrame) -> pd.Series:
    """전략 10: 3개 지표 모두 동일 방향 (컨센서스)"""
    signal = pd.Series(0, index=df.index)
    long_mask = (df["cmp_signal"] == 1) & (df["ichi_signal"] == 1) & (df["vol_signal"] == 1)
    short_mask = (df["cmp_signal"] == -1) & (df["ichi_signal"] == -1) & (df["vol_signal"] == -1)
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


# 추가 전략들
def strategy_11_gold_ichi_vol(df: pd.DataFrame) -> pd.Series:
    """전략 11: Gold + Ichimoku 강한 신호(4+) + Volume 평균 이상"""
    signal = pd.Series(0, index=df.index)
    long_mask = (
        (df["cmp_grade"] == "gold") & (df["cmp_signal"] == 1) &
        (df["ichi_bull_strength"] >= 4) & (df["vol_ratio"] > 1.0)
    )
    short_mask = (
        (df["cmp_grade"] == "gold") & (df["cmp_signal"] == -1) &
        (df["ichi_bear_strength"] >= 4) & (df["vol_ratio"] > 1.0)
    )
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


def strategy_12_silver_multi(df: pd.DataFrame) -> pd.Series:
    """전략 12: Silver 이상 + Ichimoku 구름 위 + MA 추세 일치"""
    signal = pd.Series(0, index=df.index)
    grade_ok = df["cmp_grade"].isin(["gold", "silver"])
    long_mask = (
        grade_ok & (df["cmp_signal"] == 1) &
        (df["close"] > df["kumo_top"]) & (df["ma_trend"] == 1)
    )
    short_mask = (
        grade_ok & (df["cmp_signal"] == -1) &
        (df["close"] < df["kumo_bottom"]) & (df["ma_trend"] == -1)
    )
    signal[long_mask] = 1
    signal[short_mask] = -1
    return signal


# 전략 레지스트리
STRATEGIES = {
    "S01_gold_only": strategy_1_gold_only,
    "S02_gold_ichimoku": strategy_2_gold_ichimoku,
    "S03_grade_volume": strategy_3_grade_volume,
    "S04_ma_rsi_sd": strategy_4_ma_rsi_sd,
    "S05_stoch_ichi": strategy_5_stoch_ichi,
    "S06_zigzag_vol_ichi": strategy_6_zigzag_vol_ichi,
    "S07_bronze_thin_kumo": strategy_7_bronze_thin_kumo,
    "S08_sd_rsi_div_vol": strategy_8_sd_rsi_div_vol,
    "S09_grade_chikou": strategy_9_grade_chikou,
    "S10_full_consensus": strategy_10_full_consensus,
    "S11_gold_ichi_vol": strategy_11_gold_ichi_vol,
    "S12_silver_multi": strategy_12_silver_multi,
}


# ─────────────────────────────────────────
# 최종 선택 전략 (백테스트 후 자동 설정)
# ─────────────────────────────────────────

# 초기값 — 백테스트 optimizer가 이 값을 업데이트함
SELECTED_STRATEGY = "S02_gold_ichimoku"
SELECTED_EXIT = "trailing_atr_1.5x"
SELECTED_TP_SL_RATIO = 2.0
SELECTED_LEVERAGE = 5


def generate_signal(df: pd.DataFrame, strategy_name: Optional[str] = None) -> pd.Series:
    """
    선택된 전략으로 매매 시그널 생성
    Returns: Series (1=롱, -1=숏, 0=중립)
    """
    name = strategy_name or SELECTED_STRATEGY
    fn = STRATEGIES.get(name)
    if fn is None:
        raise ValueError(f"알 수 없는 전략: {name}")
    return fn(df)
