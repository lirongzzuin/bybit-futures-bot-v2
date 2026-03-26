#!/usr/bin/env python3
"""
암호화폐 선물 자동매매 봇 — v4.0

전략: Multi-TF Scalp (15분봉 스캘핑 + 4H 크로스 검증)
거래소: Bybit USDT 무기한 선물
타임프레임: 15분봉 (4H 리샘플링으로 상위 추세 확인)

v4.0 변경사항:
  - 전략 전면 재설계: 15분 스캘핑 + 4H 트렌드 교차검증
  - 타임프레임: 4H → 15분 (4H는 내부 리샘플링)
  - 스케줄: 15분마다 캔들 분석 (매시 :01, :16, :31, :46)
  - 레버리지: 10~25x (신호 확실도 기반)
  - TP/SL: 1.5x/1.0x ATR (타이트 스캘핑)
  - 모니터링: 거래량 상위 30개 심볼
  - 리스크: 0.3%/거래, 최대 5 동시 포지션, 일일 8% 한도

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
        logger.info(f"암호화폐 선물 자동매매 봇 v9.0")
        logger.info(f"  모드: {mode}")
        logger.info(f"  전략: Pullback-in-Trend v9.0 (4H 트렌드 + 15m 풀백)")
        logger.info(f"  타임프레임: 15분봉 (4H 리샘플링)")
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

        # v8.0: 동일 심볼 재진입 쿨다운 (45분 — 과다 거래 방지)
        self._symbol_cooldown: dict[str, datetime] = {}
        self.COOLDOWN_MINUTES = 45

        # 시작 시 기존 포지션 동기화
        if self.paper_mode:
            self._sync_paper_positions()
        else:
            self._sync_positions_from_exchange()

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
        """최신 15분 OHLCV 데이터 수집 (4H 리샘플링을 위해 충분한 양 필요)"""
        try:
            klines = self.client.get_klines(symbol, interval="15", limit=limit)
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

    def _manage_existing_position(self, symbol: str, signal_data: dict = None):
        """
        포지션 동적 관리 v6.0 — 수익 극대화 시스템

        단계별 관리:
          Stage 0: 안전장치 (SL 없으면 설정, 급락 -8% 긴급 청산)
          Stage 1: 본전 보호 (수익 1%+) — SL을 본전으로 이동
          Stage 2: 트레일링 SL (수익 2%+) — 단계별 비율 강화
          Stage 3: 부분 익절 (TP 50%) — 30% 청산
          Stage 4: TP 확대 (TP 80%) — 무한 반복 확대
          Stage 5: 추가 부분 익절 (TP 200%) — 또 30% 청산
          Stage 6: 최종 잔여분 — 매우 타이트한 트레일링으로 보호
        """
        pos_info = self.risk.state.open_positions.get(symbol)
        if not pos_info or pos_info.get("status") == "pending":
            return

        side = pos_info["side"]
        entry = pos_info["entry_price"]
        tp = pos_info.get("tp", 0)
        sl = pos_info.get("sl", 0)
        pos_idx = 1 if side == "long" else 2

        # 현재가 조회
        try:
            ticker = self.client.get_ticker(symbol)
            price = ticker["last_price"]
        except Exception:
            return
        if price <= 0:
            return

        # 수익률 계산
        if side == "long":
            unrealized_pct = (price - entry) / entry
        else:
            unrealized_pct = (entry - price) / entry

        # TP 진행률 (원래 TP 기준)
        original_tp_dist = pos_info.get("original_tp_dist", 0)
        if original_tp_dist == 0 and tp > 0:
            original_tp_dist = abs(tp - entry)
            pos_info["original_tp_dist"] = original_tp_dist

        if original_tp_dist > 0:
            if side == "long":
                tp_progress = (price - entry) / original_tp_dist
            else:
                tp_progress = (entry - price) / original_tp_dist
        else:
            tp_progress = 0

        # 최고/최저가 추적 (항상)
        if side == "long":
            if price > pos_info.get("highest_price", entry):
                pos_info["highest_price"] = price
        else:
            cur_low = pos_info.get("lowest_price", entry)
            if price < cur_low or cur_low >= entry:
                pos_info["lowest_price"] = price

        # === 시그널 기반 관리 ===
        is_reverse = False
        if signal_data:
            direction = signal_data["direction"]
            strength = signal_data["strength"]
            atr = signal_data["atr"]

            # A) 반대 시그널 → 강한 시그널만 청산 (약한 시그널은 무시)
            if direction != 0:
                is_reverse = (direction == 1 and side == "short") or \
                             (direction == -1 and side == "long")
                if is_reverse and strength >= 0.65:
                    logger.info(f"[{symbol}] 강한 반대 시그널 (강도 {strength:.2f}) → 청산")
                    self.order_mgr.execute_exit(symbol, reason="reverse_signal")
                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=self.COOLDOWN_MINUTES)
                    return
                elif is_reverse:
                    logger.debug(f"[{symbol}] 약한 반대 시그널 (강도 {strength:.2f}) → 무시")

            # B) 시그널 강도 기반 TP/SL 실시간 조정 (v7.3)
            if direction != 0 and not is_reverse and atr > 0:
                self._adjust_tp_sl_by_strength(
                    symbol, side, price, entry, tp, sl, atr,
                    strength, unrealized_pct, pos_info, pos_idx
                )

            # C) 모멘텀 반전 감지: 수익 중 + RSI 극단에서 '꺾이기 시작'할 때만 청산
            #    단순히 RSI > 80이면 청산하지 않음 (RSI 80 이상에서 계속 오를 수 있음)
            #    RSI > 75 AND RSI가 직전 2봉 대비 하락 시작 → 상승 모멘텀 반전 확인
            #    RSI < 25 AND RSI가 직전 2봉 대비 상승 시작 → 하락 모멘텀 반전 확인
            # C) RSI 반전: 수익 3%+ AND RSI 극단 AND 3봉 연속 꺾임 (더 보수적)
            if unrealized_pct > 0.03:
                try:
                    df_check = self.fetch_data(symbol, limit=30)
                    if not df_check.empty and len(df_check) >= 6:
                        from strategy.final_strategy import calc_rsi
                        rsi_series = calc_rsi(df_check["close"], 14)
                        if len(rsi_series) >= 4:
                            r_now = rsi_series.iloc[-1]
                            r_prev = rsi_series.iloc[-2]
                            r_prev2 = rsi_series.iloc[-3]
                            r_prev3 = rsi_series.iloc[-4]
                            if not pd.isna(r_now) and not pd.isna(r_prev) and not pd.isna(r_prev2) and not pd.isna(r_prev3):
                                # 롱: RSI > 78 + 3봉 연속 하락 (확실한 꺾임)
                                if (side == "long" and r_now > 78 and
                                        r_now < r_prev < r_prev2 < r_prev3):
                                    logger.info(
                                        f"[{symbol}] RSI 반전 확인! "
                                        f"{r_prev3:.0f}→{r_prev2:.0f}→{r_prev:.0f}→{r_now:.0f} "
                                        f"수익 {unrealized_pct:.1%} → 시장가 익절")
                                    self.order_mgr.execute_exit(
                                        symbol, reason=f"rsi_reversal_{r_now:.0f}")
                                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=self.COOLDOWN_MINUTES)
                                    return
                                # 숏: RSI < 22 + 3봉 연속 상승 (확실한 꺾임)
                                elif (side == "short" and r_now < 22 and
                                        r_now > r_prev > r_prev2 > r_prev3):
                                    logger.info(
                                        f"[{symbol}] RSI 반전 확인! "
                                        f"{r_prev3:.0f}→{r_prev2:.0f}→{r_prev:.0f}→{r_now:.0f} "
                                        f"수익 {unrealized_pct:.1%} → 시장가 익절")
                                    self.order_mgr.execute_exit(
                                        symbol, reason=f"rsi_reversal_{r_now:.0f}")
                                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=self.COOLDOWN_MINUTES)
                                    return
                except Exception:
                    pass

            # D) ATR 수축 감지: 변동성 급감 = 추세 종료 신호 → SL 타이트닝 (수익 5%+ 이상만)
            if unrealized_pct > 0.05 and atr > 0:
                try:
                    df_atr = self.fetch_data(symbol, limit=50)
                    if not df_atr.empty and len(df_atr) >= 30:
                        from strategy.final_strategy import calc_atr as _calc_atr
                        atr_series = _calc_atr(df_atr, 14)
                        recent_atr = atr_series.iloc[-1]
                        avg_atr = atr_series.iloc[-20:].mean()
                        if not pd.isna(recent_atr) and not pd.isna(avg_atr) and avg_atr > 0:
                            atr_ratio = recent_atr / avg_atr
                            if atr_ratio < 0.5:  # ATR이 평균의 50% 미만 = 변동성 급감 (was 60%)
                                # SL을 현재 수익의 85%로 타이트닝
                                if side == "long":
                                    tight_sl = entry + (price - entry) * 0.85
                                    tight_sl = self.client.round_price(symbol, tight_sl)
                                    if tight_sl > sl and tight_sl > entry:
                                        self.client.set_tp_sl(symbol, stop_loss=tight_sl, position_idx=pos_idx)
                                        pos_info["sl"] = tight_sl
                                        logger.info(f"[{symbol}] ATR 수축! ({atr_ratio:.2f}) "
                                                    f"SL 타이트닝 → {tight_sl} (수익 85% 보호)")
                                else:
                                    tight_sl = entry - (entry - price) * 0.85
                                    tight_sl = self.client.round_price(symbol, tight_sl)
                                    if (tight_sl < sl or sl >= entry) and tight_sl < entry:
                                        self.client.set_tp_sl(symbol, stop_loss=tight_sl, position_idx=pos_idx)
                                        pos_info["sl"] = tight_sl
                                        logger.info(f"[{symbol}] ATR 수축! ({atr_ratio:.2f}) "
                                                    f"SL 타이트닝 → {tight_sl} (수익 85% 보호)")
                except Exception:
                    pass

        # === Stage 0: 안전장치 ===
        if sl == 0 or (side == "long" and sl <= 0) or (side == "short" and sl <= 0):
            default_sl_pct = 0.05
            if side == "long":
                emergency_sl = self.client.round_price(symbol, entry * (1 - default_sl_pct))
            else:
                emergency_sl = self.client.round_price(symbol, entry * (1 + default_sl_pct))
            try:
                self.client.set_tp_sl(symbol, stop_loss=emergency_sl, position_idx=pos_idx)
                pos_info["sl"] = emergency_sl
                sl = emergency_sl
                logger.info(f"[{symbol}] SL 없음 → 기본 SL 설정: {emergency_sl}")
            except Exception as e:
                logger.warning(f"[{symbol}] 기본 SL 실패: {e}")

        # 급락 긴급 청산: -5% (v7.6: was -8% → 대형 손실 방지)
        if unrealized_pct <= -0.05:
            logger.warning(f"[{symbol}] 급락! 손실 {unrealized_pct:.1%} → 긴급 청산")
            self.order_mgr.execute_exit(symbol, reason=f"emergency_{unrealized_pct:.1%}")
            self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=self.COOLDOWN_MINUTES)
            return

        # v7.6: 시간 기반 SL 타이트닝 — 데이터: 1-2시간 체류 = WR 36%
        # 60분 이상 보유 + 수익 없음 → SL 타이트닝으로 손실 축소
        entry_time_str = pos_info.get("entry_time", "")
        if entry_time_str:
            try:
                entry_time = datetime.fromisoformat(entry_time_str)
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
                hold_minutes = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60

                # v8.0: 120분+ 보유 + 손실 1%+ → 시장가 손절 (완화: 회복 기회 부여)
                if hold_minutes >= 120 and unrealized_pct < -0.01:
                    logger.info(f"[{symbol}] 시간 초과 ({hold_minutes:.0f}분) + 손실 {unrealized_pct:.1%} → 타임아웃 청산")
                    self.order_mgr.execute_exit(symbol, reason=f"timeout_{hold_minutes:.0f}m_{unrealized_pct:.1%}")
                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=self.COOLDOWN_MINUTES)
                    return

                # 60분+ 보유 + 본전 근처 → SL을 본전+수수료로 타이트닝
                if hold_minutes >= 60 and unrealized_pct > 0 and unrealized_pct < 0.01:
                    fee_offset = entry * 0.00075
                    if side == "long":
                        time_sl = self.client.round_price(symbol, entry + fee_offset)
                        if time_sl > sl:
                            try:
                                self.client.set_tp_sl(symbol, stop_loss=time_sl, position_idx=pos_idx)
                                pos_info["sl"] = time_sl
                                logger.info(f"[{symbol}] 시간 경과 ({hold_minutes:.0f}분) → 본전 SL 강제")
                            except Exception:
                                pass
                    else:
                        time_sl = self.client.round_price(symbol, entry - fee_offset)
                        if time_sl < sl or sl >= entry:
                            try:
                                self.client.set_tp_sl(symbol, stop_loss=time_sl, position_idx=pos_idx)
                                pos_info["sl"] = time_sl
                                logger.info(f"[{symbol}] 시간 경과 ({hold_minutes:.0f}분) → 본전 SL 강제")
                            except Exception:
                                pass
            except Exception:
                pass

        # === Stage 1: 본전 보호 (수익 0.8%+) — 수수료 반영 ===
        # v8.0: 0.8%로 하향 — 조기 본전 보호로 손실 전환 방지
        # 본전 SL = 진입가 + 왕복 수수료분 (순손실 제로 보장)
        if unrealized_pct >= 0.008 and not pos_info.get("breakeven_set"):
            fee_offset = entry * 0.00075  # 왕복 수수료 0.075%
            if side == "long":
                be_sl = self.client.round_price(symbol, entry + fee_offset)
            else:
                be_sl = self.client.round_price(symbol, entry - fee_offset)

            if side == "long" and (sl < be_sl or sl == 0):
                try:
                    self.client.set_tp_sl(symbol, stop_loss=be_sl, position_idx=pos_idx)
                    pos_info["sl"] = be_sl
                    pos_info["breakeven_set"] = True
                    sl = be_sl
                    logger.info(f"[{symbol}] 본전 SL 설정 — 수수료 반영 (수익 {unrealized_pct:.1%})")
                except Exception:
                    pass
            elif side == "short" and (sl > be_sl or sl == 0):
                try:
                    self.client.set_tp_sl(symbol, stop_loss=be_sl, position_idx=pos_idx)
                    pos_info["sl"] = be_sl
                    pos_info["breakeven_set"] = True
                    sl = be_sl
                    logger.info(f"[{symbol}] 본전 SL 설정 — 수수료 반영 (수익 {unrealized_pct:.1%})")
                except Exception:
                    pass

        # === Stage 2: 단계별 트레일링 SL (v7.4 강화 — MFE 누수 방지) ===
        if unrealized_pct >= 0.015:
            # v7.4: 더 공격적인 수익 보호 (MFE 대비 실현 수익 괴리 해소)
            if unrealized_pct >= 0.10:
                protect_ratio = 0.85  # 10%+ 수익 → 85% 보호 (was 80%)
            elif unrealized_pct >= 0.05:
                protect_ratio = 0.75  # 5%+ → 75% 보호 (was 65%)
            elif unrealized_pct >= 0.03:
                protect_ratio = 0.60  # 3%+ → 60% 보호 (was 50%)
            elif unrealized_pct >= 0.02:
                protect_ratio = 0.50  # 2%+ → 50% 보호 (was 35%)
            else:
                protect_ratio = 0.40  # 1.5%+ → 40% 보호 (신규)

            if side == "long":
                cur_high = pos_info.get("highest_price", price)
                trail_sl = entry + (cur_high - entry) * protect_ratio
                trail_sl = self.client.round_price(symbol, trail_sl)
                if trail_sl > sl and trail_sl > entry:
                    try:
                        self.client.set_tp_sl(symbol, stop_loss=trail_sl, position_idx=pos_idx)
                        pos_info["sl"] = trail_sl
                        logger.info(f"[{symbol}] 트레일링 SL: {sl} → {trail_sl} "
                                    f"(수익 {unrealized_pct:.1%}, 보호 {protect_ratio:.0%})")
                    except Exception:
                        pass
            else:
                cur_low = pos_info.get("lowest_price", price)
                trail_sl = entry - (entry - cur_low) * protect_ratio
                trail_sl = self.client.round_price(symbol, trail_sl)
                if (trail_sl < sl or sl >= entry) and trail_sl < entry:
                    try:
                        self.client.set_tp_sl(symbol, stop_loss=trail_sl, position_idx=pos_idx)
                        pos_info["sl"] = trail_sl
                        logger.info(f"[{symbol}] 트레일링 SL: {sl} → {trail_sl} "
                                    f"(수익 {unrealized_pct:.1%}, 보호 {protect_ratio:.0%})")
                    except Exception:
                        pass

        # === Stage 2.5 제거 (v7.5) — TP 축소로 조기 부분 익절 불필요 ===

        # === Stage 3: 1차 부분 익절 (TP 60% 도달, 30% 청산) ===
        if tp_progress >= 0.6 and not pos_info.get("partial_1_done"):
            logger.info(f"[{symbol}] TP 진행 {tp_progress:.0%} → 1차 부분 익절 (30%)")
            self.order_mgr.execute_partial_close(
                symbol, close_ratio=0.3,
                reason=f"partial_1 (진행 {tp_progress:.0%}, 수익 {unrealized_pct:.1%})"
            )
            pos_info["partial_1_done"] = True

        # === Stage 4: TP 무한 확대 (TP 80% 도달할 때마다) ===
        if tp_progress >= 0.8 and tp > 0:
            extend_count = pos_info.get("tp_extend_count", 0)
            last_extend_progress = pos_info.get("last_extend_progress", 0)

            # 마지막 확대 이후 또 80% 이상 도달했으면 재확대
            if tp_progress >= last_extend_progress + 0.8 or extend_count == 0:
                if side == "long":
                    new_tp = tp + original_tp_dist * 0.5
                else:
                    new_tp = tp - original_tp_dist * 0.5
                new_tp = self.client.round_price(symbol, new_tp)
                try:
                    self.client.set_tp_sl(symbol, take_profit=new_tp, position_idx=pos_idx)
                    old_tp = tp
                    pos_info["tp"] = new_tp
                    pos_info["tp_extend_count"] = extend_count + 1
                    pos_info["last_extend_progress"] = tp_progress
                    logger.info(f"[{symbol}] TP 확대 #{extend_count+1}: "
                                f"{old_tp:.6f} → {new_tp:.6f} "
                                f"(진행 {tp_progress:.0%}, 수익 {unrealized_pct:.1%})")
                except Exception as e:
                    logger.warning(f"[{symbol}] TP 확대 실패: {e}")

        # === Stage 5: 2차 부분 익절 (원래 TP의 200% 도달, 추가 30% 청산) ===
        if tp_progress >= 2.0 and not pos_info.get("partial_2_done"):
            logger.info(f"[{symbol}] TP 200% 초과! → 2차 부분 익절 (30%)")
            self.order_mgr.execute_partial_close(
                symbol, close_ratio=0.3,
                reason=f"partial_2 (진행 {tp_progress:.0%}, 수익 {unrealized_pct:.1%})"
            )
            pos_info["partial_2_done"] = True

    def _adjust_tp_sl_by_strength(
        self, symbol: str, side: str, price: float, entry: float,
        tp: float, sl: float, atr: float, strength: float,
        unrealized_pct: float, pos_info: dict, pos_idx: int
    ):
        """
        v7.6: 동적 SL 타이트닝 (TP 이동 제거)

        실거래 데이터 분석 결과:
        - TP를 이동시키면 체결률 하락 → TP는 진입 시 설정 그대로 유지
        - SL은 수익 중일 때만 올려서 수익 보호 (내리지 않음)
        - 약한 시그널 + 수익 중 → 수익의 70% 보호 SL
        """
        from data.storage import record_tp_sl_adjustment

        # 최소 5분 간격
        last_adjust = pos_info.get("last_strength_adjust_time", "")
        now_str = datetime.now(timezone.utc).isoformat()
        if last_adjust:
            try:
                last_dt = datetime.fromisoformat(last_adjust)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - last_dt).total_seconds() < 300:
                    return
            except Exception:
                pass

        # v7.6: TP 이동 제거 — 데이터: TP를 움직이면 체결률 하락

        # === SL 타이트닝 (수익 중일 때만, 항상 올리기만) ===
        if unrealized_pct <= 0.005:
            return  # 수익 없으면 조정 불필요

        sl_reason = ""
        new_sl = None

        # 수익 중 + 약한 시그널 → 수익의 70% 보호
        if strength < 0.7 and unrealized_pct > 0.01:
            if side == "long":
                tight_sl = entry + (price - entry) * 0.7
                tight_sl = self.client.round_price(symbol, tight_sl)
                if tight_sl > sl and tight_sl > entry:
                    new_sl = tight_sl
                    sl_reason = "weak_signal+tighten_profit"
            else:
                tight_sl = entry - (entry - price) * 0.7
                tight_sl = self.client.round_price(symbol, tight_sl)
                if (tight_sl < sl or sl >= entry) and tight_sl < entry:
                    new_sl = tight_sl
                    sl_reason = "weak_signal+tighten_profit"

        # 수익 중 + 강한 시그널 → SL을 올리되 여유 있게 (수익의 50% 보호)
        elif strength >= 0.8 and unrealized_pct > 0.015:
            if side == "long":
                tight_sl = entry + (price - entry) * 0.5
                tight_sl = self.client.round_price(symbol, tight_sl)
                if tight_sl > sl and tight_sl > entry:
                    new_sl = tight_sl
                    sl_reason = "strong_signal+protect_profit"
            else:
                tight_sl = entry - (entry - price) * 0.5
                tight_sl = self.client.round_price(symbol, tight_sl)
                if (tight_sl < sl or sl >= entry) and tight_sl < entry:
                    new_sl = tight_sl
                    sl_reason = "strong_signal+protect_profit"

        if new_sl is not None:
            try:
                self.client.set_tp_sl(symbol, stop_loss=new_sl, position_idx=pos_idx)
                old_sl = sl
                pos_info["sl"] = new_sl
                pos_info["last_strength_adjust_time"] = now_str
                pos_info["last_signal_strength"] = strength
                logger.info(
                    f"[{symbol}] SL 타이트닝 (강도 {strength:.2f}): "
                    f"{old_sl:.6f} → {new_sl:.6f} | {sl_reason}"
                )
                record_tp_sl_adjustment(
                    symbol=symbol, side=side, adjust_type="sl",
                    old_value=old_sl, new_value=new_sl,
                    reason=sl_reason, signal_strength=strength,
                    pnl_pct=unrealized_pct * 100, price_at_adjust=price,
                    entry_price=entry,
                )
            except Exception as e:
                logger.warning(f"[{symbol}] SL 조정 실패: {e}")

    def _protect_position_for_daily_candle(self, symbol: str, pos_info: dict,
                                              price: float, entry: float,
                                              side: str, current_sl: float,
                                              pnl_pct: float):
        """
        일봉 전환(KST 09:00) 전후 포지션 보호

        매일 9시 KST에 발생하는 패턴:
        - 일봉 시가 기준 스톱헌팅 (롱/숏 양방향 급등락)
        - 대량 청산 캐스케이드
        - 펀딩비 정산

        보호 전략:
        1. 수익 중인 포지션: SL을 수익의 70% 이상으로 타이트닝 → 수익 보호
        2. 손실 중인 포지션: SL을 더 타이트하게 (추가 손실 방지)
        3. 본전 근처: 본전 SL 강제 설정
        """
        from config.indicator_params import DAILY_CANDLE_PROTECTION
        pos_idx = 1 if side == "long" else 2
        protect_ratio = DAILY_CANDLE_PROTECTION.get("tighten_sl_ratio", 0.7)

        try:
            if pnl_pct > 0.5:
                # 수익 중: 수익의 70% 보호 (기존 SL보다 타이트해야만 업데이트)
                if side == "long":
                    protected_sl = entry + (price - entry) * protect_ratio
                    protected_sl = self.client.round_price(symbol, protected_sl)
                    if protected_sl > current_sl and protected_sl > entry:
                        self.client.set_tp_sl(symbol, stop_loss=protected_sl, position_idx=pos_idx)
                        pos_info["sl"] = protected_sl
                        pos_info["daily_candle_protected"] = True
                        logger.info(f"[{symbol}] ⚡ 일봉전환 보호: SL {current_sl} → {protected_sl} "
                                    f"(수익 {pnl_pct:.1f}%의 {protect_ratio:.0%} 보호)")
                else:
                    protected_sl = entry - (entry - price) * protect_ratio
                    protected_sl = self.client.round_price(symbol, protected_sl)
                    if (protected_sl < current_sl or current_sl >= entry) and protected_sl < entry:
                        self.client.set_tp_sl(symbol, stop_loss=protected_sl, position_idx=pos_idx)
                        pos_info["sl"] = protected_sl
                        pos_info["daily_candle_protected"] = True
                        logger.info(f"[{symbol}] ⚡ 일봉전환 보호: SL {current_sl} → {protected_sl} "
                                    f"(수익 {pnl_pct:.1f}%의 {protect_ratio:.0%} 보호)")

            elif pnl_pct > -0.5:
                # 본전 근처 (-0.5% ~ +0.5%): 본전 SL 강제
                be_sl = self.client.round_price(symbol, entry)
                need_update = False
                if side == "long" and (current_sl < entry or current_sl == 0):
                    need_update = True
                elif side == "short" and (current_sl > entry or current_sl == 0):
                    need_update = True

                if need_update:
                    self.client.set_tp_sl(symbol, stop_loss=be_sl, position_idx=pos_idx)
                    pos_info["sl"] = be_sl
                    pos_info["daily_candle_protected"] = True
                    logger.info(f"[{symbol}] ⚡ 일봉전환 보호: 본전 SL 설정 ({be_sl}) "
                                f"(PnL {pnl_pct:.1f}%)")

            else:
                # 손실 중: 긴급 SL 타이트닝 (-5% → -4%로 줄이기)
                if side == "long":
                    tight_sl = self.client.round_price(symbol, entry * (1 - 0.04))
                    if tight_sl > current_sl or current_sl == 0:
                        self.client.set_tp_sl(symbol, stop_loss=tight_sl, position_idx=pos_idx)
                        pos_info["sl"] = tight_sl
                        pos_info["daily_candle_protected"] = True
                        logger.info(f"[{symbol}] ⚡ 일봉전환 보호: 손실 SL 타이트닝 → {tight_sl} "
                                    f"(PnL {pnl_pct:.1f}%)")
                else:
                    tight_sl = self.client.round_price(symbol, entry * (1 + 0.04))
                    if (tight_sl < current_sl or current_sl == 0):
                        self.client.set_tp_sl(symbol, stop_loss=tight_sl, position_idx=pos_idx)
                        pos_info["sl"] = tight_sl
                        pos_info["daily_candle_protected"] = True
                        logger.info(f"[{symbol}] ⚡ 일봉전환 보호: 손실 SL 타이트닝 → {tight_sl} "
                                    f"(PnL {pnl_pct:.1f}%)")

        except Exception as e:
            logger.error(f"[{symbol}] 일봉전환 보호 실패: {e}")

    def process_symbol(self, symbol: str):
        """심볼별 시그널 처리"""
        try:
            # 1. 데이터 수집 (15분봉 500개 — 4H 리샘플링에 충분)
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

            # 3. 기존 포지션 동적 관리 (15분봉 시그널 기반)
            if symbol in self.risk.state.open_positions:
                self._manage_existing_position(symbol, signal_data=signal_data)

                # v7.0: 피라미딩 비활성화 (소액 계좌에서 레버리지 폭발 위험)
                return

            # 4. 신규 진입 (전략 심볼에만 적용)
            if symbol not in self.strategy_symbols:
                return

            if direction == 0:
                return

            # v7.5: 동일 심볼 재진입 쿨다운 (15분)
            cooldown_until = self._symbol_cooldown.get(symbol)
            if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
                remaining = (cooldown_until - datetime.now(timezone.utc)).seconds // 60
                logger.info(f"[{symbol}] 재진입 쿨다운 중 ({remaining}분 남음) → 진입 스킵")
                return

            # v9.0: Pullback 전략 — score 2.0+ 통과 (전략 자체가 엄격)
            MIN_SIGNAL_STRENGTH = 0.50
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
                except Exception:
                    pass
                return

            side = "long" if direction == 1 else "short"

            # TP/SL 계산
            tp_mult = signal_data["tp_atr_mult"]
            sl_mult = signal_data["sl_atr_mult"]

            if side == "long":
                tp_price = price + atr * tp_mult
                sl_price = price - atr * sl_mult
            else:
                tp_price = price - atr * tp_mult
                sl_price = price + atr * sl_mult

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

        except Exception as e:
            logger.error(f"[{symbol}] 처리 에러: {e}", exc_info=True)

    def _sync_balance(self):
        """실제 Bybit 잔고를 리스크 매니저에 동기화"""
        try:
            balance = self.client.get_balance()
            self.risk.state.current_balance = balance["total"]
        except Exception as e:
            logger.error(f"잔고 동기화 실패: {e}")

    def _check_position_closures(self):
        """
        Bybit 실제 포지션과 내부 상태를 비교하여
        서버사이드 TP/SL 체결(청산)을 감지하고 알림 전송
        """
        try:
            live_positions = self.client.get_positions()
            live_symbols = {p["symbol"] for p in live_positions}

            # 내부 상태에는 있지만 Bybit에 없는 포지션 = 청산됨
            tracked_symbols = list(self.risk.state.open_positions.keys())
            for symbol in tracked_symbols:
                pos_status = self.risk.state.open_positions[symbol].get("status")
                if pos_status == "pending":
                    continue
                if symbol not in live_symbols:
                    pos_info = self.risk.state.open_positions[symbol]
                    side = pos_info["side"]
                    entry_price = pos_info.get("entry_price", 0)
                    qty = pos_info.get("quantity", 0)

                    current_price = 0
                    estimated_pnl = 0
                    pnl_emoji = "?"
                    reason = "서버사이드 청산 감지"

                    try:
                        ticker = self.client.get_ticker(symbol)
                        current_price = ticker["last_price"]

                        if side == "long":
                            estimated_pnl = (current_price - entry_price) * qty
                        else:
                            estimated_pnl = (entry_price - current_price) * qty

                        pnl_emoji = "✅" if estimated_pnl >= 0 else "❌"
                        reason = "TP 체결 (추정)" if estimated_pnl >= 0 else "SL 체결 (추정)"
                    except Exception:
                        pass

                    logger.info(f"[{symbol}] 서버사이드 청산 감지 -- {reason}")

                    # 내부 상태 정리
                    del self.risk.state.open_positions[symbol]
                    self.risk.update_on_trade_close(estimated_pnl)

                    # v7.5: 재진입 쿨다운 설정
                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=self.COOLDOWN_MINUTES)

                    # DB 기록
                    record_trade(
                        symbol=symbol, side=side, action="close",
                        price=current_price,
                        quantity=qty,
                        pnl=estimated_pnl,
                        note=f"exit_reason: {reason} (server-side)",
                    )

                    from exchange.order_manager import _fmt_price
                    if estimated_pnl >= 0:
                        title = "🎯 익절 체결"
                    else:
                        title = "🛑 손절 체결"
                    side_emoji = "🟢" if side == "long" else "🔴"
                    notify(title, (
                        f"{pnl_emoji} <code>{symbol}</code> {side_emoji} {side.upper()}\n"
                        f"💵 PnL: <b>{estimated_pnl:+.4f} USDT</b>\n"
                        f"📋 {reason}"
                    ), level="trade")

            # Bybit에 있지만 내부 상태에 없는 포지션 = 외부 진입 또는 누락
            from config.indicator_params import TRADING as _TRADING
            max_pos = _TRADING["max_open_positions"]
            for p in live_positions:
                symbol = p["symbol"]
                if symbol not in self.risk.state.open_positions:
                    if len(self.risk.state.open_positions) >= max_pos:
                        logger.warning(f"[{symbol}] 외부 포지션 감지 but 포지션 한도 초과 -- 추적만 함")
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
                        "entry_time": datetime.now(timezone.utc).isoformat(),
                    }
                    logger.info(f"[{symbol}] 신규 포지션 감지 (외부 진입) -- "
                                f"{side.upper()} qty={p['size']}")

        except Exception as e:
            logger.error(f"포지션 청산 감지 에러: {e}", exc_info=True)

    def on_candle_close(self):
        """15분봉 캔들 완성 시 실행"""
        now = datetime.now(timezone.utc)
        logger.info(f"\n{'='*50}")
        logger.info(f"15min 캔들 완성 -- {now.strftime('%Y-%m-%d %H:%M')} UTC")

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
            self._check_position_closures()

        # 모든 모니터링 심볼 처리 (전략 심볼 + 보유 포지션 심볼)
        for symbol in self.symbols:
            self.process_symbol(symbol)
            time.sleep(0.3)

        # 현황 출력
        balance = self.client.get_balance()
        positions = self.client.get_positions()
        open_syms = [p["symbol"] for p in positions]
        tracked = list(self.risk.state.open_positions.keys())
        paper_tag = " [PAPER]" if self.paper_mode else ""
        logger.info(f"  잔고: {balance['total']:.2f}{paper_tag} | "
                    f"포지션: {open_syms} | 추적 중: {tracked}")

        # 페이퍼 모드: 6시간마다 요약 출력
        if self.paper_mode:
            now = datetime.now(timezone.utc)
            if now.hour % 6 == 0 and now.minute < 5:
                logger.info(f"\n{self.client.get_paper_summary()}")

    def monitor(self):
        """1분마다 포지션 실시간 모니터링 + 동적 TP/SL 관리"""
        try:
            # 실제 잔고 동기화
            self._sync_balance()

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
                self._check_position_closures()

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
                            self._protect_position_for_daily_candle(symbol, pos_info, price, entry, side, sl, pnl_pct)

                        # 포지션 관리 실행
                        self._manage_existing_position(symbol)

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

    def _start_health_server(self):
        """fly.io 헬스체크용 간단 HTTP 서버 (별도 스레드)"""
        from flask import Flask, jsonify, request
        app = Flask(__name__)

        @app.route("/health")
        def health():
            return jsonify({"status": "ok", "safe_mode": self.safe_mode})

        @app.route("/status")
        def status():
            balance = self.client.get_balance()
            positions = self.client.get_positions()
            data = {
                "balance": balance["total"],
                "positions": len(positions),
                "tracked": list(self.risk.state.open_positions.keys()),
                "strategy_symbols": self.strategy_symbols,
                "safe_mode": self.safe_mode,
                "paper_mode": self.paper_mode,
            }
            if self.paper_mode:
                data["paper_summary"] = self.client.get_paper_summary()
            return jsonify(data)

        @app.route("/paper-analysis")
        def paper_analysis():
            from data.storage import get_paper_trade_analysis
            hours = int(request.args.get("hours", 168))
            return jsonify(get_paper_trade_analysis(hours))

        @app.route("/tp-sl-adjustments")
        def tp_sl_adjustments():
            from data.storage import get_tp_sl_adjustment_analysis
            hours = int(request.args.get("hours", 168))
            return jsonify(get_tp_sl_adjustment_analysis(hours))

        @app.route("/post-exit")
        def post_exit():
            from data.storage import get_post_exit_analysis
            hours = int(request.args.get("hours", 168))
            return jsonify(get_post_exit_analysis(hours))

        @app.route("/skipped-signals")
        def skipped_signals():
            from data.storage import get_skipped_signals
            hours = int(request.args.get("hours", 168))
            signals = get_skipped_signals(hours)
            # 요약 통계
            by_reason = {}
            by_symbol = {}
            for s in signals:
                reason = s.get("skip_reason", "unknown")
                sym = s.get("symbol", "unknown")
                by_reason[reason] = by_reason.get(reason, 0) + 1
                by_symbol[sym] = by_symbol.get(sym, 0) + 1
            return jsonify({
                "total_skipped": len(signals),
                "by_reason": by_reason,
                "by_symbol": by_symbol,
                "signals": signals[:50],  # 최근 50개만
            })

        port = int(os.getenv("WEBHOOK_PORT", "5000"))
        t = threading.Thread(
            target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
            daemon=True,
        )
        t.start()
        logger.info(f"헬스체크 서버 시작 (포트 {port})")

    def run(self):
        """메인 루프"""
        self._start_health_server()
        logger.info("봇 시작!")

        pos_list = list(self.risk.state.open_positions.keys())
        notify("봇 시작", (
            f"Dual-Mode Adaptive v7.6 | {self.mode_str}\n"
            f"타임프레임: 15분봉 (4H 크로스검증)\n"
            f"모니터링: 거래량 상위 {len(self.strategy_symbols)}개 심볼\n"
            f"시장가 기준: 강도 >= {ORDER_TYPE_CONFIG['strong_signal_threshold']}\n"
            f"수수료: Maker 0.02% / Taker 0.055%\n"
            f"기존 포지션 동기화: {len(pos_list)}개 {pos_list}"
        ))

        # 15분 마감 시각 (매시 :01, :16, :31, :46)
        for minute in ["01", "16", "31", "46"]:
            schedule.every().hour.at(f":{minute}").do(self.on_candle_close)

        schedule.every(30).seconds.do(self.monitor)

        # 정기 브리핑: KST 09:00 / 15:00 / 21:00 / 03:00 -> UTC 00:00 / 06:00 / 12:00 / 18:00
        for hour in ["00:00", "06:00", "12:00", "18:00"]:
            schedule.every().day.at(hour).do(self.briefing)

        # 시작 시 1회 실행
        self.on_candle_close()

        consecutive_errors = 0
        max_consecutive_errors = 10

        while self.running:
            try:
                schedule.run_pending()
                time.sleep(5)
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
