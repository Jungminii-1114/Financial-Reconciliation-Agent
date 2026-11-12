# 업무 Tool 공통 호출 계약 — tool-call-1

> **설계 초안 / 미구현 · 2026-11-10.** TOOL-01을 구현하기 전에 정하는 Python 서비스 경계다. 이 문서의 타입·호출 기록·검증 사례는 구현 결과가 아니다. 기존 [NodeResult](../small-nodes/common-result-contract.md)의 네 필드를 유지하면서 호출·권한·오류·기록 규칙을 구체화한다.
>
> [문서 홈](../README.md) · [Tool 목록](../tools/README.md) · [TOOL-01](../tools/TOOL-01.md) · [내부 서비스](../tools/internal-services.md) · [보안 경계](security-boundary.md) · [복구](persistence-and-recovery.md)

**구현 갱신 2026-11-12:** [TOOL-01과 최소 공통 호출 경로](../tools/get-case-usage.md)는 구현됐다. 이 문서 전체의 여섯 Tool·복구·전송 계층이 모두 구현된 것은 아니다.

## 1. 무엇을 설계하는가

공통 호출 계약은 **어떤 Tool을, 누구의 권한으로, 어떤 인자로 호출하고, 결과와 실패를 어떻게 해석하고 기록할지** 정하는 규칙이다. 모델마다 다른 방식으로 업무 DB를 직접 호출하게 두지 않고 같은 Python dispatcher와 업무 서비스를 사용한다.

```text
모델의 Tool 제안 또는 코드의 호출 요청
  → 서버의 호출 ID·권한 문맥 연결
  → 시도 예약·안전한 기록
  → Tool 이름·인자·대상·정책 검사
  → 업무 handler → 범위 제한 repository / 업무 서비스
  → 응답 계약·공개 필드 검사
  → 호출 결과 기록 → NodeResult 반환
```

초기 구현은 로컬 Python 함수다. HTTP·MCP·LLM SDK 등록은 이후에 이 경계 바깥에 붙인다. 인증 플랫폼, 승인 발급, Agent 상태 복구를 공통 타입만으로 구현했다고 간주하지 않는다.

### 계약의 정본과 적용 순서

| 문서 | 소유하는 내용 |
|---|---|
| 이 문서 | 호출 요청, 신뢰 문맥, dispatcher 순서, 호출 기록, 공통 오류 처리 |
| [NodeResult](../small-nodes/common-result-contract.md) | 반환 네 필드와 실행 상태의 의미 |
| [각 TOOL 명세](../tools/README.md) | 업무별 인자·data·outcome·트랜잭션 불변식 |
| [보안](security-boundary.md) / [복구](persistence-and-recovery.md) | 권한·승인과 미확정 효과·같은 키 재시도 |

공통 계약이 개별 Tool의 업무 검사를 대신하지 않는다. 범용 예외 처리로 승인·버전·멱등 검사를 생략하지 않는다. 충돌을 발견하면 해당 문서를 함께 수정한 뒤 구현한다.

## 2. 고정할 설계 결정

1. 모델 입력, 서버 권한 문맥, 서버 내부 호출 기록을 별도 타입으로 둔다.
2. 모델의 principal·scope·시간·예산·trust 지정은 무시하지 않고 스키마 오류로 거부한다.
3. 권한은 `trial + case + batch` 대상 쌍과 Tool 권한으로 검사한다. case/batch 목록의 임의 교차 조합은 금지한다.
4. `NodeResult.status`는 함수 실행 상태다. 승인·DB 반영·업무 완료와 구별한다.
5. 한 번의 dispatcher 시도는 하나의 `tool_call_id`를 가진다. 업무 중복 방지는 별도의 `operation_id/idempotency_key`가 담당한다.
6. 읽기와 쓰기는 재시도 규칙이 다르다. 쓰기 응답 미확인은 미반영 증거가 아니다.
7. 외부 메모는 출처가 붙은 비신뢰 콘텐츠다. 구조화 권한·승인·업무 사실과 합치지 않는다.
8. 숨은 정답·미래 계획·보호 canary 값은 runtime 계약에 들어오지 않는다.

## 3. 호출 입력: 모델 제안과 내부 요청

### 3.1 모델 또는 코드가 제안하는 부분

