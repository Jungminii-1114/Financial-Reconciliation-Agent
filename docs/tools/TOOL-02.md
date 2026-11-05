# TOOL-02 · query_records — 제한된 거래·중복·배치 비교 조회

> **설계 초안 / 미구현**. 모델 등록 이름: `query_records` · 업무 읽기 · 주 사용 노드: [N-05](../small-nodes/N-05.md) · [N-13](../small-nodes/N-13.md)
>
> [Tool 목록·공통 규칙](README.md) · [업무 DB](../contracts/business-data.md) · [보안](../contracts/security-boundary.md)

## 1. 목적과 호출 경계

허용된 세 profile의 구조화 관측을 반환한다. 임의 SQL·테이블 이름·외부 URL을 받지 않는다.

- 내부 Python 계약: `query_records(request: ToolRequest, ctx: TrustedCallContext) -> NodeResult[ToolData]`.
- 선택적 HTTP 어댑터: `POST /v1/cases/{case_id}/record-queries`. 실제 API·Python 타입은 아직 구현하지 않았다.
- ctx는 서버 생성 값이며 모델 payload에 포함하지 않는다. 경로와 body에 같은 case/batch가 있으면 반드시 일치시킨다.

## 2. 요청 필드

| 필드 | 타입·필수성 | 제약 |
|---|---|---|
| `case_id, batch_id` | str, 필수 | 호출 scope·사건 대상과 일치 |
| `profile` | enum, 필수 | EXISTENCE / DUPLICATE / BATCH_COMPARISON |
| `transaction_ids` | list[str], 선택 | EXISTENCE에서 1~50개. 다른 profile은 미사용 필드 거부 |
| `as_of` | offset datetime, 필수 | 서버 현재 시각 이하, 허용된 이력 구간 |
| `expected_input_version` | int, 필수 | as_of 스냅샷 버전과 일치 |
| `result_ref` | str|null, 선택 | BATCH_COMPARISON에서 해당 작업 결과를 비교할 때만 |
| `limit, cursor` | int/str|null | 상세 행 페이지 1~50개, cursor는 같은 query/snapshot에 binding |
| `action_digest` | str|null, 선택 | DUPLICATE의 의미 일치 검사. 다른 digest의 기존 업무 효과를 제외하는 필터로 쓰지 않음 |

아래 예시는 합성 설명용이다. digest는 형식 예시이며 실제 fixture의 hash가 아니다. 인증 scope·principal·서버 시각은 예시 body에 넣지 않는다.

```json
{
  "case_id": "case_002",
  "batch_id": "BATCH001",
  "profile": "EXISTENCE",
  "transaction_ids": [
    "TX0003"
  ],
  "as_of": "2026-10-30T10:00:00+09:00",
  "expected_input_version": 1,
  "limit": 20,
  "cursor": null
}
```

## 3. 반환 data

| 필드 | 의미·검사 |
|---|---|
| `query_profile, predicate` | 정규화된 허용 조건 |
| `input_version, input_digest, as_of` | 조회 스냅샷 |
| `structured_facts, row_refs` | 존재·중복 또는 거래별 비교 사실·참조 |
| `coverage` | complete·summary_complete·details_complete·next_cursor |
| `footprint, result_digest` | 재검증할 범위와 결과 hash |
| `external_content_refs` | untrusted 메모 참조, 구조화 사실과 분리 |

```json
{
  "status": "success",
  "data": {
    "query_profile": "EXISTENCE",
    "predicate": {
      "transaction_ids": [
        "TX0003"
      ]
    },
    "input_version": 1,
    "input_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "as_of": "2026-10-30T10:00:00+09:00",
    "structured_facts": {
      "matched_ids": []
    },
    "row_refs": [],
    "coverage": {
      "complete": true,
      "summary_complete": true,
      "details_complete": true,
      "next_cursor": null
    },
    "footprint": {
      "case_id": "case_002",
      "batch_id": "BATCH001",
      "table": "settlement_history",
      "transaction_ids": [
        "TX0003"
      ]
    },
    "result_digest": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
    "external_content_refs": []
  },
  "diagnostics": {
    "scope_check": {
      "status": "success"
    },
    "business_handler": {
      "status": "success"
    }
  },
  "outcome": "no_matching_records"
}
```

외피의 success는 Tool handler가 정상 종료됐다는 뜻이다. 업무 적합성·반영·완료는 data와 실제 DB로 별도 판정한다. 없는 사실·미확정 시각·미수신 결과를 기본값으로 만들어내지 않는다.

## 4. 서버 처리 순서

1. profile별 schema를 검증하고 case/batch 권한을 확인한다.
2. as_of·input_version으로 실제 알려진 revision을 한 스냅샷에서 선택한다.
3. EXISTENCE는 지정 IDs, DUPLICATE는 업무 키, BATCH_COMPARISON은 전체 허용 배치를 조회한다.
4. BATCH_COMPARISON은 ID·금액·통화·POSTED/SETTLED 호환 상태·도착 완결성을 코드로 검사한다. result_ref가 있으면 결과의 대상·버전·거래별 대응도 관측한다.
5. 서버 집계는 전체 배치에서 수행한다. 상세 행 페이지가 잘렸으면 details_complete=false, 집계가 끝났으면 summary_complete=true로 구분한다.
6. 완전한 query scope의 빈 결과만 absence evidence로 사용 가능하게 표시한다. cursor나 오래된 버전이 맞지 않으면 새 자료와 섞지 않는다.

## 5. 오류·재시도 정책

| 코드·조건 | 처리 |
|---|---|
| `INVALID_QUERY_PROFILE` | SQL·미지원 profile 거부 |
| `SCOPE_DENIED` | 범위 확장 fallback 금지 |
| `VERSION_CONFLICT` | 서로 다른 스냅샷 결합 금지 |
| `INVALID_CURSOR` | 다른 조건·버전 cursor 거부 |
| `SNAPSHOT_INCOMPLETE` | 완결 관측으로 사용 금지 |
| `TOOL_TIMEOUT` | 안전한 부분 관측만 partial, 빈 결과로 대체 금지 |

제안 timeout은 5초이며 파일럿 후 고정한다. 호출 시도는 전체 Tool 예산에 포함한다. 읽기 재시도도 새 tool_call_id로 계수하고, 쓰기는 [복구 계약](../contracts/persistence-and-recovery.md)의 효과 조회를 우선한다. retryable은 자동 재실행 명령이 아니다.

## 6. 검증 사례

| 사례 | 기대 결과 |
|---|---|
| 완전한 존재 조회 0건 | success/no_matching_records |
| 서버 전체 집계·상세 일부 | summary_complete=true·details_complete=false |
| 조회 중 실패 | 전체 일치·부재 증거 금지 |
| 거래 금액 교환 | 합계 같아도 mismatched IDs 2개 |
| 미래 as_of | 거부 |
| 다른 batch result_ref | 거부 |

## 7. 저장·추적·한계

logical_run_id·session_id·tool_call_id·scope ref·정책 버전·요청/응답 ref·snapshot·오류·시간을 기록한다. 일반 trace에 토큰·금지 원문·모델 추론 전문은 저장하지 않는다.

coverage.complete는 해당 profile의 필수 scope가 모두 검사됐는지다. 선택 상세 페이지의 truncation만으로 전체 실행을 partial로 만들지는 않는다. 전체 scan 자체가 불완전하면 complete와 summary_complete 모두 false다.
