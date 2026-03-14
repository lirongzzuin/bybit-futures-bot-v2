"""
Adaptive Trend-Momentum (ATM) Strategy v3.0

Robust trend-following with macro filter:
  - EMA crossover (9/21) for timing
  - EMA 200 for macro trend direction
  - ADX for trend strength confirmation
  - RSI + MACD for momentum confirmation
  - Dynamic SL/TP based on ATR percentile
  - Regime-based position sizing (bull vs bear)

Designed to be profitable across ALL market conditions:
  - Bull runs (COVID 2020-2021)
  - Bear markets (2022-2023)
  - Mixed conditions (2025-2026)
"""
import numpy as np
import pandas as pd
from typing import Optional


# === Optimal Parameters ===
OPTIMAL_PARAMS = {
    "strategy_type": "adaptive_trend_momentum",
    # EMAs
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "ema_macro": 200,
    # ADX
    "adx_period": 14,
    "adx_trend_threshold": 25,
    "adx_range_threshold": 20,
    # RSI
    "rsi_period": 14,
    "rsi_ob": 70,
    "rsi_os": 30,
    # MACD
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    # Bollinger Bands
    "bb_period": 20,
    "bb_std": 2.0,
    # Risk
    "base_leverage": 3,
    "max_leverage": 7,
    "sl_atr_mult": 2.0,
    "tp_atr_mult_trend": 4.5,    # R:R = 2.25:1 (more reachable TP)
    "tp_atr_mult_range": 2.5,
    "dynamic_sl_tp": True,
    # Filters
    "cooldown_bars": 12,          # ~2 days cooldown
    "trend_confirmation_bars": 2,
    "min_trend_score": 3.0,       # Moderate quality
    "macd_hist_confirm_bars": 2,
    "use_dmi_filter": True,
    "dmi_threshold": 15,
    "macro_trend_filter": True,   # EMA200 macro filter
    "atr_vol_filter": True,
    "atr_vol_lookback": 100,
    "atr_vol_min_pct": 15,
    "atr_vol_max_pct": 95,
    # Regime-based sizing
    "bear_market_size_mult": 0.5, # 50% size in bear market
}

