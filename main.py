#!/usr/bin/env python3
"""
암호화폐 선물 자동매매 봇 — v17.4

전략: Momentum Scalper v17.4 (1H 추세 + 5m 진입)
거래소: Bybit USDT 무기한 선물
타임프레임: 5분봉 (1H 리샘플링으로 상위 추세 확인)

v17.4 변경사항 (v17.3 40건 분석 — R:R 0.46, avg_loss -37% 악화 확인):
  [저가 알트 급락 방지]
  - extreme 변동성(ATR ratio > 2.0) 진입 금지
  - 진입 5분 내 손실 -2.5%+ → 즉시 시장가 청산
    (MYX/RAVE/APR/COAI 같은 5~15분 -4~7% 케이스 차단)
  [수익 잠금 SL]
  - Stage 1 본전(+수수료) SL → entry + 0.5% 수익 잠금 SL
    (160분대 0.06% 이익 패턴을 최소 0.5% 이익으로 끌어올림)
  [Stage 2 트레일링 롤백]
  - v17.3의 4.5% 활성화 → 3.5% (v17.2 복원)
  - 보호 비율 20/30/45/60 → 25/35/50/65 (v17.2 복원)
  [v17.3 유지]
  - 시간 기반 본전 SL 160분/0.3% 미만
  - 150분+ 손실 1.5%+ 타임아웃
  [v17.2 유지]
  - 진입 조건 ADX 25, RSI Long 30-48/Short 52-70, min_score 2.3, BB 선택
  - 분석 주기 90초, 포지션 모니터 1초, max_open_positions 3

실행: python main.py
"""
import sys
import os
import time
import signal
import threading
import traceback
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import schedule

from config.settings import BYBIT_TESTNET, PAPER_TRADING, PAPER_INITIAL_BALANCE
from config.indicator_params import SYMBOLS, ORDER_TYPE_CONFIG
from strategy.final_strategy import (
    generate_signals, get_current_signal, calc_atr,
    OPTIMAL_PARAMS, ENGINE_CONFIG,
)
from exchange.bybit_client import BybitClient
from exchange.order_manager import OrderManager
from strategy.risk_manager import RiskManager
from strategy.position_manager import ExitCondition
from data.storage import record_trade, update_daily_summary
from utils.logger import setup_logger
from utils.notifier import notify
from bot.position_monitor import PositionMonitor
from bot.health_server import start_health_server
from data.collector import DataCollector

logger = setup_logger("main")


