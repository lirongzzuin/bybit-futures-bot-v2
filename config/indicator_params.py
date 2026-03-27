"""
Indicator & Trading Parameters v7.4
- Compound Growth Machine v7.4 strategy params
- Aggressive risk management + MFE 누수 방지 개선
- 실전 운용 모드 (v7.4)
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

# === Trading Config (v7.4 — 실전 운용 + MFE 누수 방지) ===
TRADING = {
    "exchange": "bybit",
    "default_leverage": 15,
    "max_leverage": 20,
    "default_timeframe": "15",        # 15-minute candles (4H via resampling)
    "risk_per_trade": 0.02,           # v8.0: 2% — 소액계좌 리스크 축소
    "max_open_positions": 2,          # v8.0: 2개 동시 — 집중 투자 + 수수료 절감
    "max_daily_loss": 0.08,           # v8.0: 8% — 일일 손실 한도 강화
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

# === Backtest Symbols ===
SYMBOLS = {
    "primary": "BTCUSDT",
    "alts": [
        "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
        "DOGEUSDT", "TRXUSDT", "HBARUSDT",
    ],
}
