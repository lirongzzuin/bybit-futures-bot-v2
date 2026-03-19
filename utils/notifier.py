"""
알림 유틸리티 — Slack Incoming Webhook (rate limit 대응)
"""
import time
import requests
from config.settings import SLACK_WEBHOOK_URL
from utils.logger import setup_logger

logger = setup_logger("notifier")

# Rate limiting: Slack Incoming Webhook은 1초에 1개 메시지 제한
# 연속 429 시 지수 백오프 적용
_last_send_time = 0.0
_MIN_INTERVAL = 2.0  # 메시지 간 최소 간격 (초) — was 1.5
_consecutive_429 = 0
_429_backoff_until = 0.0  # 429 backoff 종료 시각


def send_slack(title: str, body: str, emoji: str = "📌", level: str = "info") -> bool:
    """Slack Incoming Webhook 메시지 전송 (지수 백오프 rate limit 대응)"""
    global _last_send_time, _consecutive_429, _429_backoff_until

    if not SLACK_WEBHOOK_URL:
        logger.debug("Slack Webhook 설정 없음 — 알림 건너뜀")
        return False

    # 429 백오프 중이면 건너뜀 (trade/error 레벨만 재시도)
    now = time.time()
    if now < _429_backoff_until and level not in ("trade", "error"):
        remaining = int(_429_backoff_until - now)
        logger.debug(f"Slack 429 백오프 중 ({remaining}초 남음) — {level} 메시지 건너뜀")
        return False

    # Rate limit: 최소 간격 대기
    elapsed = now - _last_send_time
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

    # 최대 3회 retry (지수 백오프)
    for attempt in range(3):
        try:
            _last_send_time = time.time()
            resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
            if resp.status_code == 429:
                _consecutive_429 += 1
                # 지수 백오프: 10초, 30초, 60초, 120초... (최대 5분)
                backoff = min(10 * (2 ** _consecutive_429), 300)
                retry_after = int(resp.headers.get("Retry-After", backoff))
                wait = max(retry_after, backoff)
                _429_backoff_until = time.time() + wait
                logger.warning(f"Slack 429 (연속 {_consecutive_429}회) — {wait}초 백오프 ({attempt+1}/3)")
                if attempt < 2:
                    time.sleep(min(wait, 15))  # retry 시 최대 15초만 대기
                continue
            resp.raise_for_status()
            _consecutive_429 = 0  # 성공하면 카운터 리셋
            return True
        except requests.exceptions.HTTPError as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            logger.error(f"Slack 전송 실패: {e}")
            return False
        except Exception as e:
            logger.error(f"Slack 전송 실패: {e}")
            return False

    logger.error(f"Slack 전송 3회 실패 — 건너뜀 (연속 429: {_consecutive_429}회)")
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
