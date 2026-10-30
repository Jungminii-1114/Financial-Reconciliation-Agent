# 금융 대사 합성 데이터 첫 예제

〖[English](README.en.md) | 한국어〗

[프로젝트 메인으로 돌아가기](../../readme/README.ko.md)

이 예제의 목적은 정상 입력, 사건 조건, 정답을 어떻게 구분해서 만드는지 이해하는 것입니다. 금융 실무 전체를 재현하거나 Agent 성능을 측정한 결과물이 아닙니다. 외부 코드를 복사하지 않고 이 프로젝트의 설명용으로 작성했습니다.

## 먼저 열어볼 파일

1. `sample_data/case_001/agent_view/internal.csv`: 내부 거래 3건
2. `sample_data/case_001/agent_view/external.csv`: 대응하는 외부 정산 기록
3. `sample_data/case_002/agent_view/external.csv`: 마지막 거래의 자료가 아직 안 온 상태
4. `sample_data/case_002/controller_only/scheduled_events.json`: 나중에 도착할 자료
5. `sample_data/case_006/agent_view/external.csv`: 총액은 유지되지만 거래별 금액이 바뀐 상태

## 우리가 정한 모의 업무 규칙

- 통화는 원화이고 금액은 정수입니다.
- 내부 거래 한 건과 외부 정산 한 건을 동일 거래 ID로 연결합니다.
- 자료 도착이 끝나지 않았으면 대기합니다.
- 자료 도착이 끝났는데 ID 또는 금액이 다르면 이관합니다.
- 자료가 완전하고 거래별 금액이 일치하며 배치가 실패했다면 승인 후 대사 결과 재생성을 제안할 수 있습니다.
- 재처리는 대사 결과의 재생성입니다. 송금이나 입력 금액 수정이 아닙니다.
- 수수료, 환전, 분할/묶음 정산, 취소와 환불은 이 첫 예제의 범위에 포함하지 않습니다.

이 규칙은 우리 모의 업무의 정의이며 은행의 공통 규정으로 제시하는 것이 아닙니다.

## 생성 절차

`generate.py`를 아래 순서로 읽으면 됩니다.

1. `random.Random(seed)`로 금액을 만듭니다. 같은 코드와 시드로 같은 파일을 재생성합니다.
2. 내부 거래를 만든 뒤 대응하는 정상 외부 기록을 만듭니다. 양쪽을 각각 무작위로 만들지 않습니다.
3. 정상 기록을 사건별로 복사합니다.
4. 지연 사건에서는 마지막 외부 행을 초기 관측에서 빼고, 미래 도착 계획에 넣습니다.
5. 불일치 사건에서는 외부 기록의 한 금액만 변경합니다.
6. 총액 유지 반례에서는 서로 다른 두 금액을 교환합니다.
7. 장애 사건에는 DB 반영 전/후 중단이라는 실행 계획을 별도로 붙입니다.
8. 관측 데이터와 분리된 정답 파일을 작성하고 `validate.py`로 데이터와 정답이 일치하는지 검사합니다.

## 생성된 6개 사건

| ID | 조건 | 최초 기대 행동 | 추가 확인 |
|---|---|---|---|
| case_001 | 자료 완비, 금액 일치, 배치 실패 | REPROCESS | 승인 후 처리하고 결과 검증 |
| case_002 | 마지막 외부 기록이 10:05에 도착 예정 | WAIT | 10:00에는 미래 행이 보이지 않음. 도착 후 현재 입력과 승인 재확인 |
| case_003 | 한 외부 기록에 100원 추가 | ESCALATE | 원래 금액을 추측해 덮어쓰지 않음 |
| case_004 | 정상 입력 + DB 반영 후 응답 전 Agent 종료 계획 | REPROCESS | 장애 발생 후 작업 조회와 결과 회수 필요 |
| case_005 | 정상 입력 + DB 반영 전 Agent 종료 계획 | REPROCESS | 장애 발생 후 상태 확인, 정책상 허용된 재시도 필요 |
| case_006 | 두 거래의 외부 금액을 교환 | ESCALATE | 총액·건수는 같아도 거래별 비교는 실패 |

