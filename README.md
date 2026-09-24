# wonyotti-lab

워뇨띠(aoa)가 공개한 BitMEX 체결·지갑내역(2018.03–2021.12)으로 그의 매매 행동을 분석하고,
모방 모델과 그의 원칙을 옮긴 리스크 엔진을 만드는 개인 연구 프로젝트입니다. 대상은 코인(BTC 우선).

> 원작자는 이 데이터로 만든 거래봇·2차 제작물의 유료 판매를 삼가 달라고 요청했습니다. 개인 연구용으로만 쓰세요.
> 투자 조언이 아닙니다. 선물 거래는 원금 전부를 잃을 수 있습니다.

## 구조

```
시장 데이터 → 코드(숫자·날짜 계산, 구간화) → Jev(텍스트 즉답 판단)
            → 학습 모델(목표 노출 제안) → 리스크 엔진(최종 거부권) → 주문 / 관망
```

| 파일 | 역할 | 상태 |
|---|---|---|
| `src/inverse.py` | XBT 인버스 계약 수학 (평균단가, 손익, 달러 기준 노출) | 완료 + 테스트 |
| `src/inspect_data.py` | 공개 파일 구조 확인, parquet 변환 (csv / xlsx 여러 시트) | 완료 + 테스트 |
| `src/exposure.py` | 체결·지갑 → 노출 타임라인 (학습 라벨) | 완료 + 테스트, 실제 파일로 매핑 확인 필요 |
| `src/risk_engine.py` | 손실 한도·레버리지 상한·변동성 사이징·쿨다운·확신도 게이트 | 완료 + 테스트 |
| `src/jev_layer.py` | Jev 질문 세트 (미국 매크로 헤드라인, 틸트 감지) | 뼈대. API 키 필요 |
| `config/columns.yaml` | 원본 컬럼명 매핑 | 실제 파일 보고 수정 |
| `config/risk_rules.yaml` | 리스크 규칙 수치 | 출처/임의값 표시돼 있음 |

## 설치 (Python 3.10 이상)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env  → TYPESAFE_API_KEY 입력
pytest                             # 28개 테스트가 통과해야 정상
```

VS Code에서는 폴더를 연 뒤 `Python: Select Interpreter`로 `.venv`를 고르세요.

## 단계

### 1. 데이터 구조 확인 & 변환
공개 파일(디시인사이드 차트갤러리 [원글](https://gall.dcinside.com/mgallery/board/view/?id=chartanalysis&no=5051684))을
`data/raw/`에 넣고 압축을 푼 뒤:

```bash
python -m src.inspect_data inspect data/raw/<체결내역 파일>
python -m src.inspect_data inspect data/raw/<지갑내역 파일>
```

출력된 컬럼명에 맞춰 `config/columns.yaml`을 고친 다음 변환합니다.

```bash
python -m src.inspect_data convert data/raw/<체결내역 파일> data/processed/executions.parquet
python -m src.inspect_data convert data/raw/<지갑내역 파일> data/processed/wallet.parquet
```

엑셀 한 시트는 약 104만 행까지라 140만 행은 여러 시트에 나뉘어 있을 수 있습니다. 헤더가 같은 시트는 자동으로 이어 붙이고,
헤더가 다른 시트(예: 지갑내역)는 `--sheet 시트명`으로 따로 변환하세요.

### 2. 달러 기준 노출 타임라인 (학습 라벨)

```bash
python -m src.exposure --limit 100000   # 먼저 일부로 확인
python -m src.exposure                  # 전체 (140만 행 기준 수십 초, 메모리 약 1GB)
```

노출 비율: `0` = 달러 중립(BTC 증거금 + 1배 숏), `0.5` = BTC 0.5배 롱(그의 '관망'), `1` = 포지션 없음(BTC 1배 롱), `2` = 1배 롱 포지션.
결과를 커뮤니티 분석(지정가 위주 진입, 보유 시간이 점점 길어짐 등)과 대조해 보세요.

### 3. 시장 데이터 (다음 작업)
같은 기간 BTC OHLCV가 필요합니다. 비트멕스는 2026-09-23 거래를 종료했으니 과거 데이터 아카이브 접근이 언제까지 되는지 먼저 확인하세요.
다른 거래소 데이터로 대신하면 그가 보던 차트(선물 비트멕스, 현물 비트파이넥스·바이낸스)와 차이가 생깁니다.

### 4. 특징 & 학습
- 코드 특징만으로 기준 모델 → 시간순(walk-forward) 검증. 무작위 분할 금지
- 평가: 노출 칸이 바뀌는 순간 적중률 + 모방 정책 손익. "직전 노출 유지" 베이스라인과 반드시 비교
- 입력에서 날짜·절대가격 제거(정규화)

### 5. Jev 실험

```bash
python -m src.jev_layer --headline "Fed holds rates steady, signals two cuts this year"
python -m src.jev_layer --note "어제 잃은 거 오늘 안에 무조건 복구한다"
```

- Jev 특징을 넣었을 때 시간순 검증 성능이 실제로 오르는지 ablation. 안 오르면 뉴스·틸트 감지에만 사용
- 확률 임계값은 내 로그로 적중률과 대조해 보정

### 6. 리스크 엔진 + 페이퍼 트레이딩
- 실거래 API 키에는 입금·출금·이체 권한을 주지 않습니다 (물타기용 추가 입금 차단 원칙)
- 단순 기준선(보유, 단순 박스 역추세)을 시간순 테스트에서 못 이기면 거기서 멈춥니다

## 알려진 한계
- 체결 기록에는 보류·취소한 주문 판단이 없습니다 (원작자 언급)
- 포지션 약 3,200회 → 과적합 주의
- 2018~2021 시장과 지금은 다릅니다 (봇 증가, 기관 유입, 비트멕스 종료)
- 1차 버전은 XBT 인버스 계약만 계산합니다 (ETHUSD 콴토 등은 제외 목록만 출력)
- 정산 전 실현손익은 직전 지갑 기록 이후 누적분으로 근사합니다

## Claude Code로 이어서 작업하기
VS Code 확장에서 이 폴더를 열면 `CLAUDE.md`의 프로젝트 규칙을 읽고 이어서 작업합니다.
첫 세션은 패널에 `@prompts/01_kickoff.md 읽고 그대로 진행해줘`, 이후 세션은 `@prompts/resume.md`로 시작하세요.
Jev 코드를 짤 때는 TypeSafe 스킬을 설치해 두면 좋습니다 (터미널에 `claude` CLI가 있을 때):

```bash
claude plugin marketplace add typesafe-ai/skills
claude plugin install typesafe@typesafe-ai
```
