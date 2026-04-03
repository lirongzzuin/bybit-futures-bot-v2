"""
Pullback-in-Trend Strategy v10.2

4H 트렌드 방향 확인 후 15분봉 풀백에서 진입하는 전략.
v10.2: 실전 데이터 기반 생존 최적화 — 7연패/MDD 50% 문제 해결

문제점 분석 (v10.1 실전 결과):
  - SL 1.2x ATR에서도 연속 스톱아웃 발생 (7연패)
  - MDD 25.7% → circuit breaker 발동 → 봇 정지
  - 약한 추세에서 진입 → 풀백이 아닌 추세 반전으로 손실
  - 동시 2포지션 → 상관 손실 증폭

v10.2 개선:
  1. SL 1.2x → 1.5x ATR: 추가 노이즈 필터링
  2. TP 2.5x ATR: R:R 1.67:1 유지
  3. 최소 스코어 2.5 → 3.0: 최고 품질 시그널만 진입
  4. ADX >= 28: 강한 추세에서만 진입 (25에서 상향)
  5. 레버리지 7x → 5x: 연패 시 계좌 보존
  6. 최대 동시 포지션 1개: 집중도 향상
  7. RSI 풀백 범위 축소: 롱 25-38, 숏 62-75
  8. 4H 트렌드 확인 2봉 연속 요구: 추세 안정성 검증
"""
import numpy as np
import pandas as pd
from typing import Optional


OPTIMAL_PARAMS = {
    "strategy_type": "pullback_in_trend_v10.2",
    # 4H indicators
    "htf_ema_fast": 9,
    "htf_ema_mid": 21,
    "htf_rsi_period": 14,
    "htf_adx_period": 14,
    # Pullback entry params (v10.2 — 실전 데이터 기반 생존 최적화)
    "adx_min": 28,                   # 4H ADX 최소값 (28: 강한 추세만, was 25)
    "rsi_pull_long_lo": 25,          # 롱 풀백 RSI 하한
    "rsi_pull_long_hi": 38,          # 롱 풀백 RSI 상한 (38: 더 깊은 풀백, was 40)
    "rsi_pull_short_lo": 62,         # 숏 풀백 RSI 하한 (62: 더 깊은 바운스, was 60)
    "rsi_pull_short_hi": 75,         # 숏 풀백 RSI 상한
    "tp_atr_mult": 2.5,             # TP = 2.5x ATR (was 2.0: 더 넓은 목표)
    "sl_atr_mult": 1.5,             # SL = 1.5x ATR → R:R ~1.67:1 (was 1.2x)
    "leverage": 5,                   # 5x (was 7x): 연패 생존 최적화
    "cooldown": 10,                  # 10바 (2.5시간) 쿨다운 (was 8: 과잉거래 방지)
    "min_score": 3.0,                # 최소 스코어 (was 2.5: 최고 품질만)
    "bb_atr_margin": 0.15,          # BB 터치 마진 (0.15: 정확한 터치, was 0.2)
    # Shared (legacy compat)
    "tf_leverage": 5,
    "ltf_ema_fast": 9,
    "ltf_ema_mid": 21,
    "ltf_rsi_period": 14,
    "ltf_bb_period": 20,
    "ltf_bb_std": 2.0,
    "volume_ma_period": 20,
    "volume_threshold": 0,
}

ENGINE_CONFIG = {
    "risk_per_trade": 0.01,       # 1.0% — 생존 최우선 리스크 (was 1.5%)
    "use_trailing_stop": True,
    "trailing_activation_atr": 2.0,  # 2.0x ATR 수익 시 트레일링 활성화 (was 1.5: 조기 활성화 방지)
    "trailing_distance_atr": 1.2,    # 1.2x ATR 트레일링 거리 (was 1.0: 너무 타이트하면 조기 청산)
    "max_daily_loss": 0.05,       # 5% — 일일 손실 한도 (was 6%)
    "time_exit_bars": 96,         # 24h time exit (was 18h: 추세에 더 많은 시간 부여)
}


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series, fast=12, slow=26, signal=9):
    ef = series.ewm(span=fast, adjust=False).mean()
    es = series.ewm(span=slow, adjust=False).mean()
    ml = ef - es
    sl = ml.ewm(span=signal, adjust=False).mean()
    return ml, sl, ml - sl


