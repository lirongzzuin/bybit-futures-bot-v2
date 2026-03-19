"""
정기 브리핑 — 6시간 간격 Telegram 리포트
KST 09:00 / 15:00 / 21:00 / 03:00 (UTC 00:00 / 06:00 / 12:00 / 18:00)
"""
from datetime import datetime, timezone, timedelta

from exchange.bybit_client import BybitClient
from strategy.risk_manager import RiskManager
from data.storage import get_recent_trades, get_trade_stats_since
from utils.notifier import send_telegram
from utils.logger import setup_logger

logger = setup_logger("briefing")

KST = timezone(timedelta(hours=9))


def build_briefing(client: BybitClient, risk: RiskManager) -> str:
    """브리핑 메시지 생성"""

    now_kst = datetime.now(KST)
    header = f"📅 {now_kst.strftime('%Y-%m-%d %H:%M')} KST"

    # ── 1. 잔고 현황 ──
    balance = client.get_balance()
    total = balance["total"]
    available = balance["available"]
    unrealized = balance["unrealized_pnl"]

    initial = risk.state.initial_balance
    total_return = ((total + unrealized) - initial) / initial * 100 if initial > 0 else 0

    balance_section = (
        f"*💰 잔고 현황*\n"
        f"• 총 잔고: `{total:,.2f}` USDT\n"
        f"• 가용 잔고: `{available:,.2f}` USDT\n"
        f"• 미실현 PnL: `{unrealized:+,.2f}` USDT\n"
        f"• 누적 수익률: `{total_return:+.2f}%`"
    )

    # ── 2. 포지션 현황 ──
    positions = client.get_positions()
    if positions:
        pos_lines = []
        for p in positions:
            side_emoji = "🟢" if p["side"] == "Buy" else "🔴"
            side_label = "롱" if p["side"] == "Buy" else "숏"
            pnl_sign = "+" if p["unrealized_pnl"] >= 0 else ""
            entry = p["entry_price"]
            # 현재가 추정 (entry + unrealized/size)
            pos_lines.append(
                f"  {side_emoji} `{p['symbol']}` {side_label} {p['leverage']}x | "
                f"진입 `{entry:,.1f}` | "
                f"PnL `{pnl_sign}{p['unrealized_pnl']:,.2f}` USDT"
            )
        position_section = "*📊 보유 포지션*\n" + "\n".join(pos_lines)
    else:
        position_section = "*📊 보유 포지션*\n  포지션 없음"

    # ── 3. 최근 6시간 거래 내역 ──
    stats = get_trade_stats_since(hours=6)
    if stats["count"] > 0:
        trade_lines = []
        for t in stats["trades"][:5]:  # 최근 5건만
            pnl_emoji = "✅" if t["pnl"] > 0 else "❌"
            side_label = "롱" if t["side"] == "long" else "숏"
            trade_lines.append(
                f"  {pnl_emoji} `{t['symbol']}` {side_label} → `{t['pnl']:+,.2f}` USDT"
            )

        trade_section = (
            f"*📈 최근 6시간 거래*\n"
            + "\n".join(trade_lines)
            + f"\n  ─\n"
            f"  총 {stats['count']}건 | "
            f"승 {stats['wins']} / 패 {stats['losses']} | "
            f"승률 `{stats['win_rate']:.0f}%` | "
            f"합계 `{stats['total_pnl']:+,.2f}` USDT"
        )
    else:
        trade_section = "*📈 최근 6시간 거래*\n  거래 없음"

    # ── 4. 24시간 통계 ──
    stats_24h = get_trade_stats_since(hours=24)
    if stats_24h["count"] > 0:
        daily_section = (
            f"*📋 24시간 요약*\n"
            f"  총 {stats_24h['count']}건 | "
            f"승률 `{stats_24h['win_rate']:.0f}%` | "
            f"합계 `{stats_24h['total_pnl']:+,.2f}` USDT\n"
            f"  최고 `{stats_24h['best']:+,.2f}` / 최저 `{stats_24h['worst']:+,.2f}`"
        )
    else:
        daily_section = "*📋 24시간 요약*\n  거래 없음"

    # ── 5. 리스크 상태 ──
    risk_items = []
    if risk.state.consecutive_losses >= 2:
        risk_items.append(f"⚠️ 연속 {risk.state.consecutive_losses}패")
    if risk.state.daily_trade_halted:
        risk_items.append("🚫 일일 손실한도 도달 — 매매 중단")

    safe_mode = getattr(risk, '_bot_safe_mode', False)
    if safe_mode:
        risk_items.append("🛑 안전모드 활성화")

    if risk_items:
        risk_section = "*⚠️ 리스크 알림*\n  " + "\n  ".join(risk_items)
    else:
        risk_section = "*✅ 리스크 상태*\n  정상 운영 중"

    # ── 조합 ──
    body = "\n\n".join([
        header,
        balance_section,
        position_section,
        trade_section,
        daily_section,
        risk_section,
    ])

    return body


def send_briefing(client: BybitClient, risk: RiskManager):
    """브리핑 생성 + Slack 전송"""
    try:
        body = build_briefing(client, risk)
        send_telegram("정기 브리핑", body, emoji="📋", level="info")
        logger.info("브리핑 전송 완료")
    except Exception as e:
        logger.error(f"브리핑 전송 실패: {e}", exc_info=True)
