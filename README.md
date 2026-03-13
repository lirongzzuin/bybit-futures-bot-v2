# Crypto Futures Auto-Trading Bot

> Bybit USDT Perpetual Futures 자동매매 시스템 — Channel Breakout + Momentum 전략 기반

실시간으로 **거래량 상위 100개 암호화폐**를 모니터링하여, 기술적 분석 시그널에 따라 자동으로 롱/숏 포지션 진입 및 청산을 수행하는 트레이딩 봇입니다.

---

## Overview

| 항목 | 내용 |
|------|------|
| **거래소** | Bybit (USDT 무기한 선물) |
| **전략** | Channel Breakout + Momentum (추세 추종) |
| **타임프레임** | 4시간봉 |
| **모니터링** | 거래량 상위 100개 심볼 (4시간마다 갱신) |
| **레버리지** | 5x ~ 30x (시그널 강도 기반 동적 조절) |
| **인프라** | fly.io (도쿄 리전, 영구 볼륨) |
| **알림** | Slack 실시간 알림 (진입/청산/브리핑) |

### Backtest Performance (BTCUSDT 1년)

| 지표 | 결과 |
|------|------|
| 수익률 | **791%** ($10,000 → $89,096) |
| 샤프비율 | 15.33 |
| MDD | 31.0% |
| 승률 | 56.1% |
| Profit Factor | 2.72 |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     fly.io (Tokyo)                       │
│                                                         │
│  ┌──────────┐   ┌─────────────┐   ┌──────────────────┐ │
│  │  main.py  │──▶│  Strategy   │──▶│  Order Manager   │ │
│  │ Scheduler │   │  Engine     │   │  (Entry/Exit)    │ │
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

## 매매 전략

### 1. 진입 조건 (언제 포지션에 들어가는가?)

봇은 4시간마다 캔들이 마감될 때 아래 **5가지 시그널**을 동시에 분석합니다.
여러 시그널이 같은 방향을 가리킬수록 "강한 시그널"로 판단하여 더 적극적으로 진입합니다.

```
시그널 분석 흐름:

  가격 데이터 (4시간봉)
        │
        ▼
  ┌─────────────────────────────────────────────┐
  │  A. 채널 돌파 (핵심)                          │
  │     30봉 최고가 돌파 → 롱 / 최저가 이탈 → 숏    │
  │                                              │
  │  B. RSI 모멘텀                                │
  │     RSI가 50선을 상향 돌파 → 롱 / 하향 → 숏     │
  │                                              │
  │  C. 이동평균 크로스                             │
  │     5일선이 20일선 상향 돌파 → 롱 / 하향 → 숏    │
  │                                              │
  │  D. 이중 스토캐스틱                             │
  │     단기+장기 모두 50 이상 → 롱 / 이하 → 숏      │
  │                                              │
  │  E. 일목균형표 구름                             │
  │     가격이 구름 위 → 롱 보조 / 구름 아래 → 숏 보조 │
  └──────────────────┬──────────────────────────┘
                     │
                     ▼
           ┌─────────────────┐
           │   추세 필터      │
           │  30봉 이동평균    │
           │  방향 확인       │
           └────────┬────────┘
                    │
                    ▼
             강도 0.2 ~ 1.0
             (시그널 일치도)
```

**핵심 원칙**: 여러 지표가 같은 방향을 가리킬 때만 진입합니다.
추세 방향과 반대인 시그널은 무시합니다 (예: 하락 추세에서 롱 시그널 → 무시).

### 2. 포지션 사이즈 & 레버리지 (얼마나 투자하는가?)

시그널 강도에 따라 투자 규모를 자동으로 조절합니다:

| 시그널 강도 | 레버리지 | 포지션 비중 | 주문 방식 |
|-------------|----------|------------|-----------|
| **0.9 이상** (매우 강함) | 30x | 1.5x | 시장가 (즉시 체결) |
| **0.7 이상** (강함) | 20x | 1.5x | 시장가 |
| **0.5 이상** (보통) | 15x | 1.0x | 시장가 |
| **0.4 이상** (약간) | 12x | 1.0x | 지정가 (유리한 가격 대기) |
| **0.4 미만** (약함) | 5~7x | 0.7x | 지정가 |

- 확신이 높을수록 적극적으로, 낮을수록 보수적으로 진입
- 지정가 주문은 **5분 내 미체결 시 자동 취소** (기회비용 최소화)