Tool 선택 이름과 업무 인자만 받는다. TOOL-01의 모델 인자는 다음 한 필드다.

```json
{
  "case_id": "c_2e8b6b7c4702"
}
```

Tool 선택 `get_case`는 모델 SDK의 함수 선택 또는 코드 호출에서 전달받되 registry allowlist로 검사한다. 함수 이름을 받았다는 이유로 임의 import·eval·SQL을 실행하지 않는다.

### 3.2 서버가 구성하는 내부 호출 요청

논리 타입 `ToolInvocation`을 다음과 같이 제안한다. Python 실제 클래스와 검증 라이브러리는 구현 시 추가한다.

| 필드 | 타입 | 소유·규칙 |
|---|---|---|
| `contract_version` | literal `tool-call-1` | 서버 어댑터가 지정. 미지원 버전은 실패 |
| `tool_name` | 등록된 Tool 이름 | 선택 요청을 allowlist와 대조 |
| `arguments` | 해당 Tool의 엄격한 요청 타입 | 업무 인자만. 미지원 필드 거부 |

`tool_call_id` 등 추적 정보는 아래 문맥이 소유한다. 두 곳에서 받아 불일치할 수 있게 만들지 않는다. 모델 SDK의 호출 ID는 선택적 상관관계 정보일 뿐 내부 ID·권한의 근거가 아니다.

TOOL-04의 `operation_id`, `idempotency_key`, `approval_id`는 기존 업무 계약의 인자로 유지한다. **모델이 직접 생성·확정하도록 허용하지 않는다.** N-10 또는 공통 의도 준비 서비스가 만든 durable 참조와 일치하는지 dispatcher와 업무 서비스가 검사한다. B0/B1/R0도 같은 서비스·검사를 사용하고, H 전용 체크포인트의 존재만 요구하지 않는다.

### 3.3 공통 인자 검증

- 추가 필드 거부, 필수 필드·enum·길이·개수 검사. 문자열 `"1"`을 정수로, `true`를 정수 1로 암묵 변환하지 않는다.
- JSON 전송 계층은 중복 키, NaN/Infinity, 깨진 JSON을 거부한다. 이미 dict가 된 뒤에는 중복 키를 복구할 수 없으므로 decode 단계에서 검사한다.
- case ID는 현재 합성기의 `c_[0-9a-f]{12}`와 일치시킨다. Tool 문서의 `case_001` 등은 개념 예시이며 실제 pilot 호출 값이 아니다.
- hash는 해당 업무 필드의 정규화 규칙을 따른다. 인자 전체를 일괄 소문자화·공백 제거하여 업무 의미를 바꾸지 않는다.
- 시각은 offset이 있는 ISO 8601만 허용하고 내부 비교는 UTC로 정규화한다. 도착·판단 시각을 모델의 주장으로 바꾸지 않는다.
- 초기 제안 상한: 직렬화한 인자 32 KiB, 공개 응답 128 KiB. 더 작은 개별 Tool 제한이 우선한다. 상한은 UTF-8 바이트 기준이며 파일럿 후 동결한다.
- 응답 상한을 넘었다고 JSON을 바이트 중간에서 자르거나 금융 사실을 조용히 누락하지 않는다. 페이지 계약이 있는 Tool은 명시적 coverage/cursor를 사용하고, 그 외에는 `OUTPUT_LIMIT_EXCEEDED`로 실패한다.

## 4. 서버가 제공하는 TrustedCallContext

`TrustedCallContext`는 인증된 로컬 runner/향후 서버의 context factory만 구성한다. 모델 arguments와 병합하지 않는다. Python frozen dataclass를 사용하더라도 인증·OS 격리가 생기는 것은 아니다.

| 필드 | 타입·의미 |
|---|---|
| `principal_id` | 현재 호출 주체. 모델의 역할 주장과 별개 |
| `trial_id` | 격리된 업무 DB 실행 단위 |
| `logical_run_id`, `session_id` | 중단 전후 유지하는 실행 ID / 세션마다 새 ID |
| `tool_call_id` | dispatcher 시도마다 서버가 발급하는 고유 ID |
| `budget_group_id` | 같은 trial의 누적 호출 예산 참조 |
| `allowed_targets` | 불변 `(case_id, batch_id)` 쌍 목록. `trial_id` 내부에서만 유효 |
| `allowed_tools` | 서버 정책상 호출 가능한 여섯 업무 Tool의 부분집합 |
| `scope_version`, `policy_version` | 현재 권한·정책 버전. 호출마다 현재 유효성을 확인 |
| `server_now` | 실제 관측·감사용 UTC 시각. 금융 판단 시각으로 대체 사용 금지 |
| `config_hash` | 비교군·runner 설정 binding |

