"""
Pullback-in-Trend Strategy v11.0 — Aggressive Profit Runner

4H 트렌드 방향 확인 후 15분봉 풀백에서 진입하는 전략.
v11.0: 수익 극대화 — v9.1 공격적 파라미터 복원 + 동적 레버리지 + 수익 러너

핵심 철학: "덜 잃는 봇"이 아닌 "돈 버는 봇"
  - v9.1 페이퍼: 76.9% 승률, +41.89% 수익 (공격적 파라미터)
  - v10.0~10.2: 보수적 전환 → 모든 버전 손실 (방향 오류)

v11.0 설계:
  1. v9.1 공격적 파라미터 복원 (ADX 23, RSI 25-43/57-75, SL 0.8x)
  2. 동적 레버리지: 시그널 품질에 따라 10x~15x
  3. 볼륨 가중 스코어링: 거래량 = 확신도
  4. 적응형 쿨다운: 강한 추세에서 빠른 재진입
  5. R:R 2.5:1 (TP 2.0x / SL 0.8x): 높은 기대값
"""
import numpy as np
import pandas as pd
from typing import Optional


OPTIMAL_PARAMS = {
    "strategy_type": "pullback_in_trend_v11.0",
    # 4H indicators
    "htf_ema_fast": 9,
    "htf_ema_mid": 21,
    "htf_rsi_period": 14,
    "htf_adx_period": 14,
    # Pullback entry params (v11.0 — v9.1 공격적 파라미터 복원)
    "adx_min": 23,                   # v9.1 복원 (was 28): 더 많은 추세 포착
    "rsi_pull_long_lo": 25,          # 롱 풀백 RSI 하한
    "rsi_pull_long_hi": 43,          # v9.1 복원 (was 38): 얕은 풀백도 포착
    "rsi_pull_short_lo": 57,         # v9.1 복원 (was 62): 얕은 바운스도 포착
    "rsi_pull_short_hi": 75,         # 숏 풀백 RSI 상한
    "tp_atr_mult": 2.0,             # v9.1 복원 (was 2.5): 달성 가능한 목표
    "sl_atr_mult": 0.8,             # v9.1 복원 (was 1.5): 타이트 SL, 높은 R:R
    "leverage": 10,                  # v9.1 복원 (was 5): 수익 극대화
    "cooldown": 6,                   # v9.1 복원 (was 10): 빠른 재진입
    "min_score": 2.0,                # v9.1 복원 (was 3.0): 더 많은 기회
    "bb_atr_margin": 0.35,          # v9.1 복원 (was 0.15): BB 근접 허용
    # v11.0 신규: 동적 레버리지
    "max_leverage": 15,              # 최대 레버리지
    "leverage_score_threshold": 3.0, # 이 스코어 이상에서 레버리지 부스트
    "leverage_boost_per_point": 2.5, # 스코어 1점당 +2.5x
    # Shared (legacy compat)
    "tf_leverage": 10,
    "ltf_ema_fast": 9,
    "ltf_ema_mid": 21,
    "ltf_rsi_period": 14,
    "ltf_bb_period": 20,
    "ltf_bb_std": 2.0,
    "volume_ma_period": 20,
    "volume_threshold": 0,
}