class TradingBot:
    """메인 트레이딩 봇"""

    def __init__(self):
        if PAPER_TRADING:
            self.mode_str = "📋 페이퍼 트레이딩"
        elif BYBIT_TESTNET:
            self.mode_str = "테스트넷"
        else:
            self.mode_str = "메인넷"
        mode = self.mode_str
        logger.info("=" * 60)
        logger.info(f"암호화폐 선물 자동매매 봇 v17.4")
        logger.info(f"  모드: {mode}")
        logger.info(f"  전략: Momentum Scalper v17.4 (1H 추세 + 5m 진입)")
        logger.info(f"  타임프레임: 5분봉 진입 (1H 리샘플링 추세 확인)")
        logger.info(f"  TP {OPTIMAL_PARAMS['tp_atr_mult']}x / SL {OPTIMAL_PARAMS['sl_atr_mult']}x / {OPTIMAL_PARAMS['leverage']}x lev")
        logger.info(f"  ADX >= {OPTIMAL_PARAMS['adx_min']} | RSI Long {OPTIMAL_PARAMS['rsi_pull_long_lo']}-{OPTIMAL_PARAMS['rsi_pull_long_hi']} | RSI Short {OPTIMAL_PARAMS['rsi_pull_short_lo']}-{OPTIMAL_PARAMS['rsi_pull_short_hi']}")
        logger.info(f"  리스크: 거래당 {ENGINE_CONFIG['risk_per_trade']*100}%")
        logger.info("=" * 60)

        # 거래소 클라이언트
        self.paper_mode = PAPER_TRADING
        if self.paper_mode:
            from exchange.paper_client import PaperBybitClient
            self.client = PaperBybitClient(
                initial_balance=PAPER_INITIAL_BALANCE,
                testnet=False,  # 실제 시장 데이터 사용
            )
            logger.info(f"  📋 페이퍼 트레이딩 모드 (가상 잔고: ${PAPER_INITIAL_BALANCE:.2f})")
        else:
            self.client = BybitClient()
        balance = self.client.get_balance()
        logger.info(f"  잔고: {balance['total']:.2f} USDT")

        # 리스크 매니저
        self.risk = RiskManager(initial_balance=balance["total"])
        self.order_mgr = OrderManager(self.client, self.risk)

        # 전략 대상 심볼 (거래량 상위 30개, 주기적 갱신)
        self.strategy_symbols = self._load_strategy_symbols()

        self.safe_mode = False
        self.running = True

        # v15.0 0: 동일 심볼 재진입 쿨다운 (15분)
        self._symbol_cooldown: dict[str, datetime] = {}
        self.COOLDOWN_MINUTES = 15

        # 데이터 수집기 초기화
        self.collector = DataCollector()
        logger.info("  데이터 수집기 초기화 완료 (collector.db)")

        # 시작 시 기존 포지션 동기화
        if self.paper_mode:
            self._sync_paper_positions()
        else:
            self._sync_positions_from_exchange()

        # PositionMonitor 초기화
        self.pos_monitor = PositionMonitor(
            client=self.client,
            risk=self.risk,
            order_mgr=self.order_mgr,
            symbol_cooldown=self._symbol_cooldown,
            fetch_data_fn=self.fetch_data,
        )

        # 시그널 핸들러 등록 (봇 종료 알림)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _load_strategy_symbols(self) -> list[str]:
        """거래량 상위 30개 USDT 무기한 선물 심볼 로드"""
        try:
            symbols = self.client.get_top_symbols_by_volume(limit=30)
            logger.info(f"  전략 대상: 거래량 상위 {len(symbols)}개 심볼")
            return symbols
        except Exception as e:
            logger.error(f"  상위 심볼 조회 실패, 기본 심볼 사용: {e}")
            return [SYMBOLS["primary"]] + SYMBOLS["alts"][:3]

    def _sync_paper_positions(self):
        """페이퍼 모드: DB에서 복원된 포지션을 risk_manager에 동기화"""
        try:
            paper_positions = self.client._paper_positions
            if not paper_positions:
                logger.info("  페이퍼 포지션 없음")
                return

            from config.indicator_params import TRADING as _TRADING
            max_pos = _TRADING["max_open_positions"]

            # 최신 포지션 우선: opened_at 기준 정렬 (최근 진입 우선 보존)
            sorted_positions = sorted(
                paper_positions.items(),
                key=lambda x: x[1].get("opened_at", ""),
                reverse=True
            )

            # 초과분 제거 (오래된 것부터)
            if len(sorted_positions) > max_pos:
                keep = {s for s, _ in sorted_positions[:max_pos]}
                for symbol in list(paper_positions.keys()):
                    if symbol not in keep:
                        logger.warning(f"  [{symbol}] 최대 포지션({max_pos}) 초과 → 페이퍼 포지션 제거")
                        del paper_positions[symbol]

            synced = 0
            for symbol, p in paper_positions.items():
                if synced >= max_pos:
                    break

                side = "long" if p["side"] == "Buy" else "short"
                self.risk.state.open_positions[symbol] = {
                    "side": side,
                    "entry_price": p["entry_price"],
                    "quantity": p["size"],
                    "original_qty": p["size"],
                    "tp": p.get("take_profit", 0),
                    "sl": p.get("stop_loss", 0),
                    "status": "active",
                    "partial_tp_done": False,
                }
                synced += 1
                logger.info(
                    f"  페이퍼 포지션 동기화: {symbol} {side.upper()} "
                    f"qty={p['size']} entry={p['entry_price']}"
                )

            # 초과분 제거 후 저장
            self.client._save_state()
            logger.info(f"  페이퍼 포지션 {synced}개 동기화 완료 (최대 {max_pos}개)")

        except Exception as e:
            logger.error(f"  페이퍼 포지션 동기화 실패: {e}", exc_info=True)

    def _sync_positions_from_exchange(self):
        """Bybit에서 기존 포지션을 가져와 내부 상태 동기화"""
        try:
            positions = self.client.get_positions()
            if not positions:
                logger.info("  기존 포지션 없음")
                return

            for p in positions:
                symbol = p["symbol"]
                side = "long" if p["side"] == "Buy" else "short"
                self.risk.state.open_positions[symbol] = {
                    "side": side,
                    "entry_price": p["entry_price"],
                    "quantity": p["size"],
                    "original_qty": p["size"],
                    "tp": p.get("take_profit", 0),
                    "sl": p.get("stop_loss", 0),
                    "status": "active",
                    "partial_tp_done": False,
                    "highest_price": p["entry_price"],
                    "lowest_price": p["entry_price"],
                }
                logger.info(
                    f"  기존 포지션 동기화: {symbol} {side.upper()} "
                    f"qty={p['size']} entry={p['entry_price']:.6f} "
                    f"pnl={p['unrealized_pnl']:.4f}"
                )

            logger.info(f"  총 {len(positions)}개 기존 포지션 동기화 완료")

            # v15.0: 동기화된 포지션 텔레그램 알림
            if positions:
                pos_lines = []
                for p in positions:
                    side = "🟢 LONG" if p["side"] == "Buy" else "🔴 SHORT"
                    pnl = p.get("unrealized_pnl", 0)
                    pos_lines.append(f"{side} {p['symbol']} pnl:{pnl:+.4f}")
                notify("📋 기존 포지션 동기화", "\n".join(pos_lines), level="trade")

        except Exception as e:
            logger.error(f"  포지션 동기화 실패: {e}", exc_info=True)

        # 미체결 지정가 주문 정리
        self._cancel_orphan_orders()

    def _cancel_orphan_orders(self):
        """시작 시 미체결 지정가 주문 정리 — TP/SL 없이 체결되는 것 방지

        보존 대상 (취소하지 않음):
          - reduceOnly=True: 기존 포지션의 TP/SL 청산 주문
          - stopOrderType 존재: 조건부 TP/SL 주문
        취소 대상:
          - 일반 지정가 진입 주문 (reduceOnly=False, stopOrderType 없음)
        """
        try:
            open_orders = self.client.get_open_orders()
            if not open_orders:
                logger.info("  미체결 주문 없음")
                return

            cancelled = 0
            kept = 0
            for order in open_orders:
                symbol = order.get("symbol", "")
                order_id = order.get("orderId", "")
                side = order.get("side", "")
                price = order.get("price", "")
                order_type = order.get("orderType", "")

                # TP/SL 조건부 주문 유지
                stop_type = order.get("stopOrderType", "")
                if stop_type:
                    kept += 1
                    logger.info(f"  [{symbol}] TP/SL 주문 유지: {stop_type} {side} @ {price}")
                    continue

                # reduceOnly 청산 주문 유지 (포지션 보호용)
                reduce_only = order.get("reduceOnly", False)
                if reduce_only:
                    kept += 1
                    logger.info(f"  [{symbol}] 청산 주문 유지: {order_type} {side} @ {price} (reduceOnly)")
                    continue

                # 일반 진입 주문 → 취소
                try:
                    self.client.cancel_order(symbol, order_id)
                    cancelled += 1
                    logger.info(f"  [{symbol}] 미체결 진입 주문 취소: {order_type} {side} @ {price} (ID: {order_id})")
                except Exception as e:
                    logger.error(f"  [{symbol}] 주문 취소 실패: {e}")

            logger.info(f"  미체결 주문 정리: {cancelled}건 취소 / {kept}건 유지 (TP/SL)")

        except Exception as e:
            logger.error(f"  미체결 주문 정리 실패: {e}", exc_info=True)

    @property
    def symbols(self):
        """모니터링 대상 심볼 = 전략 심볼 + 보유 포지션 심볼 (중복 제거)"""
        position_symbols = list(self.risk.state.open_positions.keys())
        all_symbols = list(dict.fromkeys(
            self.strategy_symbols + position_symbols
        ))
        return all_symbols

    def _handle_shutdown(self, signum, frame):
        """종료 시그널 처리 (SIGTERM, SIGINT) — 배포 시 Slack 폭주 방지"""
        sig_name = signal.Signals(signum).name
        logger.warning(f"종료 시그널 수신: {sig_name}")
        self.running = False

        # 배포 시 종료+시작 연속 전송으로 인한 Slack 429 방지
        from utils.notifier import suppress_shutdown_messages
        suppress_shutdown_messages()

        pos_list = list(self.risk.state.open_positions.keys())
        logger.warning(
            f"봇 종료 — {sig_name} | 포지션: {len(pos_list)}개 {pos_list} | "
            f"서버사이드 TP/SL 유지됨"
        )

    def fetch_data(self, symbol: str, limit: int = 500) -> pd.DataFrame:
        """v15.0: 5분봉 OHLCV 데이터 수집 (1H 리샘플링)"""
        try:
            klines = self.client.get_klines(symbol, interval="5", limit=limit)
            if not klines:
                return pd.DataFrame()

            rows = []
            for k in klines:
                rows.append({
                    "timestamp": pd.to_datetime(int(k[0]), unit="ms"),
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "volume": float(k[5]),
                })
            df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"[{symbol}] 데이터 수집 실패: {e}")
            return pd.DataFrame()


    def process_symbol(self, symbol: str):
        """심볼별 시그널 처리"""
        try:
            # 1. 데이터 수집 (5분봉 500개 — 1H 리샘플링에 충분)
            df = self.fetch_data(symbol)
            if df.empty or len(df) < 100:
                return

            # 2. 시그널 생성 (Dual-Mode Adaptive v6.0)
            signal_data = get_current_signal(df)

            direction = signal_data["direction"]
            strength = signal_data["strength"]
            leverage = signal_data["leverage"]
            atr = signal_data["atr"]
            price = signal_data["close"]

            if pd.isna(atr) or atr <= 0:
                return

            # ── v9.2 데이터 수집: 변동성 레짐 계산 ──
            vol_regime = "normal"
            atr_ratio = 1.0
            try:
                atr_series = calc_atr(df, 14)
                atr_avg = atr_series.rolling(96).mean().iloc[-1]  # 24시간 평균 ATR
                if not pd.isna(atr_avg) and atr_avg > 0:
                    atr_ratio = atr / atr_avg
                    vol_regime = self.risk.get_volatility_regime(atr, atr_avg)
            except Exception:
                pass

            # ── v9.2 데이터 수집: 시장 스냅샷 (전략 심볼만) ──
            if symbol in self.strategy_symbols:
                try:
                    self.collector.record_market_snapshot(
                        symbol=symbol, df=df,
                        signal_data=signal_data,
                        volatility_regime=vol_regime,
                        atr_ratio=atr_ratio,
                    )
                    # 최근 캔들 아카이브 (중복 무시)
                    self.collector.archive_recent_candles(symbol, df, last_n=3)
                    # 레짐 전환 감지
                    h4_trend = signal_data.get("mode", "neutral")
                    self.collector.check_regime_change(
                        symbol=symbol,
                        volatility_regime=vol_regime,
                        h4_trend=h4_trend,
                        h4_adx=0,
                        atr_ratio=atr_ratio,
                    )
                except Exception as e:
                    logger.debug(f"[{symbol}] 데이터 수집 경고: {e}")

            # 3. 기존 포지션 동적 관리 (5분봉 시그널 기반)
            if symbol in self.risk.state.open_positions:
                self.pos_monitor.manage_existing_position(symbol, signal_data=signal_data)

                # v7.0: 피라미딩 비활성화 (소액 계좌에서 레버리지 폭발 위험)
                return

            # 4. 신규 진입 (전략 심볼에만 적용)
            if symbol not in self.strategy_symbols:
                return

            if direction == 0:
                return

            # v17.4: extreme 변동성 레짐 진입 금지
            # 저가/고변동성 알트 5~15분 -4~7% 급락 방지
            if vol_regime == "extreme":
                logger.info(f"[{symbol}] extreme 변동성 (ATR ratio > 2.0) → 진입 스킵")
                try:
                    from data.storage import record_skipped_signal
                    record_skipped_signal(
                        symbol=symbol,
                        side="long" if direction == 1 else "short",
                        signal_strength=strength,
                        skip_reason="extreme_volatility",
                        price=price,
                        atr=atr,
                        rsi=signal_data.get("rsi", 0),
                        market_regime=vol_regime,
                        leverage=leverage,
                    )
                except Exception:
                    pass
                return

            # v7.5: 동일 심볼 재진입 쿨다운 (15분)
            cooldown_until = self._symbol_cooldown.get(symbol)
            if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
                remaining = (cooldown_until - datetime.now(timezone.utc)).seconds // 60
                logger.info(f"[{symbol}] 재진입 쿨다운 중 ({remaining}분 남음) → 진입 스킵")
                # 쿨다운 스킵도 시그널 저널에 기록
                self.collector.record_signal(
                    symbol=symbol, side="long" if direction == 1 else "short",
                    signal_strength=strength, signal_score=strength * 4,
                    action="skipped", price=price, rsi=signal_data.get("rsi", 0),
                    atr=atr, volatility_regime=vol_regime,
                    volume_ratio=signal_data.get("vol_ratio", 0),
                    skip_reason=f"cooldown_{remaining}min",
                )
                return

            # v15.0: min_score 완화에 맞춰 MIN_SIGNAL_STRENGTH도 완화
            MIN_SIGNAL_STRENGTH = 0.30
            if strength < MIN_SIGNAL_STRENGTH:
                logger.info(f"[{symbol}] 시그널 강도 부족 ({strength:.2f} < {MIN_SIGNAL_STRENGTH}) → 진입 스킵")
                try:
                    from data.storage import record_skipped_signal
                    record_skipped_signal(
                        symbol=symbol,
                        side="long" if direction == 1 else "short",
                        signal_strength=strength,
                        skip_reason=f"strength_below_{MIN_SIGNAL_STRENGTH}",
                        price=price,
                        atr=atr,
                        rsi=signal_data.get("rsi", 0),
                        market_regime=signal_data.get("mode", ""),
                        leverage=leverage,
                    )
                    # v9.2: 시그널 저널에도 기록 (가격 추적용)
                    self.collector.record_signal(
                        symbol=symbol, side="long" if direction == 1 else "short",
                        signal_strength=strength, signal_score=strength * 4,
                        action="skipped", price=price,
                        rsi=signal_data.get("rsi", 0), atr=atr,
                        volatility_regime=vol_regime,
                        volume_ratio=signal_data.get("vol_ratio", 0),
                        skip_reason=f"strength_below_{MIN_SIGNAL_STRENGTH}",
                    )
                except Exception:
                    pass
                return

            side = "long" if direction == 1 else "short"

            # TP/SL 계산 (v15.0 1: 최소 거리 보장)
            tp_mult = signal_data["tp_atr_mult"]
            sl_mult = signal_data["sl_atr_mult"]

            tp_dist = atr * tp_mult
            sl_dist = atr * sl_mult

            # v17.0: 최소 TP/SL 거리 보장 (전략 파라미터와 동기화)
            MIN_SL_PCT = OPTIMAL_PARAMS.get("min_sl_pct", 0.015)  # 최소 SL 거리
            MIN_TP_PCT = OPTIMAL_PARAMS.get("min_tp_pct", 0.020)  # 최소 TP 거리
            min_sl_dist = price * MIN_SL_PCT
            min_tp_dist = price * MIN_TP_PCT

            if sl_dist < min_sl_dist:
                logger.info(f"[{symbol}] SL 거리 보정: {sl_dist/price*100:.2f}% → {MIN_SL_PCT*100}% (ATR 너무 작음)")
                sl_dist = min_sl_dist
            if tp_dist < min_tp_dist:
                logger.info(f"[{symbol}] TP 거리 보정: {tp_dist/price*100:.2f}% → {MIN_TP_PCT*100}% (ATR 너무 작음)")
                tp_dist = min_tp_dist

            if side == "long":
                tp_price = price + tp_dist
                sl_price = price - sl_dist
            else:
                tp_price = price - tp_dist
                sl_price = price + sl_dist

            # 주문 타입 결정 로그
            order_type = "시장가" if strength >= ORDER_TYPE_CONFIG["strong_signal_threshold"] else "지정가"
            fmt = lambda p: f"{p:.2f}" if p >= 1 else f"{p:.6f}"
            logger.info(
                f"[{symbol}] {'롱' if direction == 1 else '숏'} 시그널 | "
                f"강도: {strength:.2f} | 레버리지: {leverage}x | "
                f"주문: {order_type} | "
                f"TP: {fmt(tp_price)} | SL: {fmt(sl_price)}"
            )

            # 페이퍼 모드: 시그널 정보 저장 (DB 기록용)
            if self.paper_mode:
                self.client.set_signal_info(symbol, {
                    "market_regime": signal_data.get("mode", ""),
                    "signal_strength": strength,
                    "tp_atr_mult": tp_mult,
                    "sl_atr_mult": sl_mult,
                    "atr": atr,
                    "rsi": signal_data.get("rsi", 0),
                    "vol_ratio": signal_data.get("vol_ratio", 0),
                    "h4_confirmed": signal_data.get("h4_confirmed", False),
                })

            # 리스크 체크 (진입 가능 여부)
            can_enter, risk_reason = self.risk.can_open_position(symbol)
            if not can_enter:
                logger.info(f"[{symbol}] 리스크 제한: {risk_reason}")
                self.collector.record_signal(
                    symbol=symbol, side=side,
                    signal_strength=strength, signal_score=strength * 4,
                    action="skipped", price=price,
                    rsi=signal_data.get("rsi", 0), atr=atr,
                    volatility_regime=vol_regime,
                    volume_ratio=signal_data.get("vol_ratio", 0),
                    skip_reason=f"risk:{risk_reason[:50]}",
                )
                return

            # v9.2: 시그널 저널에 진입 기록
            signal_journal_id = self.collector.record_signal(
                symbol=symbol, side=side,
                signal_strength=strength, signal_score=strength * 4,
                action="entered", price=price,
                rsi=signal_data.get("rsi", 0), atr=atr,
                volatility_regime=vol_regime,
                volume_ratio=signal_data.get("vol_ratio", 0),
                entry_price=price,
            )

            # 진입 실행 (v7.0: TP/SL 직접 전달 — ExitCondition 재계산 버그 방지)
            result = self.order_mgr.execute_entry(
                symbol=symbol,
                side=side,
                entry_price=price,
                atr=atr,
                tp_sl_ratio=tp_mult / sl_mult,
                leverage=leverage,
                signal_grade=f"str_{strength:.2f}",
                strategy="dual_mode_adaptive_v7",
                signal_strength=strength,
                tp_price=tp_price,
                sl_price=sl_price,
            )

            # 진입 성공 시 시그널 ID + 변동성 레짐을 포지션에 연결
            if result and symbol in self.risk.state.open_positions:
                self.risk.state.open_positions[symbol]["signal_journal_id"] = signal_journal_id
                # v10.1: 변동성 레짐 저장 (트레일링 SL 적응에 사용)
                self.risk.state.open_positions[symbol]["vol_regime"] = signal_data.get("vol_regime", vol_regime)

            # v9.2: 드로우다운 이벤트 체크
            dd_factor = self.risk.get_drawdown_factor()
            if dd_factor < 1.0:
                self.collector.record_drawdown_event(self.risk.state, dd_factor)

        except Exception as e:
            logger.error(f"[{symbol}] 처리 에러: {e}", exc_info=True)


    def on_candle_close(self):
        """시그널 분석 틱 (v17.2: 90초 주기)"""
        now = datetime.now(timezone.utc)
        logger.info(f"\n{'='*50}")
        logger.info(f"분석 틱 -- {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        self.risk.reset_daily()

        if self.safe_mode:
            logger.warning("안전모드 -- 매매 중단")
            return

        if self.risk.should_emergency_close():
            logger.warning("긴급 청산!")
            self.order_mgr.emergency_close_all()
            self.safe_mode = True
            return

        # ★ 일봉 전환 시간대 벗어나면 보호 플래그 리셋
        if not self.risk.is_daily_candle_transition():
            for sym, pos_info in self.risk.state.open_positions.items():
                if pos_info.get("daily_candle_protected"):
                    pos_info["daily_candle_protected"] = False

        # ★ 일봉 전환 시간대 로그
        if self.risk.is_daily_candle_transition():
            logger.info("⚡ 일봉 전환 보호 구간 — 신규 진입 차단, 포지션 SL 타이트닝 활성")

        # 전략 심볼 갱신 (매시 정각에만, 15분마다 갱신하면 API 부하)
        if now.minute < 5:
            self.strategy_symbols = self._load_strategy_symbols()

        # 포지션 청산 감지 (TP/SL 서버사이드 체결) — 페이퍼 모드에서는 check_tp_sl()이 대신 처리
        if not self.paper_mode:
            self.pos_monitor.check_position_closures()

        # 모든 모니터링 심볼 처리 (전략 심볼 + 보유 포지션 심볼)
        signal_count = 0
        for symbol in self.symbols:
            self.process_symbol(symbol)
            time.sleep(0.15)

        # v15.0 1: 시그널 발생 현황 로그 (디버깅용)
        open_pos = len(self.risk.state.open_positions)
        logger.info(f"  분석 완료: {len(self.symbols)}개 심볼 | 보유 {open_pos}개 포지션")

        # 현황 출력
        balance = self.client.get_balance()
        positions = self.client.get_positions()
        open_syms = [p["symbol"] for p in positions]
        tracked = list(self.risk.state.open_positions.keys())
        paper_tag = " [PAPER]" if self.paper_mode else ""
        logger.info(f"  잔고: {balance['total']:.2f}{paper_tag} | "
                    f"포지션: {open_syms} | 추적 중: {tracked}")

        # v9.2: 스킵된 시그널 가격 추적 (15분마다)
        try:
            def _get_price(sym):
                t = self.client.get_ticker(sym)
                return t["last_price"] if t else None
            self.collector.track_skipped_signal_prices(_get_price)
        except Exception as e:
            logger.debug(f"스킵 시그널 추적 경고: {e}")

        # 페이퍼 모드: 6시간마다 요약 출력
        if self.paper_mode:
            now = datetime.now(timezone.utc)
            if now.hour % 6 == 0 and now.minute < 5:
                logger.info(f"\n{self.client.get_paper_summary()}")

    def monitor(self):
        """1분마다 포지션 실시간 모니터링 + 동적 TP/SL 관리"""
        try:
            # 실제 잔고 동기화
            self.pos_monitor.sync_balance()

            # 페이퍼 모드: TP/SL + 지정가 체결 체크
            if self.paper_mode:
                self.client.check_tp_sl()
                self.client.check_limit_fills()

            # 긴급 청산 체크 (실제 잔고 기준)
            if self.risk.should_emergency_close():
                self.order_mgr.emergency_close_all()
                self.safe_mode = True
                return

            # 서버사이드 TP/SL 체결 감지 (페이퍼 모드에서는 위에서 처리)
            if not self.paper_mode:
                self.pos_monitor.check_position_closures()

            # ★ 일봉 전환 보호: KST 09:00 전후 SL 타이트닝
            is_candle_transition = self.risk.is_daily_candle_transition()
            is_pre_candle = self.risk.is_pre_daily_candle()

            # 보유 포지션 실시간 관리 (트레일링 SL, 부분 익절, TP 확대)
            active_positions = {
                s: p for s, p in self.risk.state.open_positions.items()
                if p.get("status") != "pending"
            }

            if active_positions:
                summaries = []
                for symbol, pos_info in active_positions.items():
                    try:
                        ticker = self.client.get_ticker(symbol)
                        price = ticker["last_price"]
                        entry = pos_info["entry_price"]
                        side = pos_info["side"]
                        sl = pos_info.get("sl", 0)
                        tp = pos_info.get("tp", 0)

                        if side == "long":
                            pnl_pct = (price - entry) / entry * 100
                        else:
                            pnl_pct = (entry - price) / entry * 100

                        pyr = "P" if pos_info.get("pyramided") else ""
                        summaries.append(
                            f"{symbol} {side[0].upper()}{pyr} "
                            f"pnl={pnl_pct:+.2f}% "
                            f"SL={sl} TP={tp}"
                        )

                        # ★ 일봉 전환 시간대 SL 타이트닝
                        if (is_candle_transition or is_pre_candle) and not pos_info.get("daily_candle_protected"):
                            self.pos_monitor.protect_position_for_daily_candle(symbol, pos_info, price, entry, side, sl, pnl_pct)

                        # 포지션 관리 실행
                        self.pos_monitor.manage_existing_position(symbol)

                    except Exception as e:
                        logger.error(f"[{symbol}] 실시간 관리 에러: {e}")

                transition_tag = " ⚡일봉전환보호" if is_candle_transition else ""
                logger.info(f"[모니터] {len(active_positions)}개 포지션{transition_tag} | " +
                            " | ".join(summaries))
            else:
                # 포지션 없을 때는 5분마다만 로그
                now = datetime.now(timezone.utc)
                if now.minute % 5 == 0:
                    pending = sum(1 for p in self.risk.state.open_positions.values()
                                  if p.get("status") == "pending")
                    msg = f"[모니터] 활성 포지션 없음"
                    if pending:
                        msg += f" (pending: {pending}개)"
                    logger.info(msg)

        except Exception as e:
            logger.error(f"모니터링 에러: {e}", exc_info=True)

    def briefing(self):
        """정기 브리핑 (KST 09:00/15:00/21:00/03:00)"""
        from utils.briefing import send_briefing
        send_briefing(self.client, self.risk)

    def collect_correlation_data(self):
        """6시간마다 심볼 간 상관관계 수집"""
        try:
            symbol_returns = {}
            for symbol in self.strategy_symbols[:10]:  # 상위 10개만
                df = self.fetch_data(symbol, limit=200)
                if not df.empty and len(df) > 50:
                    symbol_returns[symbol] = df["close"].pct_change().dropna()
                time.sleep(0.2)

            if len(symbol_returns) >= 2:
                self.collector.record_correlation_snapshot(symbol_returns)
        except Exception as e:
            logger.error(f"상관관계 수집 실패: {e}")

    def collect_daily_benchmark(self):
        """일별 성과 벤치마크 기록 (매일 UTC 00:00)"""
        try:
            balance = self.client.get_balance()
            current_bal = balance["total"]
            initial_bal = self.risk.state.daily_start_balance

            strategy_return = ((current_bal - initial_bal) / initial_bal * 100
                               if initial_bal > 0 else 0)

            # BTC/ETH 수익률 (벤치마크)
            btc_ret = eth_ret = 0
            try:
                btc_df = self.fetch_data("BTCUSDT", limit=100)
                if not btc_df.empty and len(btc_df) >= 96:
                    btc_ret = (btc_df["close"].iloc[-1] - btc_df["close"].iloc[-96]) / btc_df["close"].iloc[-96] * 100
                eth_df = self.fetch_data("ETHUSDT", limit=100)
                if not eth_df.empty and len(eth_df) >= 96:
                    eth_ret = (eth_df["close"].iloc[-1] - eth_df["close"].iloc[-96]) / eth_df["close"].iloc[-96] * 100
            except Exception:
                pass

            # 당일 거래 통계
            from data.storage import get_trade_stats_since
            stats = get_trade_stats_since(24)

            dd_pct = 0
            if self.risk.state.peak_balance > 0:
                dd_pct = (self.risk.state.peak_balance - current_bal) / self.risk.state.peak_balance * 100

            self.collector.record_daily_benchmark(
                strategy_return_pct=strategy_return,
                btc_return_pct=btc_ret,
                eth_return_pct=eth_ret,
                balance=current_bal,
                total_trades=stats.get("count", 0),
                win_rate=stats.get("win_rate", 0),
                max_drawdown_pct=dd_pct,
            )
            logger.info(f"[벤치마크] 전략: {strategy_return:+.2f}% | BTC: {btc_ret:+.2f}% | ETH: {eth_ret:+.2f}%")
        except Exception as e:
            logger.error(f"일별 벤치마크 수집 실패: {e}")


    def run(self):
        """메인 루프"""
        start_health_server(self)
        logger.info("봇 시작!")

        pos_list = list(self.risk.state.open_positions.keys())
        notify("봇 시작", (
            f"Momentum Scalper v17.4 | {self.mode_str}\n"
            f"타임프레임: 5분봉 (1H 크로스검증)\n"
            f"모니터링: 거래량 상위 {len(self.strategy_symbols)}개 심볼\n"
            f"분석 주기: 90초 / 포지션 모니터: 1초\n"
            f"시장가 기준: 강도 >= {ORDER_TYPE_CONFIG['strong_signal_threshold']}\n"
            f"수수료: Maker 0.02% / Taker 0.055%\n"
            f"기존 포지션 동기화: {len(pos_list)}개 {pos_list}"
        ))

        # v17.2: 90초마다 시그널 분석 (shared-cpu 한계 내 최대 실시간성)
        # 이전: 5분마다 (12 cycles/h) → v17.2: 90초 (40 cycles/h)
        # CPU: 10초/cycle × 40 = 400s/h = 11.1% (baseline 12.5% 이내)
        schedule.every(90).seconds.do(self.on_candle_close)

        # v17.2: 포지션 모니터 1초 (was 2초) — 메인루프 동일 주기
        schedule.every(1).seconds.do(self.monitor)

        # 정기 브리핑: KST 09:00 / 15:00 / 21:00 / 03:00 -> UTC 00:00 / 06:00 / 12:00 / 18:00
        for hour in ["00:00", "06:00", "12:00", "18:00"]:
            schedule.every().day.at(hour).do(self.briefing)

        # v9.2: 데이터 수집 스케줄
        for hour in ["00:30", "06:30", "12:30", "18:30"]:
            schedule.every().day.at(hour).do(self.collect_correlation_data)
        schedule.every().day.at("23:55").do(self.collect_daily_benchmark)

        # 시작 시 1회 실행
        self.on_candle_close()

        consecutive_errors = 0
        max_consecutive_errors = 10

        while self.running:
            try:
                schedule.run_pending()
                time.sleep(1)  # v15.0 1: 1초 메인루프 (최대 실시간성)
                consecutive_errors = 0
            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"에러 ({consecutive_errors}/{max_consecutive_errors}): {e}",
                             exc_info=True)

                if consecutive_errors >= max_consecutive_errors:
                    error_msg = (
                        f"연속 에러 {consecutive_errors}회 발생 -- 봇 중단\n"
                        f"마지막 에러: {str(e)[:200]}\n"
                        f"시각: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                    )
                    logger.critical(error_msg)
                    notify("봇 비정상 중단", error_msg, level="error")
                    self.running = False
                    break

                time.sleep(60)

        # 정상 종료 처리 (Slack 알림 없음 — 배포 시 429 방지)
        logger.info(
            f"봇 종료 완료 | 포지션: {len(self.risk.state.open_positions)}개 | "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )


if __name__ == "__main__":
    try:
        bot = TradingBot()
        bot.run()
    except Exception as e:
        error_detail = traceback.format_exc()
        logger.critical(f"봇 초기화 실패: {e}\n{error_detail}")
        try:
            notify("봇 시작 실패", (
                f"에러: {str(e)[:300]}\n"
                f"시각: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
            ), level="error")
        except Exception:
            pass
        sys.exit(1)
