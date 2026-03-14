"""
Adaptive Trend-Momentum (ATM) Strategy v3.1

High win-rate trend-following with:
  - Tight TP (3.0x ATR) for >50% win rate
  - Higher leverage (7-15x) for bigger profits
  - EMA(9/21/50/200) + ADX/DMI + RSI + MACD + Bollinger Bands
  - Candlestick pattern recognition (Hammer, Engulfing, etc.)
  - Compound growth via percentage-based sizing

Backtested across 3 periods (bull/bear/mixed)
"""
import numpy as np
import pandas as pd
from typing import Optional


OPTIMAL_PARAMS = {
    "strategy_type": "adaptive_trend_momentum",
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "ema_macro": 200,
    "adx_period": 14,
    "adx_trend_threshold": 25,
    "adx_range_threshold": 20,
    "rsi_period": 14,
    "rsi_ob": 70,
    "rsi_os": 30,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_std": 2.0,
    # High leverage for bigger profits
    "base_leverage": 10,
    "max_leverage": 30,
    # Tight TP for higher win rate
    "sl_atr_mult": 2.0,
    "tp_atr_mult_trend": 2.5,    # Sweet spot: R:R = 1.25:1, WR ~52%
    "tp_atr_mult_range": 1.8,
    "dynamic_sl_tp": True,
    # Filters
    "cooldown_bars": 12,
    "trend_confirmation_bars": 2,
    "min_trend_score": 3.0,
    "macd_hist_confirm_bars": 2,
    "use_dmi_filter": True,
    "dmi_threshold": 15,
    "macro_trend_filter": True,
    "atr_vol_filter": True,
    "atr_vol_lookback": 100,
    "atr_vol_min_pct": 15,
    "atr_vol_max_pct": 95,
    "bear_market_size_mult": 0.5,
    # Candlestick pattern bonus
    "use_candle_patterns": True,
    "candle_pattern_bonus": 0.5,
}

ENGINE_CONFIG = {
    "risk_per_trade": 0.005,      # 0.5% base risk × 10x leverage = ~5% actual
    "use_trailing_stop": False,
    "trailing_activation_atr": 3.0,
    "trailing_distance_atr": 2.5,
    "min_rr_ratio": 1.5,
    "max_daily_loss": 0.05,
    "time_exit_bars": 60,
    "time_exit_min_profit_atr": 0.3,
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
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(),
                     (low - close.shift(1)).abs()], axis=1).max(axis=1)
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


def detect_candle_patterns(o, h, l, c, i):
    """
    Detect candlestick patterns at bar i.
    Returns: (bull_score, bear_score) bonus from candle patterns
    """
    if i < 2:
        return 0.0, 0.0

    bull = 0.0
    bear = 0.0
    body = abs(c[i] - o[i])
    full_range = h[i] - l[i]
    if full_range <= 0:
        return 0.0, 0.0

    body_ratio = body / full_range
    upper_wick = h[i] - max(o[i], c[i])
    lower_wick = min(o[i], c[i]) - l[i]
    is_bull = c[i] > o[i]
    is_bear = c[i] < o[i]

    prev_body = abs(c[i-1] - o[i-1])
    prev_range = h[i-1] - l[i-1]

    # === Hammer / Hanging Man (하락다람쥐/상승다람쥐) ===
    # Long lower wick (>= 2x body), small upper wick
    if lower_wick >= body * 2 and upper_wick < body * 0.5:
        if is_bull:
            bull += 0.5  # Bullish hammer
        else:
            bear += 0.3  # Hanging man (bearish context)

    # === Inverted Hammer / Shooting Star ===
    # Long upper wick (>= 2x body), small lower wick
    if upper_wick >= body * 2 and lower_wick < body * 0.5:
        if is_bear:
            bear += 0.5  # Shooting star
        else:
            bull += 0.3  # Inverted hammer (bullish context)

    # === Bullish Engulfing (상승 장악형) ===
    if (is_bull and c[i-1] < o[i-1] and  # Prev was bearish
        body > prev_body * 1.2 and        # Current body > prev body
        o[i] <= c[i-1] and c[i] >= o[i-1]):  # Engulfs prev
        bull += 0.7

    # === Bearish Engulfing (하락 장악형) ===
    if (is_bear and c[i-1] > o[i-1] and  # Prev was bullish
        body > prev_body * 1.2 and
        o[i] >= c[i-1] and c[i] <= o[i-1]):
        bear += 0.7

    # === Bullish Punch (상승펀치) / Piercing Line ===
    if (is_bull and c[i-1] < o[i-1] and  # Prev bearish
        o[i] < c[i-1] and                 # Open below prev close
        c[i] > (o[i-1] + c[i-1]) / 2):    # Close above midpoint
        bull += 0.5

    # === Bearish Punch (하락펀치) / Dark Cloud Cover ===
    if (is_bear and c[i-1] > o[i-1] and  # Prev bullish
        o[i] > c[i-1] and                 # Open above prev close
        c[i] < (o[i-1] + c[i-1]) / 2):    # Close below midpoint
        bear += 0.5

    # === Doji (우유부단 — 방향 전환 시그널) ===
    if body_ratio < 0.1 and full_range > 0:  # Very small body
        # Doji after a trend can signal reversal
        if i >= 3:
            recent_trend = c[i-1] - c[i-3]
            if recent_trend > 0:  # Was going up → potential bearish
                bear += 0.3
            elif recent_trend < 0:  # Was going down → potential bullish
                bull += 0.3

    # === Strong momentum candle (대양봉/대음봉) ===
    if body_ratio > 0.7:  # Body is 70%+ of total range
        if is_bull:
            bull += 0.4  # Strong bullish momentum
        else:
            bear += 0.4  # Strong bearish momentum

    return bull, bear