# === Engine Config ===
ENGINE_CONFIG = {
    "risk_per_trade": 0.015,
    "use_trailing_stop": False,
    "trailing_activation_atr": 3.0,
    "trailing_distance_atr": 2.5,
    "min_rr_ratio": 2.0,
    "max_daily_loss": 0.04,
    "time_exit_bars": 60,
    "time_exit_min_profit_atr": 0.5,
}


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_adx_dmi(df: pd.DataFrame, period: int = 14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx, plus_di, minus_di


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    adx, _, _ = calc_adx_dmi(df, period)
    return adx


def calc_bollinger_bands(series, period=20, std_mult=2.0):
    middle = series.rolling(period).mean()
    std = series.rolling(period).std()
    return middle + std * std_mult, middle, middle - std * std_mult


def generate_signals(df: pd.DataFrame, params: Optional[dict] = None) -> pd.DataFrame:
    """ATM Strategy v3.0 signal generation"""
    p = params or OPTIMAL_PARAMS
    df = df.copy()
    n = len(df)
    close = df["close"]

    # === Calculate indicators ===
    atr = calc_atr(df, 14)
    ema_fast = calc_ema(close, p["ema_fast"])
    ema_mid = calc_ema(close, p["ema_mid"])
    ema_slow = calc_ema(close, p["ema_slow"])
    ema_macro = calc_ema(close, p.get("ema_macro", 200))
    rsi = calc_rsi(close, p["rsi_period"])
    macd_line, macd_signal_line, macd_hist = calc_macd(
        close, p["macd_fast"], p["macd_slow"], p["macd_signal"]
    )
    adx, plus_di, minus_di = calc_adx_dmi(df, p["adx_period"])
    bb_upper, bb_middle, bb_lower = calc_bollinger_bands(
        close, p["bb_period"], p["bb_std"]
    )

    # EMA200 slope for regime detection
    ema_macro_slope = ema_macro.diff(10) / ema_macro.shift(10) * 100

    # Convert to numpy
    c = close.values
    o = df["open"].values
    atr_arr = atr.values
    ef = ema_fast.values
    em = ema_mid.values
    es = ema_slow.values
    emac = ema_macro.values
    emac_slope = ema_macro_slope.values
    r = rsi.values
    ml = macd_line.values
    ms = macd_signal_line.values
    mh = macd_hist.values
    ax = adx.values
    pdi = plus_di.values
    mdi = minus_di.values
    bbu = bb_upper.values
    bbl = bb_lower.values

    # Output
    sig_direction = np.zeros(n)
    sig_strength = np.zeros(n)
    sig_leverage = np.zeros(n)
    sig_tp_mult = np.full(n, p["tp_atr_mult_trend"])
    sig_sl_mult = np.full(n, p["sl_atr_mult"])
    sig_mode = np.zeros(n)

    last_entry = -p["cooldown_bars"]
    lookback = max(p.get("ema_macro", 200) + 10, 100)
    atr_vol_lookback = p.get("atr_vol_lookback", 100)

    for i in range(lookback, n):
        if i - last_entry < p["cooldown_bars"]:
            continue
        if np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        if np.isnan(ax[i]) or np.isnan(r[i]) or np.isnan(emac[i]):
            continue

        # ATR volatility filter
        if p.get("atr_vol_filter", False) and i >= atr_vol_lookback:
            atr_w = atr_arr[i-atr_vol_lookback:i]
            atr_w = atr_w[~np.isnan(atr_w)]
            if len(atr_w) > 10:
                pct = np.sum(atr_w < atr_arr[i]) / len(atr_w)
                min_p = p.get("atr_vol_min_pct", 15) / 100.0
                max_p = p.get("atr_vol_max_pct", 95) / 100.0
                if pct < min_p or pct > max_p:
                    continue

        # Determine macro regime
        macro_bull = c[i] > emac[i]
        macro_bear = c[i] < emac[i]
        # EMA200 slope for regime strength
        macro_slope_up = not np.isnan(emac_slope[i]) and emac_slope[i] > 0
        macro_slope_down = not np.isnan(emac_slope[i]) and emac_slope[i] < 0

        direction = 0
        strength = 0.0
        mode = 0
        sl_mult = p["sl_atr_mult"]

        # ==========================================
        # MODE 1: TREND FOLLOWING (ADX >= threshold)
        # ==========================================
        if ax[i] >= p["adx_trend_threshold"]:
            mode = 1
            bull_score = 0.0
            bear_score = 0.0

            # A) EMA crossover: fast crosses mid recently
            # Check if EMA fast > EMA mid (bullish) or < (bearish)
            ema_bull_align = ef[i] > em[i] and em[i] > es[i]
            ema_bear_align = ef[i] < em[i] and em[i] < es[i]

            if not ema_bull_align and not ema_bear_align:
                # Partial alignment: fast > mid only (weaker signal)
                if ef[i] > em[i] and c[i] > es[i]:
                    ema_bull_align = True  # Weaker bull
                elif ef[i] < em[i] and c[i] < es[i]:
                    ema_bear_align = True  # Weaker bear

            if not ema_bull_align and not ema_bear_align:
                pass  # No alignment
            else:
                # Check alignment persistence
                confirm = p["trend_confirmation_bars"]
                if ema_bull_align:
                    ok = True
                    for j in range(1, min(confirm + 1, i)):
                        if not (ef[i-j] > em[i-j]):
                            ok = False
                            break
                    if ok:
                        bull_score += 2.0

                if ema_bear_align:
                    ok = True
                    for j in range(1, min(confirm + 1, i)):
                        if not (ef[i-j] < em[i-j]):
                            ok = False
                            break
                    if ok:
                        bear_score += 2.0

            # B) RSI confirmation
            if not np.isnan(r[i]):
                if r[i] > 50 and r[i] < 75:
                    bull_score += 1.0
                if r[i] < 50 and r[i] > 25:
                    bear_score += 1.0
                if not np.isnan(r[i-1]):
                    if r[i] > r[i-1] and r[i] > 50:
                        bull_score += 0.3
                    if r[i] < r[i-1] and r[i] < 50:
                        bear_score += 0.3

            # C) MACD confirmation
            hist_bars = p.get("macd_hist_confirm_bars", 2)
            if not np.isnan(ml[i]) and not np.isnan(ms[i]):
                if ml[i] > ms[i]:
                    bull_score += 1.0
                if ml[i] < ms[i]:
                    bear_score += 1.0

                if i >= hist_bars:
                    if all(not np.isnan(mh[i-j]) and mh[i-j] > 0 for j in range(hist_bars)):
                        bull_score += 0.5
                    if all(not np.isnan(mh[i-j]) and mh[i-j] < 0 for j in range(hist_bars)):
                        bear_score += 0.5

            # D) DMI confirmation
            if p.get("use_dmi_filter", True):
                dmi_t = p.get("dmi_threshold", 15)
                if not np.isnan(pdi[i]) and not np.isnan(mdi[i]):
                    if pdi[i] > mdi[i] and pdi[i] > dmi_t:
                        bull_score += 0.5
                    if mdi[i] > pdi[i] and mdi[i] > dmi_t:
                        bear_score += 0.5

            # Entry decision
            min_score = p.get("min_trend_score", 3.0)
            if ema_bull_align and bull_score >= min_score:
                direction = 1
                strength = min(bull_score / 5.0, 1.0)
            elif ema_bear_align and bear_score >= min_score:
                direction = -1
                strength = min(bear_score / 5.0, 1.0)

        # ==========================================
        # MODE 2: MEAN REVERSION (ADX < threshold)
        # ==========================================
        elif ax[i] < p["adx_range_threshold"]:
            mode = 2
            if not np.isnan(bbl[i]) and not np.isnan(bbu[i]):
                # Long: extreme oversold
                if (c[i] <= bbl[i] and r[i] < 25 and
                    not np.isnan(r[i-1]) and r[i] > r[i-1]):
                    if not (macro_bear and macro_slope_down):  # Don't buy in strong bear
                        direction = 1
                        strength = min((25 - r[i]) / 15.0 + 0.3, 0.7)
                # Short: extreme overbought
                elif (c[i] >= bbu[i] and r[i] > 75 and
                      not np.isnan(r[i-1]) and r[i] < r[i-1]):
                    if not (macro_bull and macro_slope_up):  # Don't short in strong bull
                        direction = -1
                        strength = min((r[i] - 75) / 15.0 + 0.3, 0.7)
        else:
            continue  # Transition zone: no trade

        if direction == 0:
            continue

        # === Macro trend filter ===
        if p.get("macro_trend_filter", True):
            if direction == 1 and macro_bear:
                continue  # Don't long below EMA200
            if direction == -1 and macro_bull:
                continue  # Don't short above EMA200

        # === Candle direction check (optional) ===
        if p.get("require_candle_confirm", False):
            if not np.isnan(o[i]):
                if direction == 1 and c[i] < o[i]:
                    strength *= 0.7
                if direction == -1 and c[i] > o[i]:
                    strength *= 0.7

        if strength < 0.2:
            continue

        # === Dynamic SL/TP based on ATR percentile ===
        tp_mult = p["tp_atr_mult_trend"] if mode == 1 else p["tp_atr_mult_range"]
        sl_mult = p["sl_atr_mult"]

        if p.get("dynamic_sl_tp", False) and i >= atr_vol_lookback:
            atr_w = atr_arr[i-atr_vol_lookback:i]
            atr_w = atr_w[~np.isnan(atr_w)]
            if len(atr_w) > 10:
                pct = np.sum(atr_w < atr_arr[i]) / len(atr_w)
                if pct > 0.75:  # High vol
                    sl_mult = 2.5
                    tp_mult *= 1.2
                elif pct < 0.25:  # Low vol
                    sl_mult = 1.5
                    tp_mult *= 0.85

        # Strength bonus for TP
        tp_mult += strength * 0.5

        # === Leverage ===
        if mode == 1:
            if ax[i] >= 40:
                leverage = 5
            elif ax[i] >= 30:
                leverage = 4
            else:
                leverage = 3
        else:
            leverage = 2

        leverage = min(leverage, p["max_leverage"])

        # === Regime-based position sizing ===
        # Only reduce counter-trend trades (rare due to macro filter)
        # In-trend trades get full size
        bear_mult = p.get("bear_market_size_mult", 0.5)
        if direction == 1 and macro_bear and macro_slope_down:
            strength *= bear_mult  # Reduce longs in bear market
        elif direction == -1 and macro_bull and macro_slope_up:
            strength *= bear_mult  # Reduce shorts in bull market

        sig_direction[i] = direction
        sig_strength[i] = max(strength, 0.2)
        sig_leverage[i] = leverage
        sig_tp_mult[i] = tp_mult
        sig_sl_mult[i] = sl_mult
        sig_mode[i] = mode

        last_entry = i

    df["sig_direction"] = sig_direction
    df["sig_strength"] = sig_strength
    df["sig_leverage"] = sig_leverage
    df["sig_tp_mult"] = sig_tp_mult
    df["sig_sl_mult"] = sig_sl_mult
    df["sig_mode"] = sig_mode

    return df


def get_current_signal(df: pd.DataFrame) -> dict:
    """Get current bar signal (for live bot)"""
    sig_df = generate_signals(df)
    last = sig_df.iloc[-1]
    mode_map = {0: "none", 1: "trend", 2: "range"}

    return {
        "direction": int(last["sig_direction"]),
        "strength": float(last["sig_strength"]),
        "leverage": int(last["sig_leverage"]) if last["sig_leverage"] > 0 else 3,
        "tp_atr_mult": float(last["sig_tp_mult"]),
        "sl_atr_mult": float(last["sig_sl_mult"]),
        "atr": float(calc_atr(df).iloc[-1]),
        "close": float(last["close"]),
        "mode": mode_map.get(int(last.get("sig_mode", 0)), "none"),
    }
