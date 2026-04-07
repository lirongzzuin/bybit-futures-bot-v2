"""
Pullback-in-Trend Strategy v13.0 — Aggressive Frequency & Profit

목표: 거래 빈도 극대화 + 수익률 최적화

v13.0 핵심 변경 (v12.0 대비):
  1. ADX >= 20 (was 30): 중간 강도 추세도 포착
  2. RSI 25-50/50-75 (was 20-35/65-80): 넓은 풀백 구간
  3. MACD 1봉 개선도 허용 (was 2봉 연속만)
  4. BB/EMA 근접 조건 대폭 완화
  5. min_score 1.5 (was 2.5): 더 많은 진입 기회
  6. SL 1.2x / TP 2.5x ATR = R:R 2.08:1 (노이즈 스톱아웃 방지)
  7. 쿨다운 2봉(was 4봉): 빠른 재진입
  8. 모멘텀 추가 점수: 강한 추세 지속 시 보너스
"""
import numpy as np
import pandas as pd
from typing import Optional


OPTIMAL_PARAMS = {
    "strategy_type": "pullback_in_trend_v14.0",
    # 4H indicators
    "htf_ema_fast": 9,
    "htf_ema_mid": 21,
    "htf_rsi_period": 14,
    "htf_adx_period": 14,
    # v14.0 — 실전 데이터 기반 최적화 (v9.1 수익 파라미터 + 실전 교훈)
    # 문제: v13.0 ADX20/RSI50/score1.5 → 롱만 발생, 약한 시그널 진입, 연속 손절
    "adx_min": 25,                   # v9.1 복원 (was 20): 중간 추세 이상
    "rsi_pull_long_lo": 25,          # 풀백 하한
    "rsi_pull_long_hi": 40,          # v9.1 기반 (was 50): 실제 풀백 구간만
    "rsi_pull_short_lo": 60,         # v9.1 기반 (was 50): 실제 바운스 구간만
    "rsi_pull_short_hi": 75,         # 바운스 상한
    "tp_atr_mult": 2.0,             # v9.1 복원 (was 2.5): 달성 가능한 TP
    "sl_atr_mult": 1.0,             # 적정 SL (was 1.2): R:R 2.0:1
    "leverage": 10,                  # 기본 레버리지
    "cooldown": 4,                   # 4봉(1시간) 쿨다운 (was 2: 너무 빠른 재진입)
    "cooldown_after_sl": 2,          # SL 후 30분 (was 1: 너무 빠름)
    "min_score": 2.0,                # v9.1 복원 (was 1.5): 품질 보장
    "bb_atr_margin": 0.35,          # v9.1 복원 (was 1.0): BB 근접 필수
    # 동적 레버리지
    "max_leverage": 20,
    "leverage_score_threshold": 3.0, # 복원 (was 2.5)
    "leverage_boost_per_point": 5.0,
    # Shared
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
    "risk_per_trade": 0.02,       # 2%
    "use_trailing_stop": False,   # 고정 TP/SL (trailing은 수익 조기 청산 원인)
    "trailing_activation_atr": 1.5,
    "trailing_distance_atr": 1.0,
    "max_daily_loss": 0.10,       # 10% (was 15%: 너무 관대)
    "time_exit_bars": 48,         # 12h
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
        high - low, (high - close.shift(1)).abs(),
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
    """캔들 패턴 감지 — v12.0: 시그널 스코어링에 활용"""
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

    if lw >= body * 2 and uw < body * 0.5 and ib:
        bull += 0.5
    if uw >= body * 2 and lw < body * 0.5 and not ib:
        bear += 0.5
    if (ib and c[i - 1] < o[i - 1] and body > pb * 1.2
            and o[i] <= c[i - 1] and c[i] >= o[i - 1]):
        bull += 0.7
    if (not ib and c[i - 1] > o[i - 1] and body > pb * 1.2
            and o[i] >= c[i - 1] and c[i] <= o[i - 1]):
        bear += 0.7
    if (ib and c[i - 1] < o[i - 1] and o[i] < c[i - 1]
            and c[i] > (o[i - 1] + c[i - 1]) / 2):
        bull += 0.5
    if (not ib and c[i - 1] > o[i - 1] and o[i] > c[i - 1]
            and c[i] < (o[i - 1] + c[i - 1]) / 2):
        bear += 0.5
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
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
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
# Signal generation — v12.0
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

    # ── 4H indicators ──
    df_4h = _resample_to_4h(df)
    htf_map = _map_4h_to_15m(df, df_4h)
    hc = df_4h["close"]
    h_ef = calc_ema(hc, p["htf_ema_fast"]).values
    h_em = calc_ema(hc, p["htf_ema_mid"]).values
    h_adx, h_pdi, h_mdi = calc_adx_dmi(df_4h, p["htf_adx_period"])
    h_adx, h_pdi, h_mdi = h_adx.values, h_pdi.values, h_mdi.values

    # ── 15min indicators ──
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

    # Params
    adx_min = p.get("adx_min", 30)
    cooldown = p.get("cooldown", 4)
    cooldown_sl = p.get("cooldown_after_sl", 2)
    min_score = p.get("min_score", 2.5)
    base_tp_mult = p.get("tp_atr_mult", 2.0)
    base_sl_mult = p.get("sl_atr_mult", 0.8)
    lev = p.get("leverage", 10)
    max_lev = p.get("max_leverage", 20)
    lev_threshold = p.get("leverage_score_threshold", 3.0)
    lev_boost = p.get("leverage_boost_per_point", 5.0)
    rsi_long_lo = p.get("rsi_pull_long_lo", 20)
    rsi_long_hi = p.get("rsi_pull_long_hi", 35)
    rsi_short_lo = p.get("rsi_pull_short_lo", 65)
    rsi_short_hi = p.get("rsi_pull_short_hi", 80)
    bb_margin = p.get("bb_atr_margin", 0.3)

    # Output
    sig_dir = np.zeros(n)
    sig_str = np.zeros(n)
    sig_lev = np.zeros(n)
    sig_tp = np.zeros(n)
    sig_sl = np.zeros(n)
    sig_mode = np.zeros(n)

    last_entry = -cooldown
    last_was_sl = False  # SL 후 빠른 재진입 트래킹

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

        # v12.0: 적응형 쿨다운 (SL 후 빠른 재진입)
        cur_adx = h_adx[h_idx]
        if last_was_sl:
            eff_cooldown = cooldown_sl  # SL 후 30분
        elif cur_adx >= 40:
            eff_cooldown = max(2, cooldown - 2)  # 초강한 추세: 30분
        elif cur_adx >= 35:
            eff_cooldown = max(3, cooldown - 1)  # 강한 추세: 45분
        else:
            eff_cooldown = cooldown

        if i - last_entry < eff_cooldown:
            continue

        # ═══════════════════════════════════════════════════════════
        # 4H TREND: ADX >= 20 + DI (v13.0: EMA 교차 조건 완화)
        # ═══════════════════════════════════════════════════════════
        if cur_adx < adx_min:
            continue

        # v14.0: DI 방향 + EMA 정렬 필수 (v13.0에서 EMA를 보너스로 전환 → 롱 편향 원인)
        bullish_4h = (h_pdi[h_idx] > h_mdi[h_idx] and h_ef[h_idx] > h_em[h_idx])
        bearish_4h = (h_mdi[h_idx] > h_pdi[h_idx] and h_ef[h_idx] < h_em[h_idx])

        if not bullish_4h and not bearish_4h:
            continue

        direction = 0
        score = 0.0

        # ═══════════════════════════════════════════════════════════
        # 15m PULLBACK ENTRY (v14.0: 실전 검증된 풀백 구간)
        # ═══════════════════════════════════════════════════════════
        if bullish_4h and rsi_long_lo <= l_rsi[i] <= rsi_long_hi:
            score += 1.5

            # v14.0: BB 근접 필수 (v13.0에서 보너스 → 품질 하락 원인)
            if c[i] > l_bbl[i] + l_atr[i] * bb_margin:
                continue  # BB 하단 근접하지 않으면 스킵

            # EMA21 근접 보너스
            if c[i] <= l_em[i] + l_atr[i] * 0.3:
                score += 1.0

            # MACD 개선 (2봉 연속 우대)
            if (i >= 2 and not np.isnan(l_macd_hist[i]) and
                    not np.isnan(l_macd_hist[i-1]) and not np.isnan(l_macd_hist[i-2])):
                if l_macd_hist[i] > l_macd_hist[i-1] > l_macd_hist[i-2]:
                    score += 0.7
                elif l_macd_hist[i] > l_macd_hist[i-1]:
                    score += 0.4

            # RSI bouncing up
            if i > 0 and not np.isnan(l_rsi[i-1]):
                if l_rsi[i] > l_rsi[i-1]:
                    score += 0.5

            # 캔들 패턴 확인
            bull_pat, _ = detect_candle_patterns(o, h, lo, c, i)
            if bull_pat >= 0.5:
                score += min(bull_pat, 0.7)

            # ADX 강도 보너스
            if cur_adx >= 35:
                score += 0.3

            direction = 1

        elif bearish_4h and rsi_short_lo <= l_rsi[i] <= rsi_short_hi:
            score += 1.5

            # v14.0: BB 근접 필수
            if c[i] < l_bbu[i] - l_atr[i] * bb_margin:
                continue

            if c[i] >= l_em[i] - l_atr[i] * 0.3:
                score += 1.0

            if (i >= 2 and not np.isnan(l_macd_hist[i]) and
                    not np.isnan(l_macd_hist[i-1]) and not np.isnan(l_macd_hist[i-2])):
                if l_macd_hist[i] < l_macd_hist[i-1] < l_macd_hist[i-2]:
                    score += 0.7
                elif l_macd_hist[i] < l_macd_hist[i-1]:
                    score += 0.4

            if i > 0 and not np.isnan(l_rsi[i-1]):
                if l_rsi[i] < l_rsi[i-1]:
                    score += 0.5

            _, bear_pat = detect_candle_patterns(o, h, lo, c, i)
            if bear_pat >= 0.5:
                score += min(bear_pat, 0.7)

            if cur_adx >= 35:
                score += 0.3

            direction = -1

        # ═══════════════════════════════════════════════════════════
        # VOLUME-WEIGHTED SCORING (v12.0: 단계별)
        # ═══════════════════════════════════════════════════════════
        if not np.isnan(vol_ma[i]) and vol_ma[i] > 0:
            vol_ratio = vol[i] / vol_ma[i]
            if vol_ratio >= 3.0:
                score += 0.8
            elif vol_ratio >= 2.0:
                score += 0.5
            elif vol_ratio >= 1.5:
                score += 0.3

        if direction == 0 or score < min_score:
            continue

        strength = min(score / 4.5, 1.0)  # 4.5로 정규화 (더 넓은 범위)

        # ═══════════════════════════════════════════════════════════
        # VOLATILITY-ADAPTIVE TP/SL
        # ═══════════════════════════════════════════════════════════
        if not np.isnan(l_atr_ma[i]) and l_atr_ma[i] > 0:
            atr_ratio = l_atr[i] / l_atr_ma[i]
            if atr_ratio > 2.0:
                tp_mult, sl_mult = vol_tp["extreme"], vol_sl["extreme"]
            elif atr_ratio > 1.3:
                tp_mult, sl_mult = vol_tp["high"], vol_sl["high"]
            elif atr_ratio < 0.7:
                tp_mult, sl_mult = vol_tp["low"], vol_sl["low"]
            else:
                tp_mult, sl_mult = vol_tp["normal"], vol_sl["normal"]
        else:
            tp_mult, sl_mult = base_tp_mult, base_sl_mult

        # ADX BONUS
        if cur_adx >= adx_bonus_cfg["threshold_2"]:
            tp_mult += adx_bonus_cfg["bonus_2"]
        elif cur_adx >= adx_bonus_cfg["threshold_1"]:
            tp_mult += adx_bonus_cfg["bonus_1"]

        # ═══════════════════════════════════════════════════════════
        # v13.0: DYNAMIC LEVERAGE (8~20x)
        # ═══════════════════════════════════════════════════════════
        if score < 2.0:
            dynamic_lev = max(8, lev - 2)  # 약한 시그널: 8x (was 7)
        elif score < 2.5:
            dynamic_lev = lev  # 기본: 10x
        else:
            score_excess = score - lev_threshold
            boost = int(score_excess * lev_boost)
            dynamic_lev = min(lev + boost, max_lev)

        # 대량 거래 + 고스코어: 추가 부스트
        if (not np.isnan(vol_ma[i]) and vol_ma[i] > 0 and
                vol[i] >= vol_ma[i] * 2.5 and score >= 3.5):
            dynamic_lev = min(dynamic_lev + 2, max_lev)

        sig_dir[i] = direction
        sig_str[i] = max(strength, 0.3)
        sig_lev[i] = dynamic_lev
        sig_tp[i] = tp_mult
        sig_sl[i] = sl_mult
        sig_mode[i] = 1
        last_entry = i
        last_was_sl = False  # 진입 성공 시 리셋

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
        if ratio > 2.0: vol_regime = "extreme"
        elif ratio > 1.3: vol_regime = "high"
        elif ratio < 0.7: vol_regime = "low"

    return {
        "direction": int(last["sig_direction"]),
        "strength": float(last["sig_strength"]),
        "leverage": int(last["sig_leverage"]) if last["sig_leverage"] > 0
                    else OPTIMAL_PARAMS.get("leverage", 10),
        "tp_atr_mult": float(last["sig_tp_mult"]),
        "sl_atr_mult": float(last["sig_sl_mult"]),
        "atr": atr_val,
        "close": float(last["close"]),
        "mode": mode_map.get(mode_val, "none"),
        "rsi": rsi_val,
        "vol_ratio": vol_ratio,
        "vol_regime": vol_regime,
    }
