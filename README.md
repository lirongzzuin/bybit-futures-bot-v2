# Crypto Futures Auto-Trading Bot — v9.2 Pullback-in-Trend

> Bybit USDT Perpetual Futures 자동매매 — 4H 트렌드 확인 후 15분봉 풀백 진입 전략

## 현재 상태: 실전 운용 (v9.2)

백테스트 4개 기간(2024 H2 ~ 2026 Q1) 교차 검증 + 페이퍼 트레이딩 검증 후 실전 배포.

### v9.2 변경사항 (2026-03-27) — 구조 개선 & 리스크 강화

| 항목 | 내용 |
|------|------|
| **코드 리팩토링** | main.py 1,325줄 → 654줄 (50% 감소). 포지션 관리(`bot/position_monitor.py`), 헬스체크 서버(`bot/health_server.py`) 모듈 분리 |
| **레거시 정리** | 미사용 전략/지표 코드 7개 파일 → `archive/`로 이동 (ichimoku, candle_master_plan, volume_analysis, signal_generator 등) |
| **MDD 관리 강화** | Drawdown 기반 포지션 사이징 추가 (10%→0.7x, 20%→0.5x, 30%+→0.3x) |
| **서킷 브레이커** | MDD 25% 초과 시 자동 매매 중단 |
| **변동성 감지** | ATR 기반 시장 변동성 레짐 감지 (low/normal/high/extreme) |
| **테스트 코드** | 104개 유닛 테스트 추가 (전략, 리스크, 포지션, 스토리지) |

**전략 파라미터 변경 없음** — v9.1 파라미터 유지, 동일 시그널 생성 보장

### 페이퍼 트레이딩 결과 (2026-03-18 ~ 03-25)

| 지표 | 값 |
|------|-----|
| 초기 잔고 | $10,000 |
| 최종 잔고 | **$14,189** (+41.89%) |
| 총 트레이드 | 26건 |
| 승률 | **76.9%** (20승 6패) |
| 평균 승: 평균 패 | $234 : $180 (R:R 1.3:1) |
| 최대 수익 거래 | +$1,004 (BNBUSDT) |

### v9.1 변경사항 (페이퍼 데이터 기반 최적화)

| 항목 | v9.0 | v9.1 | 이유 |
|------|------|------|------|
| ADX 최소값 | 25 | **23** | 거래 빈도 증가 (데이터: ADX 23-25 구간 유효) |
| RSI 롱 범위 | 25-40 | **25-43** | 풀백 진입 기회 확대 |
| RSI 숏 범위 | 60-75 | **57-75** | 반등 진입 기회 확대 |
| BB 마진 | 0.2 ATR | **0.35 ATR** | BB 근접 허용 (터치 강제 완화) |
| 쿨다운 | 8바 (2시간) | **6바 (1.5시간)** | 연속 기회 포착 |

### v9.0 백테스트 결과

| 기간 | 수익률 | 승률 | PF | MDD | 거래수 |
|------|--------|------|-----|-----|--------|
| 최근 3개월 | **+37.4%** | 35.1% | 1.14 | 33.2% | 74 |
| 2025 H2 | -18.7% | 32.9% | 0.96 | 54.3% | 164 |
| 2025 H1 | **+260.2%** | 36.4% | 1.25 | 46.2% | 184 |
| 2024 H2 | **+38.9%** | 35.0% | 1.06 | 47.3% | 180 |
| **평균** | **+79.5%** | | | | |

---

## 프로젝트 구조

