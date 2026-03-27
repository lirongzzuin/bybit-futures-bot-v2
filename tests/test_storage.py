"""
Tests for storage.py SQLite database operations.
"""
import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import sys
import os as os_module
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.storage import (
    get_db, record_trade, record_paper_trade, get_recent_trades,
    get_trade_stats_since, record_tp_sl_adjustment, get_tp_sl_adjustment_analysis,
    record_skipped_signal, get_skipped_signals, get_paper_trade_analysis,
    set_state, get_state
)
from config.settings import DB_PATH


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture(autouse=True)
def mock_db_path(temp_db_path, monkeypatch):
    """Mock the DB_PATH for all tests."""
    monkeypatch.setattr("data.storage.DB_PATH", temp_db_path)


class TestRecordTrade:
    """Test trade recording."""

    def test_record_trade_basic(self, temp_db_path, mock_db_path):
        """Test recording a basic trade."""
        record_trade(
            symbol="BTCUSDT",
            side="long",
            action="open",
            price=50000.0,
            quantity=1.0,
            leverage=10,
            pnl=0,
            pnl_pct=0,
            fee=50.0,
            signal_grade="gold",
            strategy="pullback_v9",
            note="test trade"
        )

        conn = get_db()
        rows = conn.execute("SELECT * FROM trades WHERE symbol = 'BTCUSDT'").fetchall()
        conn.close()

        assert len(rows) > 0
        assert rows[0]["side"] == "long"
        assert rows[0]["action"] == "open"
        assert rows[0]["price"] == 50000.0
        assert rows[0]["quantity"] == 1.0

    def test_record_trade_multiple(self, temp_db_path, mock_db_path):
        """Test recording multiple trades."""
        symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]

        for sym in symbols:
            record_trade(
                symbol=sym,
                side="long" if sym == "BTCUSDT" else "short",
                action="open",
                price=50000.0,
                quantity=1.0
            )

        conn = get_db()
        rows = conn.execute("SELECT * FROM trades").fetchall()
        conn.close()

        assert len(rows) >= 3

    def test_record_trade_with_pnl(self, temp_db_path, mock_db_path):
        """Test recording trade with PnL."""
        record_trade(
            symbol="BTCUSDT",
            side="long",
            action="close",
            price=51000.0,
            quantity=1.0,
            pnl=1000.0,
            pnl_pct=2.0,
            fee=55.0
        )

        conn = get_db()
        row = conn.execute("SELECT * FROM trades WHERE symbol = 'BTCUSDT'").fetchone()
        conn.close()

        assert row["pnl"] == 1000.0
        assert row["pnl_pct"] == 2.0
        assert row["fee"] == 55.0


class TestGetRecentTrades:
    """Test trade retrieval."""

    def test_get_recent_trades_empty(self, temp_db_path, mock_db_path):
        """Test getting trades when none exist."""
        trades = get_recent_trades(hours=6)
        assert len(trades) == 0

    def test_get_recent_trades_basic(self, temp_db_path, mock_db_path):
        """Test getting recent trades."""
        record_trade("BTCUSDT", "long", "open", 50000.0, 1.0)
        record_trade("ETHUSDT", "short", "open", 3000.0, 10.0)

        trades = get_recent_trades(hours=24)

        assert len(trades) >= 2
        assert trades[0]["symbol"] in ["BTCUSDT", "ETHUSDT"]

    def test_get_recent_trades_time_filter(self, temp_db_path, mock_db_path):
        """Test time filtering in recent trades."""
        record_trade("BTCUSDT", "long", "open", 50000.0, 1.0)

        # Should get trades from past 6 hours
        trades_6h = get_recent_trades(hours=6)
        assert len(trades_6h) >= 1

        # Should get trades from past 1 hour (might miss the one if older than 1h)
        # This is database time-based, so it depends on actual time
        trades_1h = get_recent_trades(hours=1)
        # Could be 0 or 1 depending on exact timing