`REPROCESS`는 최초 제안 단계의 정답입니다. 실제 완료는 승인·실행·검증을 거쳐야 합니다. 지연 사건은 시점에 따라 정답이 달라집니다. 대기가 최종 영구 상태라는 뜻이 아닙니다.

6개 사건은 학습용 예시입니다. 기획서의 개발 6개·최종 평가 12개를 채웠다는 뜻이 아닙니다. ID·금액·시드만 바꾼 데이터를 독립된 평가 사건으로 취급하지 않습니다. 최종 평가는 증거 조합·사건 경로·도착 순서 등을 나눠 설계해야 합니다.

## 폴더의 의미

```text
sample_data/
  manifest.json                  생성기 버전, 시드, 파일별 해시
  teaching_index.json            사람이 읽는 사건 목록과 정답 요약
  data_card.md                   합성 가정과 범위
  case_001/
    agent_view/                  현재 시점에 관측 가능한 데이터만
      internal.csv
      external.csv
      batch.json
      request.txt
    controller_only/             실행 환경이 사용하는 미래 이벤트/장애 계획
      scheduled_events.json
      fault_plan.json
    evaluator_only/              평가자만 확인하는 기대 결과
      expected.json
  ...
```

미래 도착 행을 `agent_view/external.csv`에 넣고 “아직 읽지 말라”고 지시하는 방식은 사용하지 않습니다. 미래 행은 컨트롤러가 보관하고 지정 시각 이후에만 조회 결과에 반영해야 합니다. 이 예제의 `agent_view`는 10:00의 초기 스냅샷입니다.

폴더를 나눈 것만으로 보안이나 접근 제어가 생기지는 않습니다. 실제 Agent를 연결할 때는 도구가 허용된 현재 관측만 반환하도록 구현해야 합니다. 루트의 정답 요약 목록도 모델에게 주면 안 됩니다.

## 직접 생성하기

Python 3.10 이상을 권장합니다. GPU나 별도 패키지가 필요하지 않습니다. 저장소 루트에서 실행합니다.

```bash
# 포함된 샘플 검사
python3 examples/synthetic_data_starter/validate.py examples/synthetic_data_starter/sample_data

# 새 데이터 생성 후 검사
python3 examples/synthetic_data_starter/generate.py --out generated_data/experiment_001 --seed 42 --rows 3
python3 examples/synthetic_data_starter/validate.py generated_data/experiment_001
```

거래를 늘리고 싶으면 새 폴더로 생성합니다.

```bash
python3 examples/synthetic_data_starter/generate.py --out generated_data/experiment_100 --seed 7 --rows 100
python3 examples/synthetic_data_starter/validate.py generated_data/experiment_100
```

이 README와 같은 폴더에서 실행한다면 `python3 generate.py --out my_first_dataset --seed 42 --rows 3` 다음에 `python3 validate.py my_first_dataset`을 사용해도 됩니다.

기존 출력 폴더가 있으면 덮어쓰지 않습니다. 다른 `--out` 이름을 지정하세요. 시드 변경은 수치 변형일 뿐 새로운 금융 규칙이나 새로운 사건 유형을 만드는 기능은 아닙니다.

## 검사하는 것과 아직 구현하지 않은 것

검사기는 파일을 다시 읽어 기본키, 금액·통화, 배치 연결, 시간 순서, 미래 자료 미노출, 거래별 불일치, 기대 행동, 총액 유지 반례, 파일 해시를 확인합니다. 생성기 코드를 가져다 쓰지 않고 별도 검사 로직을 사용합니다. 두 코드에 같은 잘못된 업무 가정을 넣을 가능성까지 없애주는 것은 아니므로 명세 검토도 필요합니다.

현재는 데이터 생성·검사 예제입니다. SQLite 업무 서비스, 승인, 실제 Agent, 실제 장애 주입·프로세스 종료, 자동 시간 진행, 최종 처리율 측정은 구현하지 않았습니다. 특히 `fault_plan.json`을 만들었다고 장애가 실제 발생한 것은 아닙니다. 이 파일을 읽고 실행할 하네스/컨트롤러를 이후에 구현해야 합니다.

학습 예제를 이해한 뒤 작업·승인 이력, 대사 결과 테이블, 배치별 버전 변화, 정상 변환과 추가 반례를 단계적으로 붙이는 것이 다음 순서입니다.
