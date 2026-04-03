"""
Tests for final_strategy.py signal generation and indicator calculations.
"""
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

# Import the functions to test
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from strategy.final_strategy import (
    calc_atr, calc_rsi, calc_ema, calc_macd, calc_adx_dmi,
    calc_bollinger_bands, generate_signals, get_current_signal,
    OPTIMAL_PARAMS, ENGINE_CONFIG
)
from tests.conftest import make_test_df


class TestATRCalculation:
    """Test ATR indicator calculation."""

    def test_calc_atr_basic(self):
        """Test ATR calculation with synthetic data."""
        df = make_test_df(100)
        atr = calc_atr(df, period=14)

        # ATR should have NaN values at the beginning (before period)
        assert atr.isna().sum() >= 12  # May be 13-14 depending on rolling impl

        # After the period, ATR should have valid values
        last_valid = atr[~atr.isna()]
        assert len(last_valid) > 0
        assert last_valid.iloc[-1] > 0

        # ATR should be roughly on the order of the candle sizes
        avg_range = (df["high"] - df["low"]).mean()
        assert last_valid.iloc[-1] < avg_range * 3  # Should be less than 3x average range

    def test_calc_atr_zero_volatility(self):
        """Test ATR with zero volatility (flat prices)."""
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=100, freq="15min"),
            "open": [100.0] * 100,
            "high": [100.0] * 100,
            "low": [100.0] * 100,
            "close": [100.0] * 100,
            "volume": [1000000] * 100,
        })
        atr = calc_atr(df, period=14)
        # After warmup, ATR should be close to 0
        assert atr.iloc[-1] < 0.01

    def test_calc_atr_high_volatility(self):
        """Test ATR with high volatility."""
        n = 100
        timestamps = pd.date_range("2024-01-01", periods=n, freq="15min")
        close = [100 + np.sin(i/5) * 10 + np.random.normal(0, 2) for i in range(n)]
        high = [c + 5 for c in close]
        low = [c - 5 for c in close]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": [1000000] * n,
        })
        atr = calc_atr(df, period=14)
        # With high volatility, ATR should reflect that
        assert atr.iloc[-1] > 2


class TestRSICalculation:
    """Test RSI indicator calculation."""

    def test_calc_rsi_basic(self, test_df):
        """Test RSI calculation."""
        rsi = calc_rsi(test_df["close"], period=14)

        # RSI should be between 0 and 100
        valid_rsi = rsi.dropna()
        assert (valid_rsi >= 0).all()
        assert (valid_rsi <= 100).all()

        # Should have NaN at the beginning
        assert rsi.isna().sum() > 0

    def test_calc_rsi_oversold_overbought(self):
        """Test RSI extremes (oversold/overbought)."""
        # Create a series that goes from very low to very high
        n = 100
        close = list(range(10, 60)) + list(range(60, 110)) + [100] * (n - 100)
        series = pd.Series(close)

        rsi = calc_rsi(series, period=14)
        valid_rsi = rsi.dropna()

        # Later part (around index -1) should show high RSI in uptrend
        if len(valid_rsi) > 0:
            # Check if any values exceed 70 during uptrend
            assert (valid_rsi > 70).sum() > 0 or valid_rsi.mean() > 50

    def test_calc_rsi_downtrend(self):
        """Test RSI in downtrend."""
        # Create decreasing series
        close = list(range(100, 10, -1))
        series = pd.Series(close)

        rsi = calc_rsi(series, period=14)
        valid_rsi = rsi.dropna()

        # Should show oversold conditions
        assert valid_rsi.iloc[-1] < 30


class TestEMACalculation:
    """Test EMA indicator calculation."""

    def test_calc_ema_basic(self, test_df):
        """Test EMA calculation."""
        ema9 = calc_ema(test_df["close"], 9)
        ema21 = calc_ema(test_df["close"], 21)

        # EMA should follow the price roughly
        assert not ema9.isna().all()
        assert not ema21.isna().all()

        # Faster EMA should be more volatile
        assert ema9.std() > ema21.std() or abs(ema9.std() - ema21.std()) < 0.01

    def test_calc_ema_alignment(self):
        """Test EMA alignment in uptrend/downtrend."""
        # Uptrend
        uptrend = list(range(100, 150))
        ema_fast = calc_ema(pd.Series(uptrend), 5)
        ema_slow = calc_ema(pd.Series(uptrend), 10)

        valid_idx = ~ema_fast.isna()
        # In uptrend, fast EMA should be above slow EMA
        assert (ema_fast[valid_idx] > ema_slow[valid_idx]).mean() > 0.5


