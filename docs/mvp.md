# 금융 대사 Agent 로컬 MVP

이 MVP는 합성 원화 거래에 대해 입력을 확인하고, 대기·이관·재처리를 판단하며, 운영자 승인과 거래별 결과 검증을 거쳐 종료한다. 업무 DB, Tool 호출 원장, 하네스 의도 저장소를 분리해 프로세스 재시작 전후의 실제 업무 효과를 대조한다.

## 한 명령으로 재현

macOS 또는 Linux에서 Python 3.11 이상을 사용한다. 기본 데모에 Python 외 추가 패키지나 GPU는 필요 없다. 출력 경로는 새 디렉터리여야 한다.

```sh
python3.11 scripts/mvp.py demo --out generated_data/mvp-demo
```

| 사례 | 확인할 관측 |
|---|---|
| `clean` | 운영자 승인 이후에 결과 1회 반영 |
| `delayed_arrival` | 도착 미완료 상태에서 `WAITING_FOR_INPUT` |
| `amount_swap` | 총액이 같은 두 거래의 금액 교환을 찾아 `ESCALATED` |
| `before_commit` | 프로세스 종료 코드 71, 중단 직후 작업 0건, 재개 후 같은 의도로 1회 반영 |
| `after_commit` | 종료 코드 72, 중단 직후 작업 1건·완료 결정 0건, 재개 첫 호출 `get_operation`, 최종 작업 수 1건 |

`report.json`의 `initial`, `after_process_exit`, `audit`, `final`, `evaluation`에서 판단과 업무 반영을 단계별로 확인할 수 있다. `RESERVED`로 남은 호출은 프로세스가 종료돼 최종 응답을 기록하지 못한 시도이며, 누적 예산에 포함된다. `reconciliation_results` 개수는 거래별 결과 행 수이고, 재처리 업무 효과의 수는 `operations` 개수다.

## 각 단계 직접 실행

기본 데모를 먼저 실행하면 데이터셋과 사례별 trial이 생긴다. 아래 명령은 같은 합성 데이터에서 새 정상 사례 trial을 만든다. 실제 사례 ID는 `generated_data/mvp-demo/dataset/catalog.json`의 `clean` 항목을 확인한다. 아래 ID는 기본 seed 42의 정상 사례다.

```sh
case_id=c_336fcb8ca249
trial=generated_data/one-trial

python3.11 scripts/mvp.py prepare --dataset generated_data/mvp-demo/dataset --trial "$trial" --trial-id one --case "$case_id"
python3.11 scripts/mvp.py run --trial "$trial" --trial-id one --case "$case_id" --session s1
python3.11 scripts/mvp.py approve --trial "$trial" --trial-id one --case "$case_id" --actor demo-operator
python3.11 scripts/mvp.py run --trial "$trial" --trial-id one --case "$case_id" --session s2
python3.11 scripts/mvp.py evaluate --trial "$trial" --trial-id one --case "$case_id"
```

- `approve`는 로컬 운영자 명령이다. 운영자는 먼저 `PAUSED_FOR_APPROVAL`의 제안, 대상과 버전을 검토한다. 승인은 대상·입력 버전·digest·정책과 만료 시각을 가진다.
- 새 세션에서 같은 `--run-id`(기본 `run-1`)를 사용하면 저장된 의도를 이어 간다. 완료된 실행은 같은 결정 ID를 재전달한다. 새 입력의 후속 조사는 다른 `--run-id`를 사용한다.
- `advance --to <offset이 있는 시각>`은 기존 합성 이벤트를 적용하는 운영 명령이다. `inspect --as-of <시각> --version <버전> --transaction-id <ID>`로 도착 전후 및 과거 조회를 비교할 수 있다. 배치/기존 작업 조회와 페이지 인자는 `inspect --help`에서 확인한다.
- 중단을 직접 재현하려면 승인 후 첫 쓰기 세션에 `--fault before-commit` 또는 `--fault after-commit`을 붙인 다음, 같은 실행의 새 세션에서 fault 옵션 없이 `run`을 호출한다.

## 구현 경계

| 층 | 구현 |
|---|---|
| 업무 Tool | 공통 Python dispatcher에 여섯 도구 등록. 시점·버전 조회, 사전 확인, 재처리, 작업 효과 조회, 이관 초안 |
| 업무 저장소 | 원본 금융 행은 조회. 작업, 거래별 결과, 승인 소비는 단일 SQLite 트랜잭션. 같은 업무 키로 중복 반영 방지 |
| 하네스 | 작업 의도와 증거 참조의 별도 저장, 공통 예산 원장, 효과 우선 회수, 시점·버전 확인 후 종료, 같은 결정 재전달 |
| 독립 판정 | SQL로 시점별 대사 조건, 승인 소비, 거래별 저장 결과와 작업 수를 다시 검사 |

기본 판단은 R0 규칙이다. 설치된 로컬 Ollama 모델을 사용하려면 새 실행의 첫 `run`에 `--model qwen3-vl:4b`처럼 모델 이름을 지정한다. 모델에는 거래 건수와 검증 플래그만 전달한다. 모델 출력은 조치 하나로 제한하며 코드가 다시 대조한다. 같은 관측의 제안을 기록해 승인 후 재개 시 재사용한다. 외부 메모는 증거 참조로만 다룬다.

SQLite 단일 작업자와 런타임 파일 잠금이 유지되는 로컬 환경을 가정한다. 누락 작업의 미반영 확인은 이 직렬화 조건에 한정된다. [로컬 웹 워크벤치](../web/README.md)에서도 동일한 승인·중단·복구를 실행할 수 있다. 별도 사용자 인증, MCP 및 네트워크 장애 복구, 외부 메모 기반 모델 평가, B0/B1/H 성능 비교는 후속 범위다. 승인과 거래는 합성 실험용이며, 이관 초안은 로컬에 저장된다.

```sh
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
```
