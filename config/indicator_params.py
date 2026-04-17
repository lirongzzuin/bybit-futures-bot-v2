"""
Indicator & Trading Parameters v17.5 — 진입 품질 강화 + 수익 확정 개선
- v17.3 40건 (57.5%/R:R 0.46, -3.15 USDT) → v17.4 저가알트·수익잠금 대응 후속
- v17.5 핵심 변경:
  * min_score 2.3 → 2.5 (저품질 진입 필터링)
  * volume_min_ratio 0.8 신설 (평균 미만 유동성 진입 차단)
  * high 변동성 + score<2.8 진입 금지 (extreme 외 영역까지 확장)
  * Stage 1 수익 잠금 트리거 2.5%→1.8%, 잠금 폭 0.5%→0.3% (후퇴 대응 강화)
  * Stage 2 트레일링 활성화 3.5%→2.8%, 첫 단계 보호 25%→30%
  * 1차 부분 익절 TP진행 60%→50%, 청산 비율 30%→40%
  * v17.2 진입 조건 골격·v17.4 저가알트 방어 그대로 유지
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

# === Profit Optimization v17.5 — 진입 품질 강화 + 수익 확정 개선 ===
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
        "threshold_1": 30,
        "bonus_1": 0.3,
        "threshold_2": 40,
        "bonus_2": 0.5,
    },
    "volume_spike_threshold": 1.3,
    "volume_spike_score": 0.3,
    "atr_avg_period": 50,
    # v17.5: 진입 품질 필터 ─────────────────────────────────
    # 현재봉 볼륨이 20봉 MA × 0.8 미만이면 진입 스킵 (유동성 부족)
    "volume_min_ratio": 0.8,
    # high 변동성(ATR ratio 1.3~2.0) 구간에서 요구 스코어
    "high_vol_min_score": 2.8,
    # v17.5: 청산 단계 파라미터 ─────────────────────────────
    "stage1_trigger_pct": 0.018,     # v17.4: 0.025
    "stage1_lock_pct": 0.003,        # v17.4: 0.005
    "stage2_trigger_pct": 0.028,     # v17.4: 0.035
    "stage2_first_protect": 0.30,    # v17.4: 0.25
    "partial_1_progress": 0.50,      # v17.4: 0.60
    "partial_1_ratio": 0.40,         # v17.4: 0.30
}

SYMBOLS = {
    "primary": "BTCUSDT",
    "alts": ["ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
             "DOGEUSDT", "TRXUSDT", "HBARUSDT",
             "AVAXUSDT", "LINKUSDT", "BNBUSDT",
             "MATICUSDT", "DOTUSDT"],  # v13.0: 12개 알트 (was 7)
}
