"""
Ichimoku Cloud 지표 — 표준 파라미터 (9, 26, 52, 26)

구성요소:
  - 전환선 (Conversion Line / Tenkan-sen): 9봉 고저 중간값
  - 기준선 (Base Line / Kijun-sen): 26봉 고저 중간값
  - 선행스팬A (Leading Span A / Senkou Span A): (전환선+기준선)/2, 26봉 미래
  - 선행스팬B (Leading Span B / Senkou Span B): 52봉 고저 중간값, 26봉 미래
  - 후행스팬 (Lagging Span / Chikou Span): 종가를 26봉 과거에 표시
"""
import numpy as np
import pandas as pd
from config.indicator_params import ICHIMOKU as P


def _mid(series: pd.Series, period: int) -> pd.Series:
    """period 봉 내 (최고가 + 최저가) / 2"""
    return (series.rolling(period).max() + series.rolling(period).min()) / 2


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ichimoku 전체 지표 계산

    추가 컬럼:
      - tenkan, kijun: 전환선, 기준선
      - senkou_a, senkou_b: 선행스팬 A/B (미래 shift 적용)
      - chikou: 후행스팬 (과거 shift 적용)
      - kumo_top, kumo_bottom, kumo_thickness: 구름 상단/하단/두께
      - ichi_signal: 1(롱), -1(숏), 0(중립)
      - ichi_strength: 0~5 (신호 강도)
    """
    df = df.copy()
    conv = P["conversion_line_period"]   # 9
    base = P["base_line_period"]         # 26
    span_b = P["leading_span_b_period"]  # 52
    lag = P["lagging_span_period"]       # 26

    # 기본 5선
    df["tenkan"] = _mid(df["high"], conv)  # 전환선
    df["kijun"] = _mid(df["high"], base)   # 기준선 (high 기준)

    # 기준선은 high와 low 모두 사용해야 함 — 수정
    df["tenkan"] = (df["high"].rolling(conv).max() + df["low"].rolling(conv).min()) / 2
    df["kijun"] = (df["high"].rolling(base).max() + df["low"].rolling(base).min()) / 2

    # 선행스팬 A: (전환선 + 기준선) / 2 → 26봉 미래로 shift
    df["senkou_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(lag)

    # 선행스팬 B: 52봉 고저 중간값 → 26봉 미래로 shift
    df["senkou_b"] = (
        (df["high"].rolling(span_b).max() + df["low"].rolling(span_b).min()) / 2
    ).shift(lag)

    # 후행스팬: 현재 종가를 26봉 과거에 배치
    df["chikou"] = df["close"].shift(-lag)

    # 구름 (Kumo) 계산
    df["kumo_top"] = df[["senkou_a", "senkou_b"]].max(axis=1)
    df["kumo_bottom"] = df[["senkou_a", "senkou_b"]].min(axis=1)
    df["kumo_thickness"] = (df["kumo_top"] - df["kumo_bottom"]) / df["close"] * 100  # %

    # ── 종합 신호 계산 (5가지 조건 체크) ──
    # 조건 1: 가격 vs 구름 위치
    above_kumo = (df["close"] > df["kumo_top"]).astype(int)
    below_kumo = (df["close"] < df["kumo_bottom"]).astype(int)

    # 조건 2: 전환선 vs 기준선 크로스
    tk_cross_bull = (df["tenkan"] > df["kijun"]).astype(int)
    tk_cross_bear = (df["tenkan"] < df["kijun"]).astype(int)

    # 조건 3: 선행스팬 A > B (구름 색상)
    kumo_bull = (df["senkou_a"] > df["senkou_b"]).astype(int)
    kumo_bear = (df["senkou_a"] < df["senkou_b"]).astype(int)

    # 조건 4: 후행스팬 vs 과거 가격 (26봉 전 종가)
    past_close = df["close"].shift(lag)
    chikou_bull = (df["close"] > past_close).astype(int)  # 현재가 > 26봉 전 가격
    chikou_bear = (df["close"] < past_close).astype(int)

    # 조건 5: 가격이 기준선 위/아래
    above_kijun = (df["close"] > df["kijun"]).astype(int)
    below_kijun = (df["close"] < df["kijun"]).astype(int)

    # 신호 강도 (0~5)
    df["ichi_bull_strength"] = above_kumo + tk_cross_bull + kumo_bull + chikou_bull + above_kijun
    df["ichi_bear_strength"] = below_kumo + tk_cross_bear + kumo_bear + chikou_bear + below_kijun

    # 최종 신호
    df["ichi_signal"] = 0
    df.loc[df["ichi_bull_strength"] >= 3, "ichi_signal"] = 1
    df.loc[df["ichi_bear_strength"] >= 3, "ichi_signal"] = -1

    df["ichi_strength"] = df[["ichi_bull_strength", "ichi_bear_strength"]].max(axis=1)

    # TK 크로스 이벤트 (진입 타이밍용)
    df["tk_cross_bull"] = (
        (df["tenkan"] > df["kijun"]) &
        (df["tenkan"].shift(1) <= df["kijun"].shift(1))
    ).astype(int)
    df["tk_cross_bear"] = (
        (df["tenkan"] < df["kijun"]) &
        (df["tenkan"].shift(1) >= df["kijun"].shift(1))
    ).astype(int)

    return df
