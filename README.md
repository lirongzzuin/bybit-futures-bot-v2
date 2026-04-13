# Crypto Futures Bot v17.1 — 5m + 1H Momentum Scalper

> Bybit USDT 무기한 선물 자동매매 봇
> 5분봉 진입 + 1시간봉 추세 확인 + 동적 포지션 관리 (fly.io 상시 가동)

이 문서는 봇이 **어떤 데이터를 보고**, **어떤 조건에서 진입/청산**하는지, 그리고 **왜 그렇게 설계되어 있는지**를 누구나 따라갈 수 있게 정리한 운영 가이드입니다.

---

## 1. 한눈에 보는 구조

```
Bybit API ──▶ 5분봉 OHLCV + 거래량  ┐
                                     ├─▶ final_strategy.generate_signals() ─▶ 방향/스코어/TP/SL/레버리지
Bybit API ──▶ 5분봉 → 1H 리샘플링   ┘                                            │
                                                                                  ▼
                                                                      risk_manager.can_open_position()
                                                                                  │
                                                                                  ▼
                                                                      order_manager.execute_entry()
                                                                                  │
                                                   ┌──────────────────────────────┤
                                                   ▼                              ▼
                                     position_monitor (2초 주기)       webhook/health 서버
                                     - 본전 SL / 트레일링 SL
                                     - 부분 익절 / 긴급 청산
                                     - 시간 기반 타임아웃
                                                   │
                                                   ▼
                                         trades.db + Telegram 알림
```

| 구성 요소 | 역할 | 파일 |
|---|---|---|
| 데이터 수집 | Bybit ccxt에서 5m OHLCV + 티커 실시간 조회 | `data/fetcher.py`, `data/collector.py` |
| 신호 생성 | 1H 추세 + 5m 풀백 감지, 스코어 기반 점수화 | `strategy/final_strategy.py` |
| 리스크 판단 | 진입 가능 여부·포지션 사이즈 계산 | `strategy/risk_manager.py` |
| 주문 실행 | Bybit 주문 API, TP/SL 서버 등록 | `exchange/bybit_client.py`, `exchange/order_manager.py` |
| 포지션 동적 관리 | 본전 SL, 트레일링, 부분 익절, 긴급 청산 | `bot/position_monitor.py` |
| 메인 루프 | 1초 메인루프 / 2초 포지션 모니터 / 5분 신호 분석 | `main.py` |
| 영속 저장 | trades, skipped_signals, tp_sl_adjustments, bot_state | `data/storage.py` (SQLite) |

---

## 2. 어떤 데이터를 보는가

### 2.1 입력 데이터

| 데이터 | 타임프레임 | 출처 | 용도 |
|---|---|---|---|
| OHLCV (open/high/low/close/volume) | **5분** | Bybit USDT Perp (`GET /v5/market/kline`, ccxt) | LTF 진입 지표 (RSI/BB/EMA/ATR/MACD/캔들 패턴/거래량) |
| 1시간봉 | 5분봉을 내부 리샘플링 | `_resample_to_htf(df, "1h")` | HTF 추세 판단 (EMA9/21, ADX/+DI/-DI) |
| 현재가 (ticker) | 실시간 | Bybit `/v5/market/tickers` (2초 주기) | 포지션 동적 관리, TP/SL 감지 |
| 잔고/포지션 | 실시간 | Bybit `/v5/account/wallet-balance`, `/v5/position/list` | 리스크 매니저 상태 동기화 |

### 2.2 대상 심볼

`config/indicator_params.py`의 `SYMBOLS`에서 관리합니다. 기본 1개(BTCUSDT) + 알트 12개, 실제 봇은 거래량 상위 30개를 주기적으로 갱신합니다 (`main.py._load_strategy_symbols`).

### 2.3 저장되는 데이터 (`data/trades.db`)

