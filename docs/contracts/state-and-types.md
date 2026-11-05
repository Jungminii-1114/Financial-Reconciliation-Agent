# 상태·식별자·데이터 타입

> v0.1 설계. [NodeResult](../small-nodes/common-result-contract.md) · [업무 DB](business-data.md)

## 1. 식별자의 소유권

| 필드 | 생성·유지 주체 | 규칙 |
|---|---|---|
| `case_id`, `batch_id` | 사례 등록·업무 DB | 같은 BATCH001이 여러 사례에 있으므로 항상 case 범위와 결합 |
| `logical_run_id` | 하네스 | 중단 전후 같은 업무 실행을 연결. 평가 반복마다 새 값 |
| `parent_run_id`, `budget_group_id` | 하네스/컨트롤러 | 종료 후 후속 검토와 동일 trial의 총 예산 연결. 모델이 생성·초기화하지 않음 |
| `session_id` | 하네스 | 프로세스·세션 재개 때 새 값 |
| `tool_call_id` | 하네스 | 매 시도마다 새 값. 업무 중복 방지 키와 구별 |
| `operation_id` | N-10 및 업무 서비스 | 쓰기 전에 저장. 같은 논리 작업 재시도에서는 유지 |
| `idempotency_key` | 코드 | case·batch·input_version·action_digest로 정규화. 실험 반복은 DB 격리 |
| `evidence_id` | 증거 저장소 | 쿼리·시점·대상·해시에 연결. 모델이 임의 발급한 ID는 거부 |
| `approval_id` | 외부 승인 서비스 | 유효한 레코드 조회로만 인정 |
| `decision_id` | 내부 완료 서비스 | 조건부 종료 검사를 통과한 as-of 결정 기록 |

인증된 principal·허용 범위·서버 시각은 Tool 인자에서 모델이 지정하지 않는다. [보안 경계](security-boundary.md)의 신뢰된 호출 외피로 전달한다.

## 2. 서로 다른 상태 축

| 축 | 값 | 의미 |
|---|---|---|
| NodeResult.status | `success / partial / error` | 함수 실행 상태 |
| run_lifecycle | `RUNNING / PAUSED_FOR_APPROVAL / INTERRUPTED / TERMINAL` | 실행 생명주기 |
| business_outcome | `REPROCESSED / WAITING_FOR_INPUT / ESCALATED / CANCELLED` 또는 null | 업무 결과 |
| terminal_reason | `COMPLETED / BUDGET_EXHAUSTED / EXECUTION_ERROR / ACCESS_DENIED` 또는 null | 실행 종료 사유 |
| operation_state | `NOT_FOUND / RUNNING / SUCCEEDED / FAILED_RETRYABLE / FAILED_FINAL / REJECTED / UNKNOWN` | 서비스가 보고한 작업 상태 |
| observed_effect | `APPLIED / NOT_APPLIED / UNKNOWN` | 관측 가능한 업무 효과 |
| input_relation | `UNCHANGED / CHANGED / UNKNOWN` | 의도가 참조한 입력과 현재 입력의 관계 |

`UNKNOWN`은 실제 DB의 제3 상태라는 뜻이 아니라 지식 부족이다. 가능한 상태는 `effect={APPLIED,NOT_APPLIED} × input={UNCHANGED,CHANGED}`의 최대 4개로 유지한다. 작업 RUNNING/조회 실패는 효과를 확정하지 못하게 한다. 정답 세계 상태는 evaluator만 보관한다.

기존 교육용 `run_status=FAILED`는 실패한 **업무 배치** 상태다. 새 실행의 `run_lifecycle`과 다르다. 기존 `RESOLVED_AFTER_APPROVED_REPROCESS`는 evaluator의 기대 설명이며 관측 입력에 넣지 않는다.

## 3. ReconciliationState 최소 필드

| 묶음 | 필드·타입 | 관리 주체 |
|---|---|---|
| identity | `case_id, batch_id, logical_run_id, session_id, config_hash, parent_run_id, budget_group_id` | B-01 |
| request | `request_ref, request_content_hash, scope_ref, policy_version` | N-01 |
| observations | `decision_at, input_version, input_digest, context_revision` | N-03/05/07 |
| evidence | `evidence_refs[], dependency_refs[], invalidated_refs[]` | N-05/06/07 |
| proposal | `action, reason_codes[], evidence_refs[]` | N-08 및 코드 검사 |
| approval | `approval_request_id, approval_id, approval_status` | N-09/10 |
| pending_operation | `operation_id, idempotency_key, action_digest, input_version, dispatch_state` 또는 null | N-10/11/12 |
| uncertainty | `possible_states[], observation_refs[], unresolved_reason` | N-12 |
| verification | `check_refs[], decision_id, verified_input_version` | N-13/14 |
| budget | `tool_attempts, retry_counts, model_usage, active_elapsed_ms` | 하네스 |
| result | 위 생명주기·업무 결과·종료 사유, `final_report_ref` | N-14/15 |

필드는 논리 계약이다. 전체 원천 행·외부 문장·평가 정답을 State에 복사하지 않고 권한이 제한된 참조를 저장한다. 상태 merge는 하네스가 수행하며 노드가 다른 노드의 필드를 임의 덮어쓰지 않는다.

## 4. 주요 업무 타입

| 타입 | 핵심 내용 | 생성 노드 |
|---|---|---|
| CaseContext | 요청·대상·정책·현재 입력·관측 가능한 이력 참조 | N-03 |
| InvestigationPlan | 허용 query profile과 이유, 재개 시 필요한 조회 | N-04 |
| EvidenceBundle | 권위 있는 구조화 결과·untrusted 텍스트 분리, 범위·시점·coverage | N-05/07 |
| RevalidationReport | 유효/무효/재조회 필요 refs와 근거 | N-06 |
| ActionProposal | WAIT/ESCALATE/REPROCESS/OBSERVE, reason_codes·evidence_refs | N-08 |
| ApprovalRequest | action_digest·input_version·input_digest·policy_version·만료 | N-09 |
| OperationIntent | 쓰기 식별자·승인·대상·dispatch 상태 | N-10 |
| RecoveryObservation | 작업 효과·현재 입력·권위 있는 재시도 허용 여부 | N-12 |
| VerificationReport | 검사항목·기대/실제·근거·pass/fail/unknown | N-13 |
| FinalDecision | outcome·decision_at·input_version·decision_id·불확실성 | N-14 |

## 5. 예산 기본값 제안

Tool 시도 총 20회, 조사 3라운드, 동일 작업의 추가 재시도 2회, 누적 출력 8,000토큰, 활성 실행 180초를 개발 기본값으로 제안한다. 상태 조회·실패·재개 비용을 포함한다. 최종 값은 파일럿 이후 고정한다.

호출 전 사용량을 예약하고 호출 뒤 확정한다. 중단으로 usage를 잃으면 `usage_status=unknown`과 추정 상·하한을 남기며 0으로 기록하지 않는다. 가상 업무 시각과 실제 활성 시간은 구별한다. 사람 승인 대기는 별도 측정하고 활성 추론 시간에서 제외하되 재개 시 예산을 초기화하지 않는다.

이미 종료한 WAIT 결정 뒤 새 자료가 오면 parent_run_id로 연결된 후속 logical_run을 만든다. 같은 trial의 budget_group_id를 공유하여 후속 검토를 새 예산으로 우회하지 않는다. 같은 run의 중단·재개에서는 logical_run_id와 예산 모두 유지한다. 실운영에서 별도 예산을 부여하는 정책은 이 실험의 자동 동작으로 가정하지 않는다.
