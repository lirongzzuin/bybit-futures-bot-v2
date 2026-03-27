"""
Candle Master Plan 지표 — TradingView 로직 Python 재구현

구성요소:
  A) ZigZag + Fibonacci Breakout
  B) 이동평균선 크로스 (SMA 5/20)
  C) RSI(14) + 다이버전스
  D) 이중 스토캐스틱
  E) Supply/Demand Zone
  F) 종합 시그널 (Gold/Silver/Bronze)
"""
import numpy as np
import pandas as pd
from config.indicator_params import CANDLE_MASTER_PLAN as P


# ─────────────────────────────────────────
# A) ZigZag + Fibonacci Breakout
# ─────────────────────────────────────────

def calc_zigzag(df: pd.DataFrame, length: int = None) -> pd.DataFrame:
    """
    ZigZag 스윙 하이/로우 식별
    - length 봉 내 최고가/최저가 기준으로 pivot 포인트 결정
    Returns: 원본 df에 'zz_high', 'zz_low' 컬럼 추가
    """
    length = length or P["zigzag_length"]
    high = df["high"].values
    low = df["low"].values
    n = len(df)

    zz_high = np.full(n, np.nan)
    zz_low = np.full(n, np.nan)

    for i in range(length, n - length):
        # 스윙 하이: i가 좌우 length 봉의 최고점인지
        if high[i] == max(high[i - length: i + length + 1]):
            zz_high[i] = high[i]
        # 스윙 로우: i가 좌우 length 봉의 최저점인지
        if low[i] == min(low[i - length: i + length + 1]):
            zz_low[i] = low[i]

    df = df.copy()
    df["zz_high"] = zz_high
    df["zz_low"] = zz_low
    return df


def calc_fib_breakout(df: pd.DataFrame, fib_factor: float = None) -> pd.DataFrame:
    """
    피보나치 브레이크아웃 신호
    - 가장 최근 ZigZag High/Low 사이의 Fib 레벨 계산
    - fib_factor(0.33) 돌파 시 브레이크아웃 확인
    """
    fib_factor = fib_factor or P["fib_factor"]
    df = df.copy()

    # 최근 ZigZag High/Low를 forward fill
    df["last_zz_high"] = df["zz_high"].ffill()
    df["last_zz_low"] = df["zz_low"].ffill()

    swing_range = df["last_zz_high"] - df["last_zz_low"]

    # 피보나치 레벨
    df["fib_upper"] = df["last_zz_low"] + swing_range * (1 - fib_factor)  # 0.67 레벨
    df["fib_lower"] = df["last_zz_low"] + swing_range * fib_factor         # 0.33 레벨

    # 브레이크아웃 신호
    df["fib_breakout_bull"] = (df["close"] > df["fib_upper"]).astype(int)
    df["fib_breakout_bear"] = (df["close"] < df["fib_lower"]).astype(int)

    return df


# ─────────────────────────────────────────
# B) 이동평균선 크로스
# ─────────────────────────────────────────

def calc_ma_cross(df: pd.DataFrame) -> pd.DataFrame:
    """SMA(5) / SMA(20) 골든크로스/데드크로스"""
    df = df.copy()
    df["ma1"] = df["close"].rolling(P["ma1_length"]).mean()
    df["ma2"] = df["close"].rolling(P["ma2_length"]).mean()

    # 크로스 감지: 이전 봉 대비 교차
    df["ma_bull"] = ((df["ma1"] > df["ma2"]) & (df["ma1"].shift(1) <= df["ma2"].shift(1))).astype(int)
    df["ma_bear"] = ((df["ma1"] < df["ma2"]) & (df["ma1"].shift(1) >= df["ma2"].shift(1))).astype(int)

    # 현재 추세 방향 (크로스가 아닌 상태 포함)
    df["ma_trend"] = np.where(df["ma1"] > df["ma2"], 1, -1)

    return df


# ─────────────────────────────────────────
# C) RSI + 다이버전스
# ─────────────────────────────────────────

