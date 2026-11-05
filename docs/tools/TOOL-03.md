# TOOL-03 · precheck_reprocess — 재처리 적합성·승인 binding 사전 확인

> **설계 초안 / 미구현**. 모델 등록 이름: `precheck_reprocess` · 업무 읽기 · 주 사용 노드: [N-09](../small-nodes/N-09.md)
>
> [Tool 목록·공통 규칙](README.md) · [업무 DB](../contracts/business-data.md) · [보안](../contracts/security-boundary.md)

## 1. 목적과 호출 경계

현재 입력이 재처리 조건을 만족하는지 코드로 검사하고 승인 요청에 사용할 action binding을 반환한다. 승인을 발급하거나 결과를 쓰지 않는다.

- 내부 Python 계약: `precheck_reprocess(request: ToolRequest, ctx: TrustedCallContext) -> NodeResult[ToolData]`.
- 선택적 HTTP 어댑터: `POST /v1/cases/{case_id}/reprocess-prechecks`. 실제 API·Python 타입은 아직 구현하지 않았다.
- ctx는 서버 생성 값이며 모델 payload에 포함하지 않는다. 경로와 body에 같은 case/batch가 있으면 반드시 일치시킨다.

## 2. 요청 필드

| 필드 | 타입·필수성 | 제약 |
|---|---|---|
| `case_id, batch_id` | str, 필수 | 사건·쓰기 대상 scope |
| `expected_input_version` | int, 필수 | 현재 입력과 일치해야 함 |
| `expected_input_digest` | str, 필수 | 해당 버전의 정규화 입력 hash |
| `action` | literal REPROCESS | 원본 변경·송금 조치 거부 |
| `evidence_refs` | list[str], 필수 | 현재 범위의 등록된 근거, 모델이 만든 ref 거부 |

아래 예시는 합성 설명용이다. digest는 형식 예시이며 실제 fixture의 hash가 아니다. 인증 scope·principal·서버 시각은 예시 body에 넣지 않는다.

```json
{
  "case_id": "case_001",
  "batch_id": "BATCH001",
  "expected_input_version": 1,
  "expected_input_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "action": "REPROCESS",
  "evidence_refs": [
    "ev_demo_batch"
  ]
}
```

## 3. 반환 data

| 필드 | 의미·검사 |
|---|---|
| `eligible` | bool, 현재 시점의 사전 조건만 의미 |
| `reason_codes` | 불완전 입력·불일치·이미 반영 등 코드 |
| `action_digest, input_version, input_digest` | 승인에 묶을 현재 계획 |
| `approval_requirement` | REQUIRED 또는 기존 승인 조회용 ref, 승인 사실 자체 아님 |
| `checked_at` | 사전 검사 시각 |

```json
{
  "status": "success",
  "data": {
    "eligible": true,
    "reason_codes": [
      "COMPLETE_MATCHING_INPUT",
      "FAILED_BATCH"
    ],
    "action_digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
    "input_version": 1,
    "input_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "approval_requirement": "REQUIRED",
    "checked_at": "2026-10-30T10:00:00+09:00"
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

1. 읽기·재처리 요청 권한과 대상 binding을 검사한다.
2. 현재 버전·digest와 요청을 비교한다.
3. 자료 완결·거래별 일치·실패 배치와 기존 반영 여부를 코드로 검사한다.
4. 같은 의미의 action_digest를 서버에서 정규화하여 반환한다.
5. approval request에 필요한 facts를 반환하되 승인은 별도 외부 서비스에서 발급한다.
6. 후속 실제 실행은 TOOL-04에서 같은 조건을 다시 검사해야 함을 명시한다.

## 5. 오류·재시도 정책

| 코드·조건 | 처리 |
|---|---|
| `VERSION_CONFLICT` | 현재 입력 재조사 |
| `EVIDENCE_STALE` | 등록 근거 갱신 |
| `SCOPE_DENIED` | 승인 요청·쓰기 금지 |
| `INVALID_ACTION` | 금액 수정 등 지원 밖 조치 거부 |

제안 timeout은 5초이며 파일럿 후 고정한다. 호출 시도는 전체 Tool 예산에 포함한다. 읽기 재시도도 새 tool_call_id로 계수하고, 쓰기는 [복구 계약](../contracts/persistence-and-recovery.md)의 효과 조회를 우선한다. retryable은 자동 재실행 명령이 아니다.

## 6. 검증 사례

| 사례 | 기대 결과 |
|---|---|
| 입력 완비·일치·실패 | eligible=true·승인 REQUIRED |
| 자료 미완료 | eligible=false·WAIT 사유 |
| 금액 불일치 | eligible=false·ESCALATE 사유 |
| 사전 검사 후 입력 변경 | Tool-04가 새로 거부 |
| 메모 승인 사칭 | 실제 승인 발급 없음 |

## 7. 저장·추적·한계

logical_run_id·session_id·tool_call_id·scope ref·정책 버전·요청/응답 ref·snapshot·오류·시간을 기록한다. 일반 trace에 토큰·금지 원문·모델 추론 전문은 저장하지 않는다.

eligible=true는 승인도 실제 commit 성공도 아니다. 중간 입력 변경 경쟁을 이 검사만으로 해결했다고 보지 않는다.