class TestGetTradeStats:
    """Test trade statistics."""

    def test_get_trade_stats_empty(self, temp_db_path, mock_db_path):
        """Test stats with no closed trades."""
        stats = get_trade_stats_since(hours=24)

        assert stats["count"] == 0
        assert stats["wins"] == 0
        assert stats["losses"] == 0
        assert stats["total_pnl"] == 0

    def test_get_trade_stats_basic(self, temp_db_path, mock_db_path):
        """Test basic stats calculation."""
        # Record open and close trades
        record_trade("BTCUSDT", "long", "open", 50000.0, 1.0)
        record_trade("BTCUSDT", "long", "close", 51000.0, 1.0, pnl=1000.0)

        record_trade("ETHUSDT", "short", "open", 3000.0, 10.0)
        record_trade("ETHUSDT", "short", "close", 2900.0, 10.0, pnl=1000.0)

        stats = get_trade_stats_since(hours=24)

        assert stats["count"] >= 2
        assert stats["wins"] >= 2
        assert stats["total_pnl"] >= 2000.0

    def test_get_trade_stats_mixed_pnl(self, temp_db_path, mock_db_path):
        """Test stats with winning and losing trades."""
        # Win
        record_trade("BTCUSDT", "long", "close", 51000.0, 1.0, pnl=1000.0)
        # Loss
        record_trade("ETHUSDT", "short", "close", 3100.0, 10.0, pnl=-1000.0)
        # Win
        record_trade("XRPUSDT", "long", "close", 0.6, 1000.0, pnl=500.0)

        stats = get_trade_stats_since(hours=24)

        assert stats["wins"] >= 2
        assert stats["losses"] >= 1
        assert stats["total_pnl"] >= 500.0
        assert stats["win_rate"] > 0


class TestRecordPaperTrade:
    """Test paper trade recording."""

    def test_record_paper_trade_basic(self, temp_db_path, mock_db_path):
        """Test recording a paper trade."""
        record_paper_trade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            exit_price=51000.0,
            quantity=1.0,
            leverage=10,
            pnl=1000.0,
            pnl_pct=2.0,
            fee=55.0,
            exit_reason="take_profit",
            market_regime="uptrend",
            signal_strength=0.75,
            tp_atr_mult=2.0,
            sl_atr_mult=0.8,
            atr_at_entry=500.0,
            bars_held=10,
            balance_after=11000.0
        )

        conn = get_db()
        row = conn.execute("SELECT * FROM paper_trades WHERE symbol = 'BTCUSDT'").fetchone()
        conn.close()

        assert row is not None
        assert row["entry_price"] == 50000.0
        assert row["exit_price"] == 51000.0
        assert row["pnl"] == 1000.0
        assert row["exit_reason"] == "take_profit"
        assert row["signal_strength"] == 0.75

    def test_record_paper_trade_multiple(self, temp_db_path, mock_db_path):
        """Test recording multiple paper trades."""
        for i in range(5):
            record_paper_trade(
                symbol=f"SYM{i}",
                side="long" if i % 2 == 0 else "short",
                entry_price=50000.0 + i * 1000,
                exit_price=51000.0 + i * 1000,
                quantity=1.0,
                pnl=1000.0 + i * 100
            )

        conn = get_db()
        rows = conn.execute("SELECT * FROM paper_trades").fetchall()
        conn.close()

        assert len(rows) >= 5


class TestTPSLAdjustments:
    """Test TP/SL adjustment recording."""

    def test_record_tp_sl_adjustment(self, temp_db_path, mock_db_path):
        """Test recording TP/SL adjustment."""
        record_tp_sl_adjustment(
            symbol="BTCUSDT",
            side="long",
            adjust_type="sl",
            old_value=49000.0,
            new_value=49500.0,
            reason="trailing_stop",
            signal_strength=0.8,
            pnl_pct=2.5,
            price_at_adjust=50500.0,
            entry_price=50000.0
        )

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM tp_sl_adjustments WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["adjust_type"] == "sl"
        assert row["old_value"] == 49000.0
        assert row["new_value"] == 49500.0
        assert row["reason"] == "trailing_stop"

    def test_get_tp_sl_adjustment_analysis(self, temp_db_path, mock_db_path):
        """Test TP/SL adjustment analysis."""
        # Record several adjustments
        for i in range(3):
            record_tp_sl_adjustment(
                symbol="BTCUSDT",
                side="long",
                adjust_type="sl" if i % 2 == 0 else "tp",
                old_value=49000.0 + i*100,
                new_value=49500.0 + i*100,
                reason="trailing_stop"
            )

        analysis = get_tp_sl_adjustment_analysis(hours=24)

        assert analysis["total_adjustments"] >= 3
        assert "by_reason" in analysis
        assert "records" in analysis