기존 보안 문서의 `allowed_case_ids/allowed_batch_ids` 독립 목록은 개념 설명이었다. 구현 정본은 이 문서의 **대상 쌍**이다. 기존 목록을 받아야 한다면 서버가 실제로 허용한 쌍을 명시적으로 제공해야 하며 Cartesian product로 변환하지 않는다.

### 시간과 실행 서비스는 문맥 밖에서 주입

`RuntimeServices`는 registry, 범위 제한 repository factory, 예산 ledger, audit store, 실제/단조 시계, 승인·의도 조회 서비스를 가진 **코드 전용 의존성**이다. 모델 입력·Tool data로 직렬화하지 않는다. `TrialHandle` 전체나 evaluator/controller DB 경로를 handler에 넘기지 않는다.

금융 현재 시각은 업무 DB의 가상 시계, TOOL-02의 `as_of`는 그 시계 이내의 요청 관측 시각이다. 실제 deadline은 단조 시계로 측정한다. 승인 만료는 업무 정책에 지정된 금융 시계로 검사하고, 실제 수행 시간과 혼동하지 않는다.

TOOL-01만 시험할 때도 로컬 runner가 실행·세션·호출 ID를 발급한다. 이는 테스트 식별자이며 Agent 루프·체크포인트 복구 구현을 뜻하지 않는다.

## 5. 함수 경계와 registry

아래는 구현할 인터페이스를 설명하는 의사 코드다. 현재 import 가능한 API가 아니다.

```python
dispatch_tool(invocation, ctx, services, *, phase=None) -> NodeResult
get_case(request: GetCaseRequest, ctx, services) -> NodeResult[GetCaseData]
```

| registry 항목 | 규칙 |
|---|---|
| `name`, `request_type`, `response_type` | 고정 이름·입출력 스키마. 임의 함수 실행 금지 |
| `effect_kind` | `READ_ONLY / BUSINESS_WRITE / ARTIFACT_WRITE` |
| `handler` | 서버가 등록한 구현 |
| `timeout_ms`, `max_response_bytes` | 서버 설정. 모델의 연장 요청 금지 |
| `allowed_outcomes`, `partial_policy` | Tool별 allowlist. 새 코드 임의 생성 금지 |

TOOL-01/02/03/05는 READ_ONLY, TOOL-04는 BUSINESS_WRITE, TOOL-06은 ARTIFACT_WRITE다. TOOL-06은 단순 읽기가 아니며 초안 저장 실패·중복을 따로 다룬다. 외부 메시지를 발송하지 않는다.

모든 비교군은 같은 registry·업무 권한·서비스 검사를 사용한다. `phase`는 H의 추가 단계 게이트에만 쓰는 코드 값이다. B0의 자유 도구 루프에 H의 현재 단계가 없다는 이유로 기본 호출을 금지하지 않는다. 실제 공정성은 실험 설정으로 검사한다.

## 6. dispatcher 처리 순서

