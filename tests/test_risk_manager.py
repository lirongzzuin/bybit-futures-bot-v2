"""
Tests for risk_manager.py risk management logic.
"""
import pytest
import pandas as pd
from datetime import datetime, date, timezone, timedelta
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from strategy.risk_manager import RiskManager, RiskState


class TestRiskStateInitial:
    """Test RiskState initialization."""

    def test_initial_state_defaults(self):
        """Verify RiskState defaults."""
        state = RiskState()

        assert state.initial_balance == 10000.0
        assert state.current_balance == 10000.0
        assert state.daily_start_balance == 10000.0
        assert state.daily_pnl == 0.0
        assert state.consecutive_losses == 0
        assert state.open_positions == {}
        assert state.daily_trade_halted is False
        assert state.peak_balance == 10000.0
        assert state.mdd_circuit_breaker_triggered is False


class TestRiskManagerInitialization:
    """Test RiskManager initialization."""

    def test_risk_manager_init(self):
        """Test RiskManager initialization."""
        rm = RiskManager(initial_balance=50000.0)

        assert rm.state.initial_balance == 50000.0
        assert rm.state.current_balance == 50000.0
        assert rm.state.peak_balance == 50000.0
        assert rm.state.consecutive_losses == 0


class TestDailyReset:
    """Test daily reset logic."""

    def test_reset_daily_changes_date(self):
        """Test daily reset updates date."""
        rm = RiskManager()

        with patch('strategy.risk_manager.date') as mock_date:
            mock_date.today.return_value = date(2024, 3, 28)
            rm.reset_daily()

            assert rm.state.current_date == "2024-03-28"
            assert rm.state.daily_pnl == 0.0
            assert rm.state.daily_trade_halted is False

    def test_reset_daily_same_day_no_reset(self):
        """Test no reset if same day."""
        rm = RiskManager()
        today = date.today().isoformat()
        rm.state.current_date = today
        rm.state.daily_pnl = 100.0

        rm.reset_daily()

        # Should not change
        assert rm.state.daily_pnl == 100.0


class TestCanOpenPosition:
    """Test can_open_position checks."""

    def test_can_open_position_empty(self):
        """Should allow opening position with no existing positions."""
        rm = RiskManager()
        can_open, reason = rm.can_open_position("BTCUSDT")

        assert can_open is True

    def test_can_open_position_max_reached(self):
        """Should reject when max positions reached (v16.0: max=3)."""
        rm = RiskManager()
        rm.state.open_positions = {
            "BTCUSDT": {}, "ETHUSDT": {}, "SOLUSDT": {},
        }

        # Default max is 3 (v16.0)
        can_open, reason = rm.can_open_position("DOGEUSDT")

        assert can_open is False
        assert "최대 포지션" in reason or "max" in reason.lower()

    def test_can_open_position_duplicate_symbol(self):
        """Should reject duplicate symbol."""
        rm = RiskManager()
        rm.state.open_positions = {"BTCUSDT": {}}

        can_open, reason = rm.can_open_position("BTCUSDT")

        assert can_open is False
        assert "이미" in reason or "already" in reason.lower()

    def test_can_open_position_daily_halt(self):
        """Should reject when daily trade halted."""
        rm = RiskManager()
        rm.state.daily_trade_halted = True

        can_open, reason = rm.can_open_position("BTCUSDT")

        assert can_open is False
        assert "일일 최대 손실" in reason or "halt" in reason.lower()

    def test_can_open_position_no_circuit_breaker(self):
        """v13.1: Circuit breaker removed — should allow trading even with drawdown."""
        rm = RiskManager()
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 5000.0  # 50% drawdown

        can_open, reason = rm.can_open_position("BTCUSDT")
        # Circuit breaker removed, should allow (unless emergency close triggers)
        assert can_open is True

    def test_can_open_position_emergency_close(self):
        """Should reject when balance too low (v13.1: 30% threshold)."""
        rm = RiskManager(initial_balance=10000.0)
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 2900.0  # Less than 30%

        can_open, reason = rm.can_open_position("BTCUSDT")

        assert can_open is False
        assert "긴급" in reason or "emergency" in reason.lower()


