"""
Indicator & Trading Parameters v13.0 — Aggressive Frequency & Profit
- 거래 빈도 극대화 + 수익률 최적화
- 중간 추세 + 넓은 풀백 + 동적 레버리지 8~20x
"""

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

# === Trading Config v13.0 ===
TRADING = {
    "exchange": "bybit",
    "default_leverage": 10,
    "max_leverage": 20,             # 동적 레버리지 최대
    "default_timeframe": "15",
    "risk_per_trade": 0.02,          # v13.0: 2% (빈도↑ 리스크↓, was 3%)
    "max_open_positions": 5,         # v13.0: 5개 (was 3) — 다중 심볼 활용
    "max_daily_loss": 0.15,          # v13.0: 15% (was 10%) — 기회 확대
}

DAILY_CANDLE_PROTECTION = {
    "enabled": True,
    "pre_candle_minutes": 15,
    "post_candle_minutes": 15,
    "block_new_entries": True,
    "tighten_sl_ratio": 0.7,
    "reduce_leverage_factor": 0.5,
    "protect_profitable_positions": True,
}

FEES = {"maker": 0.0002, "taker": 0.00055}

ORDER_TYPE_CONFIG = {
    "strong_signal_threshold": 0.70,  # v13.0: 더 많은 시장가 (빠른 체결, was 0.85)
    "limit_offset_atr_mult": 0.1,
    "limit_timeout_seconds": 120,     # v13.0: 2분 (was 3분)
}

# === Profit Optimization v13.0 ===
PROFIT_OPTIMIZATION = {
    "vol_tp_scale": {
        "extreme": 3.0,    # v13.0: 극한 변동성에서 더 큰 TP (was 2.5)
        "high": 2.7,        # was 2.2
        "normal": 2.5,      # was 2.0
        "low": 2.0,         # was 1.8
    },
    "vol_sl_scale": {
        "extreme": 1.5,     # v13.0: 넓은 SL로 노이즈 방지 (was 1.2)
        "high": 1.3,         # was 1.0
        "normal": 1.2,      # was 0.8 — 핵심 변경!
        "low": 1.0,          # was 0.7
    },
    "adx_tp_bonus": {
        "threshold_1": 30,   # was 35 — 더 자주 보너스 적용
        "bonus_1": 0.3,
        "threshold_2": 40,   # was 45
        "bonus_2": 0.5,
    },
    "volume_spike_threshold": 1.3,  # was 1.5 — 약한 볼륨 스파이크도 포착
    "volume_spike_score": 0.3,
    "atr_avg_period": 50,
}

SYMBOLS = {
    "primary": "BTCUSDT",
    "alts": ["ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
             "DOGEUSDT", "TRXUSDT", "HBARUSDT",
             "AVAXUSDT", "LINKUSDT", "BNBUSDT",
             "MATICUSDT", "DOTUSDT"],  # v13.0: 12개 알트 (was 7)
}
