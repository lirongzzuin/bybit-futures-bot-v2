"""
고급 전략 엔진 — 반복 최적화용

핵심 설계 원칙:
1. 개별 지표 승률은 ~50% → 조합과 필터가 핵심
2. 시장 상태(추세/횡보) 감지 후 전략 분기
3. 지표 강도에 따른 동적 레버리지 (3x ~ 20x)
4. ATR 기반 적응형 TP/SL
5. 복수 시간대 확인 가능 구조
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    """매매 시그널"""
    direction: int      # 1=롱, -1=숏, 0=없음
    strength: float     # 0.0~1.0 (시그널 강도)
    leverage: int       # 동적 레버리지
    tp_mult: float      # ATR 배수 TP
    sl_mult: float      # ATR 배수 SL
    strategy_name: str
    details: str = ""


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR 계산"""
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def detect_regime(df: pd.DataFrame, lookback: int = 50) -> pd.Series:
    """
    시장 상태 감지
    Returns: 'trend_up', 'trend_down', 'range'
    """
    close = df["close"]

    # ADX 대용: 방향성 이동 (DI+ - DI-)
    atr = calc_atr(df)
    sma_fast = close.rolling(20).mean()
    sma_slow = close.rolling(50).mean()

    # 추세 강도: 가격이 MA 위/아래 + MA 기울기
    ma_slope = sma_slow.pct_change(10) * 100  # 10봉 기울기 (%)
    price_vs_ma = (close - sma_slow) / sma_slow * 100

    # 변동성 대비 추세 비율
    price_range = close.rolling(lookback).max() - close.rolling(lookback).min()
    net_move = (close - close.shift(lookback)).abs()
    trend_ratio = net_move / price_range.replace(0, np.nan)

    regime = pd.Series("range", index=df.index)
    regime[(trend_ratio > 0.4) & (ma_slope > 0.3) & (price_vs_ma > 0)] = "trend_up"
    regime[(trend_ratio > 0.4) & (ma_slope < -0.3) & (price_vs_ma < 0)] = "trend_down"

    return regime


