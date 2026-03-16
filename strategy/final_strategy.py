"""
Dual-Mode Adaptive Strategy v6.0

Three market regimes based on 4H ADX:
  - TREND-FOLLOWING (ADX >= 25): +DI/-DI direction, EMA alignment + MACD confirm
  - MEAN-REVERSION (ADX < 20): RSI extremes + Bollinger Band touches
  - TRANSITION (ADX 20-25): 4H EMA trend + mean-reversion entry with stricter filters

Each mode has its own TP/SL multipliers, leverage, cooldown, and entry logic.
"""
import numpy as np
import pandas as pd
from typing import Optional


OPTIMAL_PARAMS = {
    "strategy_type": "dual_mode_adaptive_v6",
    # 4H indicators
    "htf_ema_fast": 9,
    "htf_ema_mid": 21,
    "htf_rsi_period": 14,
    "htf_adx_period": 14,
    # Regime thresholds
    "adx_trend_threshold": 25,
    "adx_range_threshold": 20,
    # TREND-FOLLOWING mode (ADX >= 25)
    "tf_tp_atr_mult": 4.0,       # Wide TP for maximum returns (R:R 2:1)
    "tf_sl_atr_mult": 2.0,       # Wide SL to survive pullbacks in trends
    "tf_leverage": 20,            # Higher leverage for trend-following
    "tf_cooldown": 6,             # 1.5 hours
    "tf_min_score": 2.0,
    "tf_use_trailing": True,
    "tf_trailing_atr_mult": 2.0,
    # MEAN-REVERSION mode (ADX < 20)
    "mr_tp_atr_mult": 1.5,       # Tight TP for quick scalps
    "mr_sl_atr_mult": 1.0,       # Tight SL
    "mr_leverage": 30,            # Aggressive leverage for ranging (tight SL)
    "mr_cooldown": 4,             # 1 hour
    "mr_min_score": 1.5,
    "mr_rsi_oversold": 30,
    "mr_rsi_overbought": 70,
    # TRANSITION mode (ADX 20-25)
    "tr_tp_atr_mult": 2.0,
    "tr_sl_atr_mult": 1.5,
    "tr_leverage": 20,
    "tr_cooldown": 6,
    "tr_min_score": 2.5,         # Stricter
    "tr_rsi_oversold": 35,
    "tr_rsi_overbought": 65,
    # Shared
    "ltf_ema_fast": 9,
    "ltf_ema_mid": 21,
    "ltf_rsi_period": 14,
    "ltf_bb_period": 20,
    "ltf_bb_std": 2.0,
    "use_candle_patterns": True,
    "candle_pattern_bonus": 0.5,
    "dynamic_sl_tp": True,
}

