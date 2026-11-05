# NodeResult[T] — Small Node와 Tool 공통 반환 외피

> v0.1 설계. ReportAnchor의 4필드 구조를 참고하되 대사 상태·효과·승인 계약은 이 프로젝트에서 별도로 정의한다. [상태](../contracts/state-and-types.md)

## 1. 공통 4개 필드

| 키 | 타입 | 의미 |
|---|---|---|
| status | `success / partial / error` | 필수 처리 단계의 실행 상태 |
| data | 업무별 T 또는 null | 검증된 결과만. `error`이면 원칙적으로 null |
| diagnostics | 단계명 → StepDiagnostic | 성공·실패·생략 위치와 코드·비용 |
| outcome | 허용된 업무 코드 또는 null | 실행 성공과 별도의 판단 결과 |

status=success는 DB 반영·대사 일치·승인·최종 완료를 뜻하지 않는다. 예를 들어 금액 불일치를 정확히 찾은 조회는 실행 success, 업무상 `mismatch_found`다.

partial은 일부 안전한 관측을 반환할 때만 허용한다. 조회의 일부 페이지만 있으면 `coverage.complete=false`로 표시하고 배치 전체 일치·부재의 근거로 사용하지 않는다. 승인 유효성·쓰기 허가·완료 검사를 partial로 통과시키지 않는다.

## 2. 단계 진단

`StepDiagnostic = {status: success|failed|skipped, error_code?: str, reason?: str, retryable?: bool, elapsed_ms?: int, artifact_ref?: str}`.

retryable은 힌트다. 다음 노드·재시도는 Big Node와 예산·작업 효과 상태로 결정한다. Tool 타임아웃이 retryable이어도 쓰기를 즉시 반복하지 않는다. 원문 전체·비밀·추론 전문을 진단에 남기지 않는다.

## 3. 완전한 빈 조회의 예

```json
{
  "status": "success",
  "data": {
    "evidence_id": "ev_demo_empty",
    "query_profile": "EXISTENCE",
    "transaction_ids": ["TX0003"],
    "matched_ids": [],
    "coverage": {"complete": true},
    "input_version": 1,
    "as_of": "2026-10-30T10:00:00+09:00"
  },
  "diagnostics": {"query": {"status": "success"}},
  "outcome": "no_matching_records"
}
```

이는 해당 범위·시점에 조회가 완결됐다는 뜻이다. 원천 자료 전체가 영원히 없다는 뜻이 아니다. 도착 미완료이면 업무는 WAIT다.

## 4. 쓰기 응답 유실의 예

```json
{
  "status": "error",
  "data": null,
  "diagnostics": {
    "dispatch": {
      "status": "failed",
      "error_code": "TOOL_RESPONSE_LOST",
      "reason": "operation effect must be queried",
      "retryable": true
    }
  },
  "outcome": null
}
```

쓰기 전 저장한 OperationIntent는 State에 남는다. `data=null`을 미반영의 증거로 사용하지 않는다. N-12가 같은 작업/업무 키를 조회한다. 작업이 반영됐다는 정보는 업무 서비스의 검증된 구조화 응답으로만 회수한다.

## 5. 오류 분류

| 코드 | 의미 | 기본 처리 |
|---|---|---|
| INVALID_ARGUMENT / SCOPE_DENIED | 입력·접근 위반 | 권한 확장·무검증 폴백 없이 종료/이관 |
| VERSION_CONFLICT / APPROVAL_STALE | 현재 입력·승인 불일치 | 재조사·새 승인, 기존 승인의 자동 재사용 금지 |
| SNAPSHOT_INCOMPLETE / EVIDENCE_STALE | 근거 부족·오래됨 | 허용 조회 또는 이관 |
| TOOL_TIMEOUT / TOOL_RESPONSE_LOST | 응답 미확인 | 읽기는 제한 재시도, 쓰기는 효과 조회 |
| IDEMPOTENCY_CONFLICT | 같은 키에 다른 업무 의미 | 재시도 차단·감사 기록 |
| BUDGET_EXHAUSTED | 누적 한도 도달 | 효과 미확정 정보를 보존하고 종료 |
| STORAGE_ERROR / CONTRACT_VIOLATION | 지속 저장·계약 오류 | 쓰기·완료 차단, 실행 실패 기록 |

## 6. 계약 검증

4개 키를 항상 직렬화한다. 노드별 허용 outcome과 data 타입을 검사한다. 빈 결과와 조회 실패, 업무 불일치와 코드 오류, 응답 유실과 미반영을 각각 구분하는 사례를 검사한다. 공통 외피에는 다음 노드 이름이나 숨은 평가 정답을 넣지 않는다.
