"""
Momentum Scalper v17.3 — R:R 회복 (v17.2+ 실거래 35건 분석 후 개선)

v17.2+ 35건 결과: 승률 65.7% / R:R 0.55:1 / 총 +0.30 USDT (break-even)
원인: 120~160분 구간에서 13/35(37%)가 "시간 기반 본전 SL"로 작은 이익 탈출

v17.3 변경 (position_monitor.py 위주):
  - 시간 기반 본전 SL: 120분/수익<0.5% → 160분/수익<0.3%
  - Stage 2 트레일링 활성화: 3.5% → 4.5%
  - Stage 2 보호 비율 재구성: 25/35/50/65 → 20/30/45/60
  - 진입 조건(v17.2)은 그대로 유지
"""
import numpy as np
import pandas as pd
from typing import Optional


OPTIMAL_PARAMS = {
    "strategy_type": "momentum_scalper_v17.3",
    # 1H indicators (HTF)
    "htf_ema_fast": 9,
    "htf_ema_mid": 21,
    "htf_rsi_period": 14,
    "htf_adx_period": 14,
    # Entry params (5m)
    "adx_min": 25,                   # v17.2: 28→25 (거래 0건 해소)
    "rsi_pull_long_lo": 30,          # v17.2: 25→30
    "rsi_pull_long_hi": 48,          # v17.2: 35→48 (현실 RSI 46-54 구간 커버)
    "rsi_pull_short_lo": 52,         # v17.2: 65→52
    "rsi_pull_short_hi": 70,         # v17.2: 75→70
    "tp_atr_mult": 2.5,
    "sl_atr_mult": 1.7,              # v17.1 유지
    "leverage": 5,
    "cooldown": 6,                   # v17.2: 8→6 (30분, 빈도 확보)
    "cooldown_after_sl": 6,
    "min_score": 2.3,                # v17.2: 2.7→2.3
    "bb_atr_margin": 0.50,           # v17.2: 0.30→0.50 (BB 근접 기준 완화)
    # 동적 레버리지
    "max_leverage": 8,
    "leverage_score_threshold": 3.5,
    "leverage_boost_per_point": 2.0,
    # 최소 TP/SL 거리 (가격 대비 %)
    "min_tp_pct": 0.020,
    "min_sl_pct": 0.017,             # v17.1: 1.5→1.7% (SL ATR 1.7에 맞춤)
    # Shared
    "tf_leverage": 10,
    "ltf_ema_fast": 9,
    "ltf_ema_mid": 21,
    "ltf_rsi_period": 14,
    "ltf_bb_period": 20,
    "ltf_bb_std": 2.0,
    "volume_ma_period": 20,
    "volume_threshold": 0,
    # 타임프레임 설정 (v15.0)
    "entry_timeframe": "5",          # 5분봉 진입
    "htf_timeframe": "60",           # 1시간봉 추세
    "htf_resample": "1h",            # 리샘플링 타겟
}