class TestMACDCalculation:
    """Test MACD indicator calculation."""

    def test_calc_macd_structure(self, test_df):
        """Test MACD returns correct structure."""
        macd_line, signal_line, histogram = calc_macd(test_df["close"], fast=12, slow=26, signal=9)

        # All should be Series of same length
        assert len(macd_line) == len(test_df)
        assert len(signal_line) == len(test_df)
        assert len(histogram) == len(test_df)

        # Histogram = MACD - Signal
        assert np.allclose(histogram, macd_line - signal_line, rtol=1e-5)

    def test_calc_macd_uptrend(self):
        """Test MACD in uptrend."""
        # Strong uptrend
        close = list(range(100, 200))
        macd_line, signal_line, histogram = calc_macd(pd.Series(close), fast=12, slow=26, signal=9)

        valid_idx = ~histogram.isna()
        # In uptrend, MACD histogram should be positive
        assert (histogram[valid_idx] > 0).mean() > 0.5


class TestADXDMICalculation:
    """Test ADX and DMI indicator calculation."""

    def test_calc_adx_dmi_structure(self, test_df):
        """Test ADX/DMI returns correct structure."""
        adx, pdi, mdi = calc_adx_dmi(test_df, period=14)

        # All should be Series of same length
        assert len(adx) == len(test_df)
        assert len(pdi) == len(test_df)
        assert len(mdi) == len(test_df)

        # ADX, PDI, MDI should be non-negative
        assert (adx.dropna() >= 0).all()
        assert (pdi.dropna() >= 0).all()
        assert (mdi.dropna() >= 0).all()

    def test_calc_adx_dmi_uptrend(self):
        """Test ADX/DMI in strong uptrend."""
        n = 100
        timestamps = pd.date_range("2024-01-01", periods=n, freq="15min")
        close = list(range(100, 100+n))
        high = [c + 1 for c in close]
        low = [c - 0.5 for c in close]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": [1000000] * n,
        })

        adx, pdi, mdi = calc_adx_dmi(df, period=14)
        valid_idx = ~adx.isna()

        # In uptrend, PDI should be > MDI
        if valid_idx.sum() > 0:
            assert (pdi[valid_idx] > mdi[valid_idx]).mean() > 0.5


class TestBollingerBandsCalculation:
    """Test Bollinger Bands calculation."""

    def test_calc_bollinger_bands_structure(self, test_df):
        """Test BB bands calculation."""
        upper, mid, lower = calc_bollinger_bands(test_df["close"], period=20, std_mult=2.0)

        # All should be Series
        assert len(upper) == len(test_df)
        assert len(mid) == len(test_df)
        assert len(lower) == len(test_df)

        # Upper > Mid > Lower
        valid_idx = ~upper.isna()
        assert (upper[valid_idx] > mid[valid_idx]).all()
        assert (mid[valid_idx] > lower[valid_idx]).all()

    def test_calc_bollinger_bands_price_within(self, test_df):
        """Test price typically stays within BB bands."""
        upper, mid, lower = calc_bollinger_bands(test_df["close"], period=20, std_mult=2.0)

        valid_idx = ~upper.isna()
        # Most prices should be within bands (2-std typically contains ~95% of data)
        within_bands = (test_df["close"][valid_idx] <= upper[valid_idx]) & \
                       (test_df["close"][valid_idx] >= lower[valid_idx])
        assert within_bands.mean() > 0.90