| 순서 | 수행 내용 | 실패·기록 원칙 |
|---|---|---|
| 1 | context 생성 주체·실행 binding·현재 권한 확인 | 신뢰 문맥이 없으면 일반 접속 거부. 임의 trial 예산을 수정하지 않음 |
| 2 | 새 호출 ID, 서버 Tool 시도 예산 원자적 예약 | 미등록 Tool·잘못된 인자 요청도 예약된 시도는 소비. 모델 생성 JSON 자체의 파싱 실패는 별도 parser 실패·모델 비용으로 기록 |
| 3 | payload 없는 `ATTEMPT_RESERVED`와 예약을 durable 저장 | 기록 실패 시 handler 진입 0회. 마지막 한도 초과 거부는 별도 사건으로 기록하며 허용 시도 수를 상한 위로 예약하지 않음 |
| 4 | 계약 버전·registry·엄격한 인자·크기 검사 | 해당 단계의 안전한 오류 코드, 이후 단계 skipped |
| 5 | Tool 권한·대상 쌍·trial·참조 binding 검사 | case 존재를 탐색하기 전에 요청 case의 허용 여부 확인. 허용 case의 실제 batch를 범위 안에서 확인 |
| 6 | 선택적 H phase 검사, 필요 시 durable intent 확인 | 기본 서비스 권한·승인 검사는 모든 비교군에 유지 |
| 7 | handler 진입 사실 기록 후 업무 실행 | 읽기는 일관된 스냅샷. 새 쓰기의 승인·버전·멱등 검사는 업무 트랜잭션 안에서 다시 수행 |
| 8 | NodeResult 및 공개 필드 allowlist·길이 검사 | 잘못된 응답을 정상 데이터로 포장하지 않음. 쓰기 반영 가능성은 별도 보존 |
| 9 | 결과·시간·예산 관측·안전한 참조 저장 | 실제 수행 시간 기록. 결과를 잃으면 usage를 0으로 확정하지 않음 |
| 10 | 검증된 NodeResult만 호출자에게 전달 | 전송 실패·세션 중단으로 결과가 수신됐다고 추정하지 않음 |

예약과 audit가 별도 저장소라면 원자적이라고 주장하지 않는다. 초기 구현은 같은 runtime ledger의 한 트랜잭션으로 2~3을 기록한다. 중간 중단 후 예약을 반환하지 않고 미완료 시도를 남긴다. runtime ledger와 업무 DB는 별도이므로 두 DB commit 간 어긋남은 복구 대상이다.

허용 밖 요청은 `SCOPE_DENIED`로 통일해 해당 case/operation의 존재 여부를 공개하지 않는다. `CASE_UNAVAILABLE`은 이미 허용된 범위에서 조회 불가할 때만 사용한다. 잘못된 문맥은 공개 API 바깥의 접속 거부로 처리하며 위 네 필드 응답을 위해 가짜 인증 문맥을 만들지 않는다.

Tool handler가 범위 검사 없이 직접 호출되는 우회를 막기 위해 서버 문맥·repository binding을 handler 경계에서도 확인한다. dispatcher가 체크했다는 사실만으로 업무 서비스의 검사를 생략하지 않는다.

## 7. 응답: 네 필드를 유지한다

| 필드 | 계약 |
|---|---|
| `status` | `success / partial / error`. 필수 처리 단계의 실행 상태 |
| `data` | 해당 Tool의 검증된 공개 타입. error는 null |
| `diagnostics` | 단계 → StepDiagnostic. 안전한 오류·지연·재시도 가능성만 |
| `outcome` | Tool별 허용 업무 코드 또는 null. 다음 노드 이름·숨은 정답 금지 |

공통 외피에 `metadata`, `error`, `next_node` 등을 임의 추가하지 않는다. 내부 `ToolCallRecord`는 별도 저장소에 보관한다. 모델에게 보내는 diagnostics는 공개 필드만 투영하고, 서버 경로·principal·비공개 원문·스택 추적은 제외한다.

### 상태와 부분 결과

- 정상적으로 `eligible=false`를 판단한 TOOL-03은 success다. 업무상 재처리 불가를 파이썬 실행 오류로 취급하지 않는다.
- 읽기 실패를 빈 목록으로 바꾸지 않는다. 완전한 빈 조회와 실행 실패는 다른 결과다.
- TOOL-01은 전체 필수 메타데이터를 한 스냅샷으로 반환하거나 error다. 초기 버전에서 partial은 허용하지 않는다.
- TOOL-02는 안전한 관측과 명시적 coverage가 있을 때만 partial을 허용한다. 상세 페이지의 정상 페이징은 그 자체로 실행 partial이 아니다.
- 승인·쓰기 허가·완료 검사를 partial로 통과시키지 않는다. TOOL-04가 불명인 경우 error와 durable intent를 보존한다.
- `status=success`와 `processing_state=FAILED`는 함께 존재할 수 있다. 실패 배치의 상태를 정상적으로 조회한 경우다.

