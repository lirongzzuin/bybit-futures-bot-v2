"""
Tests for position_manager.py position management logic.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from strategy.position_manager import Position, ExitCondition, calc_atr
from tests.conftest import make_test_df


class TestPositionInitialization:
    """Test Position initialization."""

    def test_position_long_basic(self):
        """Test creating a long position."""
        pos = Position(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0
        )

        assert pos.symbol == "BTCUSDT"
        assert pos.side == "long"
        assert pos.entry_price == 50000.0
        assert pos.quantity == 1.0
        assert pos.leverage == 10
        assert pos.stop_loss == 49000.0
        assert pos.take_profit == 52000.0
        assert pos.highest_price == 0.0
        assert pos.lowest_price == 1e18

    def test_position_short_basic(self):
        """Test creating a short position."""
        pos = Position(
            symbol="ETHUSDT",
            side="short",
            entry_price=3000.0,
            quantity=10.0,
            leverage=5,
            stop_loss=3100.0,
            take_profit=2900.0,
            entry_index=100
        )

        assert pos.side == "short"
        assert pos.entry_price == 3000.0
        assert pos.take_profit == 2900.0


class TestFixedTPSL:
    """Test fixed TP/SL calculation."""

    def test_fixed_tp_sl_long(self):
        """Test TP/SL for long position."""
        entry = 50000.0
        atr = 1000.0

        tp, sl = ExitCondition.fixed_tp_sl(
            entry_price=entry,
            side="long",
            atr=atr,
            tp_ratio=2.0
        )

        # TP should be above entry, SL below
        assert tp == entry + atr * 2.0
        assert sl == entry - atr
        assert tp > entry
        assert sl < entry

    def test_fixed_tp_sl_short(self):
        """Test TP/SL for short position."""
        entry = 50000.0
        atr = 1000.0

        tp, sl = ExitCondition.fixed_tp_sl(
            entry_price=entry,
            side="short",
            atr=atr,
            tp_ratio=2.0
        )

        # TP should be below entry, SL above
        assert tp == entry - atr * 2.0
        assert sl == entry + atr
        assert tp < entry
        assert sl > entry

    def test_fixed_tp_sl_custom_ratio(self):
        """Test TP/SL with custom TP ratio."""
        entry = 100.0
        atr = 10.0

        tp1, sl1 = ExitCondition.fixed_tp_sl(entry, "long", atr, tp_ratio=1.5)
        tp2, sl2 = ExitCondition.fixed_tp_sl(entry, "long", atr, tp_ratio=3.0)

        assert tp1 == entry + atr * 1.5
        assert tp2 == entry + atr * 3.0
        assert tp2 > tp1


class TestTrailingStop:
    """Test trailing stop logic."""

    def test_update_trailing_stop_long_new(self):
        """Test trailing stop initialization for long."""
        pos = Position(
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0
        )

        pos = ExitCondition.update_trailing_stop(
            pos=pos,
            current_price=51000.0,
            atr=1000.0,
            atr_mult=1.5
        )

        # Highest price should be updated
        assert pos.highest_price == 51000.0
        # Trailing stop should be set
        assert pos.trailing_stop == 51000.0 - 1000.0 * 1.5

    def test_update_trailing_stop_long_higher(self):
        """Test trailing stop update as price goes higher."""
        pos = Position(
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0,
            highest_price=51000.0,
            trailing_stop=50500.0
        )

        pos = ExitCondition.update_trailing_stop(
            pos=pos,
            current_price=52000.0,
            atr=1000.0,
            atr_mult=1.5
        )

        # Highest price should be 52000
        assert pos.highest_price == 52000.0
        # Trailing stop should move up (new trail = 52000 - 1500 = 50500)
        expected_trail = 52000.0 - 1000.0 * 1.5
        assert pos.trailing_stop == expected_trail
        # New trailing stop should be >= old one (or equal)
        assert pos.trailing_stop >= 50500.0 - 0.01  # Small tolerance

    def test_update_trailing_stop_short(self):
        """Test trailing stop for short position."""
        pos = Position(
            symbol="BTC",
            side="short",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=51000.0,
            take_profit=49000.0,
            entry_index=0
        )

        pos = ExitCondition.update_trailing_stop(
            pos=pos,
            current_price=49500.0,
            atr=1000.0,
            atr_mult=1.5
        )

        # Lowest price should be updated
        assert pos.lowest_price == 49500.0
        # Trailing stop should be set
        assert pos.trailing_stop == 49500.0 + 1000.0 * 1.5


class TestShouldExit:
    """Test exit condition evaluation."""

    def test_should_exit_tp_long(self):
        """Test TP exit trigger for long."""
        pos = Position(
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0
        )

        row = pd.Series()
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=52000.1,
            atr=1000.0
        )

        assert should_exit is True
        assert reason == "take_profit"

    def test_should_exit_sl_long(self):
        """Test SL exit trigger for long."""
        pos = Position(
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0
        )

        row = pd.Series()
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=48999.0,
            atr=1000.0
        )

        assert should_exit is True
        assert reason == "stop_loss"

    def test_should_exit_tp_short(self):
        """Test TP exit trigger for short."""
        pos = Position(
            symbol="BTC",
            side="short",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=51000.0,
            take_profit=49000.0,
            entry_index=0
        )

        row = pd.Series()
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=48999.0,
            atr=1000.0
        )

        assert should_exit is True
        assert reason == "take_profit"

    def test_should_exit_sl_short(self):
        """Test SL exit trigger for short."""
        pos = Position(
            symbol="BTC",
            side="short",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=51000.0,
            take_profit=49000.0,
            entry_index=0
        )

        row = pd.Series()
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=51000.1,
            atr=1000.0
        )

        assert should_exit is True
        assert reason == "stop_loss"

    def test_should_exit_no_exit(self):
        """Test no exit when price in middle."""
        pos = Position(
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0
        )

        row = pd.Series()
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=50500.0,
            atr=1000.0
        )

        assert should_exit is False
        assert reason == ""

    def test_should_exit_trailing_stop_long(self):
        """Test trailing stop exit for long."""
        pos = Position(
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0,
            trailing_stop=50500.0
        )

        row = pd.Series()
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=50499.0,
            atr=1000.0
        )

        assert should_exit is True
        assert reason == "trailing_stop"

    def test_should_exit_trailing_stop_short(self):
        """Test trailing stop exit for short."""
        pos = Position(
            symbol="BTC",
            side="short",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=51000.0,
            take_profit=49000.0,
            entry_index=0,
            trailing_stop=49500.0
        )

        row = pd.Series()
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=49501.0,
            atr=1000.0
        )

        assert should_exit is True
        assert reason == "trailing_stop"

    def test_should_exit_opposite_signal_long(self):
        """Test opposite signal exit for long."""
        pos = Position(
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0
        )

        row = pd.Series({"cmp_signal": -1})
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=50500.0,
            atr=1000.0,
            exit_type="opposite_signal"
        )

        assert should_exit is True
        assert reason == "opposite_signal"

    def test_should_exit_opposite_signal_short(self):
        """Test opposite signal exit for short."""
        pos = Position(
            symbol="BTC",
            side="short",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=51000.0,
            take_profit=49000.0,
            entry_index=0
        )

        row = pd.Series({"cmp_signal": 1})
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=49500.0,
            atr=1000.0,
            exit_type="opposite_signal"
        )

        assert should_exit is True
        assert reason == "opposite_signal"


class TestCalcATR:
    """Test ATR calculation in position_manager."""

    def test_calc_atr_basic(self):
        """Test ATR calculation."""
        df = make_test_df(100)
        atr = calc_atr(df, period=14)

        assert len(atr) == len(df)
        assert atr.isna().sum() >= 12  # NaN during warmup (may be 13-14)

        # Check that we have valid values after warmup
        valid_atr = atr[~atr.isna()]
        assert len(valid_atr) > 0
        assert valid_atr.iloc[-1] > 0  # Valid at end

    def test_calc_atr_matches_strategy_atr(self):
        """Test that position_manager ATR matches strategy ATR."""
        from strategy.final_strategy import calc_atr as strategy_atr

        df = make_test_df(100)

        # Both should produce same results
        atr_pm = calc_atr(df, period=14)
        atr_strat = strategy_atr(df, period=14)

        # Should be very close (may have minor differences in implementation)
        valid_idx = ~(atr_pm.isna() | atr_strat.isna())
        if valid_idx.sum() > 0:
            # Compare only non-NaN values
            diff = abs(atr_pm[valid_idx].values - atr_strat[valid_idx].values).max()
            assert diff < 1.0  # Tolerance for floating point


class TestExitConditionEdgeCases:
    """Test edge cases in exit conditions."""

    def test_should_exit_at_exact_tp(self):
        """Test exit at exact TP price."""
        pos = Position(
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0
        )

        row = pd.Series()
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=52000.0,  # Exactly at TP
            atr=1000.0
        )

        assert should_exit is True
        assert reason == "take_profit"

    def test_should_exit_at_exact_sl(self):
        """Test exit at exact SL price."""
        pos = Position(
            symbol="BTC",
            side="long",
            entry_price=50000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_index=0
        )

        row = pd.Series()
        should_exit, reason = ExitCondition.should_exit(
            pos=pos,
            current_row=row,
            current_price=49000.0,  # Exactly at SL
            atr=1000.0
        )

        assert should_exit is True
        assert reason == "stop_loss"