def calc_rsi(series: pd.Series, length: int = None) -> pd.Series:
    """Wilder RSI 계산"""
    length = length or P["rsi_length"]
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / length, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_rsi_signals(df: pd.DataFrame) -> pd.DataFrame:
    """RSI 과매수/과매도 + 다이버전스"""
    df = df.copy()
    df["rsi"] = calc_rsi(df["close"])

    ob = P["rsi_overbought_level"]
    os_level = P["rsi_oversold_level"]

    # 과매도에서 반등 / 과매수에서 하락
    df["rsi_bull"] = ((df["rsi"] > os_level) & (df["rsi"].shift(1) <= os_level)).astype(int)
    df["rsi_bear"] = ((df["rsi"] < ob) & (df["rsi"].shift(1) >= ob)).astype(int)

    # RSI 다이버전스 — 가격 vs RSI 방향 불일치
    lookback = 14
    df["rsi_div_bull"] = 0
    df["rsi_div_bear"] = 0

    for i in range(lookback, len(df)):
        # 강세 다이버전스: 가격은 저점 갱신, RSI는 저점 미갱신
        price_lower = df["low"].iloc[i] < df["low"].iloc[i - lookback: i].min()
        rsi_higher = df["rsi"].iloc[i] > df["rsi"].iloc[i - lookback: i].min()
        if price_lower and rsi_higher:
            df.iloc[i, df.columns.get_loc("rsi_div_bull")] = 1

        # 약세 다이버전스: 가격은 고점 갱신, RSI는 고점 미갱신
        price_higher = df["high"].iloc[i] > df["high"].iloc[i - lookback: i].max()
        rsi_lower = df["rsi"].iloc[i] < df["rsi"].iloc[i - lookback: i].max()
        if price_higher and rsi_lower:
            df.iloc[i, df.columns.get_loc("rsi_div_bear")] = 1

    return df


# ─────────────────────────────────────────
# D) 이중 스토캐스틱
# ─────────────────────────────────────────

