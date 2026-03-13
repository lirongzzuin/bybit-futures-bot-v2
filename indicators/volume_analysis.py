"""
Volume Analysis 지표

구성요소:
  - 거래량 이동평균 (MA 20)
  - 거래량 비율 (현재 / 평균)
  - 급등 거래량 감지 (2x 이상)
  - 매수/매도 거래량 추정 (캔들 위치 기반)
"""
import numpy as np
import pandas as pd
from config.indicator_params import VOLUME as P


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Volume 분석 전체 계산

    추가 컬럼:
      - vol_ma: 거래량 이동평균
      - vol_ratio: 현재 거래량 / MA
      - vol_spike: 급등 여부 (bool)
      - buy_volume, sell_volume: 매수/매도 추정 거래량
      - vol_signal: 1(매수 거래량 우세), -1(매도 거래량 우세), 0(중립)
    """
    df = df.copy()
    ma_len = P["ma_length"]

    # 거래량 이동평균
    df["vol_ma"] = df["volume"].rolling(ma_len).mean()

    # 거래량 비율
    df["vol_ratio"] = df["volume"] / df["vol_ma"].replace(0, np.nan)

    # 급등 거래량 (평균 대비 2배 이상)
    df["vol_spike"] = (df["vol_ratio"] >= 2.0).astype(int)

    # ── 매수/매도 거래량 추정 (캔들 위치 기반) ──
    # 캔들 body 비율로 매수/매도 비중 계산
    candle_range = df["high"] - df["low"]
    candle_range = candle_range.replace(0, np.nan)

    # 매수 비율: (close - low) / (high - low)
    buy_ratio = (df["close"] - df["low"]) / candle_range
    sell_ratio = (df["high"] - df["close"]) / candle_range

    df["buy_volume"] = df["volume"] * buy_ratio.fillna(0.5)
    df["sell_volume"] = df["volume"] * sell_ratio.fillna(0.5)

    # 매수/매도 이동평균
    df["buy_vol_ma"] = df["buy_volume"].rolling(ma_len).mean()
    df["sell_vol_ma"] = df["sell_volume"].rolling(ma_len).mean()

    # 거래량 신호
    # 급등 + 매수 우세 → 1, 급등 + 매도 우세 → -1
    df["vol_signal"] = 0
    bull_mask = (df["vol_spike"] == 1) & (df["buy_volume"] > df["sell_volume"])
    bear_mask = (df["vol_spike"] == 1) & (df["sell_volume"] > df["buy_volume"])
    df.loc[bull_mask, "vol_signal"] = 1
    df.loc[bear_mask, "vol_signal"] = -1

    # 거래량 추세 (급등 아닐 때도 방향 판단)
    df["vol_trend"] = np.where(
        df["buy_vol_ma"] > df["sell_vol_ma"] * 1.1, 1,
        np.where(df["sell_vol_ma"] > df["buy_vol_ma"] * 1.1, -1, 0)
    )

    return df