| 테이블 | 내용 |
|---|---|
| `trades` | 진입/청산 모든 주문 (가격, 수량, 레버리지, PnL, exit_reason) |
| `daily_summary` | 일별 승률/총 PnL/잔고 |
| `skipped_signals` | 진입 스킵 기록 (스코어 부족, 쿨다운, 상관 등) |
| `post_exit_tracking` | 청산 후 15/30/60/120/240분 가격 추적 (exit 타이밍 검증) |
| `tp_sl_adjustments` | SL/TP 동적 변경 히스토리 |
| `bot_state` | 재시작 시 상태 복원용 key-value |

---

## 3. 진입 조건 (5분봉이 새로 닫힐 때마다 평가)

### 3.1 1시간봉 추세 필터 (HTF) — 모두 통과해야 진입 가능

1. **ADX(14) ≥ 28** — 약한 추세 필터링 (v17.0에서 상향)
2. **ADX 2봉 연속 28 이상** — 단발성 스파이크 배제
3. **상승 추세** = `+DI > -DI AND EMA9 > EMA21`
4. **하락 추세** = `-DI > +DI AND EMA9 < EMA21`
5. 둘 다 아니면 진입 없음 (횡보 회피)

### 3.2 5분봉 풀백 진입 스코어 (min_score **2.7점** 이상)

스코어는 누적 방식. 방향은 HTF 추세와 일치해야 함.

| 조건 | 점수 | 설명 |
|---|---|---|
| HTF 방향과 일치하는 RSI 풀백 (Long 25~35, Short 65~75) | **+1.5** | 진입 시작 요건 |
| 볼린저 밴드 하단(롱)/상단(숏) **근접 필수** (ATR × 0.30 이내) | 필수 | 풀백 깊이 확인, 아니면 스킵 |
| EMA21 근처 (ATR × 0.3 이내) | +0.8 | 지지/저항 터치 |
| MACD 히스토그램 2봉 연속 개선 | +0.7 | 추세 재개 확인 |
| MACD 1봉 개선 | +0.4 | 약한 반전 |
| 직전 봉 대비 RSI 방향 전환 | +0.5 | 모멘텀 반전 |
| 해머/상승장악 등 캔들 패턴 | +0.4~0.7 | 반전 패턴 |
| ADX ≥ 35 | +0.3 | 강한 추세 보너스 |
| 거래량 스파이크 (평균 대비 1.5x/2.0x/3.0x) | +0.3/+0.5/+0.8 | 참가자 유입 확인 |

**최종 스코어 < 2.7 이면 진입하지 않음.**

### 3.3 동적 TP/SL 계산 (변동성 레짐별)

ATR과 평균 ATR(50봉)의 비율로 레짐을 결정하고 거기 맞춰 TP/SL 배수를 씁니다:

| 레짐 | ATR 비율 | TP (× ATR) | SL (× ATR) | R:R |
|---|---|---|---|---|
| extreme | ≥ 2.0 | 3.0 | 2.0 | 1.5:1 |
| high | 1.3 ~ 2.0 | 2.7 | 1.8 | 1.5:1 |
| normal | 0.7 ~ 1.3 | **2.5** | **1.7** | ~1.47:1 |
| low | < 0.7 | 2.0 | 1.4 | ~1.43:1 |

- ADX ≥ 30 이면 TP +0.3x, ADX ≥ 40 이면 TP +0.5x (추세 강할수록 목표 확대)
- 가격 대비 **최소 TP 2.0%, 최소 SL 1.7%** 강제 (ATR이 너무 작을 때 노이즈 스탑아웃 방지)
- v17.1: SL을 1.5→1.7x ATR로 확대 → 정상 리트레이스에 스탑아웃되는 문제 완화

### 3.4 동적 레버리지

| 스코어 | 레버리지 |
|---|---|
| < 2.0 | max(5, 기본-5) = 5x |
| 2.0 ~ 2.5 | 기본 (5x) |
| ≥ 2.5 | `5 + (score - 3.5) × 2.0`, 최대 **8x** |

