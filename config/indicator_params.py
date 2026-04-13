"""
Indicator & Trading Parameters v17.2 — 진입 조건 정상화 + 실시간성 강화
- v17.1 배포 후 2.5시간 거래 0건 → ADX/RSI/BB AND 조건 과도 제한 확인
- v17.2 핵심 변경:
  * adx_min 28→25, ADX 2봉 연속 조건 제거
  * RSI Long 25-35→28-42, Short 65-75→58-72
  * BB 근접: 필수 → 선택 (스코어 +0.5)
  * min_score 2.7→2.3
  * 분석 주기 5분→90초, 포지션 모니터 2초→1초 (fly.io shared-cpu 한계 내)
  * v17.1의 Stage 1 본전 SL 저주 해소 로직은 유지
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

# === Trading Config v17.1 — 본전 SL 저주 해소 ===
TRADING = {
    "exchange": "bybit",
    "default_leverage": 5,           # v17.0 유지: 5x
    "max_leverage": 8,               # v17.0 유지: 8x
    "default_timeframe": "5",
    "risk_per_trade": 0.015,         # v17.0 유지: 1.5%
    "max_open_positions": 3,         # v17.1: 2→3 (심볼 분산, 기회 확대)
    "max_daily_loss": 0.05,          # v17.0 유지: 5%
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

# === Profit Optimization v17.1 — SL 추가 확대, 노이즈 스탑아웃 완전 차단 ===
PROFIT_OPTIMIZATION = {
    "vol_tp_scale": {
        "extreme": 3.0,
        "high": 2.7,
        "normal": 2.5,
        "low": 2.0,
    },
    "vol_sl_scale": {
        "extreme": 2.0,      # v17.1: 1.8→2.0
        "high": 1.8,         # v17.1: 1.6→1.8
        "normal": 1.7,       # v17.1: 1.5→1.7 (노이즈 여유 확대)
        "low": 1.4,          # v17.1: 1.2→1.4
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