def calc_composite_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    복합 시그널 점수 계산 (가중 합산)

    각 지표의 실제 예측력에 비례하는 가중치 사용
    점수 범위: -100 ~ +100
    """
    df = df.copy()

    # ── 1. 추세 점수 (MA + Fib Breakout) — 가중치 30% ──
    # MA 추세 방향
    ma_score = df["ma_trend"].fillna(0) * 15  # -15 ~ +15

    # Fib breakout 강도
    fib_score = (df["fib_breakout_bull"] - df["fib_breakout_bear"]).fillna(0) * 15

    # ── 2. 모멘텀 점수 (RSI + Stochastic) — 가중치 30% ──
    # RSI: 50 기준 편차
    rsi_raw = df["rsi"].fillna(50)
    rsi_score = np.where(rsi_raw > 70, -10,  # 과매수 → 약간 약세
                np.where(rsi_raw > 55, (rsi_raw - 50) / 20 * 15,
                np.where(rsi_raw < 30, 10,    # 과매도 → 약간 강세
                np.where(rsi_raw < 45, -(50 - rsi_raw) / 20 * 15,
                0))))

    # RSI 다이버전스 보너스
    rsi_score = rsi_score + df["rsi_div_bull"].fillna(0) * 10 - df["rsi_div_bear"].fillna(0) * 10

    # 이중 스토캐스틱 (추세 방향)
    stoch_score = df["stoch_trend"].fillna(0) * 15

    # ── 3. 구조 점수 (Ichimoku) — 가중치 25% ──
    ichi_bull = df.get("ichi_bull_strength", pd.Series(0, index=df.index)).fillna(0)
    ichi_bear = df.get("ichi_bear_strength", pd.Series(0, index=df.index)).fillna(0)
    ichi_score = (ichi_bull - ichi_bear) * 5  # -25 ~ +25

    # TK 크로스 이벤트 보너스
    ichi_score = ichi_score + df.get("tk_cross_bull", pd.Series(0, index=df.index)).fillna(0) * 5
    ichi_score = ichi_score - df.get("tk_cross_bear", pd.Series(0, index=df.index)).fillna(0) * 5

    # ── 4. 수급 점수 (S/D Zone + Volume) — 가중치 15% ──
    sd_score = (df["in_demand_zone"].fillna(0) * 8 - df["in_supply_zone"].fillna(0) * 8)

    # Volume 확인 (방향 보조)
    vol_ratio = df["vol_ratio"].fillna(1)
    vol_boost = np.where(vol_ratio > 1.5, 7,
                np.where(vol_ratio > 1.2, 4, 0))
    # 거래량 급등 시 방향성 강화
    vol_direction = np.where(df["buy_volume"].fillna(0) > df["sell_volume"].fillna(0), 1, -1)
    vol_score = vol_boost * vol_direction

    # ── 종합 ──
    df["trend_score"] = ma_score + fib_score
    df["momentum_score"] = rsi_score + stoch_score
    df["structure_score"] = ichi_score
    df["supply_demand_score"] = sd_score + vol_score

    df["composite_score"] = (
        df["trend_score"] +
        df["momentum_score"] +
        df["structure_score"] +
        df["supply_demand_score"]
    )

    # 스무딩 (노이즈 감소)
    df["composite_smooth"] = df["composite_score"].rolling(3, min_periods=1).mean()

    return df


def generate_advanced_signal(
    df: pd.DataFrame,
    entry_threshold: float = 25.0,
    min_leverage: int = 3,
    max_leverage: int = 20,
    base_tp_mult: float = 2.0,
    base_sl_mult: float = 1.0,
    use_regime_filter: bool = True,
    require_volume: bool = False,
    require_ichimoku: bool = False,
) -> pd.DataFrame:
    """
    고급 시그널 생성기

    Args:
        entry_threshold: 진입 최소 점수 (절대값)
        min_leverage / max_leverage: 레버리지 범위
        base_tp_mult / base_sl_mult: ATR 배수 기본값
        use_regime_filter: 시장 상태 필터 사용
        require_volume: 거래량 확인 필수 여부
        require_ichimoku: 이치모쿠 확인 필수 여부

    Returns: DataFrame에 signal 관련 컬럼 추가
    """
    df = calc_composite_score(df)
    df["regime"] = detect_regime(df)
    df["atr"] = calc_atr(df)

    n = len(df)
    df["sig_direction"] = 0
    df["sig_strength"] = 0.0
    df["sig_leverage"] = 0
    df["sig_tp_mult"] = base_tp_mult
    df["sig_sl_mult"] = base_sl_mult

    score = df["composite_smooth"].values
    regime = df["regime"].values

    for i in range(1, n):
        s = score[i]
        abs_s = abs(s)

        # 기본 방향
        direction = 0
        if s > entry_threshold:
            direction = 1
        elif s < -entry_threshold:
            direction = -1

        if direction == 0:
            continue

        # ── 필터 ──
        row = df.iloc[i]

        # 시장 상태 필터: 추세 방향과 시그널 방향 일치 확인
        if use_regime_filter:
            r = regime[i]
            if direction == 1 and r == "trend_down":
                continue  # 하락 추세에서 롱 금지
            if direction == -1 and r == "trend_up":
                continue  # 상승 추세에서 숏 금지

        # 거래량 필터
        if require_volume:
            if row.get("vol_ratio", 1) < 0.8:
                continue  # 거래량 부족 시 스킵

        # 이치모쿠 필터
        if require_ichimoku:
            ichi_sig = row.get("ichi_signal", 0)
            if direction == 1 and ichi_sig == -1:
                continue
            if direction == -1 and ichi_sig == 1:
                continue

        # ── 강도 계산 (0~1) ──
        strength = min(abs_s / 80.0, 1.0)  # 80점이면 최대 강도

        # ── 동적 레버리지 ──
        # 강도에 비례하여 레버리지 결정
        lev_range = max_leverage - min_leverage
        leverage = int(min_leverage + lev_range * strength)
        leverage = max(min_leverage, min(max_leverage, leverage))

        # ── 동적 TP/SL (강도에 따라 조절) ──
        # 강한 시그널 → 더 넓은 TP (추세 추종)
        # 약한 시그널 → 더 좁은 TP (빠른 수익 실현)
        tp_mult = base_tp_mult + strength * 1.5  # 2.0 ~ 3.5
        sl_mult = base_sl_mult  # SL은 고정 (리스크 일정)

        # 추세 장에서는 TP 확대
        if regime[i] in ("trend_up", "trend_down"):
            tp_mult *= 1.3

        df.iat[i, df.columns.get_loc("sig_direction")] = direction
        df.iat[i, df.columns.get_loc("sig_strength")] = strength
        df.iat[i, df.columns.get_loc("sig_leverage")] = leverage
        df.iat[i, df.columns.get_loc("sig_tp_mult")] = tp_mult
        df.iat[i, df.columns.get_loc("sig_sl_mult")] = sl_mult

    return df
