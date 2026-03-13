"""
OHLCV 데이터 수집 — ccxt를 통한 Bybit 데이터 다운로드
- 최소 1년치 4시간봉 데이터
- 로컬 캐싱으로 반복 다운로드 방지
"""
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import ccxt
import pandas as pd

from config.settings import DATA_DIR
from config.indicator_params import SYMBOLS, TRADING
from utils.logger import setup_logger

logger = setup_logger("fetcher")

# 타임프레임 → 밀리초 변환
TF_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


def get_exchange() -> ccxt.Exchange:
    """Bybit ccxt 인스턴스 생성"""
    return ccxt.bybit({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},  # USDT 무기한 선물
    })


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "4h",
    since_days: int = 365,
    exchange: Optional[ccxt.Exchange] = None,
) -> pd.DataFrame:
    """
    Bybit에서 OHLCV 데이터 다운로드

    Args:
        symbol: 'BTCUSDT' 형태
        timeframe: '4h', '1h', '1d' 등
        since_days: 과거 몇 일치 데이터를 가져올지
        exchange: ccxt 인스턴스 (없으면 자동 생성)

    Returns:
        DataFrame (columns: timestamp, open, high, low, close, volume)
    """
    if exchange is None:
        exchange = get_exchange()

    # ccxt 심볼 형식 변환 (BTCUSDT → BTC/USDT:USDT)
    base = symbol.replace("USDT", "")
    ccxt_symbol = f"{base}/USDT:USDT"

    since_ms = int((datetime.utcnow() - timedelta(days=since_days)).timestamp() * 1000)
    tf_ms = TF_MS.get(timeframe, 14_400_000)
    limit = 200  # Bybit 최대 200개 per request

    all_candles = []
    current_since = since_ms

    logger.info(f"[{symbol}] {since_days}일치 {timeframe} 데이터 다운로드 시작...")

    while True:
        try:
            candles = exchange.fetch_ohlcv(
                ccxt_symbol, timeframe=timeframe,
                since=current_since, limit=limit
            )
        except Exception as e:
            logger.error(f"[{symbol}] 데이터 다운로드 실패: {e}")
            time.sleep(2)
            continue

        if not candles:
            break

        all_candles.extend(candles)

        # 마지막 캔들의 다음 시간부터 재요청
        last_ts = candles[-1][0]
        current_since = last_ts + tf_ms

        # 현재 시간 도달 시 종료
        if current_since >= int(datetime.utcnow().timestamp() * 1000):
            break

        # 더 이상 새 데이터가 없으면 종료
        if len(candles) < limit:
            break

        time.sleep(0.2)  # rate limit 준수

    if not all_candles:
        logger.warning(f"[{symbol}] 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)

    logger.info(f"[{symbol}] {len(df)}개 캔들 다운로드 완료 ({df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]})")
    return df


def save_cache(df: pd.DataFrame, symbol: str, timeframe: str = "4h"):
    """로컬 CSV 파일로 캐싱"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{symbol}_{timeframe}.csv")
    df.to_csv(path, index=False)
    logger.info(f"[{symbol}] 캐시 저장: {path}")


def load_cache(symbol: str, timeframe: str = "4h") -> Optional[pd.DataFrame]:
    """캐시된 데이터 로드 (있으면 반환, 없으면 None)"""
    path = os.path.join(DATA_DIR, f"{symbol}_{timeframe}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["timestamp"])
        logger.info(f"[{symbol}] 캐시 로드: {len(df)}개 캔들")
        return df
    return None


def fetch_and_cache(
    symbol: str,
    timeframe: str = "4h",
    since_days: int = 365,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    캐시 우선 로드, 없거나 force_refresh면 다운로드 후 캐싱
    기존 캐시가 있으면 마지막 이후 데이터만 추가 다운로드
    """
    if not force_refresh:
        cached = load_cache(symbol, timeframe)
        if cached is not None and len(cached) > 0:
            # 캐시 마지막 이후 데이터만 추가
            last_ts = cached["timestamp"].max()
            days_gap = (datetime.utcnow() - last_ts.to_pydatetime().replace(tzinfo=None)).days + 1
            if days_gap <= 1:
                return cached

            new_data = fetch_ohlcv(symbol, timeframe, since_days=days_gap)
            if not new_data.empty:
                combined = pd.concat([cached, new_data]).drop_duplicates(subset="timestamp")
                combined = combined.sort_values("timestamp").reset_index(drop=True)
                save_cache(combined, symbol, timeframe)
                return combined
            return cached

    df = fetch_ohlcv(symbol, timeframe, since_days)
    if not df.empty:
        save_cache(df, symbol, timeframe)
    return df


def fetch_all_symbols(
    timeframe: str = "4h",
    since_days: int = 365,
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """모든 대상 심볼 데이터 일괄 다운로드"""
    all_symbols = [SYMBOLS["primary"]] + SYMBOLS["alts"]
    result = {}

    for sym in all_symbols:
        df = fetch_and_cache(sym, timeframe, since_days, force_refresh)
        if not df.empty:
            result[sym] = df
        time.sleep(0.5)  # 심볼 간 딜레이

    logger.info(f"총 {len(result)}개 심볼 데이터 수집 완료")
    return result
