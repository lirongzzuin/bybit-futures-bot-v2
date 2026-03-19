"""
Bybit API 래퍼 — pybit SDK 기반
- USDT 무기한 선물 전용
- 테스트넷/메인넷 전환 지원
- 에러 핸들링 + 재시도 (최대 3회)
- Rate Limit 관리
"""
import time
from typing import Optional
from functools import wraps

from pybit.unified_trading import HTTP

from config.settings import BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET
from utils.logger import setup_logger

logger = setup_logger("bybit")

MAX_RETRIES = 3
RETRY_DELAY = 1.0


def retry_on_error(func):
    """API 에러 시 재시도 데코레이터 (최대 3회)"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                last_error = e
                logger.warning(f"[{func.__name__}] 시도 {attempt}/{MAX_RETRIES} 실패: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
        raise last_error
    return wrapper


class BybitClient:
    """Bybit USDT 무기한 선물 클라이언트"""

    def __init__(self, testnet: bool = None):
        self.testnet = testnet if testnet is not None else BYBIT_TESTNET
        self.session = HTTP(
            testnet=self.testnet,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
        )
        self._instrument_cache: dict = {}  # symbol → {qty_step, tick_size, min_qty, min_notional}
        mode = "테스트넷" if self.testnet else "메인넷"
        logger.info(f"Bybit {mode} 연결 완료")

    @retry_on_error
    def get_instrument_info(self, symbol: str) -> dict:
        """심볼 거래 규격 조회 (캐시)"""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        result = self.session.get_instruments_info(category="linear", symbol=symbol)
        if result["retCode"] != 0:
            raise Exception(f"거래 규격 조회 실패: {result['retMsg']}")

        info = result["result"]["list"][0]
        lot = info.get("lotSizeFilter", {})
        price_filter = info.get("priceFilter", {})

        instrument = {
            "qty_step": float(lot.get("qtyStep") or 0.001),
            "min_qty": float(lot.get("minOrderQty") or 0.001),
            "max_qty": float(lot.get("maxMktOrderQty") or 1000),
            "tick_size": float(price_filter.get("tickSize") or 0.01),
            "min_notional": float(lot.get("minNotionalValue") or 5),
        }
        self._instrument_cache[symbol] = instrument
        return instrument

    @staticmethod
    def _step_decimals(step: float) -> int:
        """step 값에서 소수점 자릿수 계산 (과학적 표기법 대응)"""
        if step >= 1:
            return 0
        # Decimal을 사용하여 정확한 자릿수 계산
        from decimal import Decimal
        d = Decimal(str(step))
        # exponent가 음수면 소수점 자릿수
        return max(0, -d.as_tuple().exponent)

    def round_qty(self, symbol: str, qty: float) -> float:
        """심볼의 qtyStep에 맞게 수량 내림"""
        info = self.get_instrument_info(symbol)
        step = info["qty_step"]
        if step <= 0:
            return qty
        # step 단위로 내림 (올림하면 잔고 초과 가능)
        rounded = int(qty / step) * step
        decimals = self._step_decimals(step)
        return round(rounded, decimals)

    def round_price(self, symbol: str, price: float) -> float:
        """심볼의 tickSize에 맞게 가격 반올림"""
        info = self.get_instrument_info(symbol)
        tick = info["tick_size"]
        if tick <= 0:
            return price
        rounded = round(price / tick) * tick
        decimals = self._step_decimals(tick)
        return round(rounded, decimals)

    # ── 계좌 정보 ──

    @retry_on_error
    def get_balance(self) -> dict:
        """USDT 잔고 조회"""
        result = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        if result["retCode"] != 0:
            raise Exception(f"잔고 조회 실패: {result['retMsg']}")

        accounts = result["result"]["list"]
        if accounts:
            account = accounts[0]
            # account-level: 거래 가용 잔고 (Unified Trading Account)
            available = float(account.get("totalAvailableBalance") or 0)

            coins = account.get("coin", [])
            for c in coins:
                if c["coin"] == "USDT":
                    wallet = float(c.get("walletBalance") or 0)
                    # coin-level availableToWithdraw가 더 크면 그걸 사용
                    coin_available = float(c.get("availableToWithdraw") or 0)
                    available = max(available, coin_available)
                    return {
                        "total": wallet,
                        "available": available,
                        "unrealized_pnl": float(c.get("unrealisedPnl") or 0),
                    }
        return {"total": 0, "available": 0, "unrealized_pnl": 0}

    # ── 포지션 관리 ──

    @retry_on_error
    def get_positions(self, symbol: str = None) -> list[dict]:
        """포지션 조회"""
        params = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol

        result = self.session.get_positions(**params)
        if result["retCode"] != 0:
            raise Exception(f"포지션 조회 실패: {result['retMsg']}")

        positions = []
        for p in result["result"]["list"]:
            size = float(p.get("size") or 0)
            if size > 0:
                positions.append({
                    "symbol": p["symbol"],
                    "side": p["side"],  # "Buy" or "Sell"
                    "size": size,
                    "entry_price": float(p.get("avgPrice") or 0),
                    "unrealized_pnl": float(p.get("unrealisedPnl") or 0),
                    "leverage": p.get("leverage") or "1",
                    "liq_price": float(p.get("liqPrice") or 0),
                    "take_profit": float(p.get("takeProfit") or 0),
                    "stop_loss": float(p.get("stopLoss") or 0),
                })
        return positions

    def set_leverage(self, symbol: str, leverage: int):
        """레버리지 설정 (이미 동일하면 무시)"""
        try:
            result = self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            if result["retCode"] != 0 and "not modified" not in result.get("retMsg", "").lower():
                raise Exception(f"레버리지 설정 실패: {result['retMsg']}")
            logger.info(f"[{symbol}] 레버리지 {leverage}x 설정")
        except Exception as e:
            if "not modified" in str(e).lower() or "110043" in str(e):
                logger.debug(f"[{symbol}] 레버리지 {leverage}x 이미 설정됨")
            else:
                raise

    # ── 주문 ──

    @staticmethod
    def _calc_position_idx(side: str, reduce_only: bool = False) -> int:
        """
        Hedge Mode용 positionIdx 계산
        - Long 포지션(positionIdx=1): Buy로 진입, Sell로 청산
        - Short 포지션(positionIdx=2): Sell로 진입, Buy로 청산
        """
        if not reduce_only:
            return 1 if side == "Buy" else 2
        else:
            return 2 if side == "Buy" else 1

    @retry_on_error
    def market_order(self, symbol: str, side: str, qty: float,
                     reduce_only: bool = False) -> dict:
        """
        시장가 주문
        side: 'Buy' or 'Sell'
        """
        pos_idx = self._calc_position_idx(side, reduce_only)
        result = self.session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            reduceOnly=reduce_only,
            positionIdx=pos_idx,
        )
        if result["retCode"] != 0:
            raise Exception(f"시장가 주문 실패: {result['retMsg']}")

        order_id = result["result"]["orderId"]
        logger.info(f"[{symbol}] 시장가 {side} {qty} 체결 (ID: {order_id})")
        return {"order_id": order_id, "symbol": symbol, "side": side, "qty": qty}

    @retry_on_error
    def limit_order(self, symbol: str, side: str, qty: float, price: float,
                    reduce_only: bool = False) -> dict:
        """지정가 주문"""
        pos_idx = self._calc_position_idx(side, reduce_only)
        result = self.session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Limit",
            qty=str(qty),
            price=str(price),
            reduceOnly=reduce_only,
            timeInForce="GTC",
            positionIdx=pos_idx,
        )
        if result["retCode"] != 0:
            raise Exception(f"지정가 주문 실패: {result['retMsg']}")

        order_id = result["result"]["orderId"]
        logger.info(f"[{symbol}] 지정가 {side} {qty} @ {price} (ID: {order_id})")
        return {"order_id": order_id, "symbol": symbol, "side": side, "qty": qty, "price": price}

    def set_tp_sl(self, symbol: str, take_profit: float = None,
                  stop_loss: float = None, position_idx: int = 1) -> dict:
        """
        서버사이드 TP/SL 설정
        position_idx: 1=Long, 2=Short (Hedge Mode)
        "not modified" (34040)은 이미 같은 값 → 정상 처리
        """
        params = {
            "category": "linear",
            "symbol": symbol,
            "positionIdx": position_idx,
        }
        if take_profit is not None:
            params["takeProfit"] = str(take_profit)
        if stop_loss is not None:
            params["stopLoss"] = str(stop_loss)

        result = self.session.set_trading_stop(**params)
        if result["retCode"] != 0:
            msg = result.get("retMsg", "")
            # "not modified" = 이미 같은 값 설정됨 → 에러 아님
            if "not modified" in msg.lower() or "34040" in str(result["retCode"]):
                logger.debug(f"[{symbol}] TP/SL 이미 동일 — 스킵")
                return result.get("result", {})
            raise Exception(f"TP/SL 설정 실패: {msg}")

        logger.info(f"[{symbol}] TP: {take_profit}, SL: {stop_loss}")
        return result["result"]

    @retry_on_error
    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """주문 취소"""
        result = self.session.cancel_order(
            category="linear",
            symbol=symbol,
            orderId=order_id,
        )
        if result["retCode"] != 0:
            raise Exception(f"주문 취소 실패: {result['retMsg']}")
        logger.info(f"[{symbol}] 주문 취소: {order_id}")
        return result["result"]

    @retry_on_error
    def cancel_all_orders(self, symbol: str) -> dict:
        """심볼 전체 주문 취소"""
        result = self.session.cancel_all_orders(
            category="linear",
            symbol=symbol,
        )
        if result["retCode"] != 0:
            raise Exception(f"전체 주문 취소 실패: {result['retMsg']}")
        logger.info(f"[{symbol}] 전체 주문 취소")
        return result["result"]

    @retry_on_error
    def get_open_orders(self, symbol: str = None, settle_coin: str = "USDT") -> list[dict]:
        """미체결 주문 조회"""
        params = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = settle_coin

        result = self.session.get_open_orders(**params)
        if result["retCode"] != 0:
            raise Exception(f"미체결 주문 조회 실패: {result['retMsg']}")

        return result["result"]["list"]

    # ── 시장 데이터 ──

    @retry_on_error
    def get_ticker(self, symbol: str) -> dict:
        """현재가 조회"""
        result = self.session.get_tickers(category="linear", symbol=symbol)
        if result["retCode"] != 0:
            raise Exception(f"티커 조회 실패: {result['retMsg']}")

        ticker = result["result"]["list"][0]
        return {
            "symbol": symbol,
            "last_price": float(ticker.get("lastPrice") or 0),
            "bid": float(ticker.get("bid1Price") or 0),
            "ask": float(ticker.get("ask1Price") or 0),
            "volume_24h": float(ticker.get("volume24h") or 0),
        }

    @retry_on_error
    def get_klines(self, symbol: str, interval: str = "240",
                   limit: int = 200) -> list:
        """캔들스틱 조회 (interval: 분 단위, 240=4h)"""
        result = self.session.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit,
        )
        if result["retCode"] != 0:
            raise Exception(f"캔들 조회 실패: {result['retMsg']}")
        return result["result"]["list"]

    @retry_on_error
    def get_top_symbols_by_volume(self, limit: int = 50) -> list[str]:
        """
        24시간 거래량 기준 상위 N개 USDT 무기한 선물 심볼 조회
        """
        result = self.session.get_tickers(category="linear")
        if result["retCode"] != 0:
            raise Exception(f"티커 목록 조회 실패: {result['retMsg']}")

        tickers = []
        for t in result["result"]["list"]:
            symbol = t.get("symbol", "")
            # USDT 무기한 선물만 필터링
            if not symbol.endswith("USDT"):
                continue
            turnover = float(t.get("turnover24h") or 0)
            tickers.append((symbol, turnover))

        # 거래대금 기준 내림차순 정렬
        tickers.sort(key=lambda x: x[1], reverse=True)
        top_symbols = [s for s, _ in tickers[:limit]]

        logger.info(f"거래량 상위 {limit}개 심볼 조회 완료 (1위: {top_symbols[0] if top_symbols else 'N/A'})")
        return top_symbols

    # ── 유틸리티 ──

    def close_position(self, symbol: str, side: str, qty: float):
        """포지션 청산 (반대 방향 시장가)"""
        close_side = "Sell" if side == "Buy" else "Buy"
        return self.market_order(symbol, close_side, qty, reduce_only=True)

    def close_all_positions(self):
        """모든 포지션 즉시 청산"""
        positions = self.get_positions()
        for p in positions:
            self.close_position(p["symbol"], p["side"], p["size"])
            logger.warning(f"[{p['symbol']}] 긴급 청산 실행")