def calc_adx_dmi(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    pdm, mdm = high.diff(), -low.diff()
    pdm = pdm.where((pdm > mdm) & (pdm > 0), 0.0)
    mdm = mdm.where((mdm > pdm) & (mdm > 0), 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    pdi = 100 * (pdm.ewm(span=period, adjust=False).mean() / atr)
    mdi = 100 * (mdm.ewm(span=period, adjust=False).mean() / atr)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx, pdi, mdi


def calc_adx(df, period=14):
    adx, _, _ = calc_adx_dmi(df, period)
    return adx


def calc_bollinger_bands(series, period=20, std_mult=2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return mid + std * std_mult, mid, mid - std * std_mult


def detect_candle_patterns(o, h, l, c, i):
    """Detect bullish / bearish candlestick patterns at index *i*."""
    if i < 2:
        return 0.0, 0.0
    bull, bear = 0.0, 0.0
    body = abs(c[i] - o[i])
    rng = h[i] - l[i]
    if rng <= 0:
        return 0.0, 0.0
    uw = h[i] - max(o[i], c[i])
    lw = min(o[i], c[i]) - l[i]
    ib = c[i] > o[i]
    pb = abs(c[i - 1] - o[i - 1])

    # Hammer
    if lw >= body * 2 and uw < body * 0.5 and ib:
        bull += 0.5
    # Shooting star
    if uw >= body * 2 and lw < body * 0.5 and not ib:
        bear += 0.5
    # Bullish engulfing
    if (ib and c[i - 1] < o[i - 1] and body > pb * 1.2
            and o[i] <= c[i - 1] and c[i] >= o[i - 1]):
        bull += 0.7
    # Bearish engulfing
    if (not ib and c[i - 1] > o[i - 1] and body > pb * 1.2
            and o[i] >= c[i - 1] and c[i] <= o[i - 1]):
        bear += 0.7
    # Piercing
    if (ib and c[i - 1] < o[i - 1] and o[i] < c[i - 1]
            and c[i] > (o[i - 1] + c[i - 1]) / 2):
        bull += 0.5
    # Dark cloud
    if (not ib and c[i - 1] > o[i - 1] and o[i] > c[i - 1]
            and c[i] < (o[i - 1] + c[i - 1]) / 2):
        bear += 0.5
    # Strong candle
    if body / rng > 0.7:
        if ib:
            bull += 0.4
        else:
            bear += 0.4
    return bull, bear


# ---------------------------------------------------------------------------
# Timeframe helpers
# ---------------------------------------------------------------------------

def _resample_to_4h(df_15m):
    df = df_15m.copy().set_index("timestamp")
    return df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"]).reset_index()


def _map_4h_to_15m(df_15m, df_4h):
    ts15 = df_15m["timestamp"].values.astype("datetime64[ns]")
    ts4h = df_4h["timestamp"].values.astype("datetime64[ns]")
    m = np.full(len(df_15m), -1, dtype=np.int64)
    j = 0
    for i in range(len(df_15m)):
        while j + 1 < len(ts4h) and ts4h[j + 1] <= ts15[i]:
            j += 1
        if ts4h[j] <= ts15[i]:
            m[i] = j
    return m


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def generate_signals(df: pd.DataFrame, params: Optional[dict] = None) -> pd.DataFrame:
    """
    Pullback-in-Trend Strategy v9.0

    1. 4H: ADX >= 25 + DI 방향 + EMA9/21 정렬 → 트렌드 확인
    2. 15m: RSI 풀백 + BB 터치 → 과매도/과매수 확인
    3. 15m: MACD 반전 + RSI 바운스 → 반등 확인
    4. TP 2.0x ATR / SL 0.8x ATR → R:R 2.5:1
    """
    p = params or OPTIMAL_PARAMS
    df = df.copy()
    n = len(df)

    # ── Profit Optimization Config ──
    from config.indicator_params import PROFIT_OPTIMIZATION
    vol_tp = PROFIT_OPTIMIZATION["vol_tp_scale"]
    vol_sl = PROFIT_OPTIMIZATION["vol_sl_scale"]
    adx_bonus_cfg = PROFIT_OPTIMIZATION["adx_tp_bonus"]
    vol_spike_thresh = PROFIT_OPTIMIZATION["volume_spike_threshold"]
    vol_spike_score = PROFIT_OPTIMIZATION["volume_spike_score"]
    atr_avg_period = PROFIT_OPTIMIZATION["atr_avg_period"]

    # ── 4H indicators ──
    df_4h = _resample_to_4h(df)
    htf_map = _map_4h_to_15m(df, df_4h)

    hc = df_4h["close"]
    h_ef = calc_ema(hc, p["htf_ema_fast"]).values
    h_em = calc_ema(hc, p["htf_ema_mid"]).values
    h_adx, h_pdi, h_mdi = calc_adx_dmi(df_4h, p["htf_adx_period"])
    h_adx = h_adx.values
    h_pdi = h_pdi.values
    h_mdi = h_mdi.values

    # ── 15min indicators ──
    close = df["close"]
    l_rsi = calc_rsi(close, p["ltf_rsi_period"]).values
    l_bbu, l_bbm, l_bbl = calc_bollinger_bands(
        close, p["ltf_bb_period"], p["ltf_bb_std"]
    )
    l_bbu, l_bbm, l_bbl = l_bbu.values, l_bbm.values, l_bbl.values
    l_atr = calc_atr(df, 14).values
    l_em = calc_ema(close, p["ltf_ema_mid"]).values

    # ATR 이동평균 (변동성 레짐 판단)
    l_atr_ma = pd.Series(l_atr).rolling(atr_avg_period).mean().values

    # 거래량 이동평균 (거래량 확인용)
    vol = df["volume"].values
    vol_ma = df["volume"].rolling(p.get("volume_ma_period", 20)).mean().values

    # 15min MACD
    _, _, l_macd_hist = calc_macd(close, 12, 26, 9)
    l_macd_hist = l_macd_hist.values

    c = close.values

    # Params
    adx_min = p.get("adx_min", 25)
    cooldown = p.get("cooldown", 8)
    min_score = p.get("min_score", 2.0)
    base_tp_mult = p.get("tp_atr_mult", 2.0)
    base_sl_mult = p.get("sl_atr_mult", 0.8)
    lev = p.get("leverage", 10)
    rsi_long_lo = p.get("rsi_pull_long_lo", 25)
    rsi_long_hi = p.get("rsi_pull_long_hi", 40)
    rsi_short_lo = p.get("rsi_pull_short_lo", 60)
    rsi_short_hi = p.get("rsi_pull_short_hi", 75)
    bb_margin = p.get("bb_atr_margin", 0.2)

    # Output arrays
    sig_dir = np.zeros(n)
    sig_str = np.zeros(n)
    sig_lev = np.zeros(n)
    sig_tp = np.zeros(n)
    sig_sl = np.zeros(n)
    sig_mode = np.zeros(n)  # 0=none, 1=pullback

    last_entry = -cooldown

    for i in range(60, n):
        if np.isnan(l_atr[i]) or l_atr[i] <= 0:
            continue
        if np.isnan(l_rsi[i]) or np.isnan(l_bbu[i]):
            continue

        h_idx = htf_map[i]
        if h_idx < 2 or h_idx >= len(df_4h):
            continue
        if np.isnan(h_adx[h_idx]) or np.isnan(h_pdi[h_idx]):
            continue

        if i - last_entry < cooldown:
            continue

        # ═══════════════════════════════════════════════════════════
        # 4H TREND CHECK: ADX >= 28 + DI direction + EMA alignment
        # v10.2: 2봉 연속 추세 확인 (추세 안정성 검증)
        # ═══════════════════════════════════════════════════════════
        if h_adx[h_idx] < adx_min:
            continue

        bullish_4h = (h_pdi[h_idx] > h_mdi[h_idx] and
                      h_ef[h_idx] > h_em[h_idx])
        bearish_4h = (h_mdi[h_idx] > h_pdi[h_idx] and
                      h_ef[h_idx] < h_em[h_idx])

        if not bullish_4h and not bearish_4h:
            continue

        # v10.2: 이전 4H 봉에서도 같은 추세여야 진입 (추세 반전 필터)
        if h_idx >= 1 and not np.isnan(h_adx[h_idx - 1]):
            prev_bullish = (h_pdi[h_idx - 1] > h_mdi[h_idx - 1] and
                            h_ef[h_idx - 1] > h_em[h_idx - 1])
            prev_bearish = (h_mdi[h_idx - 1] > h_pdi[h_idx - 1] and
                            h_ef[h_idx - 1] < h_em[h_idx - 1])
            if bullish_4h and not prev_bullish:
                continue
            if bearish_4h and not prev_bearish:
                continue

        direction = 0
        score = 0.0

        # ═══════════════════════════════════════════════════════════
        # 15m PULLBACK ENTRY
        # ═══════════════════════════════════════════════════════════
        if bullish_4h and rsi_long_lo <= l_rsi[i] <= rsi_long_hi:
            # LONG: RSI pulled back in uptrend
            score += 1.5  # RSI in pullback zone

            # BB lower band touch required
            if c[i] > l_bbl[i] + l_atr[i] * bb_margin:
                continue  # Not near BB lower → skip

            # Price near or below EMA21 (pullback to support)
            if c[i] <= l_em[i] + l_atr[i] * 0.3:
                score += 1.0

            # MACD histogram improving (momentum turning)
            if (not np.isnan(l_macd_hist[i]) and i > 0 and
                    not np.isnan(l_macd_hist[i - 1])):
                if l_macd_hist[i] > l_macd_hist[i - 1]:
                    score += 0.5

            # RSI bouncing up from pullback
            if i > 0 and not np.isnan(l_rsi[i - 1]):
                if l_rsi[i] > l_rsi[i - 1]:
                    score += 0.5

            direction = 1

        elif bearish_4h and rsi_short_lo <= l_rsi[i] <= rsi_short_hi:
            # SHORT: RSI bounced in downtrend
            score += 1.5  # RSI in bounce zone

            # BB upper band touch required
            if c[i] < l_bbu[i] - l_atr[i] * bb_margin:
                continue  # Not near BB upper → skip

            # Price near or above EMA21 (bounce to resistance)
            if c[i] >= l_em[i] - l_atr[i] * 0.3:
                score += 1.0

            # MACD histogram worsening (momentum turning down)
            if (not np.isnan(l_macd_hist[i]) and i > 0 and
                    not np.isnan(l_macd_hist[i - 1])):
                if l_macd_hist[i] < l_macd_hist[i - 1]:
                    score += 0.5

            # RSI turning down from bounce
            if i > 0 and not np.isnan(l_rsi[i - 1]):
                if l_rsi[i] < l_rsi[i - 1]:
                    score += 0.5

            direction = -1

        # ═══════════════════════════════════════════════════════════
        # v10.0: VOLUME CONFIRMATION (score bonus)
        # ═══════════════════════════════════════════════════════════
        if (not np.isnan(vol_ma[i]) and vol_ma[i] > 0 and
                vol[i] >= vol_ma[i] * vol_spike_thresh):
            score += vol_spike_score

        if direction == 0 or score < min_score:
            continue

        strength = min(score / 4.0, 1.0)

        # ═══════════════════════════════════════════════════════════
        # v10.0: VOLATILITY-ADAPTIVE TP/SL
        # ═══════════════════════════════════════════════════════════
        if (not np.isnan(l_atr_ma[i]) and l_atr_ma[i] > 0):
            atr_ratio = l_atr[i] / l_atr_ma[i]
            if atr_ratio > 2.0:
                tp_mult = vol_tp["extreme"]
                sl_mult = vol_sl["extreme"]
            elif atr_ratio > 1.3:
                tp_mult = vol_tp["high"]
                sl_mult = vol_sl["high"]
            elif atr_ratio < 0.7:
                tp_mult = vol_tp["low"]
                sl_mult = vol_sl["low"]
            else:
                tp_mult = vol_tp["normal"]
                sl_mult = vol_sl["normal"]
        else:
            tp_mult = base_tp_mult
            sl_mult = base_sl_mult

        # ═══════════════════════════════════════════════════════════
        # v10.0: ADX STRENGTH BONUS (강한 추세 = 더 넓은 TP)
        # ═══════════════════════════════════════════════════════════
        cur_adx = h_adx[h_idx]
        if cur_adx >= adx_bonus_cfg["threshold_2"]:
            tp_mult += adx_bonus_cfg["bonus_2"]
        elif cur_adx >= adx_bonus_cfg["threshold_1"]:
            tp_mult += adx_bonus_cfg["bonus_1"]

        sig_dir[i] = direction
        sig_str[i] = max(strength, 0.3)
        sig_lev[i] = lev
        sig_tp[i] = tp_mult
        sig_sl[i] = sl_mult
        sig_mode[i] = 1  # pullback
        last_entry = i

    df["sig_direction"] = sig_dir
    df["sig_strength"] = sig_str
    df["sig_leverage"] = sig_lev
    df["sig_tp_mult"] = sig_tp
    df["sig_sl_mult"] = sig_sl
    df["sig_mode"] = sig_mode
    return df


# ---------------------------------------------------------------------------
# Live bot interface
# ---------------------------------------------------------------------------

def get_current_signal(df):
    """Return the most recent signal dict for the live trading bot."""
    sig_df = generate_signals(df)
    last = sig_df.iloc[-1]

    mode_map = {0: "none", 1: "pullback"}
    mode_val = int(last.get("sig_mode", 0))

    # v7.3: RSI, 볼륨 비율 추가 (데이터 수집용)
    rsi_val = 0.0
    vol_ratio = 0.0
    try:
        rsi_s = calc_rsi(df["close"], OPTIMAL_PARAMS.get("ltf_rsi_period", 14))
        if len(rsi_s) > 0 and not np.isnan(rsi_s.iloc[-1]):
            rsi_val = float(rsi_s.iloc[-1])
        vol_ma = df["volume"].rolling(OPTIMAL_PARAMS.get("volume_ma_period", 20)).mean()
        if len(vol_ma) > 0 and not np.isnan(vol_ma.iloc[-1]) and vol_ma.iloc[-1] > 0:
            vol_ratio = float(df["volume"].iloc[-1] / vol_ma.iloc[-1])
    except Exception:
        pass

    # v10.0: volatility regime info
    atr_val = float(calc_atr(df).iloc[-1])
    atr_ma_series = calc_atr(df).rolling(50).mean()
    atr_ma_val = float(atr_ma_series.iloc[-1]) if not pd.isna(atr_ma_series.iloc[-1]) else atr_val
    vol_regime = "normal"
    if atr_ma_val > 0:
        ratio = atr_val / atr_ma_val
        if ratio > 2.0:
            vol_regime = "extreme"
        elif ratio > 1.3:
            vol_regime = "high"
        elif ratio < 0.7:
            vol_regime = "low"

    return {
        "direction": int(last["sig_direction"]),
        "strength": float(last["sig_strength"]),
        "leverage": int(last["sig_leverage"]) if last["sig_leverage"] > 0
                    else OPTIMAL_PARAMS.get("leverage", OPTIMAL_PARAMS.get("tf_leverage", 10)),
        "tp_atr_mult": float(last["sig_tp_mult"]),
        "sl_atr_mult": float(last["sig_sl_mult"]),
        "atr": atr_val,
        "close": float(last["close"]),
        "mode": mode_map.get(mode_val, "none"),
        "rsi": rsi_val,
        "vol_ratio": vol_ratio,
        "vol_regime": vol_regime,
    }