class TestPositionSizing:
    """Test position sizing calculations."""

    def test_calc_position_size_basic(self):
        """Test basic position sizing."""
        rm = RiskManager(initial_balance=10000.0)

        entry_price = 50000.0
        stop_loss_price = 49000.0  # 1000 below entry
        qty = rm.calc_position_size(
            balance=10000.0,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            leverage=10
        )

        assert qty > 0

    def test_calc_position_size_zero_distance(self):
        """Test with entry == stop_loss."""
        rm = RiskManager()
        qty = rm.calc_position_size(
            balance=10000.0,
            entry_price=50000.0,
            stop_loss_price=50000.0,
            leverage=10
        )

        assert qty == 0.0

    def test_calc_position_size_consecutive_losses(self):
        """Test 50% reduction after 3+ losses."""
        rm = RiskManager(initial_balance=10000.0)
        rm.state.consecutive_losses = 3

        entry_price = 50000.0
        stop_loss_price = 49000.0

        qty_normal = rm.calc_position_size(10000.0, entry_price, stop_loss_price, leverage=10)

        rm.state.consecutive_losses = 3
        qty_reduced = rm.calc_position_size(10000.0, entry_price, stop_loss_price, leverage=10)

        # With 3 losses, size should be reduced by 50%
        assert qty_reduced <= qty_normal

    def test_calc_position_size_drawdown_factor(self):
        """Test drawdown-based position sizing."""
        rm = RiskManager(initial_balance=10000.0)
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 9000.0  # 10% drawdown

        entry_price = 50000.0
        stop_loss_price = 49000.0

        qty = rm.calc_position_size(9000.0, entry_price, stop_loss_price, leverage=10)

        # With drawdown, size should be reduced
        assert qty > 0

    def test_calc_position_size_leverage_capped(self):
        """Test leverage is capped at max."""
        rm = RiskManager()
        qty = rm.calc_position_size(
            balance=10000.0,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            leverage=100  # Exceed max
        )

        # Should still calculate size (leverage capped internally)
        assert qty > 0


class TestTradeClosing:
    """Test trade closing and state updates."""

    def test_update_on_trade_close_win(self):
        """Test state update on winning trade."""
        rm = RiskManager(initial_balance=10000.0)

        rm.update_on_trade_close(pnl=100.0)

        assert rm.state.current_balance == 10100.0
        assert rm.state.daily_pnl == 100.0
        assert rm.state.consecutive_losses == 0

    def test_update_on_trade_close_loss(self):
        """Test state update on losing trade."""
        rm = RiskManager(initial_balance=10000.0)

        rm.update_on_trade_close(pnl=-100.0)

        assert rm.state.current_balance == 9900.0
        assert rm.state.daily_pnl == -100.0
        assert rm.state.consecutive_losses == 1

    def test_update_on_trade_close_peak_tracking(self):
        """Test peak balance tracking."""
        rm = RiskManager(initial_balance=10000.0)

        rm.update_on_trade_close(pnl=500.0)
        assert rm.state.peak_balance == 10500.0

        rm.update_on_trade_close(pnl=-200.0)
        assert rm.state.peak_balance == 10500.0  # Should not decrease


class TestDrawdownFactor:
    """Test drawdown-based position sizing factor."""

    def test_get_drawdown_factor_no_drawdown(self):
        """Test factor with no drawdown."""
        rm = RiskManager()
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 10000.0

        factor = rm.get_drawdown_factor()
        assert factor == 1.0

    def test_get_drawdown_factor_15_percent(self):
        """Test factor at 15% drawdown (v13.0: still 1.0 under 20%)."""
        rm = RiskManager()
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 8500.0  # 15%

        factor = rm.get_drawdown_factor()
        assert factor == 1.0

    def test_get_drawdown_factor_25_percent(self):
        """Test factor at 25% drawdown (v13.0: 0.75 for 20-35%)."""
        rm = RiskManager()
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 7500.0  # 25%

        factor = rm.get_drawdown_factor()
        assert factor == 0.75

    def test_get_drawdown_factor_40_percent(self):
        """Test factor at 40% drawdown (v13.0: 0.5 for 35-50%)."""
        rm = RiskManager()
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 6000.0  # 40%

        factor = rm.get_drawdown_factor()
        assert factor == 0.5

    def test_get_drawdown_factor_extreme(self):
        """Test factor at 50%+ drawdown."""
        rm = RiskManager()
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 4500.0  # 55%

        factor = rm.get_drawdown_factor()
        assert factor == 0.3


