"""
Indicator & Trading Parameters v10.2
- Pullback-in-Trend v10.2 strategy params
- 실전 데이터 기반 생존 최적화: SL 1.5x ATR, 레버리지 5x, 리스크 1.0%
- 7연패/MDD 50% 문제 해결, 진입 필터 대폭 강화
"""

# ============================================================================
# ARCHIVED INDICATOR PARAMETERS
# ============================================================================
# NOTE: The following indicator parameters are ARCHIVED and only kept for
# backward compatibility with legacy code. These indicators are no longer
# actively used in the current strategy. Do not create new dependencies on
# these parameters. They have been moved to archive/indicators/ and should
# only be referenced if maintaining legacy integrations.
# ============================================================================

# === Candle Master Plan (legacy, kept for compute_indicators compatibility) ===
CANDLE_MASTER_PLAN = {
    "zigzag_length": 9,
    "fib_factor": 0.33,
    "timeframe": "4h",
    "ma_type": "SMA",
    "ma1_length": 5,
    "ma2_length": 20,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "stoch1_k_length": 20,
    "stoch1_k_smooth": 12,
    "stoch1_d_smooth": 12,
    "stoch2_k_length": 10,
    "stoch2_k_smooth": 6,
    "stoch2_d_smooth": 6,
    "rsi_source": "close",
    "rsi_length": 14,
    "rsi_overbought_level": 70,
    "rsi_oversold_level": 30,
    "swing_high_low_length": 10,
    "history_to_keep": 20,
    "supply_demand_box_width": 2.5,
}

# === Ichimoku Cloud (legacy, kept for compute_indicators compatibility) ===
ICHIMOKU = {
    "conversion_line_period": 9,
    "base_line_period": 26,
    "leading_span_b_period": 52,
    "lagging_span_period": 26,
}

# === Volume (legacy, kept for compute_indicators compatibility) ===
VOLUME = {
    "ma_length": 20,
}

# === Trading Config (v10.2 — 생존 최적화 + 집중 전략) ===
TRADING = {
    "exchange": "bybit",
    "default_leverage": 5,             # v10.2: 5x (was 7x) — 연패 생존
    "max_leverage": 7,                 # v10.2: 7x 제한 (was 10x)
    "default_timeframe": "15",         # 15-minute candles (4H via resampling)
    "risk_per_trade": 0.01,            # v10.2: 1.0% (was 1.5%) — 생존 최우선
    "max_open_positions": 1,           # v10.2: 1개 집중 (was 2) — 상관 손실 방지
    "max_daily_loss": 0.05,            # v10.2: 5% (was 6%) — 보수적 한도
}

# === 일봉 전환 보호 (Daily Candle Transition Protection) ===
# 매일 0:00 UTC (KST 09:00) 일봉 시작 전후로 변동성 급등 → 포지션 보호
DAILY_CANDLE_PROTECTION = {
    "enabled": True,
    # 보호 시간대 (UTC 기준): 23:45 ~ 00:15 = KST 08:45 ~ 09:15
    "pre_candle_minutes": 15,          # 일봉 전환 15분 전부터
    "post_candle_minutes": 15,         # 일봉 전환 15분 후까지
    # 보호 시간대 행동
    "block_new_entries": True,         # 신규 진입 차단
    "tighten_sl_ratio": 0.7,          # 보유 포지션 SL을 수익의 70%로 타이트닝
    "reduce_leverage_factor": 0.5,     # 보호 구간 직전 진입 시 레버리지 50% 감소
    # 수익 보호: 일봉 전환 전에 수익 중인 포지션은 SL을 최소 본전 이상으로
    "protect_profitable_positions": True,
}

# === Fee Config (Bybit USDT perpetual) ===
FEES = {
    "maker": 0.0002,                  # Limit: 0.02%
    "taker": 0.00055,                 # Market: 0.055%
}

# === Order Type Config ===
ORDER_TYPE_CONFIG = {
    "strong_signal_threshold": 0.9,   # Almost always limit orders (lower fees)
    "limit_offset_atr_mult": 0.1,
    "limit_timeout_seconds": 300,
}

# === Profit Optimization v10.2 ===
# v10.2: 생존 최적화 — 넓은 SL + 적정 TP로 노이즈 스톱아웃 제거
PROFIT_OPTIMIZATION = {
    # Volatility-Adaptive TP/SL (v10.2: SL 대폭 확대)
    "vol_tp_scale": {
        "extreme": 3.0,    # ATR ratio > 2.0: 극단 변동시 넓은 목표
        "high": 2.7,       # ATR ratio > 1.3: was 2.2 → 2.7
        "normal": 2.5,     # was 2.0 → 2.5 (기본 TP 상향)
        "low": 2.0,        # ATR ratio < 0.7: was 1.8 → 2.0
    },
    "vol_sl_scale": {
        "extreme": 2.0,    # was 1.5 → 2.0 (고변동시 넓은 SL 필수)
        "high": 1.7,       # was 1.3 → 1.7
        "normal": 1.5,     # was 1.2 → 1.5 (핵심: 스톱아웃 추가 감소)
        "low": 1.2,        # was 1.0 → 1.2
    },
    # ADX Strength Bonus: 보수적 (과도한 TP 확대 방지)
    "adx_tp_bonus": {
        "threshold_1": 45,  # was 40 → 45 (매우 강한 추세에서만)
        "bonus_1": 0.2,     # 유지
        "threshold_2": 55,  # was 50 → 55
        "bonus_2": 0.3,     # 유지
    },
    # Volume Confirmation: 강화 (확실한 거래량 확인)
    "volume_spike_threshold": 2.5,  # was 2.0 → 2.5 (평균 대비 2.5배 이상)
    "volume_spike_score": 0.3,      # 유지
    # ATR 이동평균 기간 (변동성 레짐 판단용)
    "atr_avg_period": 50,
}

# === Backtest Symbols ===
SYMBOLS = {
    "primary": "BTCUSDT",
    "alts": [
        "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
        "DOGEUSDT", "TRXUSDT", "HBARUSDT",
    ],
}
