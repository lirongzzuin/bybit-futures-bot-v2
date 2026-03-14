# Crypto Futures Auto-Trading Bot

> Bybit USDT Perpetual Futures 자동매매 시스템 — ATM (Adaptive Trend-Momentum) 전략

실시간으로 **거래량 상위 20개 암호화폐**를 모니터링하여, 시장 레짐에 적응하는 기술적 분석 시그널에 따라 자동으로 롱/숏 포지션 진입 및 청산을 수행하는 트레이딩 봇입니다.

---

## Overview

| 항목 | 내용 |
|------|------|
| **거래소** | Bybit (USDT 무기한 선물) |
| **전략** | ATM — Adaptive Trend-Momentum (시장 레짐 적응형) |
| **타임프레임** | 4시간봉 |
| **모니터링** | 거래량 상위 20개 심볼 (4시간마다 갱신) |
| **레버리지** | 2x ~ 7x (ADX 기반 동적 조절) |
| **리스크** | 거래당 1.5%, 최대 3 동시 포지션, 일일 4% 한도 |
| **인프라** | fly.io (도쿄 리전, 영구 볼륨) |
| **알림** | Slack 실시간 알림 (진입/청산/브리핑) |

### Backtest Performance (BTCUSDT, 3개 기간 검증)

| 기간 | 수익률 | MDD | 승률 | PF | 거래수 | Sharpe |
|------|--------|-----|------|-----|--------|--------|
| **최근 1년** (2025-2026) | **+29.5%** | 34.5% | 37.0% | 1.15 | 46 | 2.59 |
| **약세장 1년** (2022-2023) | **+11.1%** | 36.8% | 32.6% | 1.08 | 43 | 1.40 |
| **COVID 1년** (2020-2021) | **+200.6%** | 33.2% | 42.0% | 2.00 | 50 | 12.68 |

모든 시장 조건(강세/약세/혼합)에서 수익성을 검증했습니다.

---

## 매매 전략 (ATM v3.0)

### 핵심 원리

1. **시장 레짐 감지** (ADX): 추세장과 횡보장을 자동 구분
2. **매크로 트렌드 필터** (EMA200): 큰 추세 방향으로만 거래
3. **다중 확인** (EMA+RSI+MACD+DMI): 4개 이상 지표 합의 시에만 진입
4. **동적 SL/TP** (ATR): 변동성에 따라 자동 조정
5. **보수적 레버리지**: 최대 7x (기존 30x에서 대폭 하향)

### 1. 시장 레짐 감지

```
ADX >= 25 → 추세 모드 (Trend Following)
ADX < 20  → 횡보 모드 (Mean Reversion)
ADX 20~25 → 전환 구간 (매매 보류)
```

### 2. 진입 조건

**추세 모드 (ADX >= 25)**:
```
롱 진입 조건 (모두 충족 필요):
  ✅ EMA9 > EMA21 > EMA50 (3봉 이상 유지)
  ✅ RSI > 50 (모멘텀 확인)
  ✅ MACD > Signal Line (2봉 이상 양의 히스토그램)
  ✅ DI+ > DI- (방향성 확인)
  ✅ 가격 > EMA200 (매크로 상승 추세)

숏 진입 조건: 위의 반대
```

**횡보 모드 (ADX < 20)**:
```
롱 진입: 가격 <= 하단 볼린저 밴드 + RSI < 25 + RSI 반등
숏 진입: 가격 >= 상단 볼린저 밴드 + RSI > 75 + RSI 하락
```

### 3. 동적 레버리지

| ADX | 레버리지 | 근거 |
|-----|---------|------|
| >= 40 (매우 강한 추세) | 5x | 확실한 추세에서 적극 진입 |
| 30~40 (강한 추세) | 4x | |
| 25~30 (보통 추세) | 3x | |
| < 20 (횡보) | 2x | 방향 불확실 → 보수적 |

### 4. 동적 SL/TP (ATR 변동성 기반)

| ATR 백분위 | SL | TP | 특성 |
|-----------|-----|-----|------|
| 75% 이상 (고변동성) | 2.5x ATR | 5.4x ATR | 넓은 범위로 노이즈 회피 |
| 25~75% (보통) | 2.0x ATR | 4.5x ATR | 기본 설정 |
| 25% 미만 (저변동성) | 1.5x ATR | 3.8x ATR | 좁은 범위로 빠른 익절 |

### 5. 리스크 관리