```
trading-bot/
├── main.py                  # 메인 봇 (654줄, v9.2 리팩토링)
├── bot/
│   ├── position_monitor.py  # 포지션 동적 관리 (638줄)
│   └── health_server.py     # Flask 헬스체크/모니터링 서버
├── strategy/
│   ├── final_strategy.py    # Pullback-in-Trend v9.1 전략
│   ├── risk_manager.py      # 리스크 관리 (v9.2 MDD 강화)
│   └── position_manager.py  # TP/SL/트레일링 스탑
├── exchange/
│   ├── bybit_client.py      # Bybit API 래퍼
│   ├── order_manager.py     # 주문 실행/관리
│   └── paper_client.py      # 페이퍼 트레이딩 시뮬레이터
├── data/
│   ├── fetcher.py           # OHLCV 데이터 수집 (ccxt)
│   └── storage.py           # SQLite 거래 기록
├── utils/
│   ├── briefing.py          # 6시간 정기 브리핑
│   ├── logger.py            # 로깅
│   └── notifier.py          # Telegram 알림
├── backtest/
│   ├── engine.py            # 백테스트 엔진
│   ├── advanced_engine.py   # 고급 백테스트 (MFE/MAE)
│   └── optimizer.py         # 파라미터 최적화
├── tests/                   # 유닛 테스트 (104개)
│   ├── conftest.py
│   ├── test_final_strategy.py
│   ├── test_risk_manager.py
│   ├── test_position_manager.py
│   └── test_storage.py
├── config/
│   ├── settings.py          # 환경변수 기반 설정
│   └── indicator_params.py  # 전략/거래 파라미터
├── archive/                 # 레거시 코드 아카이브
│   ├── indicators/          # ichimoku, candle_master_plan, volume_analysis
│   └── strategy/            # signal_generator, advanced_strategy 등
├── Dockerfile
├── fly.toml
└── .github/workflows/deploy.yml
```

---

## 전략: Pullback-in-Trend

### 진입 조건 (5단계 필터)

```
1. 4H ADX >= 23          → 트렌드 존재 확인
2. 4H +DI/-DI 방향       → 트렌드 방향 결정
3. 4H EMA9 > EMA21       → 트렌드 정렬 확인
4. 15m RSI 풀백           → 과매도/과매수 확인
   - 롱: RSI 25-43 (상승 추세에서 눌림)
   - 숏: RSI 57-75 (하락 추세에서 반등)
5. 15m BB 밴드 터치       → 극단값 확인
   - 롱: 가격 <= BB 하단 + 0.35 ATR
   - 숏: 가격 >= BB 상단 - 0.35 ATR
```

### 추가 확인 (점수 가산)
- **MACD 히스토그램 반전**: 모멘텀 전환 시작 (+0.5점)
- **RSI 바운스**: RSI가 이전 봉 대비 반전 방향 (+0.5점)
- **최소 스코어 2.0** 이상만 진입

### TP/SL 설정
| 항목 | 값 |
|------|-----|
| Take Profit | **2.0x ATR** |
| Stop Loss | **0.8x ATR** |
| R:R | **2.5:1** |
| 레버리지 | **10x** |
| 쿨다운 | **6바 (1.5시간)** |

---

## 포지션 동적 관리

```
손실 -5%   → 긴급 청산
120분 보유 + 손실 1%+ → 타임아웃 청산
60분 보유 + 본전 근처 → 본전 SL 강제
수익 0.8%+ → SL을 본전으로 이동 (수수료 반영)
수익 1.5%+ → 트레일링 SL 시작 (수익의 40% 보호)
수익 2%+   → 트레일링 강화 (수익의 50% 보호)
수익 3%+   → 트레일링 강화 (수익의 60% 보호)
수익 5%+   → 트레일링 강화 (수익의 75% 보호)
수익 10%+  → 트레일링 최대 (수익의 85% 보호)
TP 60%     → 1차 부분 익절 (30% 청산)
TP 80%     → TP 자동 확대 (+50%) ← 무한 반복
TP 200%    → 2차 부분 익절 (추가 30% 청산)
```

### 시그널 기반 관리
- 반대 시그널 강도 0.65+ → 청산
- RSI 반전: 수익 3%+ AND RSI 78+/22- AND 3봉 연속 꺾임 → 익절
- ATR 수축 (평균의 50% 미만) + 수익 5%+ → SL 타이트닝 (85% 보호)

