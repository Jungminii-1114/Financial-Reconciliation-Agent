# TOOL-06 · prepare_escalation — 근거가 있는 이관 초안 구성

> **설계 초안 / 미구현**. 모델 등록 이름: `prepare_escalation` · 범위 제한 아티팩트 작성, 업무 입력 변경 없음 · 주 사용 노드: [N-15](../small-nodes/N-15.md)
>
> [Tool 목록·공통 규칙](README.md) · [업무 DB](../contracts/business-data.md) · [보안](../contracts/security-boundary.md)

## 1. 목적과 호출 경계

확인된 불일치·미확정 상태·실행 제한을 구조화된 보고 초안으로 만든다. 이메일·메신저·실제 담당자에게 자동 발송하지 않는다.

- 내부 Python 계약: `prepare_escalation(request: ToolRequest, ctx: TrustedCallContext) -> NodeResult[ToolData]`.
- 선택적 HTTP 어댑터: `POST /v1/cases/{case_id}/escalation-drafts`. 실제 API·Python 타입은 아직 구현하지 않았다.
- ctx는 서버 생성 값이며 모델 payload에 포함하지 않는다. 경로와 body에 같은 case/batch가 있으면 반드시 일치시킨다.

## 2. 요청 필드

| 필드 | 타입·필수성 | 제약 |
|---|---|---|
| `case_id, batch_id` | str, 필수 | 현재 scope의 대상 |
| `reason_codes` | list[enum], 필수 | MISMATCH/UNRESOLVED_EFFECT/BUDGET_LIMIT/APPROVAL_DENIED 등 |
| `evidence_refs` | list[str], 필수 | 등록된 같은 대상의 근거. 없으면 부족함 자체를 명시 |
| `operation_ref` | str|null, 선택 | 미확정 작업의 범위 제한 참조 |
| `unresolved_items` | list[str], 선택 | 확인하지 못한 사항. 권한·승인 명령으로 해석하지 않음 |

아래 예시는 합성 설명용이다. digest는 형식 예시이며 실제 fixture의 hash가 아니다. 인증 scope·principal·서버 시각은 예시 body에 넣지 않는다.

```json
{
  "case_id": "case_003",
  "batch_id": "BATCH001",
  "reason_codes": [
    "MISMATCH"
  ],
  "evidence_refs": [
    "ev_demo_mismatch"
  ],
  "operation_ref": null,
  "unresolved_items": []
}
```

## 3. 반환 data

| 필드 | 의미·검사 |
|---|---|
| `draft_id, status` | artifact ID와 DRAFT_ONLY |
| `case_id, batch_id, reason_codes` | 대상·이유 |
| `evidence_refs, unresolved_items` | 확인된 사실·부족한 자료 |
| `report_facts, suggested_next_checks` | 결정적 보고 사실·허용된 후속 확인 |
| `external_delivery` | 항상 false |

```json
{
  "status": "success",
  "data": {
    "draft_id": "draft_demo_001",
    "status": "DRAFT_ONLY",
    "case_id": "case_003",
    "batch_id": "BATCH001",
    "reason_codes": [
      "MISMATCH"
    ],
    "evidence_refs": [
      "ev_demo_mismatch"
    ],
    "unresolved_items": [],
    "report_facts": {
      "mismatched_transaction_ids": [
        "TX0002"
      ]
    },
    "suggested_next_checks": [
      "REVIEW_SOURCE_RECORDS"
    ],
    "external_delivery": false
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

1. scope·reason enum·refs 존재·대상·시점을 검사한다.
2. registry와 검증 결과에서 보고 facts를 가져오고 자유 입력의 사실 주장을 그대로 승인하지 않는다.
3. 불일치·확인 불가·예산 제한·승인 거부를 구분하여 템플릿을 구성한다.
4. 출력 정책의 금지 개인정보 필드·민감정보 형식·비밀과 HTML 표현을 검사한다. evaluator 전용 canary 목록은 받지 않는다.
5. 범위 제한 artifact로 저장하고 DRAFT_ONLY를 반환한다. 업무 DB 입력·작업 효과·외부 통신은 변경하지 않는다.

## 5. 오류·재시도 정책

| 코드·조건 | 처리 |
|---|---|
| `UNKNOWN_EVIDENCE_REF` | 허위 근거 초안 생성 금지 |
| `SCOPE_DENIED` | 다른 사건의 사실·문서 사용 금지 |
| `PROHIBITED_OUTPUT` | 민감 내용 반환 차단 |
| `ARTIFACT_WRITE_ERROR` | 초안 미저장을 전달 완료로 표시하지 않음 |

제안 timeout은 2초이며 파일럿 후 고정한다. 호출 시도는 전체 Tool 예산에 포함한다. 읽기 재시도도 새 tool_call_id로 계수하고, 쓰기는 [복구 계약](../contracts/persistence-and-recovery.md)의 효과 조회를 우선한다. retryable은 자동 재실행 명령이 아니다.

## 6. 검증 사례

| 사례 | 기대 결과 |
|---|---|
| 금액 불일치 | 변경된 거래 ID와 근거 포함 |
| 미확정 작업 | 완료·미반영으로 추측하지 않음 |
| 다른 batch 근거 | 거부 |
| 반복 초안 | 업무 효과 중복·외부 발송 0 |
| 금지 문자열 | 출력 검사 실패 기록 |

## 7. 저장·추적·한계

logical_run_id·session_id·tool_call_id·scope ref·정책 버전·요청/응답 ref·snapshot·오류·시간을 기록한다. 일반 trace에 토큰·금지 원문·모델 추론 전문은 저장하지 않는다.

보고 초안 생성은 업무 처리 성공이나 실제 이관 접수의 증명이 아니다. 사용자 화면이 이 초안을 검토할 수 있도록 하며 출력 실패를 재처리 재실행으로 해결하지 않는다.