class TestGenerateSignals:
    """Test signal generation."""

    def test_generate_signals_output_columns(self, test_df):
        """Test that generate_signals returns expected columns."""
        sig_df = generate_signals(test_df)

        expected_columns = [
            "sig_direction", "sig_strength", "sig_leverage",
            "sig_tp_mult", "sig_sl_mult", "sig_mode"
        ]
        for col in expected_columns:
            assert col in sig_df.columns

    def test_generate_signals_no_signal_on_short_data(self):
        """Test no signals generated on very short data."""
        short_df = make_test_df(50)
        sig_df = generate_signals(short_df)

        # Should have signal columns but very few signals (start at bar 60)
        # With only 50 bars, should have no or very few signals
        assert sig_df["sig_direction"].sum() == 0 or sig_df["sig_direction"].sum() < 2

    def test_generate_signals_direction_values(self, test_df):
        """Test signal direction values are valid."""
        sig_df = generate_signals(test_df)

        # Direction should be -1, 0, or 1
        valid_directions = sig_df["sig_direction"].unique()
        assert all(d in [-1, 0, 1] for d in valid_directions)

    def test_generate_signals_strength_bounds(self, test_df):
        """Test signal strength is between 0 and 1."""
        sig_df = generate_signals(test_df)

        non_zero_strength = sig_df[sig_df["sig_strength"] > 0]["sig_strength"]
        assert (non_zero_strength >= 0.3).all()
        assert (non_zero_strength <= 1.0).all()

    def test_generate_signals_with_custom_params(self, test_df):
        """Test signal generation with custom parameters."""
        custom_params = OPTIMAL_PARAMS.copy()
        custom_params["adx_min"] = 20

        sig_df = generate_signals(test_df, params=custom_params)

        # Should still produce valid output
        assert "sig_direction" in sig_df.columns
        assert len(sig_df) == len(test_df)


class TestGetCurrentSignal:
    """Test get_current_signal function."""

    def test_get_current_signal_returns_dict(self, test_df):
        """Test get_current_signal returns correct dict structure."""
        signal = get_current_signal(test_df)

        assert isinstance(signal, dict)
        expected_keys = {
            "direction", "strength", "leverage", "tp_atr_mult",
            "sl_atr_mult", "atr", "close", "mode", "rsi", "vol_ratio"
        }
        assert all(k in signal for k in expected_keys)

    def test_get_current_signal_direction_valid(self, test_df):
        """Test signal direction is valid."""
        signal = get_current_signal(test_df)

        assert signal["direction"] in [-1, 0, 1]

    def test_get_current_signal_strength_bounds(self, test_df):
        """Test signal strength bounds."""
        signal = get_current_signal(test_df)

        assert 0 <= signal["strength"] <= 1

    def test_get_current_signal_mode_valid(self, test_df):
        """Test signal mode is valid."""
        signal = get_current_signal(test_df)

        assert signal["mode"] in ["none", "pullback"]

    def test_get_current_signal_leverage_valid(self, test_df):
        """Test leverage is positive."""
        signal = get_current_signal(test_df)

        assert signal["leverage"] > 0
        assert signal["leverage"] <= OPTIMAL_PARAMS.get("leverage", 10) + 1


class TestOptimalParamsRegression:
    """Regression tests for OPTIMAL_PARAMS."""

    def test_optimal_params_unchanged(self):
        """Verify OPTIMAL_PARAMS values match expected (regression test v12.0)."""
        assert OPTIMAL_PARAMS["strategy_type"] == "pullback_in_trend_v12.0"
        assert OPTIMAL_PARAMS["adx_min"] == 30
        assert OPTIMAL_PARAMS["rsi_pull_long_lo"] == 20
        assert OPTIMAL_PARAMS["rsi_pull_long_hi"] == 35
        assert OPTIMAL_PARAMS["rsi_pull_short_lo"] == 65
        assert OPTIMAL_PARAMS["rsi_pull_short_hi"] == 80
        assert OPTIMAL_PARAMS["tp_atr_mult"] == 2.0
        assert OPTIMAL_PARAMS["sl_atr_mult"] == 0.8
        assert OPTIMAL_PARAMS["leverage"] == 10
        assert OPTIMAL_PARAMS["cooldown"] == 4
        assert OPTIMAL_PARAMS["min_score"] == 2.5
        assert OPTIMAL_PARAMS["bb_atr_margin"] == 0.3
        assert OPTIMAL_PARAMS["max_leverage"] == 20


class TestEngineConfigRegression:
    """Regression tests for ENGINE_CONFIG."""

    def test_engine_config_unchanged(self):
        """Verify ENGINE_CONFIG values match expected (regression test v12.0)."""
        assert ENGINE_CONFIG["risk_per_trade"] == 0.03
        assert ENGINE_CONFIG["use_trailing_stop"] is True
        assert ENGINE_CONFIG["trailing_activation_atr"] == 1.0
        assert ENGINE_CONFIG["trailing_distance_atr"] == 0.8
        assert ENGINE_CONFIG["max_daily_loss"] == 0.10
        assert ENGINE_CONFIG["time_exit_bars"] == 48
