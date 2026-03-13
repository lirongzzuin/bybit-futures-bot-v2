"""
알림 유틸리티 — Slack Incoming Webhook
"""
import requests
from config.settings import SLACK_WEBHOOK_URL
from utils.logger import setup_logger

logger = setup_logger("notifier")


def send_slack(title: str, body: str, emoji: str = "📌", level: str = "info") -> bool:
    """Slack Incoming Webhook 메시지 전송"""
    if not SLACK_WEBHOOK_URL:
        logger.debug("Slack Webhook 설정 없음 — 알림 건너뜀")
        return False

    color_map = {
        "info": "#2196F3",
        "warning": "#FF9800",
        "error": "#F44336",
        "trade": "#4CAF50",
    }
    color = color_map.get(level, "#9E9E9E")

    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{emoji} {title}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": body,
                        },
                    },
                ],
            }
        ]
    }

    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Slack 전송 실패: {e}")
        return False


def notify(title: str, body: str, level: str = "info"):
    """
    통합 알림 함수
    level: info, warning, error, trade
    """
    emoji_map = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "trade": "💰"}
    emoji = emoji_map.get(level, "📌")

    # 콘솔 로그
    log_fn = getattr(logger, "warning" if level == "error" else "info")
    log_fn(f"[{level.upper()}] {title}: {body}")

    # Slack
    send_slack(title, body, emoji, level)
