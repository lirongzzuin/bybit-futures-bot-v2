"""
전역 설정 — 환경변수 기반
"""
import os
from dotenv import load_dotenv

load_dotenv()

# === Bybit API ===
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "true").lower() == "true"

# === Slack ===
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# === Webhook ===
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))

# === 데이터 경로 ===
# fly.io 볼륨 마운트 시 DATA_DIR 환경변수로 외부 경로 지정 가능
_base_data = os.getenv("DATA_DIR", "")
if _base_data:
    DATA_DIR = os.path.join(_base_data, "cache")
    DB_PATH = os.path.join(_base_data, "db", "trades.db")
    REPORT_DIR = os.path.join(_base_data, "reports")
    LOG_DIR = os.path.join(_base_data, "logs")
else:
    _project_root = os.path.dirname(os.path.dirname(__file__))
    DATA_DIR = os.path.join(_project_root, "data", "cache")
    DB_PATH = os.path.join(_project_root, "data", "trades.db")
    REPORT_DIR = os.path.join(_project_root, "reports")
    LOG_DIR = os.path.join(_project_root, "logs")

# 디렉터리 자동 생성
for d in [DATA_DIR, REPORT_DIR, LOG_DIR, os.path.dirname(DB_PATH)]:
    os.makedirs(d, exist_ok=True)
