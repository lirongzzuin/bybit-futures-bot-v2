"""
추세 추종 풀백 전략 — 핵심 전략

원리:
  "추세의 방향으로, 풀백 시점에 진입"

구조:
  1. 추세 판단: Ichimoku 구름 + MA(5/20)
  2. 풀백 감지: RSI + 스토캐스틱 과매도/과매수
  3. 진입 확인: S/D Zone 지지/저항 + Volume 확인
  4. 동적 레버리지: 확인 요소 수에 비례 (3x ~ 20x)
  5. 비대칭 TP/SL: SL은 ATR 1x, TP는 ATR 2~4x

핵심 인사이트:
  - 개별 지표의 방향 예측력은 ~50% (무작위)
  - BUT 추세 방향으로만 거래하면 기대값이 양(+)
  - 풀백 시점 진입 → 가격이 추세 방향으로 회귀하는 것을 이용
  - 적은 거래, 높은 확신 = 동적 레버리지의 핵심
"""
import numpy as np
import pandas as pd
from typing import Optional


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def generate_signals(
    df: pd.DataFrame,
    # === 추세 파라미터 ===
    trend_ma_fast: int = 5,
    trend_ma_slow: int = 20,
    require_ichimoku_cloud: bool = True,
    # === 풀백 파라미터 ===
    rsi_pullback_bull: float = 40,     # 상승추세에서 RSI 이 아래로 떨어지면 풀백
    rsi_pullback_bear: float = 60,     # 하락추세에서 RSI 이 위로 올라가면 풀백
    stoch_confirm: bool = True,        # 스토캐스틱 크로스 확인
    # === 추가 확인 ===
    volume_confirm: bool = False,       # 거래량 평균 이상 필수
    sd_zone_confirm: bool = False,      # S/D Zone 근접 필수
    # === 레버리지 ===
    base_leverage: int = 5,
    max_leverage: int = 20,
    # === TP/SL ===
    sl_atr_mult: float = 1.0,
    tp_atr_mult: float = 2.5,
    # === 쿨다운 ===
    cooldown_bars: int = 3,            # 진입 후 최소 N봉 대기
) -> pd.DataFrame:
    """
    추세 추종 풀백 시그널 생성

    Returns: sig_direction, sig_strength, sig_leverage, sig_tp_mult, sig_sl_mult
    """
    df = df.copy()
    n = len(df)

    # === 추세 판단 ===
    ma_fast = df["close"].rolling(trend_ma_fast).mean()
    ma_slow = df["close"].rolling(trend_ma_slow).mean()
    ma_trend = np.where(ma_fast > ma_slow, 1, np.where(ma_fast < ma_slow, -1, 0))

    # Ichimoku 추세
    ichi_trend = np.zeros(n)
    if require_ichimoku_cloud and "kumo_top" in df.columns:
        ichi_trend = np.where(
            df["close"] > df["kumo_top"], 1,
            np.where(df["close"] < df["kumo_bottom"], -1, 0)
        )
    else:
        ichi_trend = ma_trend  # Ichimoku 없으면 MA만 사용

    # 최종 추세: MA와 Ichimoku 모두 일치해야
    if require_ichimoku_cloud:
        trend = np.where((ma_trend == 1) & (ichi_trend == 1), 1,
                np.where((ma_trend == -1) & (ichi_trend == -1), -1, 0))
    else:
        trend = ma_trend

    # === RSI ===
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_vals = rsi.values

    # === 스토캐스틱 ===
    stoch_k = df.get("stoch2_k", pd.Series(50, index=df.index)).values
    stoch_d = df.get("stoch2_d", pd.Series(50, index=df.index)).values

    # === Volume ===
    vol_ratio = df.get("vol_ratio", pd.Series(1, index=df.index)).values

    # === S/D Zone ===
    in_demand = df.get("in_demand_zone", pd.Series(0, index=df.index)).values
    in_supply = df.get("in_supply_zone", pd.Series(0, index=df.index)).values

    # === 시그널 생성 ===
    sig_direction = np.zeros(n)
    sig_strength = np.zeros(n)
    sig_leverage = np.zeros(n)
    sig_tp_mult = np.full(n, tp_atr_mult)
    sig_sl_mult = np.full(n, sl_atr_mult)

    last_entry = -cooldown_bars  # 쿨다운 추적

    for i in range(max(trend_ma_slow + 1, 52), n):
        if i - last_entry < cooldown_bars:
            continue

        t = trend[i]
        if t == 0:
            continue

        # === 풀백 감지 ===
        pullback = False
        if t == 1:  # 상승 추세
            # RSI가 pullback 레벨 아래에서 위로 복귀
            if rsi_vals[i] > rsi_pullback_bull and rsi_vals[i-1] <= rsi_pullback_bull:
                pullback = True
            # 또는 RSI가 과매도 후 반등
            elif rsi_vals[i] > 35 and rsi_vals[i-1] <= 35:
                pullback = True
        else:  # 하락 추세
            if rsi_vals[i] < rsi_pullback_bear and rsi_vals[i-1] >= rsi_pullback_bear:
                pullback = True
            elif rsi_vals[i] < 65 and rsi_vals[i-1] >= 65:
                pullback = True

        if not pullback:
            continue

        # === 확인 요소 점수 (강도 계산) ===
        confirmation_score = 0
        max_confirmations = 4

        # 1. 스토캐스틱 확인
        if stoch_confirm:
            if t == 1 and stoch_k[i] > stoch_d[i] and stoch_k[i-1] <= stoch_d[i-1]:
                confirmation_score += 1
            elif t == -1 and stoch_k[i] < stoch_d[i] and stoch_k[i-1] >= stoch_d[i-1]:
                confirmation_score += 1
            elif t == 1 and stoch_k[i] > stoch_d[i]:  # 크로스 아니어도 방향 맞으면 반점
                confirmation_score += 0.5
            elif t == -1 and stoch_k[i] < stoch_d[i]:
                confirmation_score += 0.5

        # 2. Volume 확인
        if vol_ratio[i] > 1.2:
            confirmation_score += 1
        elif vol_ratio[i] > 0.8:
            confirmation_score += 0.3

        # 3. S/D Zone 확인
        if t == 1 and in_demand[i]:
            confirmation_score += 1
        elif t == -1 and in_supply[i]:
            confirmation_score += 1

        # 4. Ichimoku 강도 (TK 크로스 등)
        ichi_bull = df.iloc[i].get("ichi_bull_strength", 0)
        ichi_bear = df.iloc[i].get("ichi_bear_strength", 0)
        if t == 1 and ichi_bull >= 4:
            confirmation_score += 1
        elif t == -1 and ichi_bear >= 4:
            confirmation_score += 1
        elif t == 1 and ichi_bull >= 3:
            confirmation_score += 0.5
        elif t == -1 and ichi_bear >= 3:
            confirmation_score += 0.5

        # 필수 필터
        if volume_confirm and vol_ratio[i] < 0.8:
            continue
        if sd_zone_confirm:
            if t == 1 and not in_demand[i]:
                continue
            if t == -1 and not in_supply[i]:
                continue

        # === 강도 및 레버리지 ===
        strength = min(confirmation_score / max_confirmations, 1.0)
        strength = max(strength, 0.2)  # 최소 0.2 (풀백 자체가 기본 강도)

        # 레버리지: 강도에 비례
        lev_range = max_leverage - base_leverage
        leverage = int(base_leverage + lev_range * strength)

        # 강한 시그널 → 더 넓은 TP
        tp = tp_atr_mult + strength * 1.0  # 2.5 ~ 3.5

        sig_direction[i] = t
        sig_strength[i] = strength
        sig_leverage[i] = leverage
        sig_tp_mult[i] = tp
        sig_sl_mult[i] = sl_atr_mult

        last_entry = i

    df["sig_direction"] = sig_direction
    df["sig_strength"] = sig_strength
    df["sig_leverage"] = sig_leverage
    df["sig_tp_mult"] = sig_tp_mult
    df["sig_sl_mult"] = sig_sl_mult

    return df