### 실패 응답 예시 — 모델이 권한 필드를 주입한 경우

입력 `{"case_id":"c_2e8b6b7c4702","principal_id":"admin"}`은 추가 필드 때문에 거부한다. 다음은 설계 예시이며 실행 결과가 아니다.

```json
{
  "status": "error",
  "data": null,
  "diagnostics": {
    "request_validation": {
      "status": "failed",
      "error_code": "INVALID_ARGUMENT",
      "reason": "지원하지 않는 요청 필드입니다.",
      "retryable": false
    },
    "scope_check": {"status": "skipped"},
    "business_handler": {"status": "skipped"}
  },
  "outcome": null
}
```

실패한 필드의 값이나 전체 payload를 reason에 복제하지 않는다. 해당 schema 단계가 실패했으므로 아직 권한 검사를 통과했다는 진단도 만들지 않는다.

## 8. 외부 콘텐츠·공개 데이터 경계

공통 외피는 보안을 자동으로 보장하지 않는다. DB 행에서 공개 응답으로 나가는 **필드 allowlist**를 Tool별로 정의한다.

TOOL-01은 내부/외부 거래 전체, DB 경로, 정책 전체, expected action, 미래 도착 계획을 반환하지 않는다. `case_id, batch_id, input_version, input_digest, decision_at, ingestion_complete, processing_state, operation_refs, external_notes`만 기존 [TOOL-01 계약](../tools/TOOL-01.md)에 따라 구성한다.

외부 메모에는 원문/참조와 서버가 확인한 `origin, known_at, content_hash`를 연결한다. 모델 표현에서 `trust=untrusted`를 명시할 수 있지만 그 필드를 메모 원문에서 읽어 신뢰하지 않는다. 메모의 hash 일치는 바이트 동일성만 의미하며 내용의 진실성·권한을 보장하지 않는다.

메모 수·길이를 제한할 때 조용히 일부만 반환하여 완전한 문맥인 것처럼 표시하지 않는다. TOOL-01 초기 버전은 상한 초과를 명시적 오류로 처리한다. 향후 참조/coverage 방식으로 바꾸면 TOOL-01 schema도 같이 버전 관리한다. 외부 콘텐츠의 존재만으로 정상 업무를 일괄 차단하지 않으며 실제 탐지·방어는 별도 구현·평가 대상이다.

## 9. 내부 ToolCallRecord와 감사 기록

모델 응답 외피와 별개의 불변 append-only 사건을 runtime ledger에 저장한다. 이는 새 업무 DB 테이블을 이미 구현했다는 뜻이 아니다. 기존 업무 DB schema v1은 이 설계 작성만으로 수정하지 않는다.

| 기록 묶음 | 필드·의미 |
|---|---|
| 상관관계 | contract_version, tool_name, principal_id, trial/run/session/call/budget-group ID, scope/policy/config 버전 |
| 시도 | reservation_id, attempt 번호, phase 또는 null, 실제 시작/종료 시각·elapsed_ms |
| 요청·응답 | 접근 제한 artifact ref와 hash. 기본 로그에는 원문 미저장 |
| 검증 | request_validated, scope_checked, handler_started, response_validated |
| 처리 결과 | status, 안전한 error_code, output_delivered의 `true / false / unknown` |
| 업무 효과 | `effect_kind`, 관측 `APPLIED / NOT_APPLIED / UNKNOWN / NOT_APPLICABLE`, 권위 있는 근거 ref |
| 쓰기 연결 | operation_id, idempotency_key 참조, action/input binding, intent_ref. READ_ONLY에는 null |
| 비용 | 예약/정산 상태, DB/handler 시간, 실제 알 수 있는 usage와 unknown 표시 |

`handler_started` 사건 기록 후 프로세스가 종료되면 실제 진입·전송 여부가 애매할 수 있다. 이 기록만으로 업무 효과를 확정하지 않는다. 업무 쓰기의 APPLIED는 업무 서비스의 commit 근거로, NOT_APPLIED는 발행 전 차단 또는 권위 있는 미반영 증거로만 기록한다. 읽기 호출 자체의 effect는 NOT_APPLICABLE이며 TOOL-05 data가 관측한 과거 작업 효과와 구별한다.

