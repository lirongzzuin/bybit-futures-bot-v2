"""
알림 유틸리티 — Telegram Bot API (v7.4)

Slack 대비 장점:
  - 메시지 제한 없음 (무료)
  - Rate limit 넉넉 (초당 30개)
  - 모바일 푸시 즉시 수신
"""
import time
import threading
from collections import deque
import requests
from utils.logger import setup_logger

logger = setup_logger("notifier")

# === Telegram 설정 (환경변수에서 로드) ===
_telegram_token = ""
_telegram_chat_id = ""

def _load_telegram_config():
    """환경변수에서 Telegram 설정 로드 (지연 로드)"""
    global _telegram_token, _telegram_chat_id
    if not _telegram_token:
        import os
        _telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        _telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

# === Rate Limit 상태 ===
_lock = threading.Lock()
_last_send_time = 0.0
_MIN_INTERVAL = 1.0  # Telegram은 초당 30개 가능하지만 여유 확보

# === 메시지 큐 (비동기 전송) ===
_message_queue: deque = deque(maxlen=50)
_queue_thread_started = False

# === 중복 방지 ===
_recent_titles: dict = {}
_DEDUP_INTERVAL = 30.0

# === 종료 시 메시지 억제 ===
_suppress_shutdown = False


def suppress_shutdown_messages():
    """종료 시그널 수신 후 호출 — 종료 알림 전송 억제"""
    global _suppress_shutdown
    _suppress_shutdown = True


def _do_send(title: str, body: str, emoji: str, level: str) -> bool:
    """Telegram Bot API로 메시지 전송"""
    global _last_send_time
    _load_telegram_config()

    if not _telegram_token or not _telegram_chat_id:
        logger.debug("Telegram 설정 없음 — 알림 건너뜀")
        return False

    # 최소 간격 대기
    with _lock:
        elapsed = time.time() - _last_send_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_send_time = time.time()

    # 레벨별 이모지
    level_emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "trade": "💰"}.get(level, "📌")

    # Telegram MarkdownV2 포맷
    # 특수문자 이스케이프
    def escape_md(text: str) -> str:
        special = r'_*[]()~`>#+-=|{}.!'
        for ch in special:
            text = text.replace(ch, f'\\{ch}')
        return text

    # 메시지 조합 (HTML 포맷 — MarkdownV2보다 안정적)
    message = (
        f"{level_emoji} <b>{title}</b>\n"
        f"{'─' * 20}\n"
        f"{body}"
    )

    url = f"https://api.telegram.org/bot{_telegram_token}/sendMessage"
    payload = {
        "chat_id": _telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            if data.get("ok"):
                return True
            else:
                error_desc = data.get("description", "unknown")
                if resp.status_code == 429:
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.warning(f"Telegram 429 — {retry_after}초 대기")
                    time.sleep(retry_after)
                    continue
                logger.error(f"Telegram 전송 실패: {error_desc}")
                return False
        except Exception as e:
            logger.error(f"Telegram 전송 실패: {e}")
            if attempt == 0:
                time.sleep(2)
                continue
            return False

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


def send_telegram(title: str, body: str, emoji: str = "📌", level: str = "info") -> bool:
    """Telegram 메시지 전송 (trade/error 즉시, 나머지 큐)"""
    _load_telegram_config()

    if not _telegram_token or not _telegram_chat_id:
        return False

    # 종료 메시지 억제
    if _suppress_shutdown:
        return False

    # 중복 방지
    now = time.time()
    if title in _recent_titles and (now - _recent_titles[title]) < _DEDUP_INTERVAL:
        if level not in ("trade", "error"):
            return False
    _recent_titles[title] = now

    # 오래된 dedup 엔트리 정리
    if len(_recent_titles) > 100:
        cutoff = now - _DEDUP_INTERVAL * 2
        stale = [k for k, v in _recent_titles.items() if v < cutoff]
        for k in stale:
            del _recent_titles[k]

    # trade/error는 즉시, 나머지는 큐
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

    # Telegram
    send_telegram(title, body, emoji, level)
