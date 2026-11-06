# Bybit Futures Bot v2 — 5m + 1H Momentum Scalper

Bybit USDT 무기한 선물 자동매매 봇입니다. 1시간봉으로 추세 방향을 정하고 5분봉 풀백에서 진입하며, 진입 뒤에는 1초 주기 모니터가 손절·익절 위치를 단계적으로 옮깁니다. 실거래·페이퍼 데이터를 분석해 버전을 올리는 방식으로 v6.0 부터 v17.5 까지 개선했습니다.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![pandas](https://img.shields.io/badge/pandas-150458?logo=pandas&logoColor=white)
![pybit](https://img.shields.io/badge/pybit-Bybit%20v5-F7A600)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-health%20server-000000?logo=flask)
![pytest](https://img.shields.io/badge/pytest-105%20passed-0A9EDC?logo=pytest&logoColor=white)
![Fly.io](https://img.shields.io/badge/Fly.io-Docker-8B5CF6)

> **투자 조언이 아닙니다.** 개인 학습·포트폴리오용 코드입니다. 레버리지 선물 거래는 원금 전액을 잃을 수 있고, 아래의 어떤 설정도 수익을 보장하지 않습니다. 실제로 일부 버전은 이전 버전보다 결과가 나빠졌습니다.

**상태:** 2026년 3~4월 운영하며 개선, v17.5 이후 업데이트 없음. 이전 저장소 `bybit-futures-bot` 의 후속작으로 전략·코드 구조를 새로 짰습니다.

---

## 무엇을 해결하나

이전 봇에서 확인한 문제는 "진입 신호보다 진입 뒤 관리에서 손익이 갈린다" 는 것이었습니다.

- 이익이 났다가 반락해 손실로 끝나는 거래 → 수익 구간별로 손절선을 끌어올리는 **단계적 포지션 관리**
- 저유동성·고변동성 알트의 수 분 내 급락 → **변동성 레짐별 진입 차단**과 진입 직후 **급속 손절**
- 백테스트로는 동적 관리 로직을 재현할 수 없음 → **스킵한 신호·청산 후 가격·손절/익절 변경 이력까지 저장**해 실거래 데이터로 검증

## 아키텍처

```
main.py (schedule)
 ├─ 90초마다  on_candle_close()  ─ 거래량 상위 심볼 × 5m 캔들
 │     └─ strategy/final_strategy.py   1H 추세(EMA9/21·ADX·DI) + 5m 풀백 점수 → 방향·TP/SL·레버리지
 │     └─ strategy/risk_manager.py     진입 가능 여부(일일 손실·MDD 서킷브레이커·상관관계·일봉 전환 시간대) + 수량
 │     └─ exchange/order_manager.py    신호 강도별 시장가/지정가, TP/SL 거래소 등록, 최소 주문 금액 보정
 ├─ 1초마다   monitor()
 │     └─ bot/position_monitor.py      수익 잠금 SL → 트레일링 → 부분 익절 → 시간·신호 기반 청산
 ├─ 매일      브리핑, 상관관계·벤치마크 수집
 ├─ data/storage.py, data/collector.py SQLite: trades, skipped_signals, post_exit_tracking,
 │                                      tp_sl_adjustments, bot_state, 신호 저널·레짐·낙폭 이벤트
 ├─ bot/health_server.py               /health (fly.io 헬스체크) + 토큰 보호된 분석 엔드포인트
 └─ utils/notifier.py                  Telegram 알림 (메시지 큐 + 429 백오프)
```

| 모드 | 구성 |
|---|---|
| 실거래 | `BybitClient` (pybit v5) |
| 페이퍼 | `PaperBybitClient` — 시장 데이터는 실제 API, 주문·잔고·포지션은 가상 처리하고 재시작 시 DB 에서 복원 |
| 테스트넷 | `BYBIT_TESTNET=true` |

## 기술적으로 집중한 문제

### 1. 진입 뒤 단계적 포지션 관리
- **문제:** 고정 TP/SL 만 쓰면 TP 근처까지 갔다가 되돌아와 손절되는 거래가 많았고, 반대로 본전 손절을 너무 일찍 걸면 정상적인 되돌림에도 청산돼 이익이 수수료 수준으로 줄었습니다.
- **해결:** `bot/position_monitor.py` 가 1초마다 각 포지션을 점검합니다. 일정 수익에 닿으면 손절을 진입가 위로 옮겨 최소 이익을 잠그고(Stage 1), 그 이상에서는 변동성 레짐에 따라 보호 비율을 달리하는 트레일링 손절을 걸며(Stage 2), TP 진행률에 따라 일부를 먼저 청산합니다(Stage 3). 진입 직후 급락, 장시간 보유 후 손실·무수익, 강한 반대 신호도 각각 별도 규칙으로 청산합니다. 손절/익절이 바뀔 때마다 `tp_sl_adjustments` 에 기록합니다.
- **결과:** 각 단계의 트리거·보호 비율은 버전마다 실거래 결과를 보고 조정했고, 그 근거가 커밋 메시지에 남아 있습니다(예: v17.0 → v17.1 "본전 SL" 트리거 완화, v17.3 의 트레일링 완화가 평균 손실을 키워 v17.4 에서 되돌림).

### 2. 변동성 레짐에 맞춘 TP/SL 과 진입 차단
- **문제:** 같은 ATR 배수라도 변동성이 낮을 때는 노이즈에 손절되고, 극단적 변동성에서는 몇 분 만에 큰 손실이 났습니다.
- **해결:** 현재 ATR 과 50봉 평균 ATR 의 비율로 extreme / high / normal / low 레짐을 나누고 레짐별 TP/SL 배수를 씁니다. 가격 대비 최소 TP·SL 거리를 강제하고, extreme 레짐은 진입하지 않으며 high 레짐은 더 높은 신호 점수를 요구합니다. 거래량이 20봉 평균보다 낮을 때도 진입하지 않습니다.

### 3. 실거래 데이터로 검증하는 구조
- **문제:** `backtest/engine.py` 는 고정 TP/SL 만 시뮬레이션하므로 1초 주기 동적 관리의 효과는 백테스트로 알 수 없습니다.
- **해결:** 진입하지 않은 신호와 그 이유(`skipped_signals`), 청산 뒤 15~240분 가격(`post_exit_tracking`, 페이퍼 모드), 진입 시점 MFE/MAE, 레짐 전환·낙폭 이벤트를 SQLite 에 쌓고, 헬스 서버의 분석 엔드포인트로 집계해 봅니다. 이 데이터를 근거로 다음 버전의 파라미터를 정했습니다.

### 4. 리스크 관리
- 거래당 잔고의 1.5% 위험, 손절 거리로 수량 계산 (`strategy/risk_manager.py`)
- 기본 5배, 신호 점수에 따라 최대 8배 레버리지
- 일일 손실 5% 도달 시 당일 신규 진입 중단, MDD 30% 초과 시 서킷브레이커 후 쿨다운
- 연속 손실·낙폭 구간에서 포지션 크기 축소, 수익률 상관계수가 높은 심볼 동시 보유 차단
- 일봉 전환 시각(KST 09:00) 전후 15분 신규 진입 차단
- 동시 포지션 3개, 같은 심볼 재진입 쿨다운

### 5. 운영 안정성
- Telegram 전송은 큐로 모아 보내고 429 응답 시 `retry_after` 만큼 대기합니다.
- 재시작 시 미체결 지정가 주문을 정리하되 거래소에 걸린 TP/SL 청산 주문은 보존합니다.
- 거래소에만 있는 포지션은 동기화해 관리 대상으로 넣고, TP/SL 이 없으면 기본값을 설정합니다.
- 주문 금액이 거래소 최소 금액보다 작으면 가용 증거금 한도 안에서 수량을 올립니다.

## 품질 / 테스트

- `pytest` 4개 파일, **105개 테스트 통과** (Python 3.11 에서 실행해 확인).
- 대상: 신호 생성(`final_strategy`), 리스크 매니저, 포지션 관리, SQLite 저장소.
- 알려진 한계: 백테스트 엔진은 동적 포지션 관리를 반영하지 않습니다. `archive/` 와 `webhook_server.py`(TradingView 웹훅 연동)는 이전 버전 코드로 현재 실행 경로에서 쓰지 않습니다.

## 실행 방법

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
cp .env.example .env        # 기본값: 테스트넷 + 페이퍼 모드

python -m pytest tests/ -q  # 테스트
python backtest_runner.py   # BTC 장기 구간 백테스트 (공개 시세 사용)
python main.py              # 봇 실행
```

Docker 이미지로 Fly.io 에 배포할 수 있습니다(`Dockerfile`, `fly.toml`). `fly.toml` 의 `app` 값을 본인 앱 이름으로 바꾸고, 비밀값은 `fly secrets set` 으로 넣습니다. GitHub Actions 배포 워크플로는 수동 실행(`workflow_dispatch`)으로만 동작합니다.

## 설정

| 변수 | 설명 |
|---|---|
| `BYBIT_API_KEY`, `BYBIT_API_SECRET` | Bybit API (출금 권한은 주지 마세요) |
| `BYBIT_TESTNET` | `true` 면 테스트넷 |
| `PAPER_TRADING`, `PAPER_INITIAL_BALANCE` | 페이퍼 모드와 가상 시작 잔고 |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 알림 |
| `STATUS_TOKEN` | 헬스 서버의 `/health` 외 엔드포인트 접근 토큰 (`X-Status-Token` 헤더). 비우면 해당 엔드포인트는 비활성 |
| `DATA_DIR` | DB·로그·캐시 위치 (fly.io 볼륨이면 `/data`) |
| `WEBHOOK_SECRET`, `WEBHOOK_PORT` | 헬스 서버 포트, (레거시) TradingView 웹훅 비밀값 |

전략 파라미터는 `config/indicator_params.py` 에 있습니다.

## 개발 방식

Claude Code 와 함께 설계·구현하고, 실거래 로그 분석도 함께 진행했습니다 (커밋의 `Co-Authored-By` 참고).
