"""
알림 유틸리티 — Slack Incoming Webhook (rate limit 대응)
"""
import time
import requests
from config.settings import SLACK_WEBHOOK_URL
from utils.logger import setup_logger

logger = setup_logger("notifier")

# Rate limiting: Slack은 1초에 1개 메시지 제한
_last_send_time = 0.0
_MIN_INTERVAL = 1.5  # 메시지 간 최소 간격 (초)


def send_slack(title: str, body: str, emoji: str = "📌", level: str = "info") -> bool:
    """Slack Incoming Webhook 메시지 전송 (rate limit + retry)"""
    global _last_send_time

    if not SLACK_WEBHOOK_URL:
        logger.debug("Slack Webhook 설정 없음 — 알림 건너뜀")
        return False

    # Rate limit: 최소 간격 대기
    elapsed = time.time() - _last_send_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)

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

    # 최대 3회 retry (429 대응)
    for attempt in range(3):
        try:
            _last_send_time = time.time()
            resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                logger.warning(f"Slack 429 — {retry_after}초 대기 후 재시도 ({attempt+1}/3)")
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            logger.error(f"Slack 전송 실패: {e}")
            return False
        except Exception as e:
            logger.error(f"Slack 전송 실패: {e}")
            return False

    logger.error("Slack 전송 3회 실패 — 건너뜀")
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
