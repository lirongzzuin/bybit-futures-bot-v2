"""
Indicator & Trading Parameters v17.0 — 실전 손실 분석 기반 생존 최적화
- 승률 향상: 강한 추세 + 깊은 풀백만 진입
- 손실 축소: 레버리지/리스크 대폭 감소
- 수익 보호: 넓은 SL + 큰 TP (R:R 1.67:1 유지)
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

# === Trading Config v17.0 — 실전 손실 분석 기반 생존 최적화 ===
TRADING = {
    "exchange": "bybit",
    "default_leverage": 5,           # v17.0: 5x (7→5: 손실 증폭 방지)
    "max_leverage": 8,               # v17.0: 8x (12→8: 변동성 생존)
    "default_timeframe": "5",
    "risk_per_trade": 0.015,         # v17.0: 1.5% (3→1.5: 연패 시 계좌 보존)
    "max_open_positions": 2,         # v17.0: 2개 (3→2: 상관 손실 최소화)
    "max_daily_loss": 0.05,          # v17.0: 5% (8→5: 일일 손실 한도 축소)
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

# === Profit Optimization v17.0 — R:R 1.67:1 유지, SL 넓혀서 노이즈 방지 ===
PROFIT_OPTIMIZATION = {
    "vol_tp_scale": {
        "extreme": 3.0,      # v17.0: 극단 변동성에서 큰 수익 추구
        "high": 2.7,
        "normal": 2.5,       # v17.0: 2.0→2.5 (더 큰 TP)
        "low": 2.0,
    },
    "vol_sl_scale": {
        "extreme": 1.8,      # v17.0: 노이즈 스탑아웃 방지
        "high": 1.6,
        "normal": 1.5,       # v17.0: 1.0→1.5 (넓은 SL)
        "low": 1.2,
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