### 일봉 전환 보호 (KST 08:45~09:15)
- 신규 진입 차단
- 수익 중 포지션: SL을 수익의 70% 보호
- 본전 근처: 본전 SL 강제
- 손실 중: SL을 -4%로 타이트닝

---

## 리스크 관리

| 안전장치 | 기준 |
|----------|------|
| 거래당 리스크 | **2%** |
| 최대 동시 포지션 | **2개** |
| 일일 최대 손실 | **8%** → 매매 중단 |
| 3연패 시 | 포지션 50% 축소 |
| **MDD 10~20%** | **포지션 30% 축소** (v9.2) |
| **MDD 20~30%** | **포지션 50% 축소** (v9.2) |
| **MDD 30%+** | **포지션 70% 축소** (v9.2) |
| **서킷 브레이커** | **MDD 25% → 매매 자동 중단** (v9.2) |
| 긴급 청산 | 잔고 50% 이하 |
| 급락 보호 | -5% → 즉시 청산 |
| 타임아웃 | 120분 보유 + 손실 1%+ → 시장가 청산 |
| 재진입 쿨다운 | **45분** (동일 심볼) |
| 주문 방식 | 지정가(Maker) 우선 (수수료 0.02%) |
| 포지션당 마진 | 가용잔고의 최대 20% |

---

## 실시간 모니터링

| 항목 | 주기 | 설명 |
|------|------|------|
| 포지션 모니터링 | **30초** | TP/SL 체결 감지, 트레일링 SL 업데이트, 긴급 청산 |
| 캔들 분석 | **15분** | 매시 :01, :16, :31, :46에 시그널 생성 및 진입 |
| 메인 루프 | **5초** | 스케줄러 실행 체크 |
| 브리핑 | **6시간** | KST 03/09/15/21시 상태 보고 |

---

## 테스트

```bash
python -m pytest tests/ -v          # 전체 테스트 (104개)
python -m pytest tests/ -v -k "risk" # 리스크 관련만
```

| 테스트 모듈 | 테스트 수 | 커버리지 |
|-------------|-----------|----------|
| test_final_strategy.py | 26 | 지표 계산, 시그널 생성, 파라미터 검증 |
| test_risk_manager.py | 33 | 포지션 사이징, MDD, 서킷브레이커, 일봉보호 |
| test_position_manager.py | 22 | TP/SL, 트레일링, 청산 조건 |
| test_storage.py | 23 | DB CRUD, 통계, 분석 |

---

## 배포

```bash
fly deploy                # fly.io 배포
python main.py            # 로컬 실행
```

GitHub `main` 브랜치에 push 시 자동 배포 (`.github/workflows/deploy.yml`)

### 환경변수
```
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=false
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
PAPER_TRADING=false
PAPER_INITIAL_BALANCE=10000
```

### 모니터링 엔드포인트
| 엔드포인트 | 설명 |
|-----------|------|
| `/health` | 헬스체크 |
| `/status` | 봇 상태 (잔고, 포지션, 심볼) |
| `/paper-analysis` | 페이퍼 트레이딩 분석 |
| `/tp-sl-adjustments` | TP/SL 조정 이력 |
| `/post-exit` | 청산 후 가격 추적 분석 |
| `/skipped-signals` | 스킵된 시그널 통계 |

---

## 기술 스택

| 항목 | 내용 |
|------|------|
| 언어 | Python 3.11 |
| 거래소 | Bybit (pybit SDK) |
| 지표 | EMA, RSI, ADX/DMI, MACD, Bollinger Bands |
| 타임프레임 | 15분봉 진입, 4H 리샘플링 추세 확인 |
| 인프라 | fly.io (도쿄), Docker |
| 알림 | Telegram Bot API |
| DB | SQLite |
| 테스트 | pytest (104 tests) |

---

## License

This project is for personal use and educational purposes.