### 3.5 포지션 사이즈

```
risk_amount = balance × 1.5%
position_value = risk_amount / (|entry − sl| / entry)
quantity = position_value / entry
```
- 4연패 이상이면 `risk_pct × 0.5` (과열 보호)
- 드로다운 10%부터 사이즈 축소 (10–15% → 0.7 / 15–25% → 0.5 / 25%+ → 0.3)

---

## 4. 진입 차단 조건 (안전장치)

하나라도 해당되면 신규 진입 불가 (`risk_manager.can_open_position`):

1. **일봉 전환 보호**: KST 08:45 ~ 09:15 (UTC 23:45 ~ 00:15) — 스톱헌팅/청산 캐스케이드 시간대
2. **일일 손실 ≥ 5%**: 당일 매매 중단 (다음날 자동 재개)
3. **최대 동시 포지션 3개** (v17.1: 2→3)
4. **동일 심볼 기존 포지션 보유 중**
5. **잔고 ≤ 초기 자본 × 30%**: 긴급 정지
6. **Circuit breaker (MDD 30% 초과)**: 2시간 쿨링 후 peak 리셋
7. **심볼 간 상관계수 ≥ 0.80**: 동시 진입 차단 (같은 움직임 방지)

---

## 5. 포지션 동적 관리 (2초 주기) — v17.1 핵심 수정부

`bot/position_monitor.py`가 2초마다 각 포지션의 현재가를 조회하고 상태를 점검합니다.

### 5.1 상시 안전장치

| 조건 | 동작 |
|---|---|
| TP/SL 둘 다 없음 (동기화된 포지션) | 기본 TP +3%, SL −5% 자동 설정 |
| **unrealized ≤ −8%** (급락) | 시장가 긴급 청산 + 20분 쿨다운 |
| **150분+ 보유 + 손실 1.5%+** | 타임아웃 시장가 청산 |
| **120분+ 보유 + 수익 0.5% 미만** (v17.1: 60→120) | 본전 SL 강제 |

### 5.2 Stage 1 — 본전 SL 이동 (v17.1 수정 핵심)

- **트리거: unrealized ≥ 2.5%** (v17.0: 1.5% → v17.1: 2.5%)
- SL을 `entry + 왕복 수수료(0.075%)`로 이동 → 최악의 경우 수수료만 덮는 보장
- v17.0에서는 1.5% 수익에서 본전 SL이 설정되어 **정상 풀백에 조기 스탑아웃**되는 "본전 SL 저주" 발생
- 실거래 7건 분석: 승률 71%였지만 R:R 0.27:1 — 승리 크기가 수수료 근처로 수렴
- v17.1은 풀백이 한 번 더 숨 쉴 공간(2.5% 기준)을 확보

### 5.3 Stage 2 — 변동성 적응 트레일링 SL

- **트리거: unrealized ≥ 3.5%** (v17.0: 2.5% → v17.1: 3.5%)
- 보호 비율 하향:

| 수익 구간 | v17.0 보호 비율 | **v17.1 보호 비율** |
|---|---|---|
| 3.5 ~ 4.5% | 30% | **25%** |
| 4.5 ~ 6% | 45% | **35%** |
| 6 ~ 10% | 60% | **50%** |
| 10%+ | 75% | **65%** |

- 변동성 레짐 보정: high/extreme이면 보호 비율 -5%p (여유), low면 +5%p (타이트)
- 수익 거래가 조기 종료되던 문제 완화

### 5.4 Stage 3 — 부분 익절

- **TP 진행률 60% 도달**: 30% 물량 청산 (1차 부분 익절)
- **TP 진행률 200% 도달**: 추가 30% 청산 (2차 부분 익절)

### 5.5 ~~Stage 4 — TP 무한 확대~~ (v17.1에서 **비활성화**)

