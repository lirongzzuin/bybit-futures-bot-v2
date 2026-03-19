"""
Paper Trading Client — 실제 시장 데이터 + 가상 주문 실행

실제 BybitClient를 상속하여:
- 시장 데이터 (get_ticker, get_klines, get_instrument_info 등) → 실제 API 사용
- 주문/포지션/잔고 → 가상으로 시뮬레이션
- TP/SL 체결 → monitor()에서 실시간 가격 체크로 감지
"""
import time
import uuid
from typing import Optional
from datetime import datetime, timezone

from exchange.bybit_client import BybitClient
from data.storage import record_paper_trade
from utils.logger import setup_logger
from utils.notifier import notify

logger = setup_logger("paper")


class PaperBybitClient(BybitClient):
    """가상 거래 클라이언트 — 실제 시장 데이터 + 가상 주문"""

    def __init__(self, initial_balance: float = 10000.0, testnet: bool = None):
        # 실제 API 연결 (시장 데이터용)
        super().__init__(testnet=testnet)

        # 가상 잔고
        self._paper_balance = initial_balance
        self._paper_initial = initial_balance

        # 가상 포지션: {symbol: {side, size, entry_price, leverage, take_profit, stop_loss, unrealized_pnl}}
        self._paper_positions: dict = {}

        # 가상 미체결 주문: {order_id: {symbol, side, qty, price, type, ...}}
        self._paper_orders: dict = {}

        # 거래 기록
        self._paper_trades: list = []

        # 누적 PnL
        self._total_realized_pnl = 0.0
        self._total_fees = 0.0

        # 시그널 정보 (진입 시 DB 기록용)
        self._pending_signal_info: dict = {}

        # 심볼별 레버리지 저장
        self._leverage_map: dict = {}

        logger.info(f"📋 페이퍼 트레이딩 모드 | 초기 잔고: ${initial_balance:.2f}")

    # ── 잔고 (가상) ──

    def get_balance(self) -> dict:
        """가상 잔고 반환"""
        unrealized = sum(
            p.get("unrealized_pnl", 0) for p in self._paper_positions.values()
        )
        return {
            "total": self._paper_balance + unrealized,
            "available": self._paper_balance,
            "unrealized_pnl": unrealized,
        }

    # ── 포지션 (가상) ──

    def get_positions(self, symbol: str = None) -> list[dict]:
        """가상 포지션 반환"""
        positions = []
        for sym, p in self._paper_positions.items():
            if symbol and sym != symbol:
                continue
            positions.append({
                "symbol": sym,
                "side": p["side"],
                "size": p["size"],
                "entry_price": p["entry_price"],
                "unrealized_pnl": p.get("unrealized_pnl", 0),
                "leverage": str(p.get("leverage", 1)),
                "liq_price": 0,
                "take_profit": p.get("take_profit", 0),
                "stop_loss": p.get("stop_loss", 0),
            })
        return positions

    # ── 레버리지 (가상) ──

    def set_leverage(self, symbol: str, leverage: int):
        """레버리지 설정 (저장)"""
        self._leverage_map[symbol] = leverage
        logger.info(f"[PAPER] [{symbol}] 레버리지 {leverage}x 설정")

    # ── 주문 (가상) ──

    def set_signal_info(self, symbol: str, info: dict):
        """다음 진입 시 사용할 시그널 정보 저장"""
        self._pending_signal_info[symbol] = info

    def market_order(self, symbol: str, side: str, qty: float,
                     reduce_only: bool = False) -> dict:
        """가상 시장가 주문 — 즉시 체결"""
        order_id = f"paper_{uuid.uuid4().hex[:12]}"

        # 실제 가격으로 체결
        try:
            ticker = super().get_ticker(symbol)
            fill_price = ticker["last_price"]
        except Exception:
            fill_price = 0

        if reduce_only:
            # 청산 주문 처리
            self._close_paper_position(symbol, side, qty, fill_price, exit_reason="market_close")
        else:
            # 신규 진입 (시그널 정보 첨부)
            sig = self._pending_signal_info.pop(symbol, {})
            self._open_paper_position(symbol, side, qty, fill_price, signal_info=sig)

        logger.info(f"[PAPER] [{symbol}] 시장가 {side} {qty} @ {fill_price:.2f} (ID: {order_id})")
        return {"order_id": order_id, "symbol": symbol, "side": side, "qty": qty}

    def limit_order(self, symbol: str, side: str, qty: float, price: float,
                    reduce_only: bool = False) -> dict:
        """가상 지정가 주문 — 미체결 큐에 등록"""
        order_id = f"paper_{uuid.uuid4().hex[:12]}"

        sig = self._pending_signal_info.pop(symbol, {}) if not reduce_only else {}
        self._paper_orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "type": "Limit",
            "reduce_only": reduce_only,
            "created_at": datetime.now(timezone.utc),
            "signal_info": sig,
        }

        logger.info(f"[PAPER] [{symbol}] 지정가 {side} {qty} @ {price:.2f} (ID: {order_id})")
        return {"order_id": order_id, "symbol": symbol, "side": side, "qty": qty, "price": price}

    def set_tp_sl(self, symbol: str, take_profit: float = None,
                  stop_loss: float = None, position_idx: int = 1) -> dict:
        """가상 TP/SL 설정"""
        if symbol in self._paper_positions:
            if take_profit is not None:
                self._paper_positions[symbol]["take_profit"] = take_profit
            if stop_loss is not None:
                self._paper_positions[symbol]["stop_loss"] = stop_loss
            logger.info(f"[PAPER] [{symbol}] TP: {take_profit}, SL: {stop_loss}")
        return {}

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """가상 주문 취소"""
        if order_id in self._paper_orders:
            del self._paper_orders[order_id]
            logger.info(f"[PAPER] [{symbol}] 주문 취소: {order_id}")
        return {}

    def cancel_all_orders(self, symbol: str) -> dict:
        """심볼 전체 가상 주문 취소"""
        to_remove = [oid for oid, o in self._paper_orders.items() if o["symbol"] == symbol]
        for oid in to_remove:
            del self._paper_orders[oid]
        if to_remove:
            logger.info(f"[PAPER] [{symbol}] {len(to_remove)}개 주문 취소")
        return {}

    def get_open_orders(self, symbol: str = None) -> list[dict]:
        """가상 미체결 주문"""
        orders = []
        for oid, o in self._paper_orders.items():
            if symbol and o["symbol"] != symbol:
                continue
            orders.append({"orderId": oid, **o})
        return orders

    def close_position(self, symbol: str, side: str, qty: float):
        """가상 포지션 청산"""
        close_side = "Sell" if side == "Buy" else "Buy"
        return self.market_order(symbol, close_side, qty, reduce_only=True)

    def close_all_positions(self):
        """모든 가상 포지션 청산"""
        for sym in list(self._paper_positions.keys()):
            p = self._paper_positions[sym]
            self.close_position(sym, p["side"], p["size"])

    # ── 내부 헬퍼 ──

    def _open_paper_position(self, symbol: str, side: str, qty: float, price: float,
                             signal_info: dict = None):
        """가상 포지션 오픈"""
        lev = self._leverage_map.get(symbol, 15)
        self._paper_positions[symbol] = {
            "side": side,
            "size": qty,
            "entry_price": price,
            "leverage": lev,
            "take_profit": 0,
            "stop_loss": 0,
            "unrealized_pnl": 0,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "signal_info": signal_info or {},
        }

    def _close_paper_position(self, symbol: str, side: str, qty: float, price: float,
                              exit_reason: str = "unknown"):
        """가상 포지션 청산 + PnL 계산 + DB 기록"""
        if symbol not in self._paper_positions:
            return

        pos = self._paper_positions[symbol]
        entry = pos["entry_price"]
        lev = pos.get("leverage", 1)

        # PnL 계산
        if pos["side"] == "Buy":  # Long
            raw_pnl = (price - entry) * qty
        else:  # Short
            raw_pnl = (entry - price) * qty

        raw_pnl *= lev

        # 수수료 (taker 0.055%)
        fee = price * qty * 0.00055
        net_pnl = raw_pnl - fee

        self._paper_balance += net_pnl
        self._total_realized_pnl += net_pnl
        self._total_fees += fee

        pnl_pct = net_pnl / self._paper_initial * 100
        marker = "+" if net_pnl >= 0 else ""

        # 시그널 정보 추출
        sig = pos.get("signal_info", {})

        self._paper_trades.append({
            "symbol": symbol,
            "side": pos["side"],
            "entry": entry,
            "exit": price,
            "qty": qty,
            "leverage": lev,
            "pnl": net_pnl,
            "fee": fee,
            "exit_reason": exit_reason,
            "time": datetime.now(timezone.utc).isoformat(),
        })

        # DB에 기록 (전략 개선 분석용)
        try:
            side_str = "long" if pos["side"] == "Buy" else "short"
            record_paper_trade(
                symbol=symbol,
                side=side_str,
                entry_price=entry,
                exit_price=price,
                quantity=qty,
                leverage=lev,
                pnl=net_pnl,
                pnl_pct=pnl_pct,
                fee=fee,
                exit_reason=exit_reason,
                market_regime=sig.get("market_regime", ""),
                signal_strength=sig.get("signal_strength", 0),
                tp_atr_mult=sig.get("tp_atr_mult", 0),
                sl_atr_mult=sig.get("sl_atr_mult", 0),
                atr_at_entry=sig.get("atr", 0),
                balance_after=self._paper_balance,
            )
        except Exception as e:
            logger.warning(f"[PAPER] DB 기록 실패: {e}")

        logger.info(
            f"[PAPER] [{symbol}] 청산 ({exit_reason}) | "
            f"{pos['side']} {entry:.2f} → {price:.2f} | "
            f"PnL: {marker}{net_pnl:.2f} USDT ({marker}{pnl_pct:.2f}%) | "
            f"잔고: ${self._paper_balance:.2f}"
        )

        if qty >= pos["size"]:
            del self._paper_positions[symbol]
        else:
            pos["size"] -= qty

    def check_tp_sl(self):
        """
        실시간 가격 체크 → TP/SL 체결 시뮬레이션
        main.py의 monitor()에서 주기적으로 호출
        """
        for symbol in list(self._paper_positions.keys()):
            pos = self._paper_positions.get(symbol)
            if not pos:
                continue

            try:
                ticker = super().get_ticker(symbol)
                price = ticker["last_price"]
            except Exception:
                continue

            tp = pos.get("take_profit", 0)
            sl = pos.get("stop_loss", 0)
            entry = pos["entry_price"]
            lev = pos.get("leverage", 1)

            # 미실현 PnL 업데이트
            if pos["side"] == "Buy":
                pos["unrealized_pnl"] = (price - entry) * pos["size"] * lev
            else:
                pos["unrealized_pnl"] = (entry - price) * pos["size"] * lev

            hit = False
            reason = ""

            if pos["side"] == "Buy":  # Long
                if sl > 0 and price <= sl:
                    hit, reason = True, "stop_loss"
                elif tp > 0 and price >= tp:
                    hit, reason = True, "take_profit"
            else:  # Short
                if sl > 0 and price >= sl:
                    hit, reason = True, "stop_loss"
                elif tp > 0 and price <= tp:
                    hit, reason = True, "take_profit"

            if hit:
                exit_price = sl if reason == "stop_loss" else tp
                logger.info(f"[PAPER] [{symbol}] {reason.upper()} 체결! @ {exit_price:.2f}")
                self._close_paper_position(symbol, pos["side"], pos["size"], exit_price, exit_reason=reason)

    def check_limit_fills(self):
        """
        미체결 지정가 주문 체결 체크
        """
        for order_id in list(self._paper_orders.keys()):
            order = self._paper_orders.get(order_id)
            if not order:
                continue

            try:
                ticker = super().get_ticker(order["symbol"])
                price = ticker["last_price"]
            except Exception:
                continue

            filled = False
            if order["side"] == "Buy" and price <= order["price"]:
                filled = True
            elif order["side"] == "Sell" and price >= order["price"]:
                filled = True

            if filled:
                symbol = order["symbol"]
                logger.info(f"[PAPER] [{symbol}] 지정가 체결! {order['side']} @ {order['price']:.2f}")

                if order.get("reduce_only"):
                    self._close_paper_position(symbol, order["side"], order["qty"], order["price"],
                                               exit_reason="limit_close")
                else:
                    self._open_paper_position(symbol, order["side"], order["qty"], order["price"],
                                              signal_info=order.get("signal_info", {}))

                del self._paper_orders[order_id]

    def get_paper_summary(self) -> str:
        """페이퍼 트레이딩 요약"""
        wins = sum(1 for t in self._paper_trades if t["pnl"] > 0)
        losses = sum(1 for t in self._paper_trades if t["pnl"] <= 0)
        total = len(self._paper_trades)
        win_rate = (wins / total * 100) if total > 0 else 0

        return (
            f"📋 페이퍼 트레이딩 요약\n"
            f"초기 잔고: ${self._paper_initial:.2f}\n"
            f"현재 잔고: ${self._paper_balance:.2f}\n"
            f"수익률: {(self._paper_balance / self._paper_initial - 1) * 100:+.2f}%\n"
            f"총 거래: {total} (승: {wins} / 패: {losses})\n"
            f"승률: {win_rate:.1f}%\n"
            f"누적 PnL: {self._total_realized_pnl:+.2f} USDT\n"
            f"누적 수수료: {self._total_fees:.2f} USDT\n"
            f"활성 포지션: {len(self._paper_positions)}개"
        )
