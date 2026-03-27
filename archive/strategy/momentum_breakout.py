"""
모멘텀 브레이크아웃 전략 — 빅 무브 포착

핵심 원리:
  - 크립토에서 수익의 대부분은 소수의 큰 추세에서 발생
  - 작은 손절을 많이 감수하고, 큰 수익을 가끔 취한다
  - 승률 30~40%로도 수익 가능 (RR 3:1 이상)

전략 유형:

A) 구름 돌파 (Cloud Breakout)
  - 가격이 Ichimoku 구름을 돌파할 때 진입
  - 거래량 확인 필수
  - 트레일링 스탑으로 추세 끝까지 보유

B) 채널 돌파 (Channel Breakout)
  - N봉 고점/저점 돌파
  - Donchian Channel 방식
  - 변동성 확장 구간에서 진입

C) 모멘텀 가속 (Momentum Acceleration)
  - RSI가 방향으로 가속 (50 돌파 후 계속 상승/하락)
  - 이중 스토캐스틱 동시 방향
  - MA 크로스 직후 모멘텀 확인

D) 복합 브레이크아웃 (Combined Breakout)
  - A, B, C 중 2개 이상 동시 발생 시 강한 진입
"""
import numpy as np
import pandas as pd


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def generate_signals(
    df: pd.DataFrame,
    # === 진입 타입 ===
    strategy_type: str = "combined",  # cloud, channel, momentum, combined
    # === 구름 돌파 ===
    cloud_breakout: bool = True,
    cloud_volume_mult: float = 1.0,    # 구름 돌파 시 필요 거래량 배수
    # === 채널 돌파 ===
    channel_period: int = 20,          # Donchian Channel 기간
    channel_breakout: bool = True,
    # === 모멘텀 ===
    momentum_rsi_accel: bool = True,   # RSI 50 돌파 가속
    rsi_threshold: float = 50,
    ma_cross_confirm: bool = True,     # MA 크로스 후 진입
    # === 레버리지 ===
    base_leverage: int = 3,
    max_leverage: int = 15,
    # === TP/SL ===
    sl_atr_mult: float = 1.5,
    tp_atr_mult: float = 4.0,
    # === 필터 ===
    min_atr_pct: float = 0.5,  # ATR이 가격의 최소 N% 이상이어야 (변동성 필터)
    cooldown_bars: int = 6,
    # === 추세 필터 ===
    trend_filter_period: int = 50,     # 장기 추세 MA
    require_trend_align: bool = True,  # 장기 추세 방향 일치 필수
) -> pd.DataFrame:
    """
    모멘텀 브레이크아웃 시그널 생성
    """
    df = df.copy()
    n = len(df)
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values

    atr = calc_atr(df).values

    # === 보조 지표 ===
    # MA
    ma20 = pd.Series(close).rolling(20).mean().values
    ma50 = pd.Series(close).rolling(trend_filter_period).mean().values

    # RSI
    delta = pd.Series(close).diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean().values
    loss_val = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean().values
    with np.errstate(divide='ignore', invalid='ignore'):
        rs = gain / np.where(loss_val == 0, np.nan, loss_val)
    rsi = 100 - (100 / (1 + rs))

    # Volume MA
    vol_ma = pd.Series(volume).rolling(20).mean().values

    # Donchian Channel
    high_channel = pd.Series(high).rolling(channel_period).max().values
    low_channel = pd.Series(low).rolling(channel_period).min().values

    # Ichimoku
    kumo_top = df.get("kumo_top", pd.Series(np.nan, index=df.index)).values
    kumo_bottom = df.get("kumo_bottom", pd.Series(np.nan, index=df.index)).values

    # Stochastic
    stoch1_k = df.get("stoch1_k", pd.Series(50, index=df.index)).values
    stoch2_k = df.get("stoch2_k", pd.Series(50, index=df.index)).values

    # MA cross
    ma5 = pd.Series(close).rolling(5).mean().values
    ma20_arr = pd.Series(close).rolling(20).mean().values

    # 출력 배열
    sig_direction = np.zeros(n)
    sig_strength = np.zeros(n)
    sig_leverage = np.zeros(n)
    sig_tp_mult = np.full(n, tp_atr_mult)
    sig_sl_mult = np.full(n, sl_atr_mult)

    last_entry = -cooldown_bars

    for i in range(max(trend_filter_period + 2, channel_period + 2, 60), n):
        if i - last_entry < cooldown_bars:
            continue

        if np.isnan(atr[i]) or atr[i] <= 0:
            continue

        # 변동성 필터
        atr_pct = atr[i] / close[i] * 100
        if atr_pct < min_atr_pct:
            continue

        # 장기 추세 필터
        if require_trend_align:
            if np.isnan(ma50[i]):
                continue
            long_trend = 1 if close[i] > ma50[i] else -1
        else:
            long_trend = 0  # 필터 없음

        # === 시그널 감지 ===
        signals_bull = 0  # 강세 시그널 수
        signals_bear = 0  # 약세 시그널 수

        # A) 구름 돌파
        if cloud_breakout and not np.isnan(kumo_top[i]) and not np.isnan(kumo_bottom[i]):
            prev_close = close[i-1]
            # 구름 위로 돌파 (이전 봉이 구름 안이나 아래, 현재 봉이 위)
            if close[i] > kumo_top[i] and prev_close <= kumo_top[i]:
                if volume[i] > vol_ma[i] * cloud_volume_mult:
                    signals_bull += 1
            # 구름 아래로 돌파
            if close[i] < kumo_bottom[i] and prev_close >= kumo_bottom[i]:
                if volume[i] > vol_ma[i] * cloud_volume_mult:
                    signals_bear += 1

            # 구름 위에서 지속 (약한 시그널)
            if close[i] > kumo_top[i] and close[i-1] > kumo_top[i]:
                signals_bull += 0.3
            if close[i] < kumo_bottom[i] and close[i-1] < kumo_bottom[i]:
                signals_bear += 0.3

        # B) 채널 돌파
        if channel_breakout and not np.isnan(high_channel[i-1]):
            if close[i] > high_channel[i-1]:  # N봉 최고가 돌파
                signals_bull += 1
            if close[i] < low_channel[i-1]:   # N봉 최저가 돌파
                signals_bear += 1

        # C) 모멘텀 가속
        if momentum_rsi_accel and not np.isnan(rsi[i]) and not np.isnan(rsi[i-1]):
            # RSI 50 위로 크로스 + 상승 가속
            if rsi[i] > rsi_threshold and rsi[i-1] <= rsi_threshold:
                signals_bull += 1
            if rsi[i] < (100 - rsi_threshold) and rsi[i-1] >= (100 - rsi_threshold):
                signals_bear += 1

            # RSI 60 이상에서 계속 상승 (모멘텀 지속)
            if rsi[i] > 60 and rsi[i] > rsi[i-1]:
                signals_bull += 0.3
            if rsi[i] < 40 and rsi[i] < rsi[i-1]:
                signals_bear += 0.3

        # D) MA 크로스
        if ma_cross_confirm and not np.isnan(ma5[i]) and not np.isnan(ma20_arr[i]):
            if ma5[i] > ma20_arr[i] and ma5[i-1] <= ma20_arr[i-1]:
                signals_bull += 1
            if ma5[i] < ma20_arr[i] and ma5[i-1] >= ma20_arr[i-1]:
                signals_bear += 1

            # MA 방향 유지 (약한)
            if ma5[i] > ma20_arr[i]:
                signals_bull += 0.2
            else:
                signals_bear += 0.2

        # E) 이중 스토캐스틱 동기화
        if stoch1_k[i] > 50 and stoch2_k[i] > 50:
            signals_bull += 0.3
        elif stoch1_k[i] < 50 and stoch2_k[i] < 50:
            signals_bear += 0.3

        # === 전략 타입별 최소 조건 ===
        direction = 0
        if strategy_type == "cloud":
            # 구름 돌파 필수 + 1개 이상 확인
            if signals_bull >= 1.3 and (not require_trend_align or long_trend == 1):
                direction = 1
            elif signals_bear >= 1.3 and (not require_trend_align or long_trend == -1):
                direction = -1
        elif strategy_type == "channel":
            if signals_bull >= 1.0 and (not require_trend_align or long_trend == 1):
                direction = 1
            elif signals_bear >= 1.0 and (not require_trend_align or long_trend == -1):
                direction = -1
        elif strategy_type == "momentum":
            if signals_bull >= 1.5 and (not require_trend_align or long_trend == 1):
                direction = 1
            elif signals_bear >= 1.5 and (not require_trend_align or long_trend == -1):
                direction = -1
        else:  # combined
            total_bull = signals_bull
            total_bear = signals_bear
            # 최소 2개 시그널 필요
            if total_bull >= 2.0 and (not require_trend_align or long_trend == 1):
                direction = 1
            elif total_bear >= 2.0 and (not require_trend_align or long_trend == -1):
                direction = -1

        if direction == 0:
            continue

        # === 강도 및 레버리지 ===
        raw_strength = max(signals_bull, signals_bear)
        strength = min(raw_strength / 4.0, 1.0)  # 4개 시그널이면 최대
        strength = max(strength, 0.2)

        lev = int(base_leverage + (max_leverage - base_leverage) * strength)

        # 강한 시그널 → TP 확대
        tp = tp_atr_mult + strength * 2.0

        sig_direction[i] = direction
        sig_strength[i] = strength
        sig_leverage[i] = lev
        sig_tp_mult[i] = tp
        sig_sl_mult[i] = sl_atr_mult

        last_entry = i

    df["sig_direction"] = sig_direction
    df["sig_strength"] = sig_strength
    df["sig_leverage"] = sig_leverage
    df["sig_tp_mult"] = sig_tp_mult
    df["sig_sl_mult"] = sig_sl_mult

    return df
