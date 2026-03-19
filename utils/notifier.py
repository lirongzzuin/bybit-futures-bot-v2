"""
알림 유틸리티 — Slack Incoming Webhook (rate limit 방지)

v7.4 개선:
  - 메시지 큐 + 쓰로틀링: 1초 1메시지 Slack 제한 준수
  - 비필수 메시지 자동 병합: 짧은 시간 내 중복 알림 합산
  - 429 지수 백오프: 연속 실패 시 최대 5분 대기
  - 종료 시그널 메시지 억제: 배포 시 종료+시작 연속 전송 방지
"""
import time
import threading
from collections import deque
import requests
from config.settings import SLACK_WEBHOOK_URL
from utils.logger import setup_logger

logger = setup_logger("notifier")

# === Rate Limit 상태 ===
_lock = threading.Lock()
_last_send_time = 0.0
_MIN_INTERVAL = 3.0       # 메시지 간 최소 간격 (초) — Slack 권장 1/sec, 여유 확보
_consecutive_429 = 0
_429_backoff_until = 0.0

# === 메시지 큐 (비동기 전송) ===
_message_queue: deque = deque(maxlen=20)  # 최대 20개 큐 (넘치면 오래된 것부터 버림)
_queue_thread_started = False

# === 메시지 중복/폭주 방지 ===
_recent_titles: dict = {}  # {title: last_send_time} — 같은 제목 30초 내 재전송 차단
_DEDUP_INTERVAL = 30.0

# === 종료 시 메시지 억제 ===
_suppress_shutdown = False


def suppress_shutdown_messages():
    """종료 시그널(SIGTERM/SIGINT) 수신 후 호출 — 종료 알림 전송 억제"""
    global _suppress_shutdown
    _suppress_shutdown = True


def _do_send(title: str, body: str, emoji: str, level: str) -> bool:
    """실제 Slack HTTP 전송 (동기, 내부용)"""
    global _last_send_time, _consecutive_429, _429_backoff_until

    if not SLACK_WEBHOOK_URL:
        return False

    now = time.time()

    # 429 백오프 중이면 trade/error만 허용
    if now < _429_backoff_until and level not in ("trade", "error"):
        return False

    # 최소 간격 대기
    with _lock:
        elapsed = time.time() - _last_send_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_send_time = time.time()

    color_map = {
        "info": "#2196F3",
        "warning": "#FF9800",
        "error": "#F44336",
        "trade": "#4CAF50",
    }
    payload = {
        "attachments": [{
            "color": color_map.get(level, "#9E9E9E"),
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {title}"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": body}},
            ],
        }]
    }

    for attempt in range(2):  # 최대 2회 (was 3 — 429 악화 방지)
        try:
            resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
            if resp.status_code == 429:
                _consecutive_429 += 1
                backoff = min(30 * (2 ** min(_consecutive_429, 4)), 300)
                retry_after = int(resp.headers.get("Retry-After", backoff))
                wait = max(retry_after, backoff)
                _429_backoff_until = time.time() + wait
                logger.warning(f"Slack 429 (연속 {_consecutive_429}회) — {wait}초 백오프")
                if attempt == 0:
                    time.sleep(min(wait, 10))
                continue
            resp.raise_for_status()
            _consecutive_429 = 0
            return True
        except Exception as e:
            logger.error(f"Slack 전송 실패: {e}")
            return False

    logger.error(f"Slack 전송 실패 — 건너뜀 (429 연속 {_consecutive_429}회)")
    return False


def _queue_worker():
    """백그라운드 스레드: 큐에서 메시지를 꺼내 순차 전송"""
    while True:
        try:
            if _message_queue:
                msg = _message_queue.popleft()
                _do_send(msg["title"], msg["body"], msg["emoji"], msg["level"])
            else:
                time.sleep(1)
        except Exception:
            time.sleep(5)


def _ensure_queue_thread():
    """큐 워커 스레드가 없으면 시작"""
    global _queue_thread_started
    if not _queue_thread_started:
        _queue_thread_started = True
        t = threading.Thread(target=_queue_worker, daemon=True)
        t.start()


def send_slack(title: str, body: str, emoji: str = "📌", level: str = "info") -> bool:
    """Slack 메시지 큐에 등록 (비동기, rate limit 안전)"""
    if not SLACK_WEBHOOK_URL:
        return False

    # 종료 메시지 억제 (배포 시 SIGTERM → 종료 알림 불필요)
    if _suppress_shutdown:
        logger.debug(f"Slack 종료 억제 — {title}")
        return False

    # 중복 방지: 같은 제목 30초 내 재전송 차단
    now = time.time()
    if title in _recent_titles and (now - _recent_titles[title]) < _DEDUP_INTERVAL:
        if level not in ("trade", "error"):
            logger.debug(f"Slack 중복 억제 — {title}")
            return False
    _recent_titles[title] = now

    # 오래된 dedup 엔트리 정리 (100개 초과 시)
    if len(_recent_titles) > 100:
        cutoff = now - _DEDUP_INTERVAL * 2
        stale = [k for k, v in _recent_titles.items() if v < cutoff]
        for k in stale:
            del _recent_titles[k]

    # trade/error는 즉시 전송, 나머지는 큐
    if level in ("trade", "error"):
        return _do_send(title, body, emoji, level)
    else:
        _ensure_queue_thread()
        _message_queue.append({"title": title, "body": body, "emoji": emoji, "level": level})
        return True


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
