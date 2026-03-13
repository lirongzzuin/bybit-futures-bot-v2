"""
로깅 유틸리티
- 콘솔 + 파일 동시 출력
- 거래 로그는 별도 파일로 분리
"""
import logging
import os
from datetime import datetime


def setup_logger(name: str = "trading_bot", level: str = "INFO") -> logging.Logger:
    """메인 로거 설정"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper()))

    # 로그 디렉터리
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    # 콘솔 핸들러
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console_fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s", "%H:%M:%S")
    console.setFormatter(console_fmt)

    # 파일 핸들러 (일별 로테이션)
    today = datetime.now().strftime("%Y-%m-%d")
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f"bot_{today}.log"), encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s [%(module)s:%(lineno)d] %(message)s"
    )
    file_handler.setFormatter(file_fmt)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def get_trade_logger() -> logging.Logger:
    """거래 전용 로거 — 진입/청산 기록"""
    logger = logging.getLogger("trades")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    fh = logging.FileHandler(
        os.path.join(log_dir, f"trades_{today}.log"), encoding="utf-8"
    )
    fmt = logging.Formatter("[%(asctime)s] %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger
