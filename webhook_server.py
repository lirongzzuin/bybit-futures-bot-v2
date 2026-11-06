#!/usr/bin/env python3
"""
TradingView Webhook 서버
- TradingView Alert → 자체 시그널과 교차 검증 → 진입
- 보안: 시크릿 토큰 검증
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, request, jsonify
from datetime import datetime, timezone

from config.settings import WEBHOOK_SECRET, WEBHOOK_PORT
from config.indicator_params import SYMBOLS
from exchange.bybit_client import BybitClient
from exchange.order_manager import OrderManager
from strategy.signal_generator import compute_indicators, generate_signal, SELECTED_LEVERAGE, SELECTED_TP_SL_RATIO
from strategy.risk_manager import RiskManager
from strategy.position_manager import calc_atr
from utils.logger import setup_logger
from utils.notifier import notify

logger = setup_logger("webhook")

app = Flask(__name__)

# 전역 클라이언트 (lazy init)
_client = None
_risk = None
_order_mgr = None


def get_managers():
    global _client, _risk, _order_mgr
    if _client is None:
        _client = BybitClient()
        balance = _client.get_balance()
        _risk = RiskManager(initial_balance=balance["total"])
        _order_mgr = OrderManager(_client, _risk)
    return _client, _risk, _order_mgr


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView Alert Webhook 수신

    Expected JSON:
    {
        "action": "buy" | "sell",
        "symbol": "BTCUSDT",
        "signal_grade": "gold" | "silver" | "bronze",
        "price": 70000,
        "timestamp": "2025-03-12T00:00:00Z",
        "secret": "your_webhook_secret"
    }
    """
    # 보안: 시크릿 토큰 검증
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid json"}), 400

    if not WEBHOOK_SECRET or data.get("secret") != WEBHOOK_SECRET:
        logger.warning(f"웹훅 인증 실패 — IP: {request.remote_addr}")
        return jsonify({"error": "unauthorized"}), 401

    action = data.get("action", "").lower()
    symbol = data.get("symbol", "").upper()
    grade = data.get("signal_grade", "")
    price = float(data.get("price", 0))

    if action not in ("buy", "sell") or not symbol:
        return jsonify({"error": "invalid action or symbol"}), 400

    logger.info(f"웹훅 수신: {action} {symbol} @ {price} (등급: {grade})")

    # ── 자체 시그널과 교차 검증 ──
    client, risk, order_mgr = get_managers()

    try:
        # 최신 데이터로 자체 시그널 계산
        klines = client.get_klines(symbol, interval="240", limit=200)
        if not klines:
            return jsonify({"error": "data fetch failed"}), 500

        import pandas as pd
        rows = []
        for k in klines:
            rows.append({
                "timestamp": pd.to_datetime(int(k[0]), unit="ms"),
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]),
                "volume": float(k[5]),
            })
        df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        df = compute_indicators(df)
        signals = generate_signal(df)
        self_signal = signals.iloc[-1]

        # 웹훅 시그널
        webhook_signal = 1 if action == "buy" else -1

        # 교차 검증: 두 시그널이 일치할 때만 진입
        if self_signal == webhook_signal:
            side = "long" if webhook_signal == 1 else "short"
            atr = calc_atr(df).iloc[-1]

            if pd.isna(atr) or atr <= 0:
                return jsonify({"error": "invalid ATR"}), 500

            result = order_mgr.execute_entry(
                symbol=symbol,
                side=side,
                entry_price=price or df.iloc[-1]["close"],
                atr=atr,
                tp_sl_ratio=SELECTED_TP_SL_RATIO,
                leverage=SELECTED_LEVERAGE,
                signal_grade=grade,
                strategy=f"webhook_{grade}",
            )

            if result:
                notify("웹훅 진입", f"교차 검증 통과: {symbol} {side.upper()}", level="trade")
                return jsonify({"status": "executed", "order": result}), 200
            else:
                return jsonify({"status": "rejected", "reason": "order failed"}), 200

        else:
            logger.info(f"교차 검증 불일치 — 웹훅: {webhook_signal}, 자체: {self_signal}")
            return jsonify({
                "status": "skipped",
                "reason": "cross-validation mismatch",
                "webhook_signal": webhook_signal,
                "self_signal": int(self_signal),
            }), 200

    except Exception as e:
        logger.error(f"웹훅 처리 에러: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """헬스 체크"""
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


@app.route("/status", methods=["GET"])
def status():
    """봇 상태 조회"""
    try:
        client, risk, _ = get_managers()
        balance = client.get_balance()
        positions = client.get_positions()
        return jsonify({
            "balance": balance,
            "open_positions": len(positions),
            "positions": positions,
            "daily_pnl": risk.state.daily_pnl,
            "consecutive_losses": risk.state.consecutive_losses,
            "safe_mode": risk.state.daily_trade_halted,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    logger.info(f"웹훅 서버 시작 — 포트 {WEBHOOK_PORT}")
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)