### 3. 포지션 관리 (들어간 후 어떻게 관리하는가?)

진입 후에는 **1분마다 실시간으로** 모든 포지션을 모니터링합니다:

```
포지션 진입
    │
    ▼
┌───────────────────────────────────────────┐
│          1분마다 실시간 모니터링              │
│                                           │
│  1. SL 보호 (항상 활성)                     │
│     └ 진입 시 서버사이드 SL 설정             │
│     └ SL 없는 포지션 감지 → 즉시 5% SL 설정  │
│                                           │
│  2. 급락/급등 보호                          │
│     └ 손실 8% 초과 → 즉시 시장가 청산        │
│                                           │
│  3. 트레일링 SL (수익 3% 이상 시 활성화)      │
│     └ 최고가 추적 → 이익의 60% 보호          │
│     └ SL을 수익 방향으로만 이동 (되돌리지 않음) │
│                                           │
│  4. 부분 익절 (TP 목표 50% 도달 시)          │
│     └ 포지션의 50% 청산                     │
│     └ 나머지 50%는 SL을 본전으로 이동 후 홀딩  │
│                                           │
│  5. TP 확대 (TP 목표 80% 도달 시)           │
│     └ TP를 50% 더 확대 → 수익 극대화         │
└───────────────────────────────────────────┘
```

### 4. 청산 조건 (언제 나오는가?)

| 조건 | 동작 | 체크 주기 |
|------|------|----------|
| **TP 도달** | 서버사이드 자동 청산 | 실시간 (Bybit) |
| **SL 도달** | 서버사이드 자동 청산 | 실시간 (Bybit) |
| **반대 시그널** | 즉시 전량 시장가 청산 | 4시간봉 마감 |
| **급락/급등** | 손실 8% 초과 시 즉시 청산 | 1분 |
| **트레일링 SL** | 수익 보호 후 되돌림 시 청산 | 1분 |

### 5. 리스크 관리 (안전장치)

| 안전장치 | 기준 |
|----------|------|
| 최대 동시 포지션 | **5개** (thread-safe 슬롯 관리) |
| 거래당 리스크 | 계좌의 **2%** |
| 일일 최대 손실 | **6%** → 당일 매매 중단 |
| 3연패 시 | 포지션 사이즈 **50% 자동 축소** |
| 긴급 청산 | 계좌 잔고 **50% 이하** → 전 포지션 즉시 청산 |
| 수수료 고려 | 진입/청산 수수료를 포지션 사이즈에 반영 |

---

## 주요 기술적 의사결정

### 왜 서버사이드 TP/SL인가?
봇이 다운되어도 Bybit 서버에서 TP/SL이 독립적으로 실행됩니다. 네트워크 장애나 서버 재시작 중에도 포지션이 보호됩니다.

### 왜 Hedge Mode인가?
같은 심볼에 대해 롱/숏 포지션을 독립적으로 관리할 수 있습니다. `positionIdx`로 방향별 포지션을 구분합니다.

### 왜 Thread-safe 슬롯 관리인가?
지정가 주문의 체결 확인은 별도 스레드에서 비동기로 실행됩니다. `threading.Lock`으로 포지션 제한을 원자적으로 관리하여 Race Condition을 방지합니다.

### 왜 거래량 상위 100개인가?
유동성이 높은 심볼에서만 거래하여 슬리피지를 최소화합니다. 4시간마다 거래량 순위를 갱신하여 시장 변화에 적응합니다.

---

## Tech Stack

| 영역 | 기술 |
|------|------|
| **Language** | Python 3.11 |
| **Exchange API** | pybit SDK (Bybit Unified Trading API) |
| **Strategy** | pandas, numpy, ta (Technical Analysis) |
| **Database** | SQLite (거래 기록, 일일 요약) |
| **Infra** | fly.io (Docker, Persistent Volume) |
| **Monitoring** | Slack Webhook (실시간 알림), 1분 주기 포지션 모니터링 |
| **CI/CD** | GitHub Actions → fly.io 자동 배포 |
| **Scheduling** | schedule (4h candle + 1min monitor) |
| **Web** | Flask (Health Check Endpoint) |

---

## Project Structure

