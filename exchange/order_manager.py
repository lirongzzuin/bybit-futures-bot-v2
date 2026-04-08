"""
주문 실행/관리
- 시그널 강도에 따라 시장가/지정가 진입 구분
- 수수료를 고려한 포지션 사이징
- 심볼별 수량/가격 정밀도 적용
- 포지션 진입/부분익절/청산 통합 관리
- 동적 TP/SL 관리 (시그널 기반)
"""
import time
import threading
from typing import Optional

from exchange.bybit_client import BybitClient
from strategy.risk_manager import RiskManager
from strategy.position_manager import calc_atr, ExitCondition
from data.storage import record_trade
from utils.logger import setup_logger
from utils.notifier import notify
from config.indicator_params import FEES, ORDER_TYPE_CONFIG

import pandas as pd

logger = setup_logger("order_mgr")


def _fmt_price(price: float) -> str:
    """가격 포맷: 크기에 따라 적절한 소수점 자릿수"""
    if price >= 100:
        return f"{price:.2f}"
    elif price >= 1:
        return f"{price:.4f}"
    elif price >= 0.01:
        return f"{price:.6f}"
    else:
        return f"{price:.8f}"


class OrderManager:
    """주문 실행 관리자"""

    def __init__(self, client: BybitClient, risk_manager: RiskManager):
        self.client = client
        self.risk = risk_manager
        self._pending_lock = threading.Lock()

    def _determine_order_type(self, strength: float) -> str:
        threshold = ORDER_TYPE_CONFIG["strong_signal_threshold"]
        if strength >= threshold:
            return "market"
        return "limit"

    def _get_fee_rate(self, order_type: str) -> float:
        if order_type == "market":
            return FEES["taker"]
        return FEES["maker"]

    def _calc_limit_price(self, symbol: str, side: str, current_price: float, atr: float) -> float:
        offset = atr * ORDER_TYPE_CONFIG["limit_offset_atr_mult"]
        if side == "long":
            raw_price = current_price - offset
        else:
            raw_price = current_price + offset
        return self.client.round_price(symbol, raw_price)

    def _reserve_position_slot(self, symbol: str, side: str, entry_price: float,
                                qty: float, tp: float, sl: float, status: str = "active"):
        """포지션 슬롯 즉시 예약 (제한 우회 방지)"""
        from datetime import datetime, timezone
        with self._pending_lock:
            self.risk.state.open_positions[symbol] = {
                "side": side,
                "entry_price": entry_price,
                "quantity": qty,
                "original_qty": qty,
                "tp": tp,
                "sl": sl,
                "status": status,       # "pending" / "active" / "partial_closed"
                "partial_tp_done": False,
                "entry_time": datetime.now(timezone.utc).isoformat(),  # v7.6: 시간 기반 관리용
            }

    def _release_position_slot(self, symbol: str):
        """미체결 슬롯 해제"""
        with self._pending_lock:
            if symbol in self.risk.state.open_positions:
                pos = self.risk.state.open_positions[symbol]
                if pos.get("status") == "pending":
                    del self.risk.state.open_positions[symbol]

    def _wait_and_check_limit_order(self, symbol: str, order_id: str,
                                     side: str, entry_price: float,
                                     qty: float, tp: float, sl: float,
                                     leverage: int, signal_grade: str,
                                     strategy: str, fee_rate: float):
        """지정가 주문 체결 대기 (별도 스레드) — 미체결 시 취소"""
        timeout = ORDER_TYPE_CONFIG["limit_timeout_seconds"]
        check_interval = 10
        elapsed = 0

        while elapsed < timeout:
            time.sleep(check_interval)
            elapsed += check_interval

            try:
                open_orders = self.client.get_open_orders(symbol)
                still_open = any(o.get("orderId") == order_id for o in open_orders)

                if not still_open:
                    logger.info(f"[{symbol}] 지정가 주문 체결 확인 (ID: {order_id})")

                    pos_idx = 1 if side == "long" else 2
                    self.client.set_tp_sl(symbol, take_profit=tp, stop_loss=sl, position_idx=pos_idx)

                    # pending → active 전환
                    with self._pending_lock:
                        if symbol in self.risk.state.open_positions:
                            self.risk.state.open_positions[symbol]["status"] = "active"

                    fee_amount = entry_price * qty * fee_rate
                    record_trade(
                        symbol=symbol, side=side, action="open",
                        price=entry_price, quantity=qty, leverage=leverage,
                        fee=fee_amount,
                        signal_grade=signal_grade, strategy=strategy,
                        note="order_type: limit",
                    )

                    side_emoji = "🟢 LONG" if side == "long" else "🔴 SHORT"
                    notify("📥 포지션 진입 (지정가)", (
                        f"<b>{side_emoji}</b> <code>{symbol}</code>\n"
                        f"💰 가격: <code>{_fmt_price(entry_price)}</code> | 수량: <code>{qty}</code>\n"
                        f"🎯 TP: <code>{_fmt_price(tp)}</code> | 🛑 SL: <code>{_fmt_price(sl)}</code>\n"
                        f"📊 등급: {signal_grade} | 수수료: {fee_amount:.4f} USDT"
                    ), level="trade")
                    return

            except Exception as e:
                logger.warning(f"[{symbol}] 지정가 주문 상태 확인 실패: {e}")

        # 타임아웃 — 미체결 주문 취소 + 슬롯 해제
        try:
            self.client.cancel_order(symbol, order_id)
            logger.info(f"[{symbol}] 지정가 주문 타임아웃 취소 (ID: {order_id})")
        except Exception as e:
            logger.error(f"[{symbol}] 지정가 취소 실패: {e}")
        self._release_position_slot(symbol)

    def execute_entry(
        self,
        symbol: str,
        side: str,         # 'long' / 'short'
        entry_price: float,
        atr: float,
        tp_sl_ratio: float = 2.0,
        leverage: int = 5,
        signal_grade: str = "",
        strategy: str = "",
        signal_strength: float = 0.5,
        tp_price: float = 0,
        sl_price: float = 0,
    ) -> Optional[dict]:
        """진입 주문 실행"""
        # 리스크 체크 (lock 포함)
        with self._pending_lock:
            can_open, msg = self.risk.can_open_position(symbol)
            if not can_open:
                logger.warning(f"[{symbol}] 진입 거부: {msg}")
                try:
                    from data.storage import record_skipped_signal
                    record_skipped_signal(
                        symbol=symbol, side=side,
                        signal_strength=signal_strength,
                        skip_reason=msg, price=entry_price,
                        atr=atr, leverage=leverage,
                    )
                except Exception:
                    pass
                return None

        # 주문 타입 결정
        order_type = self._determine_order_type(signal_strength)
        fee_rate = self._get_fee_rate(order_type)

        # 레버리지 설정
        lev = self.risk.get_effective_leverage(leverage)
        self.client.set_leverage(symbol, lev)

        # TP/SL: use signal-provided values if available, fallback to legacy calc
        order_side = "Buy" if side == "long" else "Sell"
        if tp_price > 0 and sl_price > 0:
            tp = self.client.round_price(symbol, tp_price)
            sl = self.client.round_price(symbol, sl_price)
        else:
            tp, sl = ExitCondition.fixed_tp_sl(entry_price, side, atr, tp_sl_ratio)
            tp = self.client.round_price(symbol, tp)
            sl = self.client.round_price(symbol, sl)

        # v7.6: 시그널 강도 기반 비중 조절 제거 — 실거래 데이터: 강도와 수익 무상관
        # 강한 시그널에 큰 비중 → 큰 손실 발생 확인. 균일 비중으로 변경.
        size_mult = 1.0

        # 수수료를 고려한 포지션 사이즈
        balance = self.client.get_balance()
        available = balance["available"]
        qty = self.risk.calc_position_size(
            available, entry_price, sl, lev, fee_rate=fee_rate
        )
        qty *= size_mult

        # v15.0: 포지션당 최대 마진 = 가용잔고의 30% (승률 개선 후 비중 확대)
        max_margin_ratio = 0.30
        max_qty = (available * max_margin_ratio * lev) / entry_price
        qty = min(qty, max_qty)

        if qty <= 0:
            logger.warning(f"[{symbol}] 포지션 사이즈 0 — 진입 불가 "
                           f"(가용잔고: {available:.2f} USDT)")
            return None

        qty = self.client.round_qty(symbol, qty)

        # 최소 수량/최소 주문 금액 검증
        try:
            info = self.client.get_instrument_info(symbol)
            if qty < info["min_qty"]:
                logger.warning(f"[{symbol}] 수량 {qty} < 최소 {info['min_qty']} — 진입 불가")
                return None
            notional = qty * entry_price
            if notional < info["min_notional"]:
                logger.warning(f"[{symbol}] 주문금액 {notional:.2f} < 최소 {info['min_notional']} USDT — 진입 불가")
                return None
        except Exception as e:
            logger.warning(f"[{symbol}] 거래 규격 확인 실패 (계속 진행): {e}")

        fee_amount = entry_price * qty * fee_rate

        logger.info(
            f"[{symbol}] 주문 타입: {order_type.upper()} | "
            f"강도: {signal_strength:.2f} | 비중: {size_mult}x | "
            f"레버리지: {lev}x | "
            f"수수료율: {fee_rate*100:.3f}% ({fee_amount:.4f} USDT)"
        )

        try:
            if order_type == "market":
                order = self.client.market_order(symbol, order_side, qty)

                pos_idx = 1 if side == "long" else 2
                self.client.set_tp_sl(symbol, take_profit=tp, stop_loss=sl, position_idx=pos_idx)

                # 즉시 슬롯 등록 (active)
                self._reserve_position_slot(symbol, side, entry_price, qty, tp, sl, "active")

                record_trade(
                    symbol=symbol, side=side, action="open",
                    price=entry_price, quantity=qty, leverage=lev,
                    fee=fee_amount,
                    signal_grade=signal_grade, strategy=strategy,
                    note="order_type: market",
                )

                ep = _fmt_price(entry_price)
                tp_s = _fmt_price(tp)
                sl_s = _fmt_price(sl)
                side_emoji = "🟢 LONG" if side == "long" else "🔴 SHORT"
                notify("⚡ 포지션 진입 (시장가)", (
                    f"<b>{side_emoji}</b> <code>{symbol}</code>\n"
                    f"💰 가격: <code>{ep}</code> | 수량: <code>{qty}</code>\n"
                    f"🎯 TP: <code>{tp_s}</code> | 🛑 SL: <code>{sl_s}</code>\n"
                    f"📊 등급: {signal_grade} | 수수료: {fee_amount:.4f} USDT"
                ), level="trade")

                return {
                    "symbol": symbol, "side": side, "qty": qty,
                    "entry_price": entry_price, "tp": tp, "sl": sl,
                    "order_id": order["order_id"],
                    "order_type": "market", "fee": fee_amount,
                }

            else:
                limit_price = self._calc_limit_price(symbol, side, entry_price, atr)
                order = self.client.limit_order(symbol, order_side, qty, limit_price)

                # 즉시 슬롯 예약 (pending) — 포지션 제한에 포함됨
                self._reserve_position_slot(symbol, side, limit_price, qty, tp, sl, "pending")

                logger.info(f"[{symbol}] {side.upper()} 지정가 주문 — "
                            f"가격: {_fmt_price(limit_price)}, 수량: {qty}")

                t = threading.Thread(
                    target=self._wait_and_check_limit_order,
                    args=(symbol, order["order_id"], side, limit_price,
                          qty, tp, sl, lev, signal_grade, strategy, fee_rate),
                    daemon=True,
                )
                t.start()

                return {
                    "symbol": symbol, "side": side, "qty": qty,
                    "entry_price": limit_price, "tp": tp, "sl": sl,
                    "order_id": order["order_id"],
                    "order_type": "limit", "fee": fee_amount,
                }

        except Exception as e:
            logger.error(f"[{symbol}] 진입 주문 실패: {e}")
            notify("주문 실패", f"{symbol} {side} 진입 실패: {e}", level="error")
            # 실패 시 슬롯 해제
            self._release_position_slot(symbol)
            return None

    def execute_exit(self, symbol: str, reason: str = "signal") -> Optional[dict]:
        """포지션 전체 청산 (시장가)"""
        positions = self.client.get_positions(symbol)
        if not positions:
            logger.info(f"[{symbol}] 청산할 포지션 없음")
            if symbol in self.risk.state.open_positions:
                del self.risk.state.open_positions[symbol]
            return None

        pos = positions[0]
        fee_rate = FEES["taker"]

        try:
            try:
                self.client.cancel_all_orders(symbol)
            except Exception:
                pass

            self.client.close_position(symbol, pos["side"], pos["size"])

            ticker = self.client.get_ticker(symbol)
            exit_price = ticker["last_price"]
            fee_amount = exit_price * pos["size"] * fee_rate

            pnl = pos["unrealized_pnl"]
            net_pnl = pnl - fee_amount
            self.risk.update_on_trade_close(net_pnl)

            if symbol in self.risk.state.open_positions:
                del self.risk.state.open_positions[symbol]

            record_trade(
                symbol=symbol, side="long" if pos["side"] == "Buy" else "short",
                action="close", price=exit_price,
                quantity=pos["size"], pnl=pnl, fee=fee_amount,
                note=f"exit_reason: {reason} | net_pnl: {net_pnl:.4f}",
            )

            if net_pnl > 0:
                pnl_emoji = "✅"
                title = "📤 익절 청산"
            else:
                pnl_emoji = "❌"
                title = "📤 손절 청산"
            notify(title, (
                f"{pnl_emoji} <code>{symbol}</code>\n"
                f"💵 PnL: <b>{net_pnl:+.4f} USDT</b>\n"
                f"📋 사유: {reason}"
            ), level="trade")

            return {"symbol": symbol, "pnl": pnl, "net_pnl": net_pnl,
                    "fee": fee_amount, "reason": reason}

        except Exception as e:
            logger.error(f"[{symbol}] 청산 실패: {e}")
            notify("청산 실패", f"{symbol} 청산 실패: {e}", level="error")
            return None

    def execute_partial_close(self, symbol: str, close_ratio: float = 0.5,
                               reason: str = "partial_tp") -> Optional[dict]:
        """포지션 부분 청산 (50% 익절 등)"""
        positions = self.client.get_positions(symbol)
        if not positions:
            return None

        pos = positions[0]
        pos_info = self.risk.state.open_positions.get(symbol)
        if not pos_info:
            return None

        close_qty = self.client.round_qty(symbol, pos["size"] * close_ratio)
        if close_qty <= 0:
            return None

        try:
            close_side = "Sell" if pos["side"] == "Buy" else "Buy"
            self.client.market_order(symbol, close_side, close_qty, reduce_only=True)

            remaining_qty = pos["size"] - close_qty
            fee_amount = pos["entry_price"] * close_qty * FEES["taker"]

            # 내부 상태 업데이트
            pos_info["quantity"] = remaining_qty
            pos_info["partial_tp_done"] = True
            pos_info["status"] = "partial_closed"

            # SL을 본전 근처로 이동 (이익 보호)
            entry = pos_info["entry_price"]
            side = pos_info["side"]
            pos_idx = 1 if side == "long" else 2
            breakeven_sl = self.client.round_price(symbol, entry)
            try:
                self.client.set_tp_sl(symbol, stop_loss=breakeven_sl, position_idx=pos_idx)
                pos_info["sl"] = breakeven_sl
            except Exception as e:
                logger.warning(f"[{symbol}] 본전 SL 설정 실패: {e}")

            logger.info(f"[{symbol}] 부분 익절 {close_ratio*100:.0f}% — "
                        f"청산: {close_qty}, 잔여: {remaining_qty}")

            notify("✂️ 부분 익절", (
                f"<code>{symbol}</code>\n"
                f"📊 청산: <code>{close_qty}</code> ({close_ratio*100:.0f}%) | 잔여: <code>{remaining_qty}</code>\n"
                f"📋 사유: {reason}"
            ), level="trade")

            return {"symbol": symbol, "closed_qty": close_qty, "remaining": remaining_qty}

        except Exception as e:
            logger.error(f"[{symbol}] 부분 청산 실패: {e}")
            return None

    def update_dynamic_sl(self, symbol: str, current_price: float, atr: float,
                           signal_strength: float):
        """시그널 강도 기반 동적 SL 조정"""
        pos_info = self.risk.state.open_positions.get(symbol)
        if not pos_info or pos_info.get("status") == "pending":
            return

        side = pos_info["side"]
        current_sl = pos_info.get("sl", 0)
        entry = pos_info["entry_price"]
        pos_idx = 1 if side == "long" else 2

        # 시그널 강할수록 SL 여유 (ATR 2.5~4.0배)
        sl_atr_mult = 2.5 + signal_strength * 1.5

        if side == "long":
            new_sl = current_price - atr * sl_atr_mult
            # SL은 진입가 아래로만 허용 (부분익절 후 본전SL은 예외)
            if not pos_info.get("partial_tp_done"):
                new_sl = min(new_sl, entry * 0.99)
            # 기존 SL보다 위로만 이동 (SL을 내리지 않음)
            if new_sl > current_sl and current_sl > 0:
                new_sl = self.client.round_price(symbol, new_sl)
                try:
                    self.client.set_tp_sl(symbol, stop_loss=new_sl, position_idx=pos_idx)
                    pos_info["sl"] = new_sl
                    logger.info(f"[{symbol}] 동적 SL 갱신: {_fmt_price(current_sl)} → {_fmt_price(new_sl)} "
                                f"(강도 {signal_strength:.2f}, ATR×{sl_atr_mult:.1f})")
                except Exception as e:
                    logger.warning(f"[{symbol}] SL 갱신 실패: {e}")
        else:
            new_sl = current_price + atr * sl_atr_mult
            if not pos_info.get("partial_tp_done"):
                new_sl = max(new_sl, entry * 1.01)
            if (new_sl < current_sl or current_sl == 0) and new_sl > 0:
                new_sl = self.client.round_price(symbol, new_sl)
                try:
                    self.client.set_tp_sl(symbol, stop_loss=new_sl, position_idx=pos_idx)
                    pos_info["sl"] = new_sl
                    logger.info(f"[{symbol}] 동적 SL 갱신: {_fmt_price(current_sl)} → {_fmt_price(new_sl)} "
                                f"(강도 {signal_strength:.2f}, ATR×{sl_atr_mult:.1f})")
                except Exception as e:
                    logger.warning(f"[{symbol}] SL 갱신 실패: {e}")

    def execute_pyramid(
        self,
        symbol: str,
        side: str,
        current_price: float,
        atr: float,
        signal_strength: float,
        leverage: int,
    ) -> Optional[dict]:
        """
        기존 포지션에 추가 진입 (피라미딩)
        조건: 같은 방향 강한 시그널 + 수익 중 + 1회 제한
        """
        pos_info = self.risk.state.open_positions.get(symbol)
        if not pos_info or pos_info.get("status") == "pending":
            return None

        # 피라미딩 최대 3회
        if pos_info.get("pyramid_count", 0) >= 3:
            return None

        entry = pos_info["entry_price"]
        original_qty = pos_info.get("original_qty", pos_info["quantity"])
        current_qty = pos_info["quantity"]

        # 수익률 체크 (2% 이상)
        if side == "long":
            unrealized_pct = (current_price - entry) / entry
        else:
            unrealized_pct = (entry - current_price) / entry

        if unrealized_pct < 0.01:
            logger.debug(f"[{symbol}] 피라미딩 조건 미달: 수익 {unrealized_pct:.1%} < 1%")
            return None

        # 다단계 피라미딩: 횟수에 따라 수량 감소
        pyramid_count = pos_info.get("pyramid_count", 0)
        if pyramid_count == 0:
            add_ratio = 0.5  # 1차: 원래의 50%
        elif pyramid_count == 1:
            add_ratio = 0.3  # 2차: 30%
        else:
            add_ratio = 0.2  # 3차: 20%
        add_qty = original_qty * add_ratio

        # 강도 >= 0.8이면 레버리지 1단계 상승
        new_lev = self.risk.get_effective_leverage(leverage)
        if signal_strength >= 0.8:
            new_lev = self.risk.get_effective_leverage(leverage + 5)
            try:
                self.client.set_leverage(symbol, new_lev)
                logger.info(f"[{symbol}] 피라미딩 레버리지 상승: {leverage}x → {new_lev}x")
            except Exception as e:
                logger.warning(f"[{symbol}] 레버리지 변경 실패 (기존 유지): {e}")

        # 잔고 체크
        balance = self.client.get_balance()
        available = balance["available"]
        margin_needed = (add_qty * current_price) / new_lev
        if margin_needed > available * 0.8:
            # 가용 잔고의 80%까지만 사용
            add_qty = (available * 0.8 * new_lev) / current_price
            if add_qty <= 0:
                logger.warning(f"[{symbol}] 피라미딩 불가: 잔고 부족 (가용: {available:.2f} USDT)")
                return None

        add_qty = self.client.round_qty(symbol, add_qty)

        # 최소 수량 검증
        try:
            info = self.client.get_instrument_info(symbol)
            if add_qty < info["min_qty"]:
                logger.warning(f"[{symbol}] 피라미딩 수량 {add_qty} < 최소 {info['min_qty']}")
                return None
            if add_qty * current_price < info["min_notional"]:
                logger.warning(f"[{symbol}] 피라미딩 주문금액 부족")
                return None
        except Exception:
            pass

        # 시장가 추가 진입
        order_side = "Buy" if side == "long" else "Sell"
        try:
            order = self.client.market_order(symbol, order_side, add_qty)

            # 평균 진입가 계산
            total_qty = current_qty + add_qty
            avg_entry = (entry * current_qty + current_price * add_qty) / total_qty

            # SL 재조정: 평균 진입가 기준 ATR 2.5배
            sl_mult = 2.5
            pos_idx = 1 if side == "long" else 2
            if side == "long":
                new_sl = self.client.round_price(symbol, avg_entry - atr * sl_mult)
            else:
                new_sl = self.client.round_price(symbol, avg_entry + atr * sl_mult)

            # TP는 기존 유지 (이미 넓게 잡혀있음)
            current_tp = pos_info.get("tp", 0)

            try:
                self.client.set_tp_sl(
                    symbol, take_profit=current_tp if current_tp > 0 else None,
                    stop_loss=new_sl, position_idx=pos_idx,
                )
            except Exception as e:
                logger.warning(f"[{symbol}] 피라미딩 SL 재설정 실패: {e}")

            # 내부 상태 업데이트
            pos_info["entry_price"] = avg_entry
            pos_info["quantity"] = total_qty
            pos_info["sl"] = new_sl
            pos_info["pyramided"] = True
            # 트레일링 기준 리셋 (평균 진입가 기준)
            if side == "long":
                pos_info["highest_price"] = max(pos_info.get("highest_price", current_price), current_price)
            else:
                pos_info["lowest_price"] = min(pos_info.get("lowest_price", current_price), current_price)

            fee_amount = current_price * add_qty * FEES["taker"]

            record_trade(
                symbol=symbol, side=side, action="pyramid",
                price=current_price, quantity=add_qty, leverage=new_lev,
                fee=fee_amount,
                note=f"pyramid | avg_entry: {avg_entry:.6f} | total_qty: {total_qty} | str: {signal_strength:.2f}",
            )

            logger.info(
                f"[{symbol}] 🔺 피라미딩 추가 진입 | "
                f"+{add_qty} (총 {total_qty}) | "
                f"평균 진입가: {_fmt_price(avg_entry)} | "
                f"새 SL: {_fmt_price(new_sl)} | "
                f"강도: {signal_strength:.2f}"
            )

            notify("피라미딩 추가 진입", (
                f"🔺 {symbol}\n"
                f"방향: {side.upper()}\n"
                f"추가 수량: {add_qty} (원래의 50%)\n"
                f"총 수량: {total_qty}\n"
                f"평균 진입가: {_fmt_price(avg_entry)}\n"
                f"새 SL: {_fmt_price(new_sl)}\n"
                f"시그널 강도: {signal_strength:.2f}\n"
                f"레버리지: {new_lev}x\n"
                f"수익률: {unrealized_pct:.1%}"
            ), level="trade")

            return {
                "symbol": symbol, "side": side,
                "add_qty": add_qty, "total_qty": total_qty,
                "avg_entry": avg_entry, "new_sl": new_sl,
            }

        except Exception as e:
            logger.error(f"[{symbol}] 피라미딩 주문 실패: {e}")
            notify("피라미딩 실패", f"{symbol}: {e}", level="error")
            return None

    def emergency_close_all(self):
        """긴급: 모든 포지션 즉시 청산"""
        logger.warning("⚠️ 긴급 전체 청산 실행!")
        notify("긴급 청산", "모든 포지션 즉시 청산 실행", level="error")
        self.client.close_all_positions()
        self.risk.state.open_positions.clear()
