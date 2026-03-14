"""
Multi-Timeframe Scalping Strategy v4.2

"Buy the dip in uptrend, sell the rally in downtrend"
  - 4H: EMA 9/21 alignment = trend direction
  - 15min: RSI oversold + BB lower touch = buy dip (in 4H uptrend)
           RSI overbought + BB upper touch = sell rally (in 4H downtrend)
  - High win rate from mean-reversion entries within trend direction
  - TP at middle Bollinger Band (mean reversion target)
  - Candlestick pattern confirmation
"""
import numpy as np
import pandas as pd
from typing import Optional


OPTIMAL_PARAMS = {
    "strategy_type": "multi_tf_scalp",
    # 4H trend
    "htf_ema_fast": 9,
    "htf_ema_mid": 21,
    "htf_rsi_period": 14,
    "htf_adx_period": 14,
    "htf_adx_threshold": 15,
    # 15min entry (mean reversion)
    "ltf_rsi_period": 14,
    "ltf_rsi_oversold": 35,     # Buy zone
    "ltf_rsi_overbought": 65,   # Sell zone
    "ltf_bb_period": 20,
    "ltf_bb_std": 2.0,
    "ltf_ema_fast": 9,
    "ltf_ema_mid": 21,
    # Risk
    "base_leverage": 5,
    "max_leverage": 15,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 2.0,         # TP > SL to overcome fees (R:R 1.33:1)
    "dynamic_sl_tp": True,
    # Filters
    "cooldown_bars": 4,          # 1 hour
    "min_score": 1.5,
    "use_candle_patterns": True,
    "candle_pattern_bonus": 0.5,
    "ema_macro": 200,
    "macro_trend_filter": False,
}

ENGINE_CONFIG = {
    "risk_per_trade": 0.001,     # 0.1% × 10x avg = 1% actual per trade
    "use_trailing_stop": False,
    "max_daily_loss": 0.06,
    "time_exit_bars": 0,         # Disabled: only TP/SL/reverse signal exit
}


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
    gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, min_periods=period).mean()
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
    tr = pd.concat([high-low, (high-close.shift(1)).abs(),
                     (low-close.shift(1)).abs()], axis=1).max(axis=1)
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
    if i < 2: return 0.0, 0.0
    bull, bear = 0.0, 0.0
    body = abs(c[i] - o[i])
    rng = h[i] - l[i]
    if rng <= 0: return 0.0, 0.0
    uw = h[i] - max(o[i], c[i])
    lw = min(o[i], c[i]) - l[i]
    ib = c[i] > o[i]
    pb = abs(c[i-1] - o[i-1])
    # Hammer
    if lw >= body * 2 and uw < body * 0.5 and ib: bull += 0.5
    # Shooting star
    if uw >= body * 2 and lw < body * 0.5 and not ib: bear += 0.5
    # Bullish engulfing
    if ib and c[i-1]<o[i-1] and body>pb*1.2 and o[i]<=c[i-1] and c[i]>=o[i-1]: bull += 0.7
    # Bearish engulfing
    if not ib and c[i-1]>o[i-1] and body>pb*1.2 and o[i]>=c[i-1] and c[i]<=o[i-1]: bear += 0.7
    # Piercing
    if ib and c[i-1]<o[i-1] and o[i]<c[i-1] and c[i]>(o[i-1]+c[i-1])/2: bull += 0.5
    # Dark cloud
    if not ib and c[i-1]>o[i-1] and o[i]>c[i-1] and c[i]<(o[i-1]+c[i-1])/2: bear += 0.5
    # Strong candle
    if body/rng > 0.7:
        if ib: bull += 0.4
        else: bear += 0.4
    return bull, bear

def _resample_to_4h(df_15m):
    df = df_15m.copy().set_index("timestamp")
    return df.resample("4h").agg({
        "open":"first","high":"max","low":"min","close":"last","volume":"sum"
    }).dropna(subset=["open"]).reset_index()

def _map_4h_to_15m(df_15m, df_4h):
    ts15 = df_15m["timestamp"].values.astype("datetime64[ns]")
    ts4h = df_4h["timestamp"].values.astype("datetime64[ns]")
    m = np.full(len(df_15m), -1, dtype=np.int64)
    j = 0
    for i in range(len(df_15m)):
        while j+1 < len(ts4h) and ts4h[j+1] <= ts15[i]: j += 1
        if ts4h[j] <= ts15[i]: m[i] = j
    return m


