"""
지표 파라미터 설정
- Candle Master Plan, Ichimoku Cloud, Volume 지표의 모든 파라미터를 정의
"""

# === Candle Master Plan 파라미터 ===
CANDLE_MASTER_PLAN = {
    "zigzag_length": 9,
    "fib_factor": 0.33,           # breakout confirmation용
    "timeframe": "4h",            # Chart 타임프레임
    "ma_type": "SMA",
    "ma1_length": 5,
    "ma2_length": 20,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    # Stochastic 1 (중장기 추세)
    "stoch1_k_length": 20,
    "stoch1_k_smooth": 12,
    "stoch1_d_smooth": 12,
    # Stochastic 2 (단기 모멘텀)
    "stoch2_k_length": 10,
    "stoch2_k_smooth": 6,
    "stoch2_d_smooth": 6,
    # RSI (별도 세트)
    "rsi_source": "close",
    "rsi_length": 14,
    "rsi_overbought_level": 70,
    "rsi_oversold_level": 30,
    # Supply/Demand & Swing
    "swing_high_low_length": 10,
    "history_to_keep": 20,
    "supply_demand_box_width": 2.5,
}

# === Ichimoku Cloud 파라미터 (표준) ===
ICHIMOKU = {
    "conversion_line_period": 9,
    "base_line_period": 26,
    "leading_span_b_period": 52,
    "lagging_span_period": 26,
}

# === Volume 파라미터 ===
VOLUME = {
    "ma_length": 20,  # Volume MA 기본값
}

# === 거래 설정 ===
TRADING = {
    "exchange": "bybit",
    "default_leverage": 5,
    "max_leverage": 30,
    "default_timeframe": "4h",
    "risk_per_trade": 0.02,       # 계좌의 2%
    "max_open_positions": 5,
    "max_daily_loss": 0.06,       # 일일 최대 손실 6%
}

# === 수수료 설정 (Bybit USDT 무기한 선물) ===
FEES = {
    "maker": 0.0002,              # 지정가(Maker): 0.02%
    "taker": 0.00055,             # 시장가(Taker): 0.055%
}

# === 주문 타입 결정 기준 ===
ORDER_TYPE_CONFIG = {
    "strong_signal_threshold": 0.5,   # 강도 >= 0.5 → 시장가(즉시 진입)
    "limit_offset_atr_mult": 0.1,     # 지정가 오프셋: ATR * 0.1 (유리한 가격에 진입)
    "limit_timeout_seconds": 300,     # 지정가 미체결 시 취소까지 대기 (5분)
}

# === 백테스트 대상 심볼 ===
SYMBOLS = {
    "primary": "BTCUSDT",
    "alts": [
        "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
        "DOGEUSDT", "TRXUSDT", "HBARUSDT",
    ],
}
