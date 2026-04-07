# Crypto Futures Auto-Trading Bot — v13.0 Aggressive Frequency

> Bybit USDT Perpetual Futures — 거래 빈도 극대화 + 동적 레버리지 8~20x + 실시간 모니터링

## v13.0 핵심 변경 (v12.0 대비)

| 항목 | v12.0 | **v13.0** | 변경 이유 |
|------|-------|-----------|-----------|
| ADX | >= 30 | **>= 20** | 중간 강도 추세도 포착 |
| RSI 롱 | 20-35 | **25-50** | 넓은 풀백 구간 |
| RSI 숏 | 65-80 | **50-75** | 넓은 바운스 구간 |
| SL | 0.8x ATR | **1.2x ATR** | 노이즈 스톱아웃 방지 |
| TP | 2.0x ATR | **2.5x ATR** | R:R 2.08:1 유지 |
| min_score | 2.5 | **1.5** | 더 많은 진입 기회 |
| BB/EMA | 필터(skip) | **보너스 스코어** | 진입 차단 → 품질 가점 |
| 4H EMA | 필수 조건 | **보너스 스코어** | DI 방향만으로 충분 |
| 쿨다운 | 4봉(1시간) | **2봉(30분)** | 빠른 재진입 |
| SL후 쿨다운 | 2봉(30분) | **1봉(15분)** | 즉시 재진입 |
| 심볼 쿨다운 | 45분 | **10분** | 기회 극대화 |
| 리스크/거래 | 3% | **2%** | 빈도↑ 리스크 분산 |
| 동시 포지션 | 3개 | **5개** | 다중 심볼 활용 |
| 일일 한도 | 10% | **15%** | 기회 확대 |
| Circuit breaker | MDD 30%, 2시간 | **MDD 50%, 1시간** | 거래 중단 방지 |
| 연패 축소 | 3연패 | **5연패** | 빈도 유지 |
| 긴급 청산 | 잔고 50% | **잔고 40%** | 생존 범위 확대 |
| 시장가 임계값 | 0.85 | **0.70** | 빠른 체결 |
| 알트코인 | 7개 | **12개** | 심볼 확대 |

## 동적 레버리지

| 시그널 스코어 | 레버리지 |
|--------------|---------|
| < 2.0 | 8x (약한 시그널) |
| 2.0 ~ 2.5 | 10x |
| 2.5 ~ 3.0 | **15x** |
| 3.0+ | **20x** |
| + 대량 거래 확인 | +2x 부스트 |

## 변동성 적응 TP/SL

| 변동성 | TP 배수 | SL 배수 | R:R |
|--------|---------|---------|-----|
| 극한 | 3.0x | 1.5x | 2.0:1 |
| 높음 | 2.7x | 1.3x | 2.08:1 |
| 보통 | 2.5x | 1.2x | 2.08:1 |
| 낮음 | 2.0x | 1.0x | 2.0:1 |

## 거래 심볼 (13개)

BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, TRXUSDT, HBARUSDT, AVAXUSDT, LINKUSDT, BNBUSDT, MATICUSDT, DOTUSDT

## 배포

```bash
fly deploy
python main.py
python -m pytest tests/  # 105 tests
python backtest/run_comparison.py
```

## 기술 스택

Python 3.11 | Bybit (pybit) | EMA/RSI/ADX/MACD/BB + 캔들패턴 | 15m+4H | fly.io Tokyo | Telegram | SQLite | pytest 105
