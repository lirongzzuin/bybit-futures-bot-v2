"""
Pytest configuration and shared fixtures for trading bot tests.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import tempfile
import os
import sqlite3


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


def make_test_df(n=200, start_price=50000, volatility=0.02):
    """
    Generate realistic-looking OHLCV test data.

    Args:
        n: Number of candles
        start_price: Starting close price
        volatility: Daily volatility as fraction

    Returns:
        DataFrame with timestamp, open, high, low, close, volume columns
    """
    np.random.seed(42)

    # Generate timestamps (15-minute candles)
    start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    timestamps = [start_time + timedelta(minutes=15*i) for i in range(n)]

    # Generate realistic OHLCV data
    closes = [start_price]
    opens = [start_price]
    highs = [start_price]
    lows = [start_price]
    volumes = [1000000]

    for i in range(1, n):
        # Random walk with mean reversion
        change = np.random.normal(0, volatility)
        close = closes[-1] * (1 + change)

        # Open near previous close
        open_price = closes[-1] + np.random.normal(0, volatility * closes[-1])

        # High and low around open/close
        candle_range = abs(close - open_price) * 0.5
        high = max(open_price, close) + np.abs(np.random.normal(0, candle_range))
        low = min(open_price, close) - np.abs(np.random.normal(0, candle_range))

        # Volume with some variance
        base_volume = 1000000
        volume = base_volume + np.random.normal(0, base_volume * 0.3)

        opens.append(open_price)
        closes.append(close)
        highs.append(high)
        lows.append(low)
        volumes.append(max(volume, 0))

    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })

    return df


@pytest.fixture
def test_df():
    """Fixture providing test OHLCV data."""
    return make_test_df(200)


@pytest.fixture
def test_df_long(test_df):
    """Fixture providing longer test OHLCV data."""
    return make_test_df(500)
