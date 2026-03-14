"""
Indicator & Trading Parameters v3.0
- ATM (Adaptive Trend-Momentum) strategy params
- Conservative risk management
"""

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

# === Trading Config (v4.0 — 15min scalping with 4H cross-validation) ===
TRADING = {
    "exchange": "bybit",
    "default_leverage": 15,
    "max_leverage": 25,
    "default_timeframe": "15",    # 15-minute candles (4H via resampling)
    "risk_per_trade": 0.001,      # 0.1% x 10x avg = ~1% actual risk
    "max_open_positions": 5,      # More positions for scalping
    "max_daily_loss": 0.08,       # 8% daily max loss
}

# === Fee Config (Bybit USDT perpetual) ===
FEES = {
    "maker": 0.0002,              # Limit: 0.02%
    "taker": 0.00055,             # Market: 0.055%
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