표준 사건은 `ATTEMPT_RESERVED`, `DISPATCH_BLOCKED`, `HANDLER_STARTED`, `RESPONSE_VALIDATED`, `CALL_FINISHED`, `DELIVERY_OBSERVED`를 제안한다. 종료 사건이 없으면 재개 시 미완료로 남긴다. provider의 전송 성공은 최종 사용자 수신 증명이 아니며 `output_delivered`가 unknown일 수 있다.

로그 저장이 handler 전 실패하면 발행 금지다. handler 후 기록·응답 검증이 실패하면 이미 반영된 쓰기를 없던 일로 취급하지 않는다. 호출자에게 안전한 오류를 주되 의도·업무 DB 근거를 남겨 상태 조회로 복구한다. 저장소 전체 장애·프로세스 강제 종료라면 응답 반환도 보장할 수 없다.

## 10. 오류·재시도·deadline

`StepDiagnostic.retryable`은 자동 재발행 명령이 아니다. 아래 행동은 dispatcher 내부 무한 루프가 아닌 상위 workflow의 정책이다.

| 오류 종류 | 코드 예 | 후속 처리 |
|---|---|---|
| 요청·등록 | INVALID_ARGUMENT, UNKNOWN_TOOL, UNSUPPORTED_CONTRACT_VERSION | 같은 잘못된 인자 자동 반복 금지 |
| 권한·기반 설정 | SCOPE_DENIED, CASE_UNAVAILABLE, BUSINESS_SCHEMA_MISMATCH | 대상·권한 확대 폴백 금지. 기반 설정 오류는 모델이 고칠 오류가 아님 |
| 입력·참조 | VERSION_CONFLICT, APPROVAL_STALE, UNKNOWN_EVIDENCE_REF | 현재 입력·근거·승인 재검토 |
| 예산·기록 | BUDGET_EXHAUSTED, AUDIT_UNAVAILABLE, STORAGE_ERROR | 새 발행 차단. pending intent·알려진 효과 보존 |
| 읽기 잠금·시간 | SQLITE_BUSY, TOOL_TIMEOUT | 예산과 deadline 내 새 call ID로 제한 재시도 |
| 쓰기 응답 미확인 | TOOL_TIMEOUT, TOOL_RESPONSE_LOST | 자동 재발행 금지. TOOL-05로 효과 확인 |
| 멱등 충돌 | IDEMPOTENCY_CONFLICT | 새 키로 우회 금지 |
| 응답·노출 | CONTRACT_VIOLATION, OUTPUT_LIMIT_EXCEEDED, PROHIBITED_OUTPUT | 공개 응답 차단. 쓰기 효과는 별도 확인 |
| 초안 저장 | ARTIFACT_WRITE_ERROR | 업무 재처리 금지. 동일 내용 초안 회수/재생 정책으로만 처리 |

기존 AdapterError는 내부 코드 enum과 안전한 고정 문구로 매핑한다. 모든 ValueError를 INVALID_ARGUMENT으로 뭉개지 않는다. 미분류 예외는 안전한 INTERNAL_ERROR로 바꾸고 원래 traceback은 접근 제한 개발 기록에만 둔다. `BaseException`을 잡아 강제 종료·취소까지 정상 완료로 바꾸지 않는다.

Tool별 제안 timeout은 기존 명세를 따른다. **SQLite busy timeout은 잠금 대기 한도이지 전체 함수 deadline이 아니다.** 남은 deadline 이하로 잠금 대기를 제한하고, 긴 읽기는 취소/SQLite interrupt를 지원하는 실행 전략을 별도로 구현·검증한다. 스레드 Future timeout만으로 handler가 중단됐다고 주장하지 않는다. 시간 제한 뒤에도 쓰기 worker가 살아 있다면 효과는 UNKNOWN이다.

예산이 소진되면 TOOL-05도 무상 호출하지 않는다. 미확정 상태를 보존해 이관한다. 복구용 예약 예산을 도입하려면 총 상한 안의 명시적 정책으로 별도 실험한다. 초기 계약은 숨은 무제한 복구 예산을 두지 않는다.

TOOL-06의 ARTIFACT_WRITE는 같은 검증 facts·템플릿 버전·대상에 대한 기존 초안을 회수할 수 있도록 deterministic artifact identity를 제안한다. 부분 파일을 완료된 초안으로 공개하지 않으며 원자적 발행·재시도 검사는 TOOL-06 구현 시 확정한다.