ENGINE_CONFIG = {
    "risk_per_trade": 0.015,
    "use_trailing_stop": False,
    "trailing_activation_atr": 1.5,
    "trailing_distance_atr": 1.0,
    "max_daily_loss": 0.05,
    "time_exit_bars": 36,         # 36 × 5분 = 3시간
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
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
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
    if lw >= body * 2 and uw < body * 0.5 and ib: bull += 0.5
    if uw >= body * 2 and lw < body * 0.5 and not ib: bear += 0.5
    if ib and c[i-1] < o[i-1] and body > pb * 1.2 and o[i] <= c[i-1] and c[i] >= o[i-1]: bull += 0.7
    if not ib and c[i-1] > o[i-1] and body > pb * 1.2 and o[i] >= c[i-1] and c[i] <= o[i-1]: bear += 0.7
    if body / rng > 0.7:
        if ib: bull += 0.4
        else: bear += 0.4
    return bull, bear


# ---------------------------------------------------------------------------
# Timeframe helpers — v15.0: 5m → 1H (was 15m → 4H)
# ---------------------------------------------------------------------------

def _resample_to_htf(df_ltf, htf_freq="1h"):
    """LTF → HTF 리샘플링 (v15.0: 5m→1H)"""
    df = df_ltf.copy().set_index("timestamp")
    return df.resample(htf_freq).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open"]).reset_index()

# 하위 호환성
def _resample_to_4h(df_15m):
    return _resample_to_htf(df_15m, "1h")  # v15.0: 1H로 변경

def _map_htf_to_ltf(df_ltf, df_htf):
    ts_ltf = df_ltf["timestamp"].values.astype("datetime64[ns]")
    ts_htf = df_htf["timestamp"].values.astype("datetime64[ns]")
    m = np.full(len(df_ltf), -1, dtype=np.int64)
    j = 0
    for i in range(len(df_ltf)):
        while j + 1 < len(ts_htf) and ts_htf[j + 1] <= ts_ltf[i]:
            j += 1
        if ts_htf[j] <= ts_ltf[i]:
            m[i] = j
    return m

# 하위 호환성
_map_4h_to_15m = _map_htf_to_ltf


# ---------------------------------------------------------------------------
# Signal generation — v15.0 Momentum Scalper
# ---------------------------------------------------------------------------

def generate_signals(df: pd.DataFrame, params: Optional[dict] = None) -> pd.DataFrame:
    p = params or OPTIMAL_PARAMS
    df = df.copy()
    n = len(df)

    from config.indicator_params import PROFIT_OPTIMIZATION
    vol_tp = PROFIT_OPTIMIZATION["vol_tp_scale"]
    vol_sl = PROFIT_OPTIMIZATION["vol_sl_scale"]
    adx_bonus_cfg = PROFIT_OPTIMIZATION["adx_tp_bonus"]
    atr_avg_period = PROFIT_OPTIMIZATION["atr_avg_period"]

    htf_freq = p.get("htf_resample", "1h")

    # ── HTF indicators (1H) ──
    df_htf = _resample_to_htf(df, htf_freq)
    htf_map = _map_htf_to_ltf(df, df_htf)
    hc = df_htf["close"]
    h_ef = calc_ema(hc, p["htf_ema_fast"]).values
    h_em = calc_ema(hc, p["htf_ema_mid"]).values
    h_adx, h_pdi, h_mdi = calc_adx_dmi(df_htf, p["htf_adx_period"])
    h_adx, h_pdi, h_mdi = h_adx.values, h_pdi.values, h_mdi.values

    # ── LTF indicators (5m) ──
    close = df["close"]
    l_rsi = calc_rsi(close, p["ltf_rsi_period"]).values
    l_bbu, l_bbm, l_bbl = calc_bollinger_bands(close, p["ltf_bb_period"], p["ltf_bb_std"])
    l_bbu, l_bbm, l_bbl = l_bbu.values, l_bbm.values, l_bbl.values
    l_atr = calc_atr(df, 14).values
    l_em = calc_ema(close, p["ltf_ema_mid"]).values
    l_atr_ma = pd.Series(l_atr).rolling(atr_avg_period).mean().values
    vol = df["volume"].values
    vol_ma = df["volume"].rolling(p.get("volume_ma_period", 20)).mean().values
    _, _, l_macd_hist = calc_macd(close, 12, 26, 9)
    l_macd_hist = l_macd_hist.values
    c = close.values
    o = df["open"].values
    h = df["high"].values
    lo = df["low"].values

    adx_min = p.get("adx_min", 20)
    cooldown = p.get("cooldown", 6)
    min_score = p.get("min_score", 2.0)
    base_tp_mult = p.get("tp_atr_mult", 2.0)
    base_sl_mult = p.get("sl_atr_mult", 1.0)
    lev = p.get("leverage", 10)
    max_lev = p.get("max_leverage", 15)
    lev_threshold = p.get("leverage_score_threshold", 3.0)
    lev_boost = p.get("leverage_boost_per_point", 3.0)
    rsi_long_lo = p.get("rsi_pull_long_lo", 25)
    rsi_long_hi = p.get("rsi_pull_long_hi", 45)
    rsi_short_lo = p.get("rsi_pull_short_lo", 55)
    rsi_short_hi = p.get("rsi_pull_short_hi", 75)
    bb_margin = p.get("bb_atr_margin", 0.5)
    min_tp_pct = p.get("min_tp_pct", 0.015)
    min_sl_pct = p.get("min_sl_pct", 0.010)

    sig_dir = np.zeros(n)
    sig_str = np.zeros(n)
    sig_lev = np.zeros(n)
    sig_tp = np.zeros(n)
    sig_sl = np.zeros(n)
    sig_mode = np.zeros(n)
    last_entry = -cooldown

    for i in range(60, n):
        if np.isnan(l_atr[i]) or l_atr[i] <= 0:
            continue
        if np.isnan(l_rsi[i]) or np.isnan(l_bbu[i]):
            continue

        h_idx = htf_map[i]
        if h_idx < 2 or h_idx >= len(df_htf):
            continue
        if np.isnan(h_adx[h_idx]) or np.isnan(h_pdi[h_idx]):
            continue

        cur_adx = h_adx[h_idx]
        if cur_adx >= 30:
            eff_cooldown = max(3, cooldown - 3)
        elif cur_adx >= 25:
            eff_cooldown = max(4, cooldown - 2)
        else:
            eff_cooldown = cooldown

        if i - last_entry < eff_cooldown:
            continue

        if cur_adx < adx_min:
            continue

        # v17.2: ADX 2봉 연속 조건 제거 (v17.1에서 거래 0건 원인)
        # 현재 봉이 adx_min 이상이면 진입 허용, ADX 상승세 여부는 스코어로 반영

        # 1H 추세: DI + EMA 필수
        bullish = (h_pdi[h_idx] > h_mdi[h_idx] and h_ef[h_idx] > h_em[h_idx])
        bearish = (h_mdi[h_idx] > h_pdi[h_idx] and h_ef[h_idx] < h_em[h_idx])
        if not bullish and not bearish:
            continue

        direction = 0
        score = 0.0

        # ── 5m PULLBACK ENTRY ──
        if bullish and rsi_long_lo <= l_rsi[i] <= rsi_long_hi:
            score += 1.5

            # v17.2: BB 하단 근접 — 필수 → 선택 (스코어 +0.5)
            if c[i] <= l_bbl[i] + l_atr[i] * bb_margin:
                score += 0.5

            if c[i] <= l_em[i] + l_atr[i] * 0.3:
                score += 0.8

            # MACD 개선
            if (i >= 2 and not np.isnan(l_macd_hist[i]) and not np.isnan(l_macd_hist[i-1])):
                if not np.isnan(l_macd_hist[i-2]) and l_macd_hist[i] > l_macd_hist[i-1] > l_macd_hist[i-2]:
                    score += 0.7
                elif l_macd_hist[i] > l_macd_hist[i-1]:
                    score += 0.4

            if i > 0 and not np.isnan(l_rsi[i-1]) and l_rsi[i] > l_rsi[i-1]:
                score += 0.5

            bull_pat, _ = detect_candle_patterns(o, h, lo, c, i)
            if bull_pat >= 0.5:
                score += min(bull_pat, 0.7)

            if cur_adx >= 35: score += 0.3
            direction = 1

        elif bearish and rsi_short_lo <= l_rsi[i] <= rsi_short_hi:
            score += 1.5

            # v17.2: BB 상단 근접 — 필수 → 선택 (스코어 +0.5)
            if c[i] >= l_bbu[i] - l_atr[i] * bb_margin:
                score += 0.5

            if c[i] >= l_em[i] - l_atr[i] * 0.3:
                score += 0.8

            if (i >= 2 and not np.isnan(l_macd_hist[i]) and not np.isnan(l_macd_hist[i-1])):
                if not np.isnan(l_macd_hist[i-2]) and l_macd_hist[i] < l_macd_hist[i-1] < l_macd_hist[i-2]:
                    score += 0.7
                elif l_macd_hist[i] < l_macd_hist[i-1]:
                    score += 0.4

            if i > 0 and not np.isnan(l_rsi[i-1]) and l_rsi[i] < l_rsi[i-1]:
                score += 0.5

            _, bear_pat = detect_candle_patterns(o, h, lo, c, i)
            if bear_pat >= 0.5:
                score += min(bear_pat, 0.7)

            if cur_adx >= 35: score += 0.3
            direction = -1

        # 볼륨 스코어
        if not np.isnan(vol_ma[i]) and vol_ma[i] > 0:
            vr = vol[i] / vol_ma[i]
            if vr >= 3.0: score += 0.8
            elif vr >= 2.0: score += 0.5
            elif vr >= 1.5: score += 0.3

        if direction == 0 or score < min_score:
            continue

        strength = min(score / 4.5, 1.0)

        # TP/SL (변동성 적응 + 최소 거리 보장)
        if not np.isnan(l_atr_ma[i]) and l_atr_ma[i] > 0:
            atr_ratio = l_atr[i] / l_atr_ma[i]
            if atr_ratio > 2.0: tp_mult, sl_mult = vol_tp["extreme"], vol_sl["extreme"]
            elif atr_ratio > 1.3: tp_mult, sl_mult = vol_tp["high"], vol_sl["high"]
            elif atr_ratio < 0.7: tp_mult, sl_mult = vol_tp["low"], vol_sl["low"]
            else: tp_mult, sl_mult = vol_tp["normal"], vol_sl["normal"]
        else:
            tp_mult, sl_mult = base_tp_mult, base_sl_mult

        if cur_adx >= adx_bonus_cfg["threshold_2"]: tp_mult += adx_bonus_cfg["bonus_2"]
        elif cur_adx >= adx_bonus_cfg["threshold_1"]: tp_mult += adx_bonus_cfg["bonus_1"]

        # 최소 TP/SL 거리 보장 (ATR이 작을 때)
        tp_dist = l_atr[i] * tp_mult
        sl_dist = l_atr[i] * sl_mult
        if c[i] > 0:
            if tp_dist / c[i] < min_tp_pct:
                tp_mult = min_tp_pct * c[i] / l_atr[i]
            if sl_dist / c[i] < min_sl_pct:
                sl_mult = min_sl_pct * c[i] / l_atr[i]

        # 동적 레버리지
        if score < 2.0:
            dynamic_lev = max(5, lev - 5)
        elif score < 2.5:
            dynamic_lev = lev
        else:
            se = score - lev_threshold
            if se > 0:
                dynamic_lev = min(lev + int(se * lev_boost), max_lev)
            else:
                dynamic_lev = lev

        sig_dir[i] = direction
        sig_str[i] = max(strength, 0.3)
        sig_lev[i] = dynamic_lev
        sig_tp[i] = tp_mult
        sig_sl[i] = sl_mult
        sig_mode[i] = 1
        last_entry = i

    df["sig_direction"] = sig_dir
    df["sig_strength"] = sig_str
    df["sig_leverage"] = sig_lev
    df["sig_tp_mult"] = sig_tp
    df["sig_sl_mult"] = sig_sl
    df["sig_mode"] = sig_mode
    return df


def get_current_signal(df):
    sig_df = generate_signals(df)
    last = sig_df.iloc[-1]
    mode_map = {0: "none", 1: "pullback"}
    mode_val = int(last.get("sig_mode", 0))

    rsi_val, vol_ratio = 0.0, 0.0
    try:
        rsi_s = calc_rsi(df["close"], OPTIMAL_PARAMS.get("ltf_rsi_period", 14))
        if len(rsi_s) > 0 and not np.isnan(rsi_s.iloc[-1]): rsi_val = float(rsi_s.iloc[-1])
        vm = df["volume"].rolling(OPTIMAL_PARAMS.get("volume_ma_period", 20)).mean()
        if len(vm) > 0 and not np.isnan(vm.iloc[-1]) and vm.iloc[-1] > 0:
            vol_ratio = float(df["volume"].iloc[-1] / vm.iloc[-1])
    except: pass

    atr_val = float(calc_atr(df).iloc[-1])
    atr_ma = calc_atr(df).rolling(50).mean()
    atr_ma_val = float(atr_ma.iloc[-1]) if not pd.isna(atr_ma.iloc[-1]) else atr_val
    vol_regime = "normal"
    if atr_ma_val > 0:
        r = atr_val / atr_ma_val
        if r > 2.0: vol_regime = "extreme"
        elif r > 1.3: vol_regime = "high"
        elif r < 0.7: vol_regime = "low"

    return {
        "direction": int(last["sig_direction"]),
        "strength": float(last["sig_strength"]),
        "leverage": int(last["sig_leverage"]) if last["sig_leverage"] > 0
                    else OPTIMAL_PARAMS.get("leverage", 10),
        "tp_atr_mult": float(last["sig_tp_mult"]),
        "sl_atr_mult": float(last["sig_sl_mult"]),
        "atr": atr_val, "close": float(last["close"]),
        "mode": mode_map.get(mode_val, "none"),
        "rsi": rsi_val, "vol_ratio": vol_ratio, "vol_regime": vol_regime,
    }
