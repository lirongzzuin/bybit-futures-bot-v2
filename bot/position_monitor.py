"""포지션 모니터링 및 동적 관리 모듈"""
from datetime import datetime, timezone, timedelta
import pandas as pd

from utils.logger import setup_logger

logger = setup_logger("position_monitor")

COOLDOWN_MINUTES = 20  # v17.0 유지: 20분 (틸트 방지)
# v17.1: Stage 1/2 완화 — v17.0 실거래에서 본전 SL 저주 확인 후 조정


class PositionMonitor:
    """포지션 모니터링 및 TP/SL 동적 관리"""

    def __init__(self, client, risk, order_mgr, symbol_cooldown, fetch_data_fn):
        """
        Args:
            client: BybitClient 또는 PaperBybitClient
            risk: RiskManager
            order_mgr: OrderManager
            symbol_cooldown: dict - 심볼별 재진입 쿨다운 추적
            fetch_data_fn: callable - fetch_data(symbol, limit) 함수
        """
        self.client = client
        self.risk = risk
        self.order_mgr = order_mgr
        self._symbol_cooldown = symbol_cooldown
        self.fetch_data_fn = fetch_data_fn

    def manage_existing_position(self, symbol: str, signal_data: dict = None):
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
                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
                    return
                elif is_reverse:
                    logger.debug(f"[{symbol}] 약한 반대 시그널 (강도 {strength:.2f}) → 무시")

            # B) 시그널 강도 기반 TP/SL 실시간 조정 (v7.3)
            if direction != 0 and not is_reverse and atr > 0:
                self._adjust_tp_sl_by_strength(
                    symbol, side, price, entry, tp, sl, atr,
                    strength, unrealized_pct, pos_info, pos_idx
                )

            # C) RSI 반전: 수익 3%+ AND RSI 극단 AND 3봉 연속 꺾임 (더 보수적)
            if unrealized_pct > 0.03:
                try:
                    df_check = self.fetch_data_fn(symbol, limit=30)
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
                                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
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
                                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
                                    return
                except Exception:
                    pass

            # D) ATR 수축 감지: 변동성 급감 = 추세 종료 신호 → SL 타이트닝 (수익 8%+ 이상만)
            # v17.1: 5%→8% (중간 수익 구간의 풀백을 수축으로 오인해 잘라내던 문제 해소)
            if unrealized_pct > 0.08 and atr > 0:
                try:
                    df_atr = self.fetch_data_fn(symbol, limit=50)
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

        # === Stage 0: 안전장치 (SL + TP 자동 설정) ===
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

        # v15.0: TP 없을 때 자동 설정 (동기화된 포지션 등)
        if tp == 0 or tp is None:
            default_tp_pct = 0.03  # 기본 TP 3%
            if side == "long":
                auto_tp = self.client.round_price(symbol, entry * (1 + default_tp_pct))
            else:
                auto_tp = self.client.round_price(symbol, entry * (1 - default_tp_pct))
            try:
                self.client.set_tp_sl(symbol, take_profit=auto_tp, position_idx=pos_idx)
                pos_info["tp"] = auto_tp
                tp = auto_tp
                logger.info(f"[{symbol}] TP 없음 → 기본 TP 설정: {auto_tp} (+{default_tp_pct*100}%)")
            except Exception as e:
                logger.warning(f"[{symbol}] 기본 TP 설정 실패: {e}")

        # 급락 긴급 청산: -8% (v17.0: -5%→-8%, 레버리지 5x 기준 적절)
        if unrealized_pct <= -0.08:
            logger.warning(f"[{symbol}] 급락! 손실 {unrealized_pct:.1%} → 긴급 청산")
            self.order_mgr.execute_exit(symbol, reason=f"emergency_{unrealized_pct:.1%}")
            self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
            return

        # v17.1: 시간 기반 SL 타이트닝 — 120분+/수익 0.5% 미만만 (60→120: 추세 초기 여유)
        entry_time_str = pos_info.get("entry_time", "")
        if entry_time_str:
            try:
                entry_time = datetime.fromisoformat(entry_time_str)
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
                hold_minutes = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60

                # v17.0 유지: 150분+ + 손실 1.5%+ → 시장가 손절
                if hold_minutes >= 150 and unrealized_pct < -0.015:
                    logger.info(f"[{symbol}] 시간 초과 ({hold_minutes:.0f}분) + 손실 {unrealized_pct:.1%} → 타임아웃 청산")
                    self.order_mgr.execute_exit(symbol, reason=f"timeout_{hold_minutes:.0f}m_{unrealized_pct:.1%}")
                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
                    return

                # v17.3: 120→160분, 0.5%→0.3% 미만으로 완화
                # 실거래 분석: 120~160분 구간에서 13/35 거래가 작은 이익으로 조기 탈출됨
                # → 이 구간에 TP 도달 여유 확보 (승률 소폭 양보하되 R:R 회복)
                if hold_minutes >= 160 and 0 <= unrealized_pct < 0.003:
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

        # === Stage 1: 본전 보호 (수익 2.5%+) — v17.1 본전 SL 저주 해소 ===
        # v17.0: 1.5% → v17.1: 2.5% (풀백에 조기 탈출 방지, 승리 규모 확대)
        # 본전 SL = 진입가 + 왕복 수수료분 (순손실 제로 보장)
        if unrealized_pct >= 0.025 and not pos_info.get("breakeven_set"):
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

        # === Stage 2: 변동성 적응 트레일링 SL (v17.3 — 더 느슨한 활성화) ===
        # v17.3: 3.5%→4.5% 활성화 (추세 초기 여유 확대)
        # 첫 밴드 보호 비율 25%→20%, 나머지 대역 소폭 하향
        if unrealized_pct >= 0.045:
            vol_regime = pos_info.get("vol_regime", "normal")
            if vol_regime in ("extreme", "high"):
                vol_offset = -0.05
            elif vol_regime == "low":
                vol_offset = 0.05
            else:
                vol_offset = 0.0

            # v17.3: 대역 재구성 — 20/30/45/60
            if unrealized_pct >= 0.10:
                protect_ratio = min(0.60 + vol_offset, 0.70)
            elif unrealized_pct >= 0.07:
                protect_ratio = min(0.45 + vol_offset, 0.60)
            elif unrealized_pct >= 0.055:
                protect_ratio = min(0.30 + vol_offset, 0.45)
            else:
                protect_ratio = min(0.20 + vol_offset, 0.30)

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

        # === Stage 4: TP 확대 — v17.1에서 비활성화 ===
        # v17.0 실거래 분석: TP 근처 도달 자체가 드물고, 확대 후 트레일링에 걸림
        # → 초기 TP(ATR 2.5x)를 그대로 유지하는 게 유리
        pass

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

    def protect_position_for_daily_candle(self, symbol: str, pos_info: dict,
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

    def check_position_closures(self):
        """
        Bybit 실제 포지션과 내부 상태를 비교하여
        서버사이드 TP/SL 체결(청산)을 감지하고 알림 전송
        """
        from data.storage import record_trade
        from utils.notifier import notify
        from exchange.order_manager import _fmt_price

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
                    self._symbol_cooldown[symbol] = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)

                    # DB 기록
                    record_trade(
                        symbol=symbol, side=side, action="close",
                        price=current_price,
                        quantity=qty,
                        pnl=estimated_pnl,
                        note=f"exit_reason: {reason} (server-side)",
                    )

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

    def sync_balance(self):
        """실제 Bybit 잔고를 리스크 매니저에 동기화"""
        try:
            balance = self.client.get_balance()
            self.risk.state.current_balance = balance["total"]
        except Exception as e:
            logger.error(f"잔고 동기화 실패: {e}")
