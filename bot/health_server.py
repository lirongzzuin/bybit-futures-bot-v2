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

    # ── v9.2 데이터 수집 분석 엔드포인트 ──

    @app.route("/collector/signals")
    def collector_signals():
        """시그널 저널 분석 (진입 vs 스킵 비교, 가상 성과)"""
        days = int(request.args.get("days", 7))
        return jsonify(bot_instance.collector.get_signal_journal_analysis(days))

    @app.route("/collector/regimes")
    def collector_regimes():
        """변동성/트렌드 레짐 전환 이력"""
        days = int(request.args.get("days", 30))
        return jsonify(bot_instance.collector.get_regime_analysis(days))

    @app.route("/collector/drawdowns")
    def collector_drawdowns():
        """드로우다운 이벤트 분석"""
        days = int(request.args.get("days", 30))
        return jsonify(bot_instance.collector.get_drawdown_analysis(days))

    @app.route("/collector/correlations")
    def collector_correlations():
        """심볼 간 상관관계 분석"""
        return jsonify(bot_instance.collector.get_correlation_analysis())

    @app.route("/collector/benchmark")
    def collector_benchmark():
        """일별 성과 벤치마크 (전략 vs BTC vs ETH)"""
        days = int(request.args.get("days", 30))
        return jsonify(bot_instance.collector.get_benchmark_analysis(days))

    @app.route("/collector/candles")
    def collector_candles():
        """캔들 아카이브 통계"""
        return jsonify(bot_instance.collector.get_candle_archive_stats())

    @app.route("/collector/summary")
    def collector_summary():
        """데이터 수집 종합 현황"""
        return jsonify({
            "signals": bot_instance.collector.get_signal_journal_analysis(7),
            "regimes": bot_instance.collector.get_regime_analysis(7),
            "candles": bot_instance.collector.get_candle_archive_stats(),
            "benchmark": bot_instance.collector.get_benchmark_analysis(30),
            "correlations": bot_instance.collector.get_correlation_analysis(),
            "drawdowns": bot_instance.collector.get_drawdown_analysis(30),
        })

    port = int(os.getenv("WEBHOOK_PORT", "5000"))
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True,
    )
    t.start()
    logger.info(f"헬스체크 서버 시작 (포트 {port})")