```
trading-bot/
├── main.py                    # 메인 봇 (스케줄러, 포지션 관리)
├── config/
│   ├── settings.py            # API 키, 환경변수
│   └── indicator_params.py    # 지표/거래 파라미터
├── strategy/
│   ├── final_strategy.py      # 핵심 전략 (시그널 생성)
│   ├── signal_generator.py    # 보조 지표 계산
│   ├── risk_manager.py        # 리스크 관리 (포지션 제한, 사이징)
│   └── position_manager.py    # TP/SL/트레일링 로직
├── exchange/
│   ├── bybit_client.py        # Bybit API 래퍼 (주문, 포지션, 시세)
│   └── order_manager.py       # 주문 실행 (시장가/지정가, 부분 익절)
├── data/
│   ├── storage.py             # SQLite 거래 기록
│   └── fetcher.py             # 시세 데이터 수집
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

### CI/CD (GitHub Actions → fly.io 자동 배포)

`main` 브랜치에 push하면 GitHub Actions가 자동으로 fly.io에 배포합니다.

```yaml
# .github/workflows/deploy.yml
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --strategy rolling --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

**설정 방법:**
1. `flyctl tokens create deploy` 로 배포 토큰 생성
2. GitHub 레포지토리 → Settings → Secrets → `FLY_API_TOKEN` 추가
3. 이후 `git push` 시 자동 배포

### fly.io 수동 배포

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

---

## Monitoring

| 채널 | 내용 | 주기 |
|------|------|------|
| **로그** | 포지션별 수익률, SL, TP 현황 | **1분** |
| **Slack** | 포지션 진입/청산 알림, PnL | 실시간 |
| **Slack** | 포트폴리오 브리핑 | 6시간 (KST 09/15/21/03시) |
| **HTTP** | `/health` — 봇 상태 | 30초 (fly.io) |
| **HTTP** | `/status` — 잔고, 포지션, 심볼 | 요청 시 |
| **SQLite** | 전체 거래 기록 | 매 거래 |

1분 모니터링 로그 예시:
```
[모니터] 3개 포지션 | FUSDT L pnl=+0.02% SL=0.005372 TP=0.006048 | GALAUSDT L pnl=+0.39% SL=0.003369 TP=0.003771 | XPLUSDT L pnl=+0.99% SL=0.10386 TP=0.11929
```

---

## Key Engineering Challenges & Solutions

### 1. Bybit Unified Trading Account 잔고 조회 이슈
**문제**: `availableToWithdraw` API 필드가 빈 문자열을 반환하여 가용 잔고가 항상 0으로 계산됨
**해결**: Account-level `totalAvailableBalance`와 Coin-level `availableToWithdraw` 중 큰 값을 사용하는 이중 확인 로직 구현

### 2. Hedge Mode positionIdx 호환성
**문제**: Bybit Hedge Mode 계정에서 모든 주문/TP/SL에 `positionIdx` 필수 (1=Long, 2=Short)
**해결**: `_calc_position_idx()` 메서드로 진입/청산 방향에 따른 자동 인덱스 계산

### 3. 포지션 제한 Race Condition
**문제**: 지정가 주문의 비동기 체결 확인 중 동시에 새 주문이 들어가 제한을 초과 (5개 제한에 8개 진입)
**해결**: `threading.Lock` 기반 슬롯 예약 시스템 — 주문 즉시 "pending" 슬롯 예약, 미체결 시 해제

### 4. 시그널 소멸 오판 청산
**문제**: 4시간봉 시그널은 진입 시점에만 발생하고 다음 봉에서는 자연히 0이 되는데, 이를 "시그널 소멸"로 오판하여 진입 직후 전량 청산
**해결**: `direction=0`을 청산 사유에서 제거. 반대 방향 시그널이 명시적으로 발생할 때만 청산

### 5. 재시작 시 TP/SL 동기화 누락
**문제**: 봇 재배포 시 Bybit에서 포지션을 동기화하지만, 서버사이드에 설정된 TP/SL 값을 가져오지 않아 내부 추적 값이 0으로 초기화됨. 부분 익절/TP 확대 등 내부 로직이 작동하지 않는 문제
**해결**: `get_positions()` API 응답에서 `takeProfit`/`stopLoss` 필드를 함께 파싱하여 내부 상태에 반영

### 6. API Rate Limit 낭비
**문제**: 트레일링 SL이 동일한 값을 매분 반복 설정하여 "not modified" (34040) 에러 + 3회 재시도로 API 호출 낭비
**해결**: SL 변경 전 현재값 비교 + "not modified" 에러를 정상 응답으로 처리 (재시도 방지)

---

## License

This project is for personal use and educational purposes.