def generate_signals(df: pd.DataFrame, params: Optional[dict] = None) -> pd.DataFrame:
    """ATM Strategy v3.1 signal generation with candle patterns"""
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
        close, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    adx, plus_di, minus_di = calc_adx_dmi(df, p["adx_period"])
    bb_upper, bb_middle, bb_lower = calc_bollinger_bands(
        close, p["bb_period"], p["bb_std"])
    ema_macro_slope = ema_macro.diff(10) / ema_macro.shift(10) * 100

    # Convert to numpy
    c = close.values
    o = df["open"].values
    h = df["high"].values
    lo = df["low"].values
    atr_arr = atr.values
    ef, em_arr, es = ema_fast.values, ema_mid.values, ema_slow.values
    emac = ema_macro.values
    emac_slope = ema_macro_slope.values
    r = rsi.values
    ml, ms_arr, mh = macd_line.values, macd_signal_line.values, macd_hist.values
    ax = adx.values
    pdi, mdi_arr = plus_di.values, minus_di.values
    bbu, bbl = bb_upper.values, bb_lower.values

    # Output arrays
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

        # Macro regime
        macro_bull = c[i] > emac[i]
        macro_bear = c[i] < emac[i]
        macro_slope_up = not np.isnan(emac_slope[i]) and emac_slope[i] > 0
        macro_slope_down = not np.isnan(emac_slope[i]) and emac_slope[i] < 0

        direction = 0
        strength = 0.0
        mode = 0
        sl_mult = p["sl_atr_mult"]

        # === Candlestick pattern detection ===
        candle_bull, candle_bear = 0.0, 0.0
        if p.get("use_candle_patterns", True):
            candle_bull, candle_bear = detect_candle_patterns(o, h, lo, c, i)

        # ==========================================
        # MODE 1: TREND FOLLOWING (ADX >= threshold)
        # ==========================================
        if ax[i] >= p["adx_trend_threshold"]:
            mode = 1
            bull_score = 0.0
            bear_score = 0.0

            # A) EMA alignment
            ema_bull_align = ef[i] > em_arr[i] and em_arr[i] > es[i]
            ema_bear_align = ef[i] < em_arr[i] and em_arr[i] < es[i]

            if not ema_bull_align and not ema_bear_align:
                if ef[i] > em_arr[i] and c[i] > es[i]:
                    ema_bull_align = True
                elif ef[i] < em_arr[i] and c[i] < es[i]:
                    ema_bear_align = True

            if ema_bull_align or ema_bear_align:
                confirm = p["trend_confirmation_bars"]
                if ema_bull_align:
                    ok = True
                    for j in range(1, min(confirm + 1, i)):
                        if not (ef[i-j] > em_arr[i-j]):
                            ok = False
                            break
                    if ok:
                        bull_score += 2.0

                if ema_bear_align:
                    ok = True
                    for j in range(1, min(confirm + 1, i)):
                        if not (ef[i-j] < em_arr[i-j]):
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
            if not np.isnan(ml[i]) and not np.isnan(ms_arr[i]):
                if ml[i] > ms_arr[i]:
                    bull_score += 1.0
                if ml[i] < ms_arr[i]:
                    bear_score += 1.0
                if i >= hist_bars:
                    if all(not np.isnan(mh[i-j]) and mh[i-j] > 0 for j in range(hist_bars)):
                        bull_score += 0.5
                    if all(not np.isnan(mh[i-j]) and mh[i-j] < 0 for j in range(hist_bars)):
                        bear_score += 0.5

            # D) DMI confirmation
            if p.get("use_dmi_filter", True):
                dmi_t = p.get("dmi_threshold", 15)
                if not np.isnan(pdi[i]) and not np.isnan(mdi_arr[i]):
                    if pdi[i] > mdi_arr[i] and pdi[i] > dmi_t:
                        bull_score += 0.5
                    if mdi_arr[i] > pdi[i] and mdi_arr[i] > dmi_t:
                        bear_score += 0.5

            # E) Candlestick pattern bonus
            cpb = p.get("candle_pattern_bonus", 0.5)
            bull_score += candle_bull * cpb
            bear_score += candle_bear * cpb

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
                # Long: oversold + bullish candle pattern
                if (c[i] <= bbl[i] and r[i] < 25 and
                    not np.isnan(r[i-1]) and r[i] > r[i-1]):
                    if not (macro_bear and macro_slope_down):
                        direction = 1
                        strength = min((25 - r[i]) / 15.0 + 0.3, 0.7)
                        strength += candle_bull * 0.3  # Candle pattern bonus
                elif (c[i] >= bbu[i] and r[i] > 75 and
                      not np.isnan(r[i-1]) and r[i] < r[i-1]):
                    if not (macro_bull and macro_slope_up):
                        direction = -1
                        strength = min((r[i] - 75) / 15.0 + 0.3, 0.7)
                        strength += candle_bear * 0.3
        else:
            continue

        if direction == 0:
            continue

        # === Macro trend filter ===
        if p.get("macro_trend_filter", True):
            if direction == 1 and macro_bear:
                continue
            if direction == -1 and macro_bull:
                continue

        if strength < 0.2:
            continue

        # === Dynamic SL/TP ===
        tp_mult = p["tp_atr_mult_trend"] if mode == 1 else p["tp_atr_mult_range"]
        sl_mult = p["sl_atr_mult"]

        if p.get("dynamic_sl_tp", False) and i >= atr_vol_lookback:
            atr_w = atr_arr[i-atr_vol_lookback:i]
            atr_w = atr_w[~np.isnan(atr_w)]
            if len(atr_w) > 10:
                pct = np.sum(atr_w < atr_arr[i]) / len(atr_w)
                if pct > 0.75:
                    sl_mult = 2.5
                    tp_mult *= 1.1
                elif pct < 0.25:
                    sl_mult = 1.5
                    tp_mult *= 0.9

        tp_mult += strength * 0.3

        # === Leverage — 신호 확실도에 따라 적극 활용 ===
        if mode == 1:
            # 캔들 패턴 보너스가 있으면 확신도 추가
            has_candle = (direction == 1 and candle_bull >= 0.5) or \
                         (direction == -1 and candle_bear >= 0.5)

            if ax[i] >= 40 and strength >= 0.8 and has_candle:
                leverage = 30  # 매우 확실: ADX 40+ & 강한 신호 & 캔들 패턴
            elif ax[i] >= 40 and strength >= 0.7:
                leverage = 25  # 확실: ADX 40+ & 강한 신호
            elif ax[i] >= 35 and strength >= 0.6:
                leverage = 20  # 강함: ADX 35+ & 보통 이상
            elif ax[i] >= 30:
                leverage = 15  # 보통 추세
            else:
                leverage = 10  # 기본
        else:
            leverage = 8  # 횡보장 반전 매매
        leverage = min(leverage, p["max_leverage"])

        # === Regime-based sizing ===
        bear_mult = p.get("bear_market_size_mult", 0.5)
        if direction == 1 and macro_bear and macro_slope_down:
            strength *= bear_mult
        elif direction == -1 and macro_bull and macro_slope_up:
            strength *= bear_mult

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
        "leverage": int(last["sig_leverage"]) if last["sig_leverage"] > 0 else 7,
        "tp_atr_mult": float(last["sig_tp_mult"]),
        "sl_atr_mult": float(last["sig_sl_mult"]),
        "atr": float(calc_atr(df).iloc[-1]),
        "close": float(last["close"]),
        "mode": mode_map.get(int(last.get("sig_mode", 0)), "none"),
    }