- v17.0: TP 80% 도달 시마다 TP를 +50%씩 확대
- 실거래 분석 결과 TP 근처 도달 자체가 드물었고, 확대 후 트레일링에 먼저 걸리는 경우가 많아 기여 없음
- v17.1: 초기 TP(ATR 2.5x)를 그대로 유지

### 5.6 Stage 6 — 시그널 기반 청산

| 조건 | 동작 |
|---|---|
| 강한 반대 시그널 (강도 ≥ 0.65) | 시장가 청산 + 20분 쿨다운 |
| 수익 3%+ 롱 + RSI > 78 + 3봉 연속 RSI 하락 | 시장가 익절 |
| 수익 3%+ 숏 + RSI < 22 + 3봉 연속 RSI 상승 | 시장가 익절 |
| **수익 8%+ + ATR 급감 (평균의 50% 미만)** (v17.1: 5→8%) | SL을 현재 수익의 85%로 타이트닝 |

### 5.7 주문 실행 방식

- **시그널 강도 ≥ 0.70**: 시장가 진입 (Taker 0.055%)
- **시그널 강도 < 0.70**: 지정가 진입 (Maker 0.02%), 2분 내 미체결 시 취소

---

## 6. 리스크 관리 요약 (v17.1)

| 항목 | 값 |
|---|---|
| 거래당 리스크 | **1.5%** (잔고 기준) |
| 최대 동시 포지션 | **3개** (v17.1: 2→3) |
| 레버리지 | 5x 기본 / 최대 8x (동적) |
| 일일 최대 손실 | 5% → 당일 매매 중단 |
| Circuit breaker | MDD 30% → 2시간 쿨링 |
| 긴급 청산 잔고 | 초기 자본의 50% 이하 |
| 재진입 쿨다운 | 20분 (동일 심볼) |
| 4연패 이상 | 포지션 사이즈 50% 축소 |
| 수수료 | Maker 0.02% / Taker 0.055% (PnL에서 차감) |

---

## 7. 실시간 운영 주기

| 작업 | 주기 |
|---|---|
| 메인 루프 | **1초** |
| 포지션 모니터링 (현재가·TP/SL 체결·Stage 관리) | **2초** |
| 심볼별 신호 분석 (30개 심볼) | **5분** |
| 거래량 상위 심볼 갱신 | 매시간 |
| 텔레그램 알림 (진입/청산/동기화/오류) | 즉시 |
| Health check 서버 | 30초 |

---

## 8. v17.1 변경사항 요약 (v17.0 실거래 분석 기반)

v17.0 실거래 7건 (2026-04-12 배포 후 약 24시간) 분석 결과:
- 승률 71% (5승 2패)
- 총 PnL **-0.61 USDT**
- 평균 승 +0.264 / 평균 패 -0.963 → **실제 R:R 0.27:1** (의도 1.67:1의 1/6)
- 근본 원인: `position_monitor.py` Stage 1 본전 SL이 **1.5% 수익에서 트리거** → 정상 풀백마다 조기 탈출

### 적용된 변경

| 영역 | v17.0 | v17.1 | 이유 |
|---|---|---|---|
| Stage 1 본전 SL 트리거 | 수익 1.5% | **수익 2.5%** | 본전 SL 저주 해소 |
| Stage 2 트레일링 활성화 | 수익 2.5% | **수익 3.5%** | 추세 초기 숨쉴 공간 |
| Stage 2 보호 비율 | 30/45/60/75% | **25/35/50/65%** | 수익 거래 조기 종료 방지 |
| Stage 4 TP 확대 | 활성화 | **비활성화** | 기여 없음, 트레일링이 먼저 걸림 |
| 60분 본전 타이트닝 | 60분+수익<1% | **120분+수익<0.5%** | 추세 초기 여유 확보 |
| ATR 수축 SL 타이트닝 | 수익 5%+ | **수익 8%+** | 중간 수익 구간 학살 방지 |
| `sl_atr_mult` | 1.5 | **1.7** | 노이즈 스탑아웃 추가 완화 |
| `min_score` | 3.0 | **2.7** | 기회 빈도 확대 |
| `max_open_positions` | 2 | **3** | 심볼 분산 |
| 버전 문자열 | momentum_scalper_v17.0 | momentum_scalper_v17.1 | — |

