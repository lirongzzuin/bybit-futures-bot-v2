"""
Indicator & Trading Parameters v11.0 — Aggressive Profit Runner
- v9.1 공격적 파라미터 복원 + 동적 레버리지 + 수익 극대화
- 수익률과 승률 극대화가 목표 (덜 잃는 봇이 아닌 돈 버는 봇)
"""

# ============================================================================
# ARCHIVED INDICATOR PARAMETERS
# ============================================================================
CANDLE_MASTER_PLAN = {
    "zigzag_length": 9, "fib_factor": 0.33, "timeframe": "4h",
    "ma_type": "SMA", "ma1_length": 5, "ma2_length": 20,
    "rsi_oversold": 30, "rsi_overbought": 70,
    "stoch1_k_length": 20, "stoch1_k_smooth": 12, "stoch1_d_smooth": 12,
    "stoch2_k_length": 10, "stoch2_k_smooth": 6, "stoch2_d_smooth": 6,
    "rsi_source": "close", "rsi_length": 14,
    "rsi_overbought_level": 70, "rsi_oversold_level": 30,
    "swing_high_low_length": 10, "history_to_keep": 20,
    "supply_demand_box_width": 2.5,
}

ICHIMOKU = {
    "conversion_line_period": 9, "base_line_period": 26,
    "leading_span_b_period": 52, "lagging_span_period": 26,
}

VOLUME = {"ma_length": 20}

# === Trading Config (v11.0 — 수익 극대화) ===
TRADING = {
    "exchange": "bybit",
    "default_leverage": 10,            # v11.0: 10x 복원 (was 5x)
    "max_leverage": 15,                # v11.0: 15x (동적 레버리지 최대)
    "default_timeframe": "15",
    "risk_per_trade": 0.025,           # v11.0: 2.5% (was 1.0%)
    "max_open_positions": 3,           # v11.0: 3개 동시 (was 1)
    "max_daily_loss": 0.08,            # v11.0: 8% (was 5%)
}

# === 일봉 전환 보호 ===
DAILY_CANDLE_PROTECTION = {
    "enabled": True,
    "pre_candle_minutes": 15,
    "post_candle_minutes": 15,
    "block_new_entries": True,
    "tighten_sl_ratio": 0.7,
    "reduce_leverage_factor": 0.5,
    "protect_profitable_positions": True,
}

# === Fee Config ===
FEES = {
    "maker": 0.0002,
    "taker": 0.00055,
}

# === Order Type Config ===
ORDER_TYPE_CONFIG = {
    "strong_signal_threshold": 0.9,
    "limit_offset_atr_mult": 0.1,
    "limit_timeout_seconds": 300,
}

# === Profit Optimization v11.0 — 수익 극대화 ===
PROFIT_OPTIMIZATION = {
    # v11.0: v9.1 공격적 TP/SL 복원 (타이트 SL, 달성 가능한 TP)
    "vol_tp_scale": {
        "extreme": 2.5,    # 고변동: 넓은 TP (추세 활용)
        "high": 2.2,       # 복원
        "normal": 2.0,     # 복원 (was 2.5)
        "low": 1.8,        # 저변동: 적정 TP
    },
    "vol_sl_scale": {
        "extreme": 1.2,    # 고변동에서도 타이트 SL
        "high": 1.0,       # 복원
        "normal": 0.8,     # 핵심 복원 (was 1.5): 타이트 SL = 높은 R:R
        "low": 0.7,        # 저변동: 더 타이트
    },
    # ADX Strength Bonus: 공격적 (강한 추세 = 큰 TP)
    "adx_tp_bonus": {
        "threshold_1": 35,  # 복원 (was 45): 빠른 TP 확대
        "bonus_1": 0.3,     # 복원 (was 0.2)
        "threshold_2": 45,  # 복원 (was 55)
        "bonus_2": 0.5,     # 복원 (was 0.3)
    },
    # Volume: 민감하게 (더 많은 시그널에 볼륨 보너스)
    "volume_spike_threshold": 1.5,  # 복원 (was 2.5)
    "volume_spike_score": 0.3,
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