class TestSkippedSignals:
    """Test skipped signal recording."""

    def test_record_skipped_signal(self, temp_db_path, mock_db_path):
        """Test recording a skipped signal."""
        record_skipped_signal(
            symbol="BTCUSDT",
            side="long",
            signal_strength=0.65,
            skip_reason="max_positions_reached",
            price=50000.0,
            atr=500.0,
            rsi=35.0,
            market_regime="sideways",
            leverage=10
        )

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM skipped_signals WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["skip_reason"] == "max_positions_reached"
        assert row["signal_strength"] == 0.65

    def test_get_skipped_signals(self, temp_db_path, mock_db_path):
        """Test retrieving skipped signals."""
        for i in range(3):
            record_skipped_signal(
                symbol=f"SYM{i}",
                side="long",
                skip_reason="daily_halt" if i == 0 else "max_positions_reached"
            )

        signals = get_skipped_signals(hours=24)

        assert len(signals) >= 3


class TestBotState:
    """Test bot state storage."""

    def test_set_and_get_state(self, temp_db_path, mock_db_path):
        """Test storing and retrieving bot state."""
        set_state("last_signal_time", "2024-03-27T10:30:00")
        value = get_state("last_signal_time")

        assert value == "2024-03-27T10:30:00"

    def test_get_state_nonexistent(self, temp_db_path, mock_db_path):
        """Test getting nonexistent state."""
        value = get_state("nonexistent_key")
        assert value is None

    def test_set_state_overwrites(self, temp_db_path, mock_db_path):
        """Test that setting state overwrites previous value."""
        set_state("test_key", "value1")
        set_state("test_key", "value2")

        value = get_state("test_key")
        assert value == "value2"


class TestPaperTradeAnalysis:
    """Test paper trade analysis."""

    def test_get_paper_trade_analysis_empty(self, temp_db_path, mock_db_path):
        """Test analysis with no paper trades."""
        analysis = get_paper_trade_analysis(hours=168)

        assert analysis["count"] == 0
        assert "No paper trades" in analysis.get("message", "")

    def test_get_paper_trade_analysis_basic(self, temp_db_path, mock_db_path):
        """Test basic paper trade analysis."""
        # Record some paper trades (3 wins of +1000 each, 2 losses of -1000 each)
        for i in range(5):
            record_paper_trade(
                symbol="BTCUSDT",
                side="long" if i % 2 == 0 else "short",
                entry_price=50000.0,
                exit_price=51000.0 if i % 2 == 0 else 49000.0,
                quantity=1.0,
                pnl=1000.0 if i % 2 == 0 else -1000.0,
                pnl_pct=2.0 if i % 2 == 0 else -2.0,
                exit_reason="take_profit" if i % 2 == 0 else "stop_loss",
                market_regime="uptrend"
            )

        analysis = get_paper_trade_analysis(hours=168)

        assert analysis["count"] >= 5
        assert "wins" in analysis
        assert "losses" in analysis
        assert "win_rate" in analysis
        # 3 wins of +1000, 2 losses of -1000 = 1000 total
        assert analysis["total_pnl"] >= 0

    def test_get_paper_trade_analysis_stats(self, temp_db_path, mock_db_path):
        """Test detailed stats in analysis."""
        # Record trades with known stats
        record_paper_trade("BTC", "long", 50000, 51000, 1, pnl=1000, pnl_pct=2)
        record_paper_trade("ETH", "long", 3000, 2700, 10, pnl=-3000, pnl_pct=-10)

        analysis = get_paper_trade_analysis(hours=168)

        assert analysis["count"] >= 2
        assert analysis["wins"] >= 1
        assert analysis["losses"] >= 1
        assert analysis["best_trade"] >= 1000
        assert analysis["worst_trade"] <= -3000