ENGINE_CONFIG = {
    "risk_per_trade": 0.01,       # 1% — 소액 계좌에서 의미있는 수량 확보
    "use_trailing_stop": True,   # Enabled for trend mode
    "trailing_activation_atr": 2.0,
    "trailing_distance_atr": 2.0,
    "max_daily_loss": 0.15,
    "time_exit_bars": 0,
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
    Dual-Mode Adaptive Strategy v6.0

    1. Resample 15min -> 4H, compute ADX/+DI/-DI/EMA/RSI
    2. Classify regime: trend (ADX>=25), range (ADX<20), transition (20-25)
    3. Apply mode-specific entry logic on 15min bars
    4. Output per-bar: direction, strength, leverage, tp_mult, sl_mult, mode
    """
    p = params or OPTIMAL_PARAMS
    df = df.copy()
    n = len(df)

    # ── 4H indicators ──
    df_4h = _resample_to_4h(df)
    htf_map = _map_4h_to_15m(df, df_4h)

    hc = df_4h["close"]
    h_ef = calc_ema(hc, p["htf_ema_fast"]).values
    h_em = calc_ema(hc, p["htf_ema_mid"]).values
    h_rsi = calc_rsi(hc, p["htf_rsi_period"]).values
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
    l_ef = calc_ema(close, p["ltf_ema_fast"]).values
    l_em = calc_ema(close, p["ltf_ema_mid"]).values

    # 15min MACD
    l_macd, l_macd_sig, l_macd_hist = calc_macd(close, 12, 26, 9)
    l_macd_hist = l_macd_hist.values

    o = df["open"].values
    hi = df["high"].values
    lo = df["low"].values
    c = close.values

    # Output arrays
    sig_dir = np.zeros(n)
    sig_str = np.zeros(n)
    sig_lev = np.zeros(n)
    sig_tp = np.zeros(n)
    sig_sl = np.zeros(n)
    sig_mode = np.zeros(n)  # 0=none, 1=trend, 2=range, 3=transition

    last_entry = -max(p["tf_cooldown"], p["mr_cooldown"], p["tr_cooldown"])

    for i in range(30, n):
        if np.isnan(l_atr[i]) or l_atr[i] <= 0:
            continue
        if np.isnan(l_rsi[i]) or np.isnan(l_bbu[i]):
            continue

        h_idx = htf_map[i]
        if h_idx < 2 or h_idx >= len(df_4h):
            continue
        if np.isnan(h_adx[h_idx]) or np.isnan(h_rsi[h_idx]):
            continue
        if np.isnan(h_pdi[h_idx]) or np.isnan(h_mdi[h_idx]):
            continue

        adx_val = h_adx[h_idx]

        # ═══════════════════════════════════════════════════════════════
        # REGIME CLASSIFICATION
        # ═══════════════════════════════════════════════════════════════

        if adx_val >= p["adx_trend_threshold"]:
            # ─── TREND-FOLLOWING MODE ───
            cooldown = p["tf_cooldown"]
            min_score = p["tf_min_score"]

            if i - last_entry < cooldown:
                continue

            # Direction from +DI vs -DI
            direction = 0
            score = 0.0

            if h_pdi[h_idx] > h_mdi[h_idx]:
                htf_dir = 1   # Bullish trend
            else:
                htf_dir = -1  # Bearish trend

            if htf_dir == 1:
                # LONG: 15min EMA9 > EMA21 (aligns with 4H)
                if l_ef[i] > l_em[i]:
                    score += 1.5  # EMA alignment
                else:
                    continue  # No alignment, skip

                # MACD histogram confirms (positive for long)
                if not np.isnan(l_macd_hist[i]) and l_macd_hist[i] > 0:
                    score += 1.0  # MACD confirm

                # RSI in favorable range (40-70 for long — NOT extreme)
                if 40 <= l_rsi[i] <= 70:
                    score += 0.5  # RSI zone

                direction = 1

            elif htf_dir == -1:
                # SHORT: 15min EMA9 < EMA21 (aligns with 4H)
                if l_ef[i] < l_em[i]:
                    score += 1.5  # EMA alignment
                else:
                    continue  # No alignment, skip

                # MACD histogram confirms (negative for short)
                if not np.isnan(l_macd_hist[i]) and l_macd_hist[i] < 0:
                    score += 1.0  # MACD confirm

                # RSI in favorable range (30-60 for short — NOT extreme)
                if 30 <= l_rsi[i] <= 60:
                    score += 0.5  # RSI zone

                direction = -1

            if direction == 0 or score < min_score:
                continue

            # Candle pattern bonus
            cb, cr = detect_candle_patterns(o, hi, lo, c, i)
            cpb = p.get("candle_pattern_bonus", 0.5)
            if direction == 1:
                score += cb * cpb
            else:
                score += cr * cpb

            strength = min(score / 4.0, 1.0)

            sig_dir[i] = direction
            sig_str[i] = max(strength, 0.2)
            sig_lev[i] = p["tf_leverage"]
            sig_tp[i] = p["tf_tp_atr_mult"]
            sig_sl[i] = p["tf_sl_atr_mult"]
            sig_mode[i] = 1  # trend
            last_entry = i

        elif adx_val < p["adx_range_threshold"]:
            # ─── MEAN-REVERSION MODE ───
            cooldown = p["mr_cooldown"]
            min_score = p["mr_min_score"]

            if i - last_entry < cooldown:
                continue

            direction = 0
            score = 0.0

            rsi_os = p["mr_rsi_oversold"]
            rsi_ob = p["mr_rsi_overbought"]

            # LONG: RSI oversold + lower BB
            if l_rsi[i] < rsi_os:
                score += 1.5  # RSI extreme
                if c[i] <= l_bbl[i]:
                    score += 1.0  # BB touch
                elif c[i] < l_bbm[i]:
                    score += 0.3  # Below middle BB

                # RSI turning up (reversal confirmation)
                if i > 0 and not np.isnan(l_rsi[i - 1]):
                    if l_rsi[i] > l_rsi[i - 1]:
                        score += 0.5  # RSI bouncing

                direction = 1

            # SHORT: RSI overbought + upper BB
            elif l_rsi[i] > rsi_ob:
                score += 1.5  # RSI extreme
                if c[i] >= l_bbu[i]:
                    score += 1.0  # BB touch
                elif c[i] > l_bbm[i]:
                    score += 0.3  # Above middle BB

                # RSI turning down
                if i > 0 and not np.isnan(l_rsi[i - 1]):
                    if l_rsi[i] < l_rsi[i - 1]:
                        score += 0.5  # RSI turning

                direction = -1

            if direction == 0 or score < min_score:
                continue

            # Candle pattern bonus
            cb, cr = detect_candle_patterns(o, hi, lo, c, i)
            cpb = p.get("candle_pattern_bonus", 0.5)
            if direction == 1:
                score += cb * cpb
            else:
                score += cr * cpb

            strength = min(score / 4.0, 1.0)

            sig_dir[i] = direction
            sig_str[i] = max(strength, 0.2)
            sig_lev[i] = p["mr_leverage"]
            sig_tp[i] = p["mr_tp_atr_mult"]
            sig_sl[i] = p["mr_sl_atr_mult"]
            sig_mode[i] = 2  # range
            last_entry = i

        else:
            # ─── TRANSITION MODE (ADX 20-25) ───
            cooldown = p["tr_cooldown"]
            min_score = p["tr_min_score"]

            if i - last_entry < cooldown:
                continue

            # 4H trend direction from EMA9 > EMA21
            if h_ef[h_idx] > h_em[h_idx]:
                htf_dir = 1
            elif h_ef[h_idx] < h_em[h_idx]:
                htf_dir = -1
            else:
                continue  # No clear trend in transition

            direction = 0
            score = 0.0

            rsi_os = p["tr_rsi_oversold"]
            rsi_ob = p["tr_rsi_overbought"]

            # Mean-reversion entry WITH 4H trend filter
            if htf_dir == 1:
                # Only take LONG mean-reversion in uptrend
                if l_rsi[i] < rsi_os:
                    score += 1.5  # RSI extreme
                    if c[i] <= l_bbl[i]:
                        score += 1.0  # BB touch
                    elif c[i] < l_bbm[i]:
                        score += 0.3

                    if i > 0 and not np.isnan(l_rsi[i - 1]):
                        if l_rsi[i] > l_rsi[i - 1]:
                            score += 0.5

                    direction = 1

            elif htf_dir == -1:
                # Only take SHORT mean-reversion in downtrend
                if l_rsi[i] > rsi_ob:
                    score += 1.5
                    if c[i] >= l_bbu[i]:
                        score += 1.0
                    elif c[i] > l_bbm[i]:
                        score += 0.3

                    if i > 0 and not np.isnan(l_rsi[i - 1]):
                        if l_rsi[i] < l_rsi[i - 1]:
                            score += 0.5

                    direction = -1

            if direction == 0 or score < min_score:
                continue

            # Candle pattern bonus
            cb, cr = detect_candle_patterns(o, hi, lo, c, i)
            cpb = p.get("candle_pattern_bonus", 0.5)
            if direction == 1:
                score += cb * cpb
            else:
                score += cr * cpb

            strength = min(score / 4.0, 1.0)

            sig_dir[i] = direction
            sig_str[i] = max(strength, 0.2)
            sig_lev[i] = p["tr_leverage"]
            sig_tp[i] = p["tr_tp_atr_mult"]
            sig_sl[i] = p["tr_sl_atr_mult"]
            sig_mode[i] = 3  # transition
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

    mode_map = {0: "none", 1: "trend", 2: "range", 3: "transition"}
    mode_val = int(last.get("sig_mode", 0))

    return {
        "direction": int(last["sig_direction"]),
        "strength": float(last["sig_strength"]),
        "leverage": int(last["sig_leverage"]) if last["sig_leverage"] > 0
                    else OPTIMAL_PARAMS["tf_leverage"],
        "tp_atr_mult": float(last["sig_tp_mult"]),
        "sl_atr_mult": float(last["sig_sl_mult"]),
        "atr": float(calc_atr(df).iloc[-1]),
        "close": float(last["close"]),
        "mode": mode_map.get(mode_val, "none"),
    }
