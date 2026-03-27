"""Health check server for fly.io"""
import os
import threading
from flask import Flask, jsonify, request

from utils.logger import setup_logger

logger = setup_logger("health_server")


def start_health_server(bot_instance):
    """fly.io 헬스체크용 간단 HTTP 서버 (별도 스레드)"""
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "safe_mode": bot_instance.safe_mode})

    @app.route("/status")
    def status():
        balance = bot_instance.client.get_balance()
        positions = bot_instance.client.get_positions()
        data = {
            "balance": balance["total"],
            "positions": len(positions),
            "tracked": list(bot_instance.risk.state.open_positions.keys()),
            "strategy_symbols": bot_instance.strategy_symbols,
            "safe_mode": bot_instance.safe_mode,
            "paper_mode": bot_instance.paper_mode,
        }
        if bot_instance.paper_mode:
            data["paper_summary"] = bot_instance.client.get_paper_summary()
        return jsonify(data)

    @app.route("/paper-analysis")
    def paper_analysis():
        from data.storage import get_paper_trade_analysis
        hours = int(request.args.get("hours", 168))
        return jsonify(get_paper_trade_analysis(hours))

    @app.route("/tp-sl-adjustments")
    def tp_sl_adjustments():
        from data.storage import get_tp_sl_adjustment_analysis
        hours = int(request.args.get("hours", 168))
        return jsonify(get_tp_sl_adjustment_analysis(hours))

    @app.route("/post-exit")
    def post_exit():
        from data.storage import get_post_exit_analysis
        hours = int(request.args.get("hours", 168))
        return jsonify(get_post_exit_analysis(hours))

    @app.route("/skipped-signals")
    def skipped_signals():
        from data.storage import get_skipped_signals
        hours = int(request.args.get("hours", 168))
        signals = get_skipped_signals(hours)
        # 요약 통계
        by_reason = {}
        by_symbol = {}
        for s in signals:
            reason = s.get("skip_reason", "unknown")
            sym = s.get("symbol", "unknown")
            by_reason[reason] = by_reason.get(reason, 0) + 1
            by_symbol[sym] = by_symbol.get(sym, 0) + 1
        return jsonify({
            "total_skipped": len(signals),
            "by_reason": by_reason,
            "by_symbol": by_symbol,
            "signals": signals[:50],  # 최근 50개만
        })

    port = int(os.getenv("WEBHOOK_PORT", "5000"))
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True,
    )
    t.start()
    logger.info(f"헬스체크 서버 시작 (포트 {port})")
