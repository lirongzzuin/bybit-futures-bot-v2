# Crypto Futures Bot v15.0 — 5m + 1H Momentum Scalper

> Bybit USDT Perpetual Futures — 5분봉 진입 + 1시간봉 추세 확인

## 타임프레임 비교 검증 (실제 Bybit BTC 데이터)

| 조합 | 수익률 | 연환산 | PF |
|------|--------|--------|-----|
| 3m+30m | -30.7% | -2,688% | 0.00 |
| **5m+1H** | **+22.8%** | **+1,201%** | **1.26** |
| 15m+4H | -37.2% | -651% | 0.70 |

## 현재 설정

| 항목 | 값 |
|------|-----|
| 진입 TF | **5분봉** |
| 추세 TF | **1시간봉** |
| ADX | >= 20 (1H) |
| RSI 롱/숏 | 25-45 / 55-75 (5m) |
| TP/SL | 2.0x / 1.0x ATR (R:R 2.0:1) |
| 최소 TP/SL | 1.5% / 1.0% |
| 리스크/거래 | **4%** |
| 최대 마진/포지션 | **30%** |
| 동시 포지션 | **5개** |
| 레버리지 | 5~15x 동적 |
| 모니터링 | 5초 |
| 메인루프 | 1초 |
| 수수료 | Maker 0.02% / Taker 0.055% |
| TP/SL 미설정 포지션 | 자동 TP 3% / SL 5% 설정 |

## 배포

```bash
fly deploy
python main.py
python -m pytest tests/  # 105 tests
```

## 기술 스택

Python 3.11 | Bybit (pybit) | 5m+1H | EMA/RSI/ADX/MACD/BB | fly.io Tokyo | Telegram | SQLite | pytest 105
