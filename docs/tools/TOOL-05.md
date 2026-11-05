# TOOL-05 · get_operation — 작업 효과·결과·재시도 조건 조회

> **설계 초안 / 미구현**. 모델 등록 이름: `get_operation` · 업무 읽기 · 주 사용 노드: [N-12](../small-nodes/N-12.md) · [N-13](../small-nodes/N-13.md)
>
> [Tool 목록·공통 규칙](README.md) · [업무 DB](../contracts/business-data.md) · [보안](../contracts/security-boundary.md)

## 1. 목적과 호출 경계

중단 전 작업의 실제 처리 상태와 결과를 조회한다. 단순 404를 확정 미반영으로 바꾸지 않는다.

- 내부 Python 계약: `get_operation(request: ToolRequest, ctx: TrustedCallContext) -> NodeResult[ToolData]`.
- 선택적 HTTP 어댑터: `POST /v1/cases/{case_id}/operation-lookups`. 실제 API·Python 타입은 아직 구현하지 않았다.
- ctx는 서버 생성 값이며 모델 payload에 포함하지 않는다. 경로와 body에 같은 case/batch가 있으면 반드시 일치시킨다.

## 2. 요청 필드

| 필드 | 타입·필수성 | 제약 |
|---|---|---|
| `case_id, batch_id` | str, 필수 | 현재 읽기 scope와 대상 |
| `operation_id 또는 idempotency_key` | str, 정확히 하나 | operation별 조회 또는 canonical 업무 키 조회 |
| `expected_action_digest` | str, 필수 | 해당 의도의 의미를 검증 |
| `expected_input_version` | int, 필수 | 의도가 참조한 입력. 현재 입력과 다른 사실은 별도 표시 |

아래 예시는 합성 설명용이다. digest는 형식 예시이며 실제 fixture의 hash가 아니다. 인증 scope·principal·서버 시각은 예시 body에 넣지 않는다.

```json
{
  "case_id": "case_004",
  "batch_id": "BATCH001",
  "idempotency_key": "idem_demo_case004_v1",
  "expected_action_digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  "expected_input_version": 1
}
```

## 3. 반환 data

| 필드 | 의미·검사 |
|---|---|
| `operation_state` | NOT_FOUND/RUNNING/SUCCEEDED/FAILED_RETRYABLE/FAILED_FINAL/REJECTED/UNKNOWN |
| `observed_effect` | APPLIED/NOT_APPLIED/UNKNOWN |
| `canonical_operation_id, result_ref` | 반영된 경우 실제 참조 |
| `intent_input_version, current_input_version` | 효과 상태와 입력 변경을 별도 관측 |
| `authoritative, inflight_free` | 직렬화된 현재 DB 관측·활성 요청 종료 증명 여부 |
| `safe_to_retry_same_key` | 코드가 확인한 동일 키 재시도 가능성. 새 키 허가 아님 |

```json
{
  "status": "success",
  "data": {
    "operation_state": "SUCCEEDED",
    "observed_effect": "APPLIED",
    "canonical_operation_id": "op_demo_001",
    "result_ref": "result_demo_001",
    "intent_input_version": 1,
    "current_input_version": 1,
    "authoritative": true,
    "inflight_free": true,
    "safe_to_retry_same_key": false
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

1. 현재 읽기 권한과 대상·의도 binding을 검사한다. 다른 case의 작업 ID를 존재 여부 조회 수단으로 사용하지 못하게 한다.
2. operation_id 또는 scope에 bound된 업무 키로 current business repository를 조회한다.
3. 성공한 operation과 결과의 대상·입력·action binding을 확인해 회수한다.
4. 현재 입력 버전을 별도 읽어 의도의 과거 버전과 비교할 수 있게 반환한다.
5. NOT_FOUND라도 활성 writer·미확인 in-flight가 있으면 observed_effect=UNKNOWN, safe_to_retry=false다.
6. 같은 키의 선행 요청 종료·rollback/current read·inflight 없음이 확인된 경우에만 NOT_APPLIED와 동일 키 재시도 가능성을 반환한다.
7. 임의 operation_id의 조회 실패만으로는 안전 재시도를 허가하지 않는다. 해당 의도에 binding된 키와 의미상 업무 키(case/batch/input_version/action_kind)의 권위 있는 검사가 필요하며, 다른 action binding의 기존 효과는 충돌로 반환한다.

## 5. 오류·재시도 정책

| 코드·조건 | 처리 |
|---|---|
| `SCOPE_DENIED` | 다른 case 내용·결과 미노출 |
| `OPERATION_BINDING_MISMATCH` | 다른 작업 근거 사용 금지 |
| `TOOL_TIMEOUT` | UNKNOWN 유지·조회 한도 적용 |
| `OBSERVATION_NOT_AUTHORITATIVE` | 미반영 증거로 사용 금지 |
| `INVALID_LOOKUP` | 두 lookup key 동시 사용·근거 없는 ID 거부 |

제안 timeout은 2초이며 파일럿 후 고정한다. 호출 시도는 전체 Tool 예산에 포함한다. 읽기 재시도도 새 tool_call_id로 계수하고, 쓰기는 [복구 계약](../contracts/persistence-and-recovery.md)의 효과 조회를 우선한다. retryable은 자동 재실행 명령이 아니다.

## 6. 검증 사례

| 사례 | 기대 결과 |
|---|---|
| commit 성공+응답 누락 | APPLIED·result_ref 회수 |
| rollback 완료+동일 키 조회 | 권위 있는 NOT_APPLIED |
| worker 미확인+없음 | UNKNOWN·재시도 false |
| 다른 case op_id | 데이터 미노출 |
| 과거 작업+현재 입력 변경 | APPLIED와 CHANGED를 함께 표현 |

## 7. 저장·추적·한계

logical_run_id·session_id·tool_call_id·scope ref·정책 버전·요청/응답 ref·snapshot·오류·시간을 기록한다. 일반 trace에 토큰·금지 원문·모델 추론 전문은 저장하지 않는다.

get_operation은 부작용 없는 관측이다. 상태 조회 횟수도 비용에 포함한다. 응답의 retryable 또는 safe_to_retry는 외부 메모가 선언할 수 없으며 신뢰된 서비스 계약 검증을 통과해야 한다.