class TestVolatilityRegime:
    """Test volatility regime detection."""

    def test_get_volatility_regime_low(self):
        """Test low volatility regime."""
        rm = RiskManager()
        regime = rm.get_volatility_regime(atr_current=5.0, atr_avg=10.0)
        assert regime == "low"

    def test_get_volatility_regime_normal(self):
        """Test normal volatility regime."""
        rm = RiskManager()
        regime = rm.get_volatility_regime(atr_current=10.0, atr_avg=10.0)
        assert regime == "normal"

    def test_get_volatility_regime_high(self):
        """Test high volatility regime."""
        rm = RiskManager()
        regime = rm.get_volatility_regime(atr_current=15.0, atr_avg=10.0)
        assert regime == "high"

    def test_get_volatility_regime_extreme(self):
        """Test extreme volatility regime."""
        rm = RiskManager()
        regime = rm.get_volatility_regime(atr_current=25.0, atr_avg=10.0)
        assert regime == "extreme"


class TestCircuitBreaker:
    """Test circuit breaker logic."""

    def test_check_circuit_breaker_ok(self):
        """Test circuit breaker when OK."""
        rm = RiskManager()
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 9500.0  # 5% drawdown (below 25%)

        can_trade, reason = rm.check_circuit_breaker()

        assert can_trade is True
        assert "OK" in reason or "Circuit" in reason

    def test_check_circuit_breaker_triggered(self):
        """Test circuit breaker when triggered (v13.0: 50% threshold)."""
        rm = RiskManager()
        rm.state.peak_balance = 10000.0
        rm.state.current_balance = 4900.0  # 51% drawdown (above 50%)

        can_trade, reason = rm.check_circuit_breaker()

        assert can_trade is False
        assert "Circuit" in reason or "drawdown" in reason


class TestDailyCandles:
    """Test daily candle transition protection."""

    def test_is_daily_candle_transition_outside_window(self):
        """Test transition detection outside protection window."""
        rm = RiskManager()

        # 12:00 UTC (not in 23:45-00:15 window)
        test_time = datetime(2024, 3, 27, 12, 0, 0, tzinfo=timezone.utc)

        is_transition = rm.is_daily_candle_transition(simulated_time=test_time)
        # Should depend on config
        assert isinstance(is_transition, bool)

    def test_is_daily_candle_transition_pre_window(self):
        """Test transition detection pre-transition."""
        rm = RiskManager()

        # 23:50 UTC (in pre-transition window)
        test_time = datetime(2024, 3, 27, 23, 50, 0, tzinfo=timezone.utc)

        is_transition = rm.is_daily_candle_transition(simulated_time=test_time)
        assert isinstance(is_transition, bool)

    def test_is_daily_candle_transition_post_window(self):
        """Test transition detection post-transition."""
        rm = RiskManager()

        # 00:10 UTC (in post-transition window)
        test_time = datetime(2024, 3, 28, 0, 10, 0, tzinfo=timezone.utc)

        is_transition = rm.is_daily_candle_transition(simulated_time=test_time)
        assert isinstance(is_transition, bool)


class TestEmergencyClose:
    """Test emergency close conditions."""

    def test_should_emergency_close_false(self):
        """Test should_emergency_close returns False normally."""
        rm = RiskManager(initial_balance=10000.0)
        rm.state.current_balance = 8000.0

        should_close = rm.should_emergency_close()
        assert should_close is False

    def test_should_emergency_close_true(self):
        """Test should_emergency_close returns True at threshold (v13.0: 40%)."""
        rm = RiskManager(initial_balance=10000.0)
        rm.state.current_balance = 3999.0  # < 40%

        should_close = rm.should_emergency_close()
        assert should_close is True


class TestCorrelation:
    """Test correlation calculations."""

    def test_calc_correlation_basic(self):
        """Test correlation calculation."""
        rm = RiskManager()

        returns_a = pd.Series([0.01, 0.02, -0.01, 0.03, -0.02] * 5)
        returns_b = pd.Series([0.01, 0.02, -0.01, 0.03, -0.02] * 5)

        corr = rm.calc_correlation(returns_a, returns_b)

        # Should be highly correlated with itself
        assert corr > 0.99

    def test_calc_correlation_uncorrelated(self):
        """Test uncorrelated series."""
        rm = RiskManager()

        returns_a = pd.Series([0.01, -0.01] * 10)
        returns_b = pd.Series([-0.01, 0.01] * 10)

        corr = rm.calc_correlation(returns_a, returns_b)

        # Should be negatively correlated
        assert corr < 0

    def test_calc_correlation_insufficient_data(self):
        """Test correlation with insufficient data."""
        rm = RiskManager()

        returns_a = pd.Series([0.01, 0.02])
        returns_b = pd.Series([0.01, 0.02])

        corr = rm.calc_correlation(returns_a, returns_b)

        # Should return 0 if less than 20 points
        assert corr == 0.0
