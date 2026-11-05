# TOOL-04 · execute_reprocess — 승인된 대사 결과 생성·멱등 재요청

> **설계 초안 / 미구현**. 모델 등록 이름: `execute_reprocess` · 업무 쓰기 · 주 사용 노드: [N-11](../small-nodes/N-11.md)
>
> [Tool 목록·공통 규칙](README.md) · [업무 DB](../contracts/business-data.md) · [보안](../contracts/security-boundary.md)

## 1. 목적과 호출 경계

등록된 승인과 현재 입력이 일치하는 경우에만 대사 결과를 생성한다. 작업·결과·승인 소비를 하나의 업무 transaction으로 반영한다.

- 내부 Python 계약: `execute_reprocess(request: ToolRequest, ctx: TrustedCallContext) -> NodeResult[ToolData]`.
- 선택적 HTTP 어댑터: `POST /v1/cases/{case_id}/batches/{batch_id}/reprocess`. 실제 API·Python 타입은 아직 구현하지 않았다.
- ctx는 서버 생성 값이며 모델 payload에 포함하지 않는다. 경로와 body에 같은 case/batch가 있으면 반드시 일치시킨다.

## 2. 요청 필드

| 필드 | 타입·필수성 | 제약 |
|---|---|---|
| `case_id, batch_id` | str, 필수 | 현재 실행의 허용 대상 |
| `operation_id` | str, 필수 | 쓰기 전에 N-10이 durable 저장한 ID |
| `idempotency_key` | str, 필수 | case/batch/input_version/action_digest에 연결된 코드 생성 키 |
| `expected_input_version, expected_input_digest` | int/str, 필수 | 새 쓰기에 필요한 현재 입력 binding |
| `action_digest` | str, 필수 | 서버 재계산 값과 일치 |
| `approval_id` | str, 필수 | 외부 발급·현재 계획의 승인 DB 레코드 |

아래 예시는 합성 설명용이다. digest는 형식 예시이며 실제 fixture의 hash가 아니다. 인증 scope·principal·서버 시각은 예시 body에 넣지 않는다.

```json
{
  "case_id": "case_004",
  "batch_id": "BATCH001",
  "operation_id": "op_demo_001",
  "idempotency_key": "idem_demo_case004_v1",
  "expected_input_version": 1,
  "expected_input_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "action_digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  "approval_id": "approval_demo_001"
}
```

## 3. 반환 data

| 필드 | 의미·검사 |
|---|---|
| `operation_id` | 기존 동일 키가 있으면 canonical ID를 반환 |
| `operation_state, observed_effect` | SUCCEEDED/APPLIED 등 검증된 상태 |
| `case_id, batch_id, input_version, action_digest` | 반영 대상·입력 binding |
| `result_ref, committed_at` | 실제 결과 참조·commit 시각 |
| `idempotent_replay` | 기존 효과 회수인지 새 commit인지 구분 |

```json
{
  "status": "success",
  "data": {
    "operation_id": "op_demo_001",
    "operation_state": "SUCCEEDED",
    "observed_effect": "APPLIED",
    "case_id": "case_004",
    "batch_id": "BATCH001",
    "input_version": 1,
    "action_digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
    "result_ref": "result_demo_001",
    "committed_at": "2026-10-30T10:00:00+09:00",
    "idempotent_replay": false
  },
  "diagnostics": {
    "scope_check": {
      "status": "success"
    },
    "business_handler": {
      "status": "success"
    }
  },
  "outcome": null
}
```

외피의 success는 Tool handler가 정상 종료됐다는 뜻이다. 업무 적합성·반영·완료는 data와 실제 DB로 별도 판정한다. 없는 사실·미확정 시각·미수신 결과를 기본값으로 만들어내지 않는다.

## 4. 서버 처리 순서

1. 새 호출의 현재 읽기 scope와 payload의 case/batch binding을 확인하고 작업 키·의미상 업무 키를 직렬화한다.
2. 동일 idempotency_key 또는 case/batch/input_version/action_kind가 이미 있으면 action binding과 권한을 검사한다. 같은 의미의 성공이면 기존 결과와 canonical operation_id를 반환한다. 다른 의미면 충돌 거부한다.
3. 새 쓰기에만 현재 쓰기 권한, input_version/digest, 금융 규칙, 승인 대상·digest·정책·만료·미소비를 검사한다.
4. DB write transaction에서 원천을 다시 읽고 대사 결과를 생성한다. 원본 금액 수정·송금은 수행하지 않는다.
5. operation SUCCEEDED·result rows·승인 CONSUMED를 같은 transaction으로 commit한다. 실패하면 함께 rollback한다.
6. 테스트 훅은 commit 전 rollback 경계와 commit 후 응답 전 경계를 명확히 구분한다.
7. 응답을 보낼 수 없더라도 commit을 되돌렸다고 가정하지 않는다. 후속 TOOL-05로 회수한다.

## 5. 오류·재시도 정책

| 코드·조건 | 처리 |
|---|---|
| `APPROVAL_REQUIRED / APPROVAL_STALE` | 새 쓰기 0회, 승인 재요청 |
| `VERSION_CONFLICT` | 원본·작업 효과 변경 없이 재조사 |
| `IDEMPOTENCY_CONFLICT` | 다른 키로 우회 재실행 금지 |
| `OPERATION_RUNNING` | 동일 키 처리 중, 효과 조회 우선 |
| `TOOL_RESPONSE_LOST / TOOL_TIMEOUT` | 효과 UNKNOWN, 직접 쓰기 재시도 금지 |
| `STORAGE_ERROR` | rollback 확인 여부와 불명 상태 구분 |

제안 timeout은 10초이며 파일럿 후 고정한다. 호출 시도는 전체 Tool 예산에 포함한다. 읽기 재시도도 새 tool_call_id로 계수하고, 쓰기는 [복구 계약](../contracts/persistence-and-recovery.md)의 효과 조회를 우선한다. retryable은 자동 재실행 명령이 아니다.

## 6. 검증 사례

| 사례 | 기대 결과 |
|---|---|
| 승인된 정상 입력 | 원본 유지·결과 1회·승인 1회 소비 |
| 같은 키 2회 | 새 효과 0·동일 result_ref |
| 같은 키+다른 action | 충돌·변경 0 |
| 같은 업무+다른 operation_id | canonical 기존 작업 회수 |
| commit 전 fault | 결과·성공·승인 소비 모두 rollback |
| commit 후 응답 유실 | DB 효과 유지·상태 조회로 회수 |
| 승인 이후 입력 변경 | transaction 내부 버전 거부 |

## 7. 저장·추적·한계

logical_run_id·session_id·tool_call_id·scope ref·정책 버전·요청/응답 ref·snapshot·오류·시간을 기록한다. 일반 trace에 토큰·금지 원문·모델 추론 전문은 저장하지 않는다.

원자성은 업무 DB 내부에만 적용한다. Agent checkpoint와 함께 commit된다고 가정하지 않는다. 이미 성공한 키의 회수는 기존 승인이 이후 만료됐어도 읽기 권한·동일 의미를 확인하여 허용하고 새 승인을 소비하지 않는다. 현재 새 입력을 처리할 때는 새 승인·업무 키를 사용한다.