### 비교 백테스트 (5분봉, 5심볼 × 3구간 / 최근 90일)

| 지표 | v17.0 | v17.1 | 비고 |
|---|---|---|---|
| 총 trades | 307 | 364 | v17.1이 기회 더 많음 |
| 평균 승률 | 29.6% | 27.7% | 소폭 하락 |
| 평균 R:R | 2.98 | **3.29** | v17.1이 더 좋음 |
| 평균 MDD | 15.0% | **14.4%** | v17.1이 약간 양호 |
| 합산 수익률 | +140.9% | +102.6% | 엔진 시뮬레이션상 v17.0이 나음 |

**⚠️ 중요한 한계**: 이 백테스트는 `BacktestEngine`이 고정 TP/SL만 시뮬레이션합니다. v17.1 수정의 핵심인 `position_monitor.py` Stage 1/2 동적 관리는 시뮬레이션되지 않으므로, 실거래에서 드러난 "본전 SL 저주" 자체가 백테스트 숫자에는 반영되지 않습니다. 따라서 백테스트만 보면 v17.0이 나아 보이지만, **실거래에서 v17.0이 손실 중이었던 근본 원인은 v17.1이 수정한 부분**입니다. v17.1의 실제 성능은 배포 후 `trades.db` + 로그로만 완전히 검증 가능합니다.

---

## 9. 로컬 실행 및 개발

### 사전 준비

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # API 키 채우기
```

### 환경변수 (.env)

```
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=false        # true로 테스트넷 사용
PAPER_TRADING=false        # true로 페이퍼 모드
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DATA_DIR=/data             # fly.io 볼륨 또는 ./data
```

### 실행

```bash
python main.py                             # 메인 봇
python -m pytest tests/ -q                 # 테스트 105개
python backtest/run_comparison.py          # 기존 파라미터 그리드 비교
python backtest/v17_1_comparison.py        # v17.0 vs v17.1 비교
python backtest_runner.py                  # BTC 장기 구간 백테스트
```

### 배포 (fly.io)

```bash
fly deploy                        # Dockerfile 기반 배포
fly status -a your-fly-app-name
fly logs  -a your-fly-app-name
fly ssh console -a your-fly-app-name
```

`fly.toml` 주요 설정:
- 리전: `nrt` (Tokyo)
- VM: shared 1 CPU / 512MB
- `auto_stop_machines = "off"` + `min_machines_running = 1` → 24/7 상시 가동
- Volume: `bot_data` → `/data` (trades.db, collector.db, 로그)
- Health check: `GET /health` 30초 주기

---

## 10. 기술 스택

Python 3.11 · ccxt (Bybit USDT Perp) · pandas/numpy · schedule · SQLite · python-telegram-bot · Flask (health/webhook) · fly.io (Tokyo, 상시 가동) · pytest (105 tests) · Claude Code로 개발·분석

---

## 11. 알려진 한계

1. `trades.pnl_pct` 컬럼이 v17.1 시점 기준 모두 0으로 저장되는 버그 있음 (분석에는 `pnl` 사용). 우선순위 낮음.
2. `BacktestEngine`은 `position_monitor` 동적 관리(Stage 1/2/3/4/6)를 시뮬레이션하지 않음 → 백테스트와 실거래 괴리 가능.
3. 5분봉 기반이라 5초~1분 단위의 초단기 스캘핑 패턴은 포착 불가 (의도된 설계).
4. Bybit API rate limit(~15%)만 사용 중이지만 심볼 30개 + 2초 주기 조합이므로 추가 심볼 확장 시 여유 재검토 필요.