def generate_signals(df: pd.DataFrame, params: Optional[dict] = None) -> pd.DataFrame:
    """v4.2: Buy dip in uptrend, sell rally in downtrend"""
    p = params or OPTIMAL_PARAMS
    df = df.copy()
    n = len(df)

    # 4H indicators
    df_4h = _resample_to_4h(df)
    htf_map = _map_4h_to_15m(df, df_4h)
    hc = df_4h["close"]
    h_ef = calc_ema(hc, p["htf_ema_fast"]).values
    h_em = calc_ema(hc, p["htf_ema_mid"]).values
    h_rsi = calc_rsi(hc, p["htf_rsi_period"]).values
    h_adx = calc_adx_dmi(df_4h, p["htf_adx_period"])[0].values

    # 15min indicators
    close = df["close"]
    l_rsi = calc_rsi(close, p["ltf_rsi_period"]).values
    l_bbu, l_bbm, l_bbl = calc_bollinger_bands(close, p["ltf_bb_period"], p["ltf_bb_std"])
    l_bbu, l_bbm, l_bbl = l_bbu.values, l_bbm.values, l_bbl.values
    l_atr = calc_atr(df, 14).values
    l_ef = calc_ema(close, p["ltf_ema_fast"]).values
    l_em = calc_ema(close, p["ltf_ema_mid"]).values

    o, hi, lo, c = df["open"].values, df["high"].values, df["low"].values, close.values

    sig_dir = np.zeros(n)
    sig_str = np.zeros(n)
    sig_lev = np.zeros(n)
    sig_tp = np.full(n, p["tp_atr_mult"])
    sig_sl = np.full(n, p["sl_atr_mult"])

    last_entry = -p["cooldown_bars"]

    for i in range(30, n):
        if i - last_entry < p["cooldown_bars"]: continue
        if np.isnan(l_atr[i]) or l_atr[i] <= 0: continue
        if np.isnan(l_rsi[i]) or np.isnan(l_bbu[i]): continue

        h_idx = htf_map[i]
        if h_idx < 2 or h_idx >= len(df_4h): continue
        if np.isnan(h_adx[h_idx]) or np.isnan(h_rsi[h_idx]): continue

        # === 4H TREND ===
        trend = 0
        if h_ef[h_idx] > h_em[h_idx] and h_rsi[h_idx] > 45:
            trend = 1  # Bullish
        elif h_ef[h_idx] < h_em[h_idx] and h_rsi[h_idx] < 55:
            trend = -1  # Bearish

        if trend == 0: continue
        if h_adx[h_idx] < p["htf_adx_threshold"]: continue

        # === 15min MEAN REVERSION ENTRY ===
        direction = 0
        score = 0.0

        rsi_os = p["ltf_rsi_oversold"]
        rsi_ob = p["ltf_rsi_overbought"]

        if trend == 1:
            # BUY THE DIP: RSI oversold + near lower BB
            if l_rsi[i] < rsi_os:
                score += 1.5  # RSI oversold in uptrend = strong buy
                if c[i] <= l_bbl[i]:
                    score += 1.0  # At lower BB = double confirmation
                elif c[i] < l_bbm[i]:
                    score += 0.3  # Below middle BB

                # RSI turning up (reversal)
                if i > 0 and not np.isnan(l_rsi[i-1]):
                    if l_rsi[i] > l_rsi[i-1]:
                        score += 0.5  # RSI bouncing

                direction = 1

        elif trend == -1:
            # SELL THE RALLY: RSI overbought + near upper BB
            if l_rsi[i] > rsi_ob:
                score += 1.5
                if c[i] >= l_bbu[i]:
                    score += 1.0
                elif c[i] > l_bbm[i]:
                    score += 0.3

                if i > 0 and not np.isnan(l_rsi[i-1]):
                    if l_rsi[i] < l_rsi[i-1]:
                        score += 0.5  # RSI turning down

                direction = -1

        if direction == 0 or score < p["min_score"]: continue

        # Candlestick pattern bonus
        cb, cr = detect_candle_patterns(o, hi, lo, c, i)
        cpb = p.get("candle_pattern_bonus", 0.5)
        if direction == 1: score += cb * cpb
        else: score += cr * cpb

        strength = min(score / 4.0, 1.0)

        # Dynamic SL/TP
        sl_m, tp_m = p["sl_atr_mult"], p["tp_atr_mult"]
        if p.get("dynamic_sl_tp", True) and i >= 100:
            aw = l_atr[i-100:i]; aw = aw[~np.isnan(aw)]
            if len(aw) > 10:
                pct = np.sum(aw < l_atr[i]) / len(aw)
                if pct > 0.75: sl_m = 2.0; tp_m = 1.3
                elif pct < 0.25: sl_m = 1.0; tp_m = 0.8

        # Leverage
        if h_adx[h_idx] >= 30 and score >= 3.0: lev = 15
        elif h_adx[h_idx] >= 20: lev = 10
        else: lev = 5
        lev = min(lev, p["max_leverage"])

        sig_dir[i] = direction
        sig_str[i] = max(strength, 0.2)
        sig_lev[i] = lev
        sig_tp[i] = tp_m
        sig_sl[i] = sl_m
        last_entry = i

    df["sig_direction"] = sig_dir
    df["sig_strength"] = sig_str
    df["sig_leverage"] = sig_lev
    df["sig_tp_mult"] = sig_tp
    df["sig_sl_mult"] = sig_sl
    return df


def get_current_signal(df):
    sig_df = generate_signals(df)
    last = sig_df.iloc[-1]
    return {
        "direction": int(last["sig_direction"]),
        "strength": float(last["sig_strength"]),
        "leverage": int(last["sig_leverage"]) if last["sig_leverage"] > 0 else 5,
        "tp_atr_mult": float(last["sig_tp_mult"]),
        "sl_atr_mult": float(last["sig_sl_mult"]),
        "atr": float(calc_atr(df).iloc[-1]),
        "close": float(last["close"]),
        "mode": "multi_tf_scalp",
    }