def _stochastic(df: pd.DataFrame, k_length: int, k_smooth: int, d_smooth: int,
                prefix: str) -> pd.DataFrame:
    """스토캐스틱 %K, %D 계산"""
    df = df.copy()
    lowest_low = df["low"].rolling(k_length).min()
    highest_high = df["high"].rolling(k_length).max()

    raw_k = 100 * (df["close"] - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    df[f"{prefix}_k"] = raw_k.rolling(k_smooth).mean()
    df[f"{prefix}_d"] = df[f"{prefix}_k"].rolling(d_smooth).mean()
    return df


def calc_dual_stochastic(df: pd.DataFrame) -> pd.DataFrame:
    """이중 스토캐스틱 계산 및 신호 생성"""
    # Stoch 1 (중장기)
    df = _stochastic(df, P["stoch1_k_length"], P["stoch1_k_smooth"],
                     P["stoch1_d_smooth"], "stoch1")
    # Stoch 2 (단기)
    df = _stochastic(df, P["stoch2_k_length"], P["stoch2_k_smooth"],
                     P["stoch2_d_smooth"], "stoch2")

    # 과매수/과매도 동시 확인
    os_thresh, ob_thresh = 20, 80

    # 강세: 두 스토캐스틱 모두 과매도 영역에서 %K > %D 크로스
    df["stoch_bull"] = (
        (df["stoch1_k"] > df["stoch1_d"]) &
        (df["stoch1_k"].shift(1) <= df["stoch1_d"].shift(1)) &
        (df["stoch2_k"] > df["stoch2_d"]) &
        (df["stoch1_k"] < ob_thresh) &
        (df["stoch2_k"] < ob_thresh)
    ).astype(int)

    # 약세: 두 스토캐스틱 모두 과매수 영역에서 %K < %D 크로스
    df["stoch_bear"] = (
        (df["stoch1_k"] < df["stoch1_d"]) &
        (df["stoch1_k"].shift(1) >= df["stoch1_d"].shift(1)) &
        (df["stoch2_k"] < df["stoch2_d"]) &
        (df["stoch1_k"] > os_thresh) &
        (df["stoch2_k"] > os_thresh)
    ).astype(int)

    # 스토캐스틱 추세 방향 (현재 상태)
    df["stoch_trend"] = np.where(
        (df["stoch1_k"] > df["stoch1_d"]) & (df["stoch2_k"] > df["stoch2_d"]), 1,
        np.where(
            (df["stoch1_k"] < df["stoch1_d"]) & (df["stoch2_k"] < df["stoch2_d"]), -1, 0
        )
    )

    return df


# ─────────────────────────────────────────
# E) Supply/Demand Zone
# ─────────────────────────────────────────

def calc_supply_demand(df: pd.DataFrame) -> pd.DataFrame:
    """
    스윙 하이/로우 기반 수급 영역 식별
    - Supply Zone: 스윙 하이 근처 (저항)
    - Demand Zone: 스윙 로우 근처 (지지)
    """
    length = P["swing_high_low_length"]
    box_pct = P["supply_demand_box_width"] / 100  # 2.5% → 0.025
    history = P["history_to_keep"]

    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    n = len(df)

    supply_zones = []  # (index, upper, lower)
    demand_zones = []

    for i in range(length, n - length):
        # 스윙 하이 → Supply Zone
        if high[i] == max(high[i - length: i + length + 1]):
            upper = high[i] * (1 + box_pct / 2)
            lower = high[i] * (1 - box_pct / 2)
            supply_zones.append((i, upper, lower))
        # 스윙 로우 → Demand Zone
        if low[i] == min(low[i - length: i + length + 1]):
            upper = low[i] * (1 + box_pct / 2)
            lower = low[i] * (1 - box_pct / 2)
            demand_zones.append((i, upper, lower))

    # 최근 history개만 유지
    supply_zones = supply_zones[-history:]
    demand_zones = demand_zones[-history:]

    df = df.copy()
    df["in_supply_zone"] = 0
    df["in_demand_zone"] = 0
    df["nearest_supply"] = np.nan
    df["nearest_demand"] = np.nan

    for i in range(n):
        # 현재 가격이 Supply Zone 내에 있는지
        for _, upper, lower in supply_zones:
            if lower <= close[i] <= upper:
                df.iloc[i, df.columns.get_loc("in_supply_zone")] = 1
                break

        # 현재 가격이 Demand Zone 내에 있는지
        for _, upper, lower in demand_zones:
            if lower <= close[i] <= upper:
                df.iloc[i, df.columns.get_loc("in_demand_zone")] = 1
                break

        # 가장 가까운 Supply/Demand 레벨
        active_supply = [s for s in supply_zones if s[0] <= i]
        active_demand = [d for d in demand_zones if d[0] <= i]

        if active_supply:
            nearest_s = min(active_supply, key=lambda s: abs(close[i] - (s[1] + s[2]) / 2))
            df.iloc[i, df.columns.get_loc("nearest_supply")] = (nearest_s[1] + nearest_s[2]) / 2
        if active_demand:
            nearest_d = min(active_demand, key=lambda d: abs(close[i] - (d[1] + d[2]) / 2))
            df.iloc[i, df.columns.get_loc("nearest_demand")] = (nearest_d[1] + nearest_d[2]) / 2

    # S/D 신호: Demand Zone 반등(bull), Supply Zone 저항(bear)
    df["sd_bull"] = (
        (df["in_demand_zone"] == 1) &
        (df["close"] > df["open"])  # 양봉으로 반등
    ).astype(int)

    df["sd_bear"] = (
        (df["in_supply_zone"] == 1) &
        (df["close"] < df["open"])  # 음봉으로 저항
    ).astype(int)

    return df


# ─────────────────────────────────────────
# F) 종합 시그널 생성
# ─────────────────────────────────────────

def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    모든 Candle Master Plan 하위 지표를 계산하고 종합 시그널 생성

    Returns: 아래 컬럼이 추가된 DataFrame
      - cmp_signal: 1(롱), -1(숏), 0(중립)
      - cmp_grade: 'gold', 'silver', 'bronze', 'none'
      - upper_c, lower_c: 채널 상단/하단
      - ma1, ma2: 이동평균
    """
    # 각 하위 지표 순차 계산
    df = calc_zigzag(df)
    df = calc_fib_breakout(df)
    df = calc_ma_cross(df)
    df = calc_rsi_signals(df)
    df = calc_dual_stochastic(df)
    df = calc_supply_demand(df)

    # ── 각 지표의 방향 점수 (1: 롱, -1: 숏, 0: 중립) ──
    # 상태(state) 기반 — 이벤트(cross)와 달리 현재 위치/방향으로 판단

    # 1. ZigZag/Fib Breakout (상태: 현재 가격이 Fib 상단/하단 위/아래)
    df["sub_zigzag"] = df["fib_breakout_bull"] - df["fib_breakout_bear"]

    # 2. MA 추세 (상태: MA1 > MA2 or MA1 < MA2)
    df["sub_ma"] = df["ma_trend"]

    # 3. RSI (상태 기반: 50 이상=강세, 50 이하=약세 + 이벤트 가중)
    df["sub_rsi"] = np.where(
        df["rsi"] > 55, 1,
        np.where(df["rsi"] < 45, -1, 0)
    )
    # 이벤트(과매도 반등/과매수 하락/다이버전스) 발생 시 강제 방향
    df.loc[(df["rsi_bull"] == 1) | (df["rsi_div_bull"] == 1), "sub_rsi"] = 1
    df.loc[(df["rsi_bear"] == 1) | (df["rsi_div_bear"] == 1), "sub_rsi"] = -1

    # 4. 이중 스토캐스틱 (상태: %K > %D = 강세)
    df["sub_stoch"] = df["stoch_trend"]

    # 5. Supply/Demand (상태: Demand Zone 근처=강세, Supply Zone 근처=약세)
    # 이벤트 + 근접성 기반
    df["sub_sd"] = 0
    # Demand Zone 안이거나 가까우면 강세
    df.loc[df["in_demand_zone"] == 1, "sub_sd"] = 1
    df.loc[df["sd_bull"] == 1, "sub_sd"] = 1
    # Supply Zone 안이거나 가까우면 약세
    df.loc[df["in_supply_zone"] == 1, "sub_sd"] = -1
    df.loc[df["sd_bear"] == 1, "sub_sd"] = -1

    # ── 종합 점수 ──
    sub_cols = ["sub_zigzag", "sub_ma", "sub_rsi", "sub_stoch", "sub_sd"]
    df["bull_count"] = sum((df[c] > 0).astype(int) for c in sub_cols)
    df["bear_count"] = sum((df[c] < 0).astype(int) for c in sub_cols)

    # 등급 분류
    # Gold: 5/5 일치, Silver: 4/5, Bronze: 3/5
    conditions_long = [
        df["bull_count"] >= 5,
        df["bull_count"] >= 4,
        df["bull_count"] >= 3,
    ]
    conditions_short = [
        df["bear_count"] >= 5,
        df["bear_count"] >= 4,
        df["bear_count"] >= 3,
    ]

    df["long_grade"] = np.select(conditions_long, ["gold", "silver", "bronze"], default="none")
    df["short_grade"] = np.select(conditions_short, ["gold", "silver", "bronze"], default="none")

    # 최종 신호 및 등급
    df["cmp_signal"] = 0
    df["cmp_grade"] = "none"

    # 롱 우선 (동시 발생 시 더 강한 쪽)
    long_mask = df["bull_count"] > df["bear_count"]
    short_mask = df["bear_count"] > df["bull_count"]

    df.loc[long_mask & (df["long_grade"] != "none"), "cmp_signal"] = 1
    df.loc[long_mask & (df["long_grade"] != "none"), "cmp_grade"] = df.loc[
        long_mask & (df["long_grade"] != "none"), "long_grade"
    ]

    df.loc[short_mask & (df["short_grade"] != "none"), "cmp_signal"] = -1
    df.loc[short_mask & (df["short_grade"] != "none"), "cmp_grade"] = df.loc[
        short_mask & (df["short_grade"] != "none"), "short_grade"
    ]

    # 채널 상단/하단 (최근 ZigZag High/Low)
    df["upper_c"] = df["last_zz_high"]
    df["lower_c"] = df["last_zz_low"]

    return df
