#!/usr/bin/env python3
"""
암호화폐 선물 자동매매 봇 — v2.2

전략: Channel Breakout + Momentum (동적 레버리지)
거래소: Bybit USDT 무기한 선물
타임프레임: 4시간봉

v2.2 변경사항:
  - 시작 시 Bybit 기존 포지션 동기화
  - 보유 포지션 심볼 동적 모니터링
  - TP/SL 서버사이드 체결 감지 + 슬랙 알림
  - 봇 종료/크래시 슬랙 알림

실행: python main.py
"""
import sys
import os
import time
import signal
import threading
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import schedule

from config.settings import BYBIT_TESTNET
from config.indicator_params import SYMBOLS, ORDER_TYPE_CONFIG
from strategy.final_strategy import (
    generate_signals, get_current_signal, calc_atr,
    OPTIMAL_PARAMS, ENGINE_CONFIG,
)
from strategy.signal_generator import compute_indicators
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
        mode = "테스트넷" if BYBIT_TESTNET else "⚠️ 메인넷"
        logger.info("=" * 60)
        logger.info(f"암호화폐 선물 자동매매 봇 v2.2")
        logger.info(f"  모드: {mode}")
        logger.info(f"  전략: Channel Breakout + Momentum")
        logger.info(f"  레버리지: {OPTIMAL_PARAMS['base_leverage']}x ~ {OPTIMAL_PARAMS['max_leverage']}x (동적)")
        logger.info(f"  리스크: 거래당 {ENGINE_CONFIG['risk_per_trade']*100}%")
        logger.info(f"  SL: ATR {OPTIMAL_PARAMS['sl_atr_mult']}x / TP: ATR {OPTIMAL_PARAMS['tp_atr_mult']}x+")
        logger.info(f"  시장가 기준: 강도 >= {ORDER_TYPE_CONFIG['strong_signal_threshold']}")
        logger.info("=" * 60)

        # 거래소 클라이언트
        self.client = BybitClient()
        balance = self.client.get_balance()
        logger.info(f"  잔고: {balance['total']:.2f} USDT")

        # 리스크 매니저
        self.risk = RiskManager(initial_balance=balance["total"])
        self.order_mgr = OrderManager(self.client, self.risk)

        # 전략 대상 심볼 (거래량 상위 100개, 주기적 갱신)
        self.strategy_symbols = self._load_strategy_symbols()

        self.safe_mode = False
        self.running = True

        # 시작 시 기존 포지션 동기화
        self._sync_positions_from_exchange()

        # 시그널 핸들러 등록 (봇 종료 알림)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _load_strategy_symbols(self) -> list[str]:
        """거래량 상위 100개 USDT 무기한 선물 심볼 로드"""
        try:
            symbols = self.client.get_top_symbols_by_volume(limit=100)
            logger.info(f"  전략 대상: 거래량 상위 {len(symbols)}개 심볼")
            return symbols
        except Exception as e:
            logger.error(f"  상위 심볼 조회 실패, 기본 심볼 사용: {e}")
            return [SYMBOLS["primary"]] + SYMBOLS["alts"][:3]

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

    @property
    def symbols(self):
        """모니터링 대상 심볼 = 전략 심볼 + 보유 포지션 심볼 (중복 제거)"""
        position_symbols = list(self.risk.state.open_positions.keys())
        all_symbols = list(dict.fromkeys(
            self.strategy_symbols + position_symbols
        ))
        return all_symbols

    def _handle_shutdown(self, signum, frame):
        """종료 시그널 처리 (SIGTERM, SIGINT)"""
        sig_name = signal.Signals(signum).name
        logger.warning(f"종료 시그널 수신: {sig_name}")
        self.running = False

        pos_list = list(self.risk.state.open_positions.keys())
        notify("봇 종료", (
            f"종료 시그널: {sig_name}\n"
            f"시각: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"보유 포지션: {len(pos_list)}개 {pos_list}\n"
            f"⚠️ 서버사이드 TP/SL은 유지됩니다"
        ), level="error")

    def fetch_data(self, symbol: str, limit: int = 200) -> pd.DataFrame:
        """최신 OHLCV 데이터 수집"""
        try:
            klines = self.client.get_klines(symbol, interval="240", limit=limit)
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
        기존 포지션 실시간 동적 관리 (1분마다 호출)
        - 반대 시그널 → 즉시 전량 청산
        - 시그널 소멸 → 청산
        - 트레일링 SL: 수익 방향으로 SL 끌어올림
        - 부분 익절: TP 50% 도달 시 절반 청산 + SL 본전 이동
        - 약한 시그널 + 손실 → 조기 손절
        """
        pos_info = self.risk.state.open_positions.get(symbol)
        if not pos_info or pos_info.get("status") == "pending":
            return

        side = pos_info["side"]
        entry = pos_info["entry_price"]
        tp = pos_info.get("tp", 0)
        sl = pos_info.get("sl", 0)

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

        # TP 진행률
        if tp > 0 and entry > 0:
            if side == "long":
                tp_progress = (price - entry) / (tp - entry) if tp != entry else 0
            else:
                tp_progress = (entry - price) / (entry - tp) if tp != entry else 0
        else:
            tp_progress = 0

        # === 시그널 기반 관리 (4시간봉 캔들 마감 시에만 전달됨) ===
        if signal_data:
            direction = signal_data["direction"]
            strength = signal_data["strength"]
            atr = signal_data["atr"]

            # A) 반대 시그널 → 즉시 전량 청산
            if direction != 0:
                is_reverse = (direction == 1 and side == "short") or \
                             (direction == -1 and side == "long")
                if is_reverse:
                    logger.info(f"[{symbol}] 반대 시그널 (강도 {strength:.2f}) → 즉시 청산")
                    self.order_mgr.execute_exit(symbol, reason="reverse_signal")
                    return

            # B) direction==0은 "진입 시그널 없음"일 뿐, 기존 포지션 청산 사유 아님
            #    (시그널은 진입 시점에만 발생하고 다음 봉에서는 자연히 0이 됨)

            # C) 같은 방향 시그널 발생 → 동적 SL 조정
            if direction != 0 and not (
                (direction == 1 and side == "short") or
                (direction == -1 and side == "long")
            ):
                if strength >= 0.5:
                    self.order_mgr.update_dynamic_sl(symbol, price, atr, strength)

        # === 가격 기반 관리 (매 1분) ===

        # 0) 안전장치: SL이 없는 포지션에 기본 SL 설정 (급락 대비)
        if sl == 0 or (side == "long" and sl <= 0) or (side == "short" and sl <= 0):
            # ATR 2.5배 기본 SL 설정
            default_sl_pct = 0.05  # 진입가 대비 5%
            if side == "long":
                emergency_sl = self.client.round_price(symbol, entry * (1 - default_sl_pct))
                pos_idx = 1
            else:
                emergency_sl = self.client.round_price(symbol, entry * (1 + default_sl_pct))
                pos_idx = 2
            try:
                self.client.set_tp_sl(symbol, stop_loss=emergency_sl, position_idx=pos_idx)
                pos_info["sl"] = emergency_sl
                sl = emergency_sl
                logger.info(f"[{symbol}] SL 없음 → 기본 SL 설정: {emergency_sl} (진입가 대비 {default_sl_pct:.0%})")
            except Exception as e:
                logger.warning(f"[{symbol}] 기본 SL 설정 실패: {e}")

        # 0-1) 급락/급등 강제 청산: 손실 > 8%이면 SL 대기하지 않고 즉시 시장가 청산
        if unrealized_pct <= -0.08:
            logger.warning(f"[{symbol}] 급락 감지! 손실 {unrealized_pct:.1%} → 긴급 청산")
            self.order_mgr.execute_exit(symbol, reason=f"emergency_loss_{unrealized_pct:.1%}")
            return

        # 1) 트레일링 SL: 수익이 충분할 때만 활성화 (수익 > 3%)
        #    수익 방향으로 SL을 끌어올려서 이익 보호
        if unrealized_pct >= 0.03:
            if side == "long":
                highest = pos_info.get("highest_price", entry)
                if price > highest:
                    pos_info["highest_price"] = price
                # 최고가 기준으로 트레일링 (이익의 60% 보호)
                cur_highest = pos_info.get("highest_price", price)
                trail_sl = cur_highest - (cur_highest - entry) * 0.4
                trail_sl = self.client.round_price(symbol, trail_sl)
                if trail_sl > sl and trail_sl > entry and trail_sl != sl:
                    try:
                        self.client.set_tp_sl(symbol, stop_loss=trail_sl, position_idx=1)
                        pos_info["sl"] = trail_sl
                        logger.info(f"[{symbol}] 트레일링 SL 갱신: {sl} → {trail_sl} "
                                    f"(수익 {unrealized_pct:.1%})")
                    except Exception as e:
                        logger.warning(f"[{symbol}] 트레일링 SL 실패: {e}")
            else:
                lowest = pos_info.get("lowest_price", entry)
                if price < lowest or lowest >= entry:
                    pos_info["lowest_price"] = price
                cur_lowest = pos_info.get("lowest_price", price)
                trail_sl = cur_lowest + (entry - cur_lowest) * 0.4
                trail_sl = self.client.round_price(symbol, trail_sl)
                if trail_sl < entry and trail_sl != sl and (trail_sl < sl or sl == 0 or sl >= entry):
                    try:
                        self.client.set_tp_sl(symbol, stop_loss=trail_sl, position_idx=2)
                        pos_info["sl"] = trail_sl
                        logger.info(f"[{symbol}] 트레일링 SL 갱신: {sl} → {trail_sl} "
                                    f"(수익 {unrealized_pct:.1%})")
                    except Exception as e:
                        logger.warning(f"[{symbol}] 트레일링 SL 실패: {e}")

        # 2) 부분 익절: TP 50% 이상 도달 + 아직 미실행
        if tp_progress >= 0.5 and not pos_info.get("partial_tp_done"):
            logger.info(f"[{symbol}] TP 진행 {tp_progress:.0%} → 50% 부분 익절")
            self.order_mgr.execute_partial_close(
                symbol, close_ratio=0.5,
                reason=f"partial_tp (진행 {tp_progress:.0%}, 수익 {unrealized_pct:.2%})"
            )

        # 3) TP 확대: TP 80% 도달 + 강한 모멘텀 → TP를 50% 더 확대
        if tp_progress >= 0.8 and tp > 0:
            tp_extension = pos_info.get("tp_extended", False)
            if not tp_extension:
                if side == "long":
                    new_tp = tp + (tp - entry) * 0.5
                else:
                    new_tp = tp - (entry - tp) * 0.5
                new_tp = self.client.round_price(symbol, new_tp)
                pos_idx = 1 if side == "long" else 2
                try:
                    self.client.set_tp_sl(symbol, take_profit=new_tp, position_idx=pos_idx)
                    pos_info["tp"] = new_tp
                    pos_info["tp_extended"] = True
                    logger.info(f"[{symbol}] TP 확대: {tp:.2f} → {new_tp:.2f} (진행 {tp_progress:.0%})")
                except Exception as e:
                    logger.warning(f"[{symbol}] TP 확대 실패: {e}")

    def process_symbol(self, symbol: str):
        """심볼별 시그널 처리"""
        try:
            # 1. 데이터 수집 + 지표 계산
            df = self.fetch_data(symbol)
            if df.empty or len(df) < 100:
                return

            df = compute_indicators(df)

            # 2. 시그널 생성
            signal_data = get_current_signal(df)

            direction = signal_data["direction"]
            strength = signal_data["strength"]
            leverage = signal_data["leverage"]
            atr = signal_data["atr"]
            price = signal_data["close"]

            if pd.isna(atr) or atr <= 0:
                return

            # 3. 기존 포지션 동적 관리 (4시간봉 시그널 기반)
            if symbol in self.risk.state.open_positions:
                self._manage_existing_position(symbol, signal_data=signal_data)
                return  # 기존 포지션 있으면 신규 진입 안 함

            # 4. 신규 진입 (전략 심볼에만 적용)
            if symbol not in self.strategy_symbols:
                return

            if direction == 0:
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
                f"[{symbol}] {'🟢 롱' if direction == 1 else '🔴 숏'} 시그널 | "
                f"강도: {strength:.2f} | 레버리지: {leverage}x | "
                f"주문: {order_type} | "
                f"TP: {fmt(tp_price)} | SL: {fmt(sl_price)}"
            )

            # 진입 실행
            result = self.order_mgr.execute_entry(
                symbol=symbol,
                side=side,
                entry_price=price,
                atr=atr,
                tp_sl_ratio=tp_mult / sl_mult,
                leverage=leverage,
                signal_grade=f"str_{strength:.2f}",
                strategy="channel_breakout_v2",
                signal_strength=strength,
            )

            if result and result.get("order_type") == "market":
                try:
                    pos_idx = 1 if side == "long" else 2
                    self.client.set_tp_sl(symbol, take_profit=tp_price, stop_loss=sl_price, position_idx=pos_idx)
                except Exception as e:
                    logger.error(f"[{symbol}] TP/SL 설정 실패: {e}")

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
                # pending 지정가 주문은 아직 Bybit에 포지션이 없으므로 스킵
                pos_status = self.risk.state.open_positions[symbol].get("status")
                if pos_status == "pending":
                    continue
                if symbol not in live_symbols:
                    pos_info = self.risk.state.open_positions[symbol]
                    side = pos_info["side"]
                    entry_price = pos_info.get("entry_price", 0)
                    qty = pos_info.get("quantity", 0)

                    # 현재가 조회하여 대략적 PnL 추정
                    current_price = 0
                    estimated_pnl = 0
                    pnl_emoji = "❓"
                    reason = "서버사이드 청산 감지"

                    try:
                        ticker = self.client.get_ticker(symbol)
                        current_price = ticker["last_price"]

                        if side == "long":
                            estimated_pnl = (current_price - entry_price) * qty
                        else:
                            estimated_pnl = (entry_price - current_price) * qty

                        pnl_emoji = "🟢" if estimated_pnl >= 0 else "🔴"
                        reason = "TP 체결 (추정)" if estimated_pnl >= 0 else "SL 체결 (추정)"
                    except Exception:
                        pass

                    logger.info(f"[{symbol}] 서버사이드 청산 감지 — {reason}")

                    # 내부 상태 정리
                    del self.risk.state.open_positions[symbol]
                    self.risk.update_on_trade_close(estimated_pnl)

                    # DB 기록
                    record_trade(
                        symbol=symbol, side=side, action="close",
                        price=current_price,
                        quantity=qty,
                        pnl=estimated_pnl,
                        note=f"exit_reason: {reason} (server-side)",
                    )

                    # 슬랙 알림
                    from exchange.order_manager import _fmt_price
                    notify("포지션 청산 (서버사이드)", (
                        f"{pnl_emoji} {symbol}\n"
                        f"방향: {side.upper()}\n"
                        f"진입가: {_fmt_price(entry_price)}\n"
                        f"추정 PnL: {estimated_pnl:+.4f} USDT\n"
                        f"사유: {reason}"
                    ), level="trade")

            # Bybit에 있지만 내부 상태에 없는 포지션 = 외부 진입 또는 누락
            from config.indicator_params import TRADING as _TRADING
            max_pos = _TRADING["max_open_positions"]
            for p in live_positions:
                symbol = p["symbol"]
                if symbol not in self.risk.state.open_positions:
                    if len(self.risk.state.open_positions) >= max_pos:
                        logger.warning(f"[{symbol}] 외부 포지션 감지 but 포지션 한도 초과 — 추적만 함")
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
                    logger.info(f"[{symbol}] 신규 포지션 감지 (외부 진입) — "
                                f"{side.upper()} qty={p['size']}")

        except Exception as e:
            logger.error(f"포지션 청산 감지 에러: {e}", exc_info=True)

    def on_candle_close(self):
        """4시간봉 캔들 완성 시 실행"""
        now = datetime.now(timezone.utc)
        logger.info(f"\n{'━'*50}")
        logger.info(f"📊 캔들 완성 — {now.strftime('%Y-%m-%d %H:%M')} UTC")

        self.risk.reset_daily()

        if self.safe_mode:
            logger.warning("안전모드 — 매매 중단")
            return

        if self.risk.should_emergency_close():
            logger.warning("⚠️ 긴급 청산!")
            self.order_mgr.emergency_close_all()
            self.safe_mode = True
            return

        # 전략 심볼 갱신 (거래량 상위 100개)
        self.strategy_symbols = self._load_strategy_symbols()

        # 포지션 청산 감지 (TP/SL 서버사이드 체결)
        self._check_position_closures()

        # 모든 모니터링 심볼 처리 (전략 심볼 + 보유 포지션 심볼)
        for symbol in self.symbols:
            self.process_symbol(symbol)
            time.sleep(0.5)

        # 현황 출력
        balance = self.client.get_balance()
        positions = self.client.get_positions()
        open_syms = [p["symbol"] for p in positions]
        tracked = list(self.risk.state.open_positions.keys())
        logger.info(f"  잔고: {balance['total']:.2f} | "
                    f"Bybit 포지션: {open_syms} | 추적 중: {tracked}")

    def monitor(self):
        """1분마다 포지션 실시간 모니터링 + 동적 TP/SL 관리"""
        try:
            # 실제 잔고 동기화
            self._sync_balance()

            # 긴급 청산 체크 (실제 잔고 기준)
            if self.risk.should_emergency_close():
                self.order_mgr.emergency_close_all()
                self.safe_mode = True
                return

            # 서버사이드 TP/SL 체결 감지
            self._check_position_closures()

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

                        summaries.append(
                            f"{symbol} {side[0].upper()} "
                            f"pnl={pnl_pct:+.2f}% "
                            f"SL={sl} TP={tp}"
                        )

                        # 포지션 관리 실행
                        self._manage_existing_position(symbol)

                    except Exception as e:
                        logger.error(f"[{symbol}] 실시간 관리 에러: {e}")

                logger.info(f"[모니터] {len(active_positions)}개 포지션 | " +
                            " | ".join(summaries))
            else:
                # 포지션 없을 때는 5분마다만 로그
                from datetime import datetime, timezone
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
        from flask import Flask, jsonify
        app = Flask(__name__)

        @app.route("/health")
        def health():
            return jsonify({"status": "ok", "safe_mode": self.safe_mode})

        @app.route("/status")
        def status():
            balance = self.client.get_balance()
            positions = self.client.get_positions()
            return jsonify({
                "balance": balance["total"],
                "positions": len(positions),
                "tracked": list(self.risk.state.open_positions.keys()),
                "strategy_symbols": self.strategy_symbols,
                "safe_mode": self.safe_mode,
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
        logger.info("🚀 봇 시작!")

        pos_list = list(self.risk.state.open_positions.keys())
        notify("봇 시작", (
            f"Channel Breakout v2.2 | {'테스트넷' if BYBIT_TESTNET else '메인넷'}\n"
            f"모니터링: 거래량 상위 {len(self.strategy_symbols)}개 심볼\n"
            f"시장가 기준: 강도 >= {ORDER_TYPE_CONFIG['strong_signal_threshold']}\n"
            f"수수료: Maker 0.02% / Taker 0.055%\n"
            f"기존 포지션 동기화: {len(pos_list)}개 {pos_list}"
        ))

        # 4시간 마감 시각 (UTC)
        for hour in ["00:01", "04:01", "08:01", "12:01", "16:01", "20:01"]:
            schedule.every().day.at(hour).do(self.on_candle_close)

        schedule.every(1).minutes.do(self.monitor)

        # 정기 브리핑: KST 09:00 / 15:00 / 21:00 / 03:00 → UTC 00:00 / 06:00 / 12:00 / 18:00
        for hour in ["00:00", "06:00", "12:00", "18:00"]:
            schedule.every().day.at(hour).do(self.briefing)

        # 시작 시 1회 실행
        self.on_candle_close()

        consecutive_errors = 0
        max_consecutive_errors = 10

        while self.running:
            try:
                schedule.run_pending()
                time.sleep(10)
                consecutive_errors = 0
            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"에러 ({consecutive_errors}/{max_consecutive_errors}): {e}",
                             exc_info=True)

                if consecutive_errors >= max_consecutive_errors:
                    error_msg = (
                        f"연속 에러 {consecutive_errors}회 발생 — 봇 중단\n"
                        f"마지막 에러: {str(e)[:200]}\n"
                        f"시각: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                    )
                    logger.critical(error_msg)
                    notify("봇 비정상 중단", error_msg, level="error")
                    self.running = False
                    break

                time.sleep(60)

        # 정상 종료 처리
        logger.info("봇 종료")
        notify("봇 종료 완료", (
            f"시각: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"보유 포지션: {len(self.risk.state.open_positions)}개"
        ), level="warning")


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