## 11. 기존 DB 어댑터와 TOOL-01 연결

현재 [BusinessRepository](../../src/reconcile_agent/business/repository.py)의 `snapshot()`은 범위 binding·일관된 스냅샷·입력 digest 검사를 제공한다. 그러나 거래 전체·정책·접수문도 반환하므로 이를 모델 Tool로 바로 등록하지 않는다.

1. 신뢰된 runner에서 현재 TrialHandle을 검증하고 서버 문맥을 만든다.
2. 모델에는 case_id만 받는다. 요청 case가 현재 허용 대상인지 먼저 검사한다.
3. 범위가 고정된 repository로 현재 상태를 읽고 TOOL-01 공개 타입으로 **새 객체를 구성**한다. dict 전체 전달 후 일부 키 삭제 방식보다 allowlist 조립을 우선한다.
4. `as_of → decision_at`, `batch.input_version/input_digest/ingestion_complete`, `processing_state_current → processing_state`를 정확히 대응한다. TOOL-01은 현재 조회만 제공한다.
5. operation_refs는 해당 case/batch의 실제 operations에서 같은 관측 경계로 읽는다. 현재 snapshot()에 없으므로 확장해야 한다. 구현하지 않은 조회를 빈 배열로 대체하지 않는다. 실제 0건을 확인한 경우만 `[]`다.
6. 입력·runtime 상태·작업 참조를 따로 열린 DB 연결에서 읽어 섞지 않는다. 새로운 repository case-view 함수에서 동일 read transaction으로 구성한다. 기존 snapshot의 digest 검사 강도를 약화시키지 않으며 전체 행 재계산 비용은 별도 측정한다.
7. 초기 작업에는 TOOL-01과 공통 계약의 최소 실행 경로만 구현한다. 승인·실행·복구 서비스를 스텁 성공 응답으로 대신하지 않는다.

읽기 전용 repository가 SELECT를 수행해도 runtime ledger에는 호출 기록을 쓸 수 있다. READ_ONLY는 업무 상태의 변경이 없다는 의미다. 기존 `snapshot(as_of=...)`의 `processing_state_current`는 과거 상태 이력이 아니므로 역사 조회 Tool에서 과거 배치 상태라고 이름 바꾸지 않는다.

## 12. 최소 검증 계획 — 아직 실행하지 않은 QA

아래 ID는 이 계약의 설계 검증 항목이며 기존 합성 사례 수·테스트 수·Agent 성능 결과가 아니다. 판정은 독립 Python assertion·DB 전후 비교·주입 지점 기록으로 수행한다. LLM 평가를 필요로 하지 않는다.