ENGINE_CONFIG = {
    "risk_per_trade": 0.025,      # 2.5% — 의미있는 포지션 사이즈 (was 1.0%)
    "use_trailing_stop": True,
    "trailing_activation_atr": 1.0,  # 1.0x ATR에서 트레일링 활성화 (빠른 수익 보호)
    "trailing_distance_atr": 0.8,    # 0.8x ATR 트레일링 거리 (타이트)
    "max_daily_loss": 0.08,       # 8% — 수익 기회를 위한 여유 (was 5%)
    "time_exit_bars": 72,         # 18h time exit (빠른 손절)
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
    Pullback-in-Trend Strategy v11.0 — Aggressive Profit Runner

    1. 4H: ADX >= 23 + DI 방향 + EMA9/21 정렬 → 트렌드 확인
    2. 15m: RSI 풀백 + BB 근접 → 과매도/과매수 확인
    3. 15m: MACD 반전 + RSI 바운스 → 반등 확인
    4. 볼륨 가중 스코어링 → 확신도 기반 점수
    5. 동적 레버리지 → 고품질 시그널에 더 큰 레버리지
    6. TP 2.0x ATR / SL 0.8x ATR → R:R 2.5:1
    """
    p = params or OPTIMAL_PARAMS
    df = df.copy()
    n = len(df)

    # ── Profit Optimization Config ──
    from config.indicator_params import PROFIT_OPTIMIZATION
    vol_tp = PROFIT_OPTIMIZATION["vol_tp_scale"]
    vol_sl = PROFIT_OPTIMIZATION["vol_sl_scale"]
    adx_bonus_cfg = PROFIT_OPTIMIZATION["adx_tp_bonus"]
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

    # 거래량 이동평균
    vol = df["volume"].values
    vol_ma = df["volume"].rolling(p.get("volume_ma_period", 20)).mean().values

    # 15min MACD
    _, _, l_macd_hist = calc_macd(close, 12, 26, 9)
    l_macd_hist = l_macd_hist.values

    c = close.values

    # Params
    adx_min = p.get("adx_min", 23)
    cooldown = p.get("cooldown", 6)
    min_score = p.get("min_score", 2.0)
    base_tp_mult = p.get("tp_atr_mult", 2.0)
    base_sl_mult = p.get("sl_atr_mult", 0.8)
    lev = p.get("leverage", 10)
    max_lev = p.get("max_leverage", 15)
    lev_threshold = p.get("leverage_score_threshold", 3.0)
    lev_boost = p.get("leverage_boost_per_point", 2.5)
    rsi_long_lo = p.get("rsi_pull_long_lo", 25)
    rsi_long_hi = p.get("rsi_pull_long_hi", 43)
    rsi_short_lo = p.get("rsi_pull_short_lo", 57)
    rsi_short_hi = p.get("rsi_pull_short_hi", 75)
    bb_margin = p.get("bb_atr_margin", 0.35)

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

        # ═══════════════════════════════════════════════════════════
        # v11.0: 적응형 쿨다운 — 강한 추세에서 빠른 재진입
        # ═══════════════════════════════════════════════════════════
        cur_adx = h_adx[h_idx]
        if cur_adx >= 35:
            effective_cooldown = max(4, cooldown - 2)  # 강한 추세: 1시간
        else:
            effective_cooldown = cooldown  # 기본: 1.5시간

        if i - last_entry < effective_cooldown:
            continue

        # ═══════════════════════════════════════════════════════════
        # 4H TREND CHECK: ADX >= 23 + DI direction + EMA alignment
        # ═══════════════════════════════════════════════════════════
        if cur_adx < adx_min:
            continue

        bullish_4h = (h_pdi[h_idx] > h_mdi[h_idx] and
                      h_ef[h_idx] > h_em[h_idx])
        bearish_4h = (h_mdi[h_idx] > h_pdi[h_idx] and
                      h_ef[h_idx] < h_em[h_idx])

        if not bullish_4h and not bearish_4h:
            continue

        direction = 0
        score = 0.0

        # ═══════════════════════════════════════════════════════════
        # 15m PULLBACK ENTRY
        # ═══════════════════════════════════════════════════════════
        if bullish_4h and rsi_long_lo <= l_rsi[i] <= rsi_long_hi:
            score += 1.5

            # BB lower band proximity (v11.0: 넓은 마진)
            if c[i] > l_bbl[i] + l_atr[i] * bb_margin:
                continue

            # Price near EMA21 (pullback to support)
            if c[i] <= l_em[i] + l_atr[i] * 0.3:
                score += 1.0

            # MACD histogram improving
            if (not np.isnan(l_macd_hist[i]) and i > 0 and
                    not np.isnan(l_macd_hist[i - 1])):
                if l_macd_hist[i] > l_macd_hist[i - 1]:
                    score += 0.5

            # RSI bouncing up
            if i > 0 and not np.isnan(l_rsi[i - 1]):
                if l_rsi[i] > l_rsi[i - 1]:
                    score += 0.5

            direction = 1

        elif bearish_4h and rsi_short_lo <= l_rsi[i] <= rsi_short_hi:
            score += 1.5

            # BB upper band proximity
            if c[i] < l_bbu[i] - l_atr[i] * bb_margin:
                continue

            # Price near EMA21 (bounce to resistance)
            if c[i] >= l_em[i] - l_atr[i] * 0.3:
                score += 1.0

            # MACD histogram worsening
            if (not np.isnan(l_macd_hist[i]) and i > 0 and
                    not np.isnan(l_macd_hist[i - 1])):
                if l_macd_hist[i] < l_macd_hist[i - 1]:
                    score += 0.5

            # RSI turning down
            if i > 0 and not np.isnan(l_rsi[i - 1]):
                if l_rsi[i] < l_rsi[i - 1]:
                    score += 0.5

            direction = -1

        # ═══════════════════════════════════════════════════════════
        # v11.0: VOLUME-WEIGHTED SCORING (단계별 가산)
        # ═══════════════════════════════════════════════════════════
        if not np.isnan(vol_ma[i]) and vol_ma[i] > 0:
            vol_ratio = vol[i] / vol_ma[i]
            if vol_ratio >= 3.0:
                score += 0.8    # 대량 거래 = 최고 확신
            elif vol_ratio >= 2.0:
                score += 0.5    # 강한 거래량
            elif vol_ratio >= 1.5:
                score += 0.3    # 평균 이상

        if direction == 0 or score < min_score:
            continue

        strength = min(score / 4.0, 1.0)

        # ═══════════════════════════════════════════════════════════
        # VOLATILITY-ADAPTIVE TP/SL
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
        # ADX STRENGTH BONUS (강한 추세 = 더 넓은 TP)
        # ═══════════════════════════════════════════════════════════
        if cur_adx >= adx_bonus_cfg["threshold_2"]:
            tp_mult += adx_bonus_cfg["bonus_2"]
        elif cur_adx >= adx_bonus_cfg["threshold_1"]:
            tp_mult += adx_bonus_cfg["bonus_1"]

        # ═══════════════════════════════════════════════════════════
        # v11.0: DYNAMIC LEVERAGE (시그널 품질 기반)
        # ═══════════════════════════════════════════════════════════
        dynamic_lev = lev
        score_excess = score - lev_threshold
        if score_excess > 0:
            boost = int(score_excess * lev_boost)
            dynamic_lev = min(lev + boost, max_lev)

        # 대량 거래 + 고스코어: 추가 +1x
        if (not np.isnan(vol_ma[i]) and vol_ma[i] > 0 and
                vol[i] >= vol_ma[i] * 2.0 and score >= 3.0):
            dynamic_lev = min(dynamic_lev + 1, max_lev)

        sig_dir[i] = direction
        sig_str[i] = max(strength, 0.3)
        sig_lev[i] = dynamic_lev
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