| 안전장치 | 기준 |
|----------|------|
| 최대 동시 포지션 | **3개** |
| 거래당 리스크 | 계좌의 **1.5%** |
| 일일 최대 손실 | **4%** → 당일 매매 중단 |
| 3연패 시 | 포지션 사이즈 **50% 자동 축소** |
| 긴급 청산 | 계좌 잔고 **50% 이하** → 전 포지션 즉시 청산 |
| 쿨다운 | 진입 후 **12봉(2일)** 재진입 금지 |
| 시간 청산 | **60봉** 내 유의미한 수익 없으면 자동 청산 |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     fly.io (Tokyo)                       │
│                                                         │
│  ┌──────────┐   ┌─────────────┐   ┌──────────────────┐ │
│  │  main.py  │──▶│  ATM v3.0   │──▶│  Order Manager   │ │
│  │ Scheduler │   │  Strategy   │   │  (Entry/Exit)    │ │
│  └────┬─────┘   └──────┬──────┘   └────────┬─────────┘ │
│       │                │                    │           │
│  ┌────▼─────┐   ┌──────▼──────┐   ┌────────▼─────────┐ │
│  │ Position  │   │    Risk     │   │  Bybit Client    │ │
│  │ Monitor   │   │  Manager    │   │  (pybit SDK)     │ │
│  │ (1min)    │   │             │   │                  │ │
│  └──────────┘   └─────────────┘   └────────┬─────────┘ │
│                                             │           │
│  ┌──────────┐   ┌─────────────┐             │           │
│  │  SQLite  │   │   Slack     │◀────────────┘           │
│  │  (logs)  │   │  Notifier   │                         │
│  └──────────┘   └─────────────┘                         │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
                   ┌─────────────┐
                   │  Bybit API  │
                   │  (Mainnet)  │
                   └─────────────┘
```

---

## Tech Stack

| 영역 | 기술 |
|------|------|
| **Language** | Python 3.11 |
| **Exchange API** | pybit SDK (Bybit Unified Trading API) |
| **Strategy** | pandas, numpy (EMA, RSI, MACD, ADX, Bollinger Bands) |
| **Database** | SQLite (거래 기록, 일일 요약) |
| **Infra** | fly.io (Docker, Persistent Volume) |
| **Monitoring** | Slack Webhook, 1분 주기 포지션 모니터링 |
| **CI/CD** | GitHub Actions → fly.io 자동 배포 |
| **Scheduling** | schedule (4h candle + 1min monitor) |
| **Web** | Flask (Health Check Endpoint) |

---

## Project Structure

```
trading-bot/
├── main.py                    # 메인 봇 (스케줄러, 포지션 관리)
├── backtest_runner.py         # 멀티 기간 백테스트 실행기
├── config/
│   ├── settings.py            # API 키, 환경변수
│   └── indicator_params.py    # 지표/거래 파라미터
├── strategy/
│   ├── final_strategy.py      # ATM v3.0 핵심 전략
│   ├── signal_generator.py    # 보조 지표 계산
│   ├── risk_manager.py        # 리스크 관리
│   └── position_manager.py    # TP/SL/트레일링 로직
├── exchange/
│   ├── bybit_client.py        # Bybit API 래퍼
│   └── order_manager.py       # 주문 실행
├── backtest/
│   ├── engine.py              # 백테스트 엔진
│   ├── optimizer.py           # 전략 최적화
│   └── report.py              # HTML 리포트
├── data/
│   ├── storage.py             # SQLite 거래 기록
│   └── fetcher.py             # 시세 데이터 수집 (ccxt)
├── utils/
│   ├── logger.py              # 로깅
│   ├── notifier.py            # Slack 알림
│   └── briefing.py            # 정기 브리핑 생성
├── Dockerfile                 # Docker 이미지
├── fly.toml                   # fly.io 배포 설정
└── requirements.txt           # Python 패키지
```

---

## Deployment

### 환경변수 설정

```bash
# .env
BYBIT_API_KEY=your_api_key
BYBIT_API_SECRET=your_api_secret
BYBIT_TESTNET=false
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### fly.io 배포

```bash
# 최초 배포
flyctl launch
flyctl volumes create bot_data --region nrt --size 1

# 업데이트 배포
flyctl deploy --strategy rolling

# 상태 확인
flyctl status
flyctl logs
```

### 로컬 실행

```bash
pip install -r requirements.txt
python main.py
```

### 백테스트 실행

```bash
python backtest_runner.py
```

---

## Monitoring

| 채널 | 내용 | 주기 |
|------|------|------|
| **로그** | 포지션별 수익률, SL, TP 현황 | **1분** |
| **Slack** | 포지션 진입/청산 알림, PnL | 실시간 |
| **Slack** | 포트폴리오 브리핑 | 6시간 |
| **HTTP** | `/health` — 봇 상태 | 30초 (fly.io) |
| **HTTP** | `/status` — 잔고, 포지션, 심볼 | 요청 시 |

---

## License

This project is for personal use and educational purposes.
