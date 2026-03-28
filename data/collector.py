"""
양질 데이터 수집기 (v9.2)

실전/페이퍼 운용 중 자동으로 다음 데이터를 축적:
  1. market_snapshots   — 15분마다 전체 시장 스냅샷 (지표, 레짐, 상관관계)
  2. signal_journal     — 모든 시그널(진입/스킵) 전후 맥락 + 결과 추적
  3. regime_history      — 변동성·트렌드 레짐 전환 이력
  4. drawdown_events     — 드로우다운 구간 기록
  5. candle_archive      — 15분봉 원시 데이터 장기 보관 (향후 백테스트용)
  6. correlation_snapshots — 심볼 간 상관관계 주기적 기록

사용법:
  collector = DataCollector(db_path)
  collector.record_market_snapshot(symbol, indicators)
  collector.record_signal_journal(...)
"""
import sqlite3
import os
import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Optional

from config.settings import DB_PATH
from utils.logger import setup_logger

logger = setup_logger("collector")


def _get_collector_db() -> sqlite3.Connection:
    """데이터 수집 전용 DB 연결"""
    db_dir = os.path.dirname(DB_PATH)
    collector_db_path = os.path.join(db_dir, "collector.db")
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(collector_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 동시 읽기/쓰기 성능
    conn.execute("PRAGMA synchronous=NORMAL")
    _init_collector_tables(conn)
    return conn


def _init_collector_tables(conn: sqlite3.Connection):
    """수집용 테이블 초기화"""
    conn.executescript("""
        -- 1. 시장 스냅샷: 15분마다 각 심볼의 지표 상태 기록
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT DEFAULT '15m',
            -- 가격
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            -- 15m 지표
            rsi_14 REAL,
            bb_upper REAL, bb_mid REAL, bb_lower REAL,
            bb_width REAL,
            ema_9 REAL, ema_21 REAL,
            macd_line REAL, macd_signal REAL, macd_hist REAL,
            atr_14 REAL,
            -- 4H 지표 (리샘플링)
            h4_adx REAL, h4_pdi REAL, h4_mdi REAL,
            h4_ema_fast REAL, h4_ema_mid REAL,
            h4_trend TEXT,          -- 'bullish' / 'bearish' / 'neutral'
            -- 시장 상태
            volatility_regime TEXT, -- 'low' / 'normal' / 'high' / 'extreme'
            atr_ratio REAL,         -- current ATR / avg ATR
            spread_pct REAL,        -- bid-ask spread %
            -- 시그널 상태
            signal_direction INTEGER DEFAULT 0,  -- -1, 0, 1
            signal_strength REAL DEFAULT 0,
            signal_score REAL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ms_ts_sym ON market_snapshots(timestamp, symbol);
        CREATE INDEX IF NOT EXISTS idx_ms_symbol ON market_snapshots(symbol);

        -- 2. 시그널 저널: 모든 시그널 (진입 + 스킵) 상세 기록
        CREATE TABLE IF NOT EXISTS signal_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,          -- 'long' / 'short'
            signal_strength REAL,
            signal_score REAL,
            -- 진입 여부
            action TEXT NOT NULL,        -- 'entered' / 'skipped'
            skip_reason TEXT,
            -- 시그널 시점 지표
            price REAL,
            rsi REAL, atr REAL, bb_position REAL,  -- BB 내 위치 (0=하단, 1=상단)
            macd_hist REAL,
            h4_adx REAL, h4_trend TEXT,
            volatility_regime TEXT,
            volume_ratio REAL,
            -- 결과 추적 (진입한 경우, 나중에 업데이트)
            entry_price REAL,
            exit_price REAL,
            exit_reason TEXT,
            pnl REAL, pnl_pct REAL,
            bars_held INTEGER,
            mfe_pct REAL,               -- Max Favorable Excursion %
            mae_pct REAL,               -- Max Adverse Excursion %
            -- 미진입 시 가상 결과 (스킵 후 가격 추적)
            price_after_15m REAL,
            price_after_30m REAL,
            price_after_1h REAL,
            price_after_4h REAL,
            virtual_pnl_1h_pct REAL,    -- 진입했다면 1시간 후 PnL
            virtual_pnl_4h_pct REAL     -- 진입했다면 4시간 후 PnL
        );
        CREATE INDEX IF NOT EXISTS idx_sj_ts ON signal_journal(timestamp);
        CREATE INDEX IF NOT EXISTS idx_sj_sym ON signal_journal(symbol);
        CREATE INDEX IF NOT EXISTS idx_sj_action ON signal_journal(action);

        -- 3. 레짐 전환 이력
        CREATE TABLE IF NOT EXISTS regime_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            regime_type TEXT NOT NULL,   -- 'volatility' / 'trend' / 'momentum'
            old_value TEXT,
            new_value TEXT,
            trigger_indicator TEXT,      -- 어떤 지표가 전환을 트리거했는지
            indicator_value REAL
        );
        CREATE INDEX IF NOT EXISTS idx_rh_ts ON regime_history(timestamp);

        -- 4. 드로우다운 이벤트
        CREATE TABLE IF NOT EXISTS drawdown_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            peak_balance REAL,
            current_balance REAL,
            drawdown_pct REAL,
            drawdown_tier TEXT,         -- 'normal' / 'warning' / 'danger' / 'critical'
            position_factor REAL,       -- 적용된 포지션 사이징 팩터
            open_positions TEXT,        -- JSON: 보유 포지션 목록
            daily_pnl REAL,
            consecutive_losses INTEGER
        );

        -- 5. 캔들 아카이브: 15분봉 원시 데이터 장기 보관
        CREATE TABLE IF NOT EXISTS candle_archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            UNIQUE(symbol, timeframe, timestamp)
        );
        CREATE INDEX IF NOT EXISTS idx_ca_sym_tf ON candle_archive(symbol, timeframe);
        CREATE INDEX IF NOT EXISTS idx_ca_ts ON candle_archive(timestamp);

        -- 6. 심볼 간 상관관계 스냅샷
        CREATE TABLE IF NOT EXISTS correlation_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol_a TEXT NOT NULL,
            symbol_b TEXT NOT NULL,
            correlation REAL,
            lookback_bars INTEGER,
            timeframe TEXT DEFAULT '15m'
        );
        CREATE INDEX IF NOT EXISTS idx_cs_ts ON correlation_snapshots(timestamp);

        -- 7. 성과 벤치마크: 일별 전략 성과 vs 시장 벤치마크
        CREATE TABLE IF NOT EXISTS performance_benchmark (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            strategy_return_pct REAL,
            btc_return_pct REAL,
            eth_return_pct REAL,
            balance REAL,
            total_trades INTEGER,
            win_rate REAL,
            avg_rr REAL,               -- 평균 Risk:Reward
            sharpe_proxy REAL,         -- 간이 Sharpe 비율
            max_drawdown_pct REAL,
            volatility_regime TEXT,
            UNIQUE(date)
        );
    """)
    conn.commit()


class DataCollector:
    """실시간 양질 데이터 수집기"""

    def __init__(self):
        self._last_regimes: dict[str, dict] = {}  # symbol → {volatility, trend}
        self._pending_signals: dict[int, dict] = {}  # signal_id → signal info (가격 추적용)

    def _conn(self) -> sqlite3.Connection:
        return _get_collector_db()

    # ──────────────────────────────────────────────────────────
    # 1. Market Snapshot
    # ──────────────────────────────────────────────────────────
    def record_market_snapshot(
        self,
        symbol: str,
        df: pd.DataFrame,
        signal_data: dict = None,
        volatility_regime: str = "normal",
        atr_ratio: float = 1.0,
    ):
        """15분봉 분석 시점의 전체 시장 상태 기록"""
        try:
            if df.empty or len(df) < 60:
                return

            from strategy.final_strategy import (
                calc_rsi, calc_ema, calc_bollinger_bands,
                calc_atr, calc_adx_dmi, calc_macd,
                _resample_to_4h, _map_4h_to_15m,
                OPTIMAL_PARAMS as p,
            )

            last = df.iloc[-1]
            close = df["close"]

            # 15m indicators
            rsi = calc_rsi(close, p["ltf_rsi_period"])
            bb_u, bb_m, bb_l = calc_bollinger_bands(close, p["ltf_bb_period"], p["ltf_bb_std"])
            ema9 = calc_ema(close, p["ltf_ema_fast"])
            ema21 = calc_ema(close, p["ltf_ema_mid"])
            macd_line, macd_sig, macd_hist = calc_macd(close, 12, 26, 9)
            atr = calc_atr(df, 14)

            # BB width (변동성 수축/확장 감지)
            bb_width = 0.0
            if not pd.isna(bb_m.iloc[-1]) and bb_m.iloc[-1] > 0:
                bb_width = (bb_u.iloc[-1] - bb_l.iloc[-1]) / bb_m.iloc[-1]

            # 4H indicators
            h4_adx_val = h4_pdi_val = h4_mdi_val = None
            h4_ef_val = h4_em_val = None
            h4_trend = "neutral"
            try:
                df_4h = _resample_to_4h(df)
                if len(df_4h) >= 30:
                    h4_map = _map_4h_to_15m(df, df_4h)
                    h_adx, h_pdi, h_mdi = calc_adx_dmi(df_4h, p["htf_adx_period"])
                    h_ef = calc_ema(df_4h["close"], p["htf_ema_fast"])
                    h_em = calc_ema(df_4h["close"], p["htf_ema_mid"])
                    h_idx = h4_map[-1]
                    if 0 <= h_idx < len(df_4h):
                        h4_adx_val = _safe_float(h_adx.iloc[h_idx])
                        h4_pdi_val = _safe_float(h_pdi.iloc[h_idx])
                        h4_mdi_val = _safe_float(h_mdi.iloc[h_idx])
                        h4_ef_val = _safe_float(h_ef.iloc[h_idx])
                        h4_em_val = _safe_float(h_em.iloc[h_idx])
                        if h4_pdi_val and h4_mdi_val and h4_ef_val and h4_em_val:
                            if h4_pdi_val > h4_mdi_val and h4_ef_val > h4_em_val:
                                h4_trend = "bullish"
                            elif h4_mdi_val > h4_pdi_val and h4_ef_val < h4_em_val:
                                h4_trend = "bearish"
            except Exception:
                pass

            sig_dir = signal_data.get("direction", 0) if signal_data else 0
            sig_str = signal_data.get("strength", 0) if signal_data else 0

            conn = self._conn()
            conn.execute("""
                INSERT INTO market_snapshots
                (timestamp, symbol, timeframe,
                 open, high, low, close, volume,
                 rsi_14, bb_upper, bb_mid, bb_lower, bb_width,
                 ema_9, ema_21,
                 macd_line, macd_signal, macd_hist,
                 atr_14,
                 h4_adx, h4_pdi, h4_mdi, h4_ema_fast, h4_ema_mid, h4_trend,
                 volatility_regime, atr_ratio,
                 signal_direction, signal_strength)
                VALUES (?, ?, '15m',
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(), symbol,
                _safe_float(last["open"]), _safe_float(last["high"]),
                _safe_float(last["low"]), _safe_float(last["close"]),
                _safe_float(last["volume"]),
                _safe_float(rsi.iloc[-1]),
                _safe_float(bb_u.iloc[-1]), _safe_float(bb_m.iloc[-1]),
                _safe_float(bb_l.iloc[-1]), bb_width,
                _safe_float(ema9.iloc[-1]), _safe_float(ema21.iloc[-1]),
                _safe_float(macd_line.iloc[-1]), _safe_float(macd_sig.iloc[-1]),
                _safe_float(macd_hist.iloc[-1]),
                _safe_float(atr.iloc[-1]),
                h4_adx_val, h4_pdi_val, h4_mdi_val, h4_ef_val, h4_em_val, h4_trend,
                volatility_regime, atr_ratio,
                sig_dir, sig_str,
            ))
            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"[{symbol}] 시장 스냅샷 기록 실패: {e}")

    # ──────────────────────────────────────────────────────────
    # 2. Signal Journal
    # ──────────────────────────────────────────────────────────
    def record_signal(
        self,
        symbol: str,
        side: str,
        signal_strength: float,
        signal_score: float,
        action: str,  # 'entered' / 'skipped'
        price: float,
        rsi: float = 0,
        atr: float = 0,
        macd_hist: float = 0,
        h4_adx: float = 0,
        h4_trend: str = "",
        volatility_regime: str = "",
        volume_ratio: float = 0,
        bb_position: float = 0.5,
        skip_reason: str = "",
        entry_price: float = 0,
    ) -> int:
        """시그널 기록 (진입 여부 무관하게 모두 기록)"""
        try:
            conn = self._conn()
            cursor = conn.execute("""
                INSERT INTO signal_journal
                (timestamp, symbol, side, signal_strength, signal_score,
                 action, skip_reason,
                 price, rsi, atr, bb_position, macd_hist,
                 h4_adx, h4_trend, volatility_regime, volume_ratio,
                 entry_price)
                VALUES (?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?)
            """, (
                datetime.now(timezone.utc).isoformat(), symbol, side,
                signal_strength, signal_score,
                action, skip_reason,
                price, rsi, atr, bb_position, macd_hist,
                h4_adx, h4_trend, volatility_regime, volume_ratio,
                entry_price if action == "entered" else 0,
            ))
            signal_id = cursor.lastrowid
            conn.commit()
            conn.close()

            # 스킵된 시그널도 가격 추적을 위해 등록
            if action == "skipped":
                self._pending_signals[signal_id] = {
                    "symbol": symbol,
                    "side": side,
                    "price": price,
                    "timestamp": datetime.now(timezone.utc),
                    "checks_done": set(),
                }

            return signal_id

        except Exception as e:
            logger.error(f"[{symbol}] 시그널 저널 기록 실패: {e}")
            return 0

    def update_signal_result(
        self,
        signal_id: int,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        pnl_pct: float,
        bars_held: int = 0,
        mfe_pct: float = 0,
        mae_pct: float = 0,
    ):
        """진입한 시그널의 결과 업데이트"""
        try:
            conn = self._conn()
            conn.execute("""
                UPDATE signal_journal
                SET exit_price = ?, exit_reason = ?,
                    pnl = ?, pnl_pct = ?,
                    bars_held = ?, mfe_pct = ?, mae_pct = ?
                WHERE id = ?
            """, (exit_price, exit_reason, pnl, pnl_pct,
                  bars_held, mfe_pct, mae_pct, signal_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"시그널 결과 업데이트 실패 (id={signal_id}): {e}")

    def track_skipped_signal_prices(self, get_price_fn):
        """
        스킵된 시그널의 후속 가격 추적
        get_price_fn: symbol → current_price (callable)
        15분, 30분, 1시간, 4시간 후 가격을 기록
        """
        now = datetime.now(timezone.utc)
        completed = []

        for sig_id, info in self._pending_signals.items():
            elapsed_min = (now - info["timestamp"]).total_seconds() / 60
            symbol = info["symbol"]

            try:
                current_price = get_price_fn(symbol)
                if current_price is None or current_price <= 0:
                    continue
            except Exception:
                continue

            updates = {}
            side = info["side"]
            base_price = info["price"]

            # 각 체크포인트별 가격 기록
            checkpoints = [
                (15, "price_after_15m"),
                (30, "price_after_30m"),
                (60, "price_after_1h"),
                (240, "price_after_4h"),
            ]

            for minutes, col in checkpoints:
                if minutes not in info["checks_done"] and elapsed_min >= minutes:
                    updates[col] = current_price
                    info["checks_done"].add(minutes)

                    # 가상 PnL 계산 (1시간, 4시간)
                    if minutes == 60:
                        if side == "long":
                            virt_pnl = (current_price - base_price) / base_price * 100
                        else:
                            virt_pnl = (base_price - current_price) / base_price * 100
                        updates["virtual_pnl_1h_pct"] = virt_pnl
                    elif minutes == 240:
                        if side == "long":
                            virt_pnl = (current_price - base_price) / base_price * 100
                        else:
                            virt_pnl = (base_price - current_price) / base_price * 100
                        updates["virtual_pnl_4h_pct"] = virt_pnl

            if updates:
                try:
                    set_clause = ", ".join(f"{k} = ?" for k in updates)
                    values = list(updates.values()) + [sig_id]
                    conn = self._conn()
                    conn.execute(
                        f"UPDATE signal_journal SET {set_clause} WHERE id = ?",
                        values,
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"스킵 시그널 가격 추적 실패 (id={sig_id}): {e}")

            # 4시간 체크 완료 → 추적 종료
            if 240 in info["checks_done"]:
                completed.append(sig_id)

        for sig_id in completed:
            del self._pending_signals[sig_id]

    # ──────────────────────────────────────────────────────────
    # 3. Regime History
    # ──────────────────────────────────────────────────────────
    def check_regime_change(
        self,
        symbol: str,
        volatility_regime: str,
        h4_trend: str,
        h4_adx: float = 0,
        atr_ratio: float = 1.0,
    ):
        """레짐 전환 감지 및 기록"""
        prev = self._last_regimes.get(symbol, {})

        changes = []
        if prev.get("volatility") and prev["volatility"] != volatility_regime:
            changes.append(("volatility", prev["volatility"], volatility_regime, "atr_ratio", atr_ratio))

        if prev.get("trend") and prev["trend"] != h4_trend:
            changes.append(("trend", prev["trend"], h4_trend, "h4_adx", h4_adx))

        if changes:
            try:
                conn = self._conn()
                for regime_type, old, new, indicator, value in changes:
                    conn.execute("""
                        INSERT INTO regime_history
                        (timestamp, symbol, regime_type, old_value, new_value,
                         trigger_indicator, indicator_value)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        datetime.now(timezone.utc).isoformat(), symbol,
                        regime_type, old, new, indicator, value,
                    ))
                    logger.info(f"[{symbol}] 레짐 전환: {regime_type} {old} → {new}")
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"[{symbol}] 레짐 기록 실패: {e}")

        self._last_regimes[symbol] = {
            "volatility": volatility_regime,
            "trend": h4_trend,
        }

    # ──────────────────────────────────────────────────────────
    # 4. Drawdown Events
    # ──────────────────────────────────────────────────────────
    def record_drawdown_event(self, risk_state, drawdown_factor: float):
        """드로우다운 이벤트 기록 (10% 이상일 때)"""
        try:
            if risk_state.peak_balance <= 0:
                return
            dd_pct = (risk_state.peak_balance - risk_state.current_balance) / risk_state.peak_balance
            if dd_pct < 0.05:  # 5% 미만은 기록 안 함
                return

            if dd_pct < 0.10:
                tier = "normal"
            elif dd_pct < 0.20:
                tier = "warning"
            elif dd_pct < 0.30:
                tier = "danger"
            else:
                tier = "critical"

            positions_json = json.dumps(
                {s: {"side": p["side"], "pnl_pct": 0} for s, p in risk_state.open_positions.items()}
            )

            conn = self._conn()
            conn.execute("""
                INSERT INTO drawdown_events
                (timestamp, peak_balance, current_balance, drawdown_pct,
                 drawdown_tier, position_factor, open_positions,
                 daily_pnl, consecutive_losses)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                risk_state.peak_balance, risk_state.current_balance,
                dd_pct, tier, drawdown_factor, positions_json,
                risk_state.daily_pnl, risk_state.consecutive_losses,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"드로우다운 이벤트 기록 실패: {e}")

    # ──────────────────────────────────────────────────────────
    # 5. Candle Archive
    # ──────────────────────────────────────────────────────────
    def archive_candles(self, symbol: str, df: pd.DataFrame, timeframe: str = "15m"):
        """15분봉 원시 데이터를 장기 보관 DB에 저장"""
        try:
            if df.empty:
                return

            conn = self._conn()
            rows = []
            for _, row in df.iterrows():
                ts = row["timestamp"]
                if isinstance(ts, pd.Timestamp):
                    ts = ts.isoformat()
                rows.append((
                    symbol, timeframe, str(ts),
                    float(row["open"]), float(row["high"]),
                    float(row["low"]), float(row["close"]),
                    float(row["volume"]),
                ))

            conn.executemany("""
                INSERT OR IGNORE INTO candle_archive
                (symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"[{symbol}] 캔들 아카이브 실패: {e}")

    def archive_recent_candles(self, symbol: str, df: pd.DataFrame, last_n: int = 5):
        """최근 N개 캔들만 아카이브 (매 15분 호출용, 중복 무시)"""
        if df.empty or len(df) < last_n:
            return
        self.archive_candles(symbol, df.tail(last_n), "15m")

    # ──────────────────────────────────────────────────────────
    # 6. Correlation Snapshots
    # ──────────────────────────────────────────────────────────
    def record_correlation_snapshot(
        self,
        symbol_returns: dict[str, pd.Series],
        lookback: int = 96,  # 96 x 15min = 24시간
    ):
        """심볼 간 상관관계 기록 (6시간마다 호출)"""
        try:
            symbols = list(symbol_returns.keys())
            if len(symbols) < 2:
                return

            now = datetime.now(timezone.utc).isoformat()
            conn = self._conn()
            pairs_recorded = 0

            for i in range(len(symbols)):
                for j in range(i + 1, len(symbols)):
                    a, b = symbols[i], symbols[j]
                    ra, rb = symbol_returns[a], symbol_returns[b]

                    min_len = min(len(ra), len(rb), lookback)
                    if min_len < 20:
                        continue

                    corr = ra.iloc[-min_len:].corr(rb.iloc[-min_len:])
                    if pd.isna(corr):
                        continue

                    conn.execute("""
                        INSERT INTO correlation_snapshots
                        (timestamp, symbol_a, symbol_b, correlation, lookback_bars, timeframe)
                        VALUES (?, ?, ?, ?, ?, '15m')
                    """, (now, a, b, float(corr), min_len))
                    pairs_recorded += 1

            conn.commit()
            conn.close()
            logger.info(f"상관관계 스냅샷: {pairs_recorded}쌍 기록")

        except Exception as e:
            logger.error(f"상관관계 스냅샷 실패: {e}")

    # ──────────────────────────────────────────────────────────
    # 7. Performance Benchmark
    # ──────────────────────────────────────────────────────────
    def record_daily_benchmark(
        self,
        strategy_return_pct: float,
        btc_return_pct: float,
        eth_return_pct: float,
        balance: float,
        total_trades: int,
        win_rate: float,
        avg_rr: float = 0,
        max_drawdown_pct: float = 0,
        volatility_regime: str = "normal",
    ):
        """일별 성과 벤치마크 기록"""
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # 간이 Sharpe (수익률 / 변동성) — 일별이라 프록시
            sharpe_proxy = strategy_return_pct / max(max_drawdown_pct, 0.01)

            conn = self._conn()
            conn.execute("""
                INSERT OR REPLACE INTO performance_benchmark
                (date, strategy_return_pct, btc_return_pct, eth_return_pct,
                 balance, total_trades, win_rate, avg_rr,
                 sharpe_proxy, max_drawdown_pct, volatility_regime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today, strategy_return_pct, btc_return_pct, eth_return_pct,
                balance, total_trades, win_rate, avg_rr,
                sharpe_proxy, max_drawdown_pct, volatility_regime,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"일별 벤치마크 기록 실패: {e}")

    # ──────────────────────────────────────────────────────────
    # 조회/분석 유틸리티
    # ──────────────────────────────────────────────────────────
    def get_signal_journal_analysis(self, days: int = 7) -> dict:
        """시그널 저널 분석 — 진입 vs 스킵 비교"""
        try:
            conn = self._conn()
            rows = conn.execute("""
                SELECT * FROM signal_journal
                WHERE timestamp >= datetime('now', ?)
                ORDER BY timestamp DESC
            """, (f"-{days} days",)).fetchall()
            conn.close()

            if not rows:
                return {"count": 0, "message": "No signals recorded yet"}

            records = [dict(r) for r in rows]
            entered = [r for r in records if r["action"] == "entered"]
            skipped = [r for r in records if r["action"] == "skipped"]

            # 스킵된 시그널의 가상 성과 분석
            skip_virtual_1h = [r["virtual_pnl_1h_pct"] for r in skipped
                               if r.get("virtual_pnl_1h_pct") is not None]
            skip_virtual_4h = [r["virtual_pnl_4h_pct"] for r in skipped
                               if r.get("virtual_pnl_4h_pct") is not None]

            # 진입한 시그널의 실제 성과
            entered_pnls = [r["pnl_pct"] for r in entered if r.get("pnl_pct") is not None]

            # 레짐별 승률
            regime_stats = {}
            for r in entered:
                regime = r.get("volatility_regime") or "unknown"
                if regime not in regime_stats:
                    regime_stats[regime] = {"count": 0, "wins": 0, "pnl": 0}
                regime_stats[regime]["count"] += 1
                if r.get("pnl", 0) > 0:
                    regime_stats[regime]["wins"] += 1
                regime_stats[regime]["pnl"] += r.get("pnl", 0)

            return {
                "total_signals": len(records),
                "entered": len(entered),
                "skipped": len(skipped),
                "entry_rate": len(entered) / len(records) * 100 if records else 0,
                "entered_avg_pnl_pct": sum(entered_pnls) / len(entered_pnls) if entered_pnls else 0,
                "skipped_virtual_1h_avg": sum(skip_virtual_1h) / len(skip_virtual_1h) if skip_virtual_1h else 0,
                "skipped_virtual_4h_avg": sum(skip_virtual_4h) / len(skip_virtual_4h) if skip_virtual_4h else 0,
                "skip_reasons": _count_by_key(skipped, "skip_reason"),
                "regime_stats": regime_stats,
                "top_skip_reasons": sorted(
                    _count_by_key(skipped, "skip_reason").items(),
                    key=lambda x: x[1], reverse=True,
                )[:10],
            }
        except Exception as e:
            logger.error(f"시그널 저널 분석 실패: {e}")
            return {"error": str(e)}

    def get_regime_analysis(self, days: int = 30) -> dict:
        """레짐 전환 분석"""
        try:
            conn = self._conn()
            rows = conn.execute("""
                SELECT * FROM regime_history
                WHERE timestamp >= datetime('now', ?)
                ORDER BY timestamp DESC
            """, (f"-{days} days",)).fetchall()
            conn.close()

            records = [dict(r) for r in rows]
            return {
                "total_changes": len(records),
                "by_type": _count_by_key(records, "regime_type"),
                "records": records[:50],
            }
        except Exception as e:
            return {"error": str(e)}

    def get_candle_archive_stats(self) -> dict:
        """캔들 아카이브 통계"""
        try:
            conn = self._conn()
            rows = conn.execute("""
                SELECT symbol, timeframe, COUNT(*) as count,
                       MIN(timestamp) as first_ts, MAX(timestamp) as last_ts
                FROM candle_archive
                GROUP BY symbol, timeframe
                ORDER BY symbol
            """).fetchall()
            conn.close()
            return {"archives": [dict(r) for r in rows]}
        except Exception as e:
            return {"error": str(e)}

    def get_correlation_analysis(self) -> dict:
        """최근 상관관계 분석"""
        try:
            conn = self._conn()
            rows = conn.execute("""
                SELECT * FROM correlation_snapshots
                WHERE timestamp = (SELECT MAX(timestamp) FROM correlation_snapshots)
                ORDER BY ABS(correlation) DESC
            """).fetchall()
            conn.close()

            records = [dict(r) for r in rows]
            high_corr = [r for r in records if abs(r["correlation"]) >= 0.7]
            return {
                "total_pairs": len(records),
                "high_correlation_pairs": high_corr,
                "records": records,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_drawdown_analysis(self, days: int = 30) -> dict:
        """드로우다운 이벤트 분석"""
        try:
            conn = self._conn()
            rows = conn.execute("""
                SELECT * FROM drawdown_events
                WHERE timestamp >= datetime('now', ?)
                ORDER BY drawdown_pct DESC
            """, (f"-{days} days",)).fetchall()
            conn.close()

            records = [dict(r) for r in rows]
            return {
                "total_events": len(records),
                "max_drawdown": max(r["drawdown_pct"] for r in records) if records else 0,
                "by_tier": _count_by_key(records, "drawdown_tier"),
                "records": records[:50],
            }
        except Exception as e:
            return {"error": str(e)}

    def get_benchmark_analysis(self, days: int = 30) -> dict:
        """성과 벤치마크 분석"""
        try:
            conn = self._conn()
            rows = conn.execute("""
                SELECT * FROM performance_benchmark
                WHERE date >= date('now', ?)
                ORDER BY date DESC
            """, (f"-{days} days",)).fetchall()
            conn.close()

            records = [dict(r) for r in rows]
            if not records:
                return {"count": 0, "message": "No benchmark data yet"}

            returns = [r["strategy_return_pct"] for r in records]
            btc_returns = [r["btc_return_pct"] for r in records if r.get("btc_return_pct")]

            return {
                "days": len(records),
                "cumulative_return": sum(returns),
                "avg_daily_return": sum(returns) / len(returns),
                "best_day": max(returns),
                "worst_day": min(returns),
                "btc_cumulative": sum(btc_returns) if btc_returns else 0,
                "alpha": sum(returns) - sum(btc_returns) if btc_returns else 0,
                "records": records,
            }
        except Exception as e:
            return {"error": str(e)}


# ──────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    """NaN/None을 None으로 변환"""
    if val is None:
        return None
    try:
        f = float(val)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _count_by_key(records: list[dict], key: str) -> dict:
    """레코드 리스트에서 특정 키의 값별 카운트"""
    counts = {}
    for r in records:
        v = r.get(key) or "unknown"
        counts[v] = counts.get(v, 0) + 1
    return counts
