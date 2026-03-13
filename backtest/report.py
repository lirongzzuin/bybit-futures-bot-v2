"""
백테스트 성과 리포트 생성 (HTML + 차트)
"""
import os
import json
from datetime import datetime

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest.engine import BacktestResult
from config.settings import REPORT_DIR
from utils.logger import setup_logger

logger = setup_logger("report")


def generate_html_report(optimization_result: dict, output_dir: str = None) -> str:
    """
    최적화 결과를 HTML 리포트로 생성

    Returns: 생성된 HTML 파일 경로
    """
    output_dir = output_dir or REPORT_DIR
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(output_dir, f"backtest_report_{timestamp}.html")

    final = optimization_result.get("final_strategy", {})
    all_results = optimization_result.get("all_results", [])
    multi = optimization_result.get("multi_symbol", {})
    wf = optimization_result.get("walkforward", {})

    # ── 차트 생성 ──
    charts_html = ""

    # 1. 에쿼티 커브 (최종 전략)
    if final and "btc_result" in final:
        btc_result: BacktestResult = final["btc_result"]
        if btc_result.equity_curve:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                y=btc_result.equity_curve,
                mode="lines",
                name="Equity",
                line=dict(color="#5048e5", width=2),
            ))
            fig.update_layout(
                title=f"에쿼티 커브 — {btc_result.strategy_name}",
                yaxis_title="Balance (USDT)",
                template="plotly_white",
                height=400,
            )
            charts_html += fig.to_html(full_html=False, include_plotlyjs=False)

        # 2. 거래별 PnL 분포
        if btc_result.trades:
            pnls = [t.pnl for t in btc_result.trades]
            fig2 = go.Figure()
            colors = ["#22c55e" if p > 0 else "#ef4444" for p in pnls]
            fig2.add_trace(go.Bar(y=pnls, marker_color=colors, name="PnL"))
            fig2.update_layout(
                title="거래별 손익 (USDT)",
                yaxis_title="PnL",
                template="plotly_white",
                height=350,
            )
            charts_html += fig2.to_html(full_html=False, include_plotlyjs=False)

    # 3. 전략 비교 테이블
    table_rows = ""
    for i, (score, name, ret, sharpe, mdd, wr, plr, trades) in enumerate(all_results[:20], 1):
        highlight = ' style="background:#e8f5e9;font-weight:bold"' if i == 1 else ""
        table_rows += f"""
        <tr{highlight}>
            <td>{i}</td><td>{name}</td>
            <td>{ret:.1f}%</td><td>{sharpe:.2f}</td><td>{mdd:.1f}%</td>
            <td>{wr:.1f}%</td><td>{plr:.2f}</td><td>{trades}</td>
            <td>{score:.3f}</td>
        </tr>"""

    # 4. 멀티 심볼 결과
    multi_rows = ""
    for name, info in multi.items():
        for sym, ret in info["symbol_returns"].items():
            multi_rows += f"<tr><td>{name}</td><td>{sym}</td><td>{ret:.1f}%</td></tr>"
        overfit_badge = '<span style="color:red">⚠ 과적합</span>' if info["overfitting"] else '<span style="color:green">✓</span>'
        multi_rows += f"<tr style='background:#f5f5f5'><td colspan=2><b>평균 / 변동계수</b></td><td>{info['mean_return']:.1f}% (CV: {info['cv']:.1f}%) {overfit_badge}</td></tr>"

    # 5. 워크포워드 결과
    wf_rows = ""
    for name, info in wf.items():
        status = "⚠" if info["overfit"] else "✓"
        wf_rows += f"<tr><td>{name}</td><td>{info['train_return']:.1f}%</td><td>{info['test_return']:.1f}%</td><td>{info['performance_ratio']:.2f}</td><td>{status}</td></tr>"

    # ── HTML 조합 ──
    final_info = final or {}
    final_name = final_info.get("name", "N/A")
    final_base = final_info.get("base_strategy", "N/A")
    final_exit = final_info.get("exit_config", {}).get("label", "N/A")
    final_lev = final_info.get("leverage", "N/A")

    btc_r = final_info.get("btc_result")
    final_stats = ""
    if btc_r:
        final_stats = f"""
        <div class="stat-grid">
            <div class="stat"><span class="label">총수익률</span><span class="value">{btc_r.total_return_pct:.1f}%</span></div>
            <div class="stat"><span class="label">샤프비율</span><span class="value">{btc_r.sharpe_ratio:.2f}</span></div>
            <div class="stat"><span class="label">MDD</span><span class="value">{btc_r.max_drawdown_pct:.1f}%</span></div>
            <div class="stat"><span class="label">승률</span><span class="value">{btc_r.win_rate:.1f}%</span></div>
            <div class="stat"><span class="label">손익비</span><span class="value">{btc_r.avg_pnl_ratio:.2f}</span></div>
            <div class="stat"><span class="label">거래수</span><span class="value">{btc_r.total_trades}</span></div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>백테스트 리포트 — {timestamp}</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 20px; background: #f8f9fa; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #1a1a2e; border-bottom: 3px solid #5048e5; padding-bottom: 10px; }}
        h2 {{ color: #16213e; margin-top: 30px; }}
        .final-box {{ background: #5048e5; color: white; padding: 20px; border-radius: 12px; margin: 20px 0; }}
        .final-box h3 {{ margin-top: 0; }}
        .stat-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 15px; }}
        .stat {{ background: rgba(255,255,255,0.15); padding: 12px; border-radius: 8px; text-align: center; }}
        .stat .label {{ display: block; font-size: 12px; opacity: 0.8; }}
        .stat .value {{ display: block; font-size: 24px; font-weight: bold; margin-top: 4px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: right; font-size: 13px; }}
        th {{ background: #1a1a2e; color: white; }}
        td:first-child, td:nth-child(2) {{ text-align: left; }}
        tr:hover {{ background: #f0f0f0; }}
    </style>
</head>
<body>
<div class="container">
    <h1>🤖 암호화폐 선물 자동매매 백테스트 리포트</h1>
    <p>생성: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>

    <div class="final-box">
        <h3>🏆 최종 선정 전략</h3>
        <p><b>{final_name}</b></p>
        <p>기본 전략: {final_base} | 청산: {final_exit} | 레버리지: {final_lev}x</p>
        {final_stats}
    </div>

    <h2>📊 에쿼티 커브 & 거래 분포</h2>
    {charts_html}

    <h2>📋 전략 비교 (상위 20개)</h2>
    <table>
        <tr><th>#</th><th>전략명</th><th>수익률</th><th>샤프</th><th>MDD</th><th>승률</th><th>손익비</th><th>거래수</th><th>스코어</th></tr>
        {table_rows}
    </table>

    <h2>🌐 멀티 심볼 검증</h2>
    <table>
        <tr><th>전략</th><th>심볼</th><th>수익률</th></tr>
        {multi_rows}
    </table>

    <h2>🔄 워크포워드 분석 (70/30)</h2>
    <table>
        <tr><th>전략</th><th>학습 수익률</th><th>테스트 수익률</th><th>비율</th><th>판정</th></tr>
        {wf_rows}
    </table>
</div>
</body>
</html>"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"리포트 생성: {filepath}")
    return filepath
