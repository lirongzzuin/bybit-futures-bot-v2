# Crypto Futures Bot v15.0 — 5m + 1H Momentum Scalper

> Bybit USDT Perpetual Futures — 5분봉 진입 + 1시간봉 추세 확인

## 왜 5m + 1H인가?

실제 Bybit BTC 데이터로 타임프레임 조합 비교 검증:

| 조합 | 수익률 | 연환산 | 승률 | PF | MDD |
|------|--------|--------|------|-----|-----|
| 3m+30m | -30.7% | -2,688% | 0% | 0.00 | 31% |
| **5m+1H** | **+22.8%** | **+1,201%** | **29%** | **1.26** | **30%** |
| 15m+4H (기존) | -37.2% | -651% | 35% | 0.70 | 53% |

- 15m+4H: 4시간봉이 너무 느림 → 추세 변화 감지 지연 → MDD 53%
- 3m+30m: 3분봉이 너무 빠름 → 노이즈 과다 → 승률 0%
- **5m+1H: 유일하게 수익 (+22.8%), PF > 1.0**

## v15.0 핵심

| 항목 | 값 |
|------|-----|
| 진입 TF | **5분봉** (was 15분) |
| 추세 TF | **1시간봉** (was 4시간) |
| ADX | >= 20 (1H) |
| RSI 롱 | 25-45 (5m) |
| RSI 숏 | 55-75 (5m) |
| TP/SL | 2.0x / 1.0x ATR (R:R 2.0:1) |
| 최소 TP/SL | 1.5% / 1.0% 가격 대비 |
| 레버리지 | 5~15x 동적 |
| 모니터링 | 5초 |
| 메인루프 | 1초 |

## 배포

```bash
fly deploy
python main.py
python -m pytest tests/  # 105 tests
```

## 기술 스택

Python 3.11 | Bybit (pybit) | 5m+1H | EMA/RSI/ADX/MACD/BB | fly.io Tokyo | Telegram | SQLite | pytest 105
