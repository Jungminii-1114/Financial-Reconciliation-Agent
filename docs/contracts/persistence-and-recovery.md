# 지속 상태·작업 의도·중단 후 복구

> v0.1 설계. [B-04](../big-nodes/B-04.md) · [TOOL-04](../tools/TOOL-04.md) · [TOOL-05](../tools/TOOL-05.md)

## 1. 세 가지 저장을 구분한다

1. Agent의 의도·체크포인트 저장.
2. 업무 서비스의 승인 검사·실제 결과 commit.
3. Agent의 응답 수신·검증·완료 기록.

2와 3 사이의 중단을 실험한다. 체크포인트에 완료가 없다는 이유로 2의 미반영을 단정하지 않는다. 두 DB 간 분산 트랜잭션은 구현하지 않는다.

## 2. 정상 쓰기 프로토콜

| 순서 | 책임 | durable 기록·검사 |
|---|---|---|
| P1 | N-09 | 외부 발급 승인 레코드와 현재 계획 연결 |
| P2 | N-10 | operation_id·idempotency_key·승인·입력 버전·action_digest를 operation_intents에 commit |
| P3 | 하네스 | Tool 시도 예산 예약·dispatch 상태 기록. 저장 실패 시 호출 금지 |
| P4 | TOOL-04 | 현재 접근 권한, 작업 키 직렬화, 기존 반영 여부 조회 |
| P5 | 업무 서비스 | 새 쓰기일 때 승인·현재 입력·업무 적합성 검사, 결과·작업 성공·승인 소비를 한 transaction에 commit |
| P6 | N-11 | 응답을 구조화 관측으로 저장. 응답 유실은 효과 UNKNOWN |
| P7 | N-13/14 | 결과·시점·대상을 검증하고 조건부 완료 결정 기록 |

같은 키가 이미 성공했으면 현재 읽기 권한과 동일 action binding을 확인하고 기존 결과를 반환한다. 만료된 승인을 다시 소비하거나 현재 입력으로 결과를 몰래 갱신하지 않는다. 새 입력에 대한 재처리는 새 계획·승인·키를 사용한다.

## 3. 중단 경계

| 경계 | 실제 세계 | 재개한 Agent가 하는 일 |
|---|---|---|
| 의도 저장 전 | 쓰기 미발행 | 일반 조사부터 시작 |
| 의도 저장 후·dispatch 전 | 쓰기 미발행이나 Agent는 의도만 가짐 | 같은 키 조회, 확인 후 실행 가능성 재검토 |
| before_commit | 통제된 서비스가 rollback을 완료한 뒤 Agent 중단 | 권위 있는 미반영 관측 뒤 동일 의도 재시도 검토 |
| after_commit_before_response | 결과·작업·승인 소비 durable, Agent 응답 미수신 | 기존 작업 조회 → 결과 회수·검증 |
| 응답 수신 후·체크포인트 전 | 업무 성공, 관측 기록 일부 누락 가능 | 업무 DB 재조회로 관측 복원 |
| 완료 결정 후·출력 전 | decision_id durable, 사용자 전달 누락 | 같은 결정 사실을 다시 전달, 새 쓰기 금지 |

## 4. 미반영을 확정할 수 있는 조건

초기 서비스는 동기 작업과 작업 키 단위 직렬화를 사용한다. `NOT_FOUND + authoritative=true + safe_to_retry_same_key=true`는 다음을 모두 확인한 경우에만 반환한다.

- 이전 요청 핸들러가 commit 또는 rollback으로 종료됐다.
- 같은 키의 활성 writer·미확인 in-flight 요청이 없다.
- 직렬화 경계에서 업무 DB의 작업·결과를 현재 읽기로 확인했다.

일반 HTTP 타임아웃, 비일관 replica의 404, 아직 실행 중인 worker는 이 증명이 아니다. 그런 조건을 시뮬레이션하면 UNKNOWN/RUNNING으로 남기고 조회 상한 뒤 이관한다. 데이터에 실제 미반영 정답이 있다는 이유로 runtime에 정답을 주지 않는다.

같은 키 재시도 자체도 서버 멱등성으로 보호한다. 새 키로 불확실성을 회피하지 않는다. 잘못된 시도·차단·실제 중복 효과는 각각 집계한다.

## 5. 가능한 상태와 추가 관측

effect 두 값 × input_relation 두 값의 최대 4가설을 시작점으로 한다. TOOL-05는 효과, TOOL-01/02는 현재 입력을 관측한다. 한 관측으로 다른 축을 추측하지 않는다. 관측 충돌·응답 계약 위반이면 상태를 임의 축소하지 않고 이관 근거로 남긴다.

```text
effect=APPLIED, input=UNCHANGED → 결과 검증
effect=NOT_APPLIED, input=UNCHANGED → 승인·재시도 한도 확인
effect=APPLIED, input=CHANGED → 과거 결과 보존 + 현재 입력 재조사
effect=NOT_APPLIED, input=CHANGED → 과거 승인 무효 + 새 계획
```

## 6. 종료와 as-of 의미

`finalize_if_current` 내부 서비스는 대상·입력 버전·결과·검증 근거를 같은 업무 직렬화/transaction 경계에서 검사하고 completion_decisions를 기록한다. 미리 읽은 버전과 달라졌으면 VERSION_CONFLICT이며 다시 조사한다.

이는 decision_at 시점의 결정을 보존한다. 이후 자료 도착은 이전 결정을 지우지 않고 후속 검토를 시작한다. 사용자 결과에 시점·버전을 명시한다. 업무 완료 기록과 체크포인트가 어긋나면 durable decision_id를 조회하여 같은 사실을 재전달한다.

종료 후 새 입력을 검토하는 follow-up은 새 logical_run_id와 parent_run_id를 사용한다. 평가 controller는 같은 trial의 budget_group_id를 유지한다. PAUSED_FOR_APPROVAL의 재개나 미확정 작업 복구는 종료 후 새 요청과 다르며 기존 logical_run_id·의도·예산으로 이어간다.

## 7. 검증할 실패

- 의도 저장 실패 후 Tool이 실행되지 않는가?
- 반영 후 응답 유실에서 같은 operation을 조회하고 새 업무 효과를 만들지 않는가?
- NOT_FOUND가 비권위적일 때 재시도를 허가하지 않는가?
- 입력이 바뀐 상태에서 과거 승인으로 새 쓰기를 막는가?
- 이미 반영된 결과의 회수에 승인을 재소비하지 않는가?
- 재개 후 호출·시간·토큰 한도가 초기화되지 않는가?
- 프로세스 재시작과 단순 예외·응답 지연 실험을 별도 표기하는가?
