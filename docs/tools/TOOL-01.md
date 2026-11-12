# TOOL-01 · get_case — 사건·대상·현재 입력 조회

> **로컬 Python 구현 / 2026-11-12**. [실행·검증·한계](get-case-usage.md). HTTP·MCP 연결은 미구현. 모델 등록 이름: `get_case` · 업무 읽기 · 주 사용 노드: [N-01](../small-nodes/N-01.md) · [N-03](../small-nodes/N-03.md) · [N-12](../small-nodes/N-12.md)
>
> [Tool 목록·공통 규칙](README.md) · [업무 DB](../contracts/business-data.md) · [보안](../contracts/security-boundary.md)

**구현 선행 설계:** [공통 호출 계약](../contracts/tool-call-contract.md) · [현재 repository와의 연결](../contracts/tool-call-contract.md#11-기존-db-어댑터와-tool-01-연결). 아래 API 표기는 개념 계약이며 공통 문서의 서비스 주입·엄격한 요청 타입으로 구체화한다.

## 1. 목적과 호출 경계

인증된 scope 내 사건의 대상·도착 상태·현재 입력·허용 작업 참조를 반환한다. 접수문·외부 메모를 권한 정보와 분리한다.

- 내부 Python 계약: `get_case(request: ToolRequest, ctx: TrustedCallContext) -> NodeResult[ToolData]`.
- 선택적 HTTP 어댑터: `GET /v1/cases/{case_id}`. 실제 API·Python 타입은 아직 구현하지 않았다.
- ctx는 서버 생성 값이며 모델 payload에 포함하지 않는다. 경로와 body에 같은 case/batch가 있으면 반드시 일치시킨다.

## 2. 요청 필드

| 필드 | 타입·필수성 | 제약 |
|---|---|---|
| `case_id` | str, 필수 | 현재 실행에 허용된 사건. 다른 case의 존재·내용 탐색 금지 |

아래 예시는 합성 설명용이다. digest는 형식 예시이며 실제 fixture의 hash가 아니다. 인증 scope·principal·서버 시각은 예시 body에 넣지 않는다.

```json
{
  "case_id": "case_004"
}
```

## 3. 반환 data

| 필드 | 의미·검사 |
|---|---|
| `case_id, batch_id` | 요청·scope와 일치 |
| `input_version, input_digest` | 현재 금융 입력 스냅샷 |
| `decision_at, ingestion_complete, processing_state` | 가상 업무 시각·도착·업무 배치 상태 |
| `operation_refs` | 현재 scope의 관련 작업/업무 키. 모든 비교군에 동일 공개 |
| `external_notes` | 내용·origin·known_at·hash. untrusted 콘텐츠로 분리 |

```json
{
  "status": "success",
  "data": {
    "case_id": "case_004",
    "batch_id": "BATCH001",
    "input_version": 1,
    "input_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "decision_at": "2026-10-30T10:00:00+09:00",
    "ingestion_complete": true,
    "processing_state": "FAILED",
    "operation_refs": [],
    "external_notes": []
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

1. 서버 생성 TrustedCallContext에서 읽기 scope를 확인한다.
2. case와 대상 batch를 같은 현재 스냅샷으로 읽는다.
3. input_version과 그 버전의 digest·도착 상태를 연결한다.
4. 허용된 operation refs만 반환하고 다른 case 정보를 섞지 않는다.
5. 접수문·메모는 external_notes로 분리하고 개인 식별 필드는 반환 목록에서 제외한다.

## 5. 오류·재시도 정책

| 코드·조건 | 처리 |
|---|---|
| `INVALID_ARGUMENT` | 필수 case_id·계약 오류, 재시도 없음 |
| `SCOPE_DENIED` | 권한 범위 거부, 데이터 반환 없음 |
| `CASE_UNAVAILABLE` | 현재 범위에서 사건을 찾지 못함 |
| `TOOL_TIMEOUT` | 읽기 재시도 가능, 마지막 값을 최신이라고 단정하지 않음 |

제안 timeout은 2초이며 파일럿 후 고정한다. 호출 시도는 전체 Tool 예산에 포함한다. 읽기 재시도도 새 tool_call_id로 계수하고, 쓰기는 [복구 계약](../contracts/persistence-and-recovery.md)의 효과 조회를 우선한다. retryable은 자동 재실행 명령이 아니다.

## 6. 검증 사례

| 사례 | 기대 결과 |
|---|---|
| 정상 조회 | 대상·버전·도착 상태 일치 |
| scope 밖 case | 내용·작업 refs 미노출 |
| operation 성공 뒤 조회 | processing_state 변화와 input_version 변화 구분 |
| 메모 승인 사칭 | external content로 유지 |

## 7. 저장·추적·한계

logical_run_id·session_id·tool_call_id·scope ref·정책 버전·요청/응답 ref·snapshot·오류·시간을 기록한다. 일반 trace에 토큰·금지 원문·모델 추론 전문은 저장하지 않는다.

operation_refs를 H에만 제공하지 않는다. B0/B1도 같은 조회 능력을 가져야 상태 저장·관측 전략의 차이를 공정하게 비교할 수 있다.