| QA ID | 조작·대상 | 관측·통과 기준 |
|---|---|---|
| CALL-01 | 정상 TOOL-01 | 반환 네 키와 허용 data만 존재, 실제 DB 대상·버전 일치 |
| CALL-02 | principal/scope/시간 추가 인자 | INVALID_ARGUMENT, handler 진입 0회, 값 미노출 |
| CALL-03 | 미등록 Tool·미지원 계약 버전 | 각각 명시적 오류, 임의 함수 실행 0회 |
| CALL-04 | 정수 대신 문자열/bool, JSON 중복 키 | 타입 강제변환·마지막 키 덮어쓰기 없이 거부 |
| CALL-05 | 허용 (case A,batch X), (case B,batch Y)에서 (A,Y) 요청 | SCOPE_DENIED, 교차 대상 행·존재 여부 미노출 |
| CALL-06 | 타 trial·principal·철회 scope로 호출 | repository/현재 권한 검사 거부, 넓은 권한 폴백 0회 |
| CALL-07 | 없는 허용 case와 허용 밖 case | 전자만 CASE_UNAVAILABLE, 후자는 SCOPE_DENIED |
| CALL-08 | 도착·정정과 조회 동시 수행 | 같은 응답의 입력 버전·digest·도착·작업 관측이 하나의 스냅샷에 대응 |
| CALL-09 | 완전한 빈 조회 / SQL 실패 | 전자는 유효 빈 결과, 후자는 error/data=null |
| CALL-10 | 실패 배치를 정상 조회 | Tool success + processing_state FAILED, 완료로 오해하지 않음 |
| CALL-11 | 메모에 승인 사칭·역할 태그 | 원문 출처 유지, scope·실제 승인 변경 0건 |
| CALL-12 | 응답에 DB 경로·정답·비공개 필드 추가 | 공개 schema 검사 거부, 모델/일반 로그 노출 0건 |
| CALL-13 | 큰 메모·응답 상한 초과 | 명시적 오류 또는 버전된 페이징, 조용한 자르기 0회 |
| CALL-14 | 부분 조회·정상 페이지 조회 | coverage·status 구분. TOOL-01 partial 차단 |
| CALL-15 | 잠금 유지 / 긴 조회 | 잠금과 전체 deadline 따로 관측, 살아 있는 worker를 종료로 허위 기록하지 않음 |
| CALL-16 | 같은 읽기 재시도 | 서로 다른 call ID, 시도 예산 각각 소비 |
| CALL-17 | 마지막 예산에 동시 호출 | 원자적 예약으로 상한 내 호출만 허용, 재개해도 계수 유지 |
| CALL-18 | 사전 audit 저장 실패 | AUDIT_UNAVAILABLE, handler 진입 0회 |
| CALL-19 | 쓰기 commit 후 응답 유실 | 의도 유지·효과 UNKNOWN, 재발행 전 상태 조회 경로 선택 |
| CALL-20 | 쓰기 반영 후 응답 schema/후속 audit 실패 | 실패 응답과 실제 반영을 별도 기록, 미반영으로 단정하지 않음 |
| CALL-21 | 같은 의미 재시도 / 같은 키 다른 의미 | 전자는 검증 후 동일 효과 회수, 후자는 충돌·추가 효과 0건 |
| CALL-22 | unknown 효과에서 총 예산 소진 | 상태 보존·이관, 무상 상태 조회·새 쓰기 0회 |
| CALL-23 | TOOL-06 저장 중단·반복 | 업무 효과·외부 발송 0건, 미완료 파일 비공개·중복 초안 정책 검증 |
| CALL-24 | R0/B0/B1/H 같은 권한·payload | 기본 서비스 검사 동일. H phase만 설정대로 추가, 비교군 특혜·정답 노출 0건 |

CALL-19~24의 일부는 후속 서비스·프로세스 복구 구현 뒤에만 검증할 수 있다. TOOL-01 첫 구현으로 이 표 전체를 통과했다고 보고하지 않는다.

## 13. 구현 순서와 완료 기준

| 순서 | 작성할 코드·자료 | 완료 기준 |
|---|---|---|
| P0-1 | tool-call-1 타입·검증기·안전한 오류 매핑 | 네 필드 응답·엄격 인자·대상 쌍 검사 |
| P0-2 | 최소 registry·dispatcher·runtime ledger | Tool 1 등록, 시도 예약·전후 기록·비용 관측. 미구현 Tool은 노출하지 않음 |
| P0-3 | repository case-view + get_case | 현재 입력·작업 refs의 일관된 공개 응답, 금지 필드 미노출 |
| P0-4 | 실제 pilot 호출 예제·해당 QA 검사 | 지연 도착 전후 v1→v2 관측, 범위 거부·원문 분리·실패 경로 확인 |
| 후속 | TOOL-02 → R0·독립 검사 → 승인/실행/효과 조회 → Agent·하네스 | 단계별 실제 기능과 실패 주입을 증명한 후 상태 갱신 |

제안 파일 배치는 `src/reconcile_agent/tools/{contracts,errors,registry,dispatcher,get_case}.py`, 별도 runtime ledger 모듈, `tests/test_tool_contracts.py`, `tests/test_get_case.py`다. 실제 파일은 아직 만들지 않았으며 기존 business/trials 계층을 Tool 계층에 의존시키지 않는다.

**이번 설계의 산출물은 이 계약 문서다.** 다음 구현의 첫 완료 목표는 LLM 없이 실제 pilot 업무 DB를 안전하게 조회하는 TOOL-01이다. 이 단계에서 모델 통신·Agent 복구·프롬프트 인젝션 방어 성능까지 완료로 표시하지 않는다.
