# 하네스 내부 서비스 — 모델 Tool과 분리

> 설계 초안 / 미구현. 이 함수들은 6개 업무 Tool을 늘리는 것이 아니다. 하네스·승인 주체·평가 컨트롤러의 코드 전용 기능이다. [구조](../overall-plan/architecture.md) · [Tool 목록](README.md)

## 1. Dispatcher·권한 외피

`dispatch_tool(tool_name, payload, trusted_context, phase) -> NodeResult[T]`

- tool registry·payload schema·scope binding·단계·예산을 검사하고 6개 업무 구현으로 전달한다.
- 모델이 전달한 principal/scope/clock/trust 값은 거부한다. 현재 권한을 서버 컨텍스트에서 가져온다.
- 시도와 실제 handler 도달·서비스 거부·DB 효과를 다른 이벤트로 기록한다.
- H의 단계 게이트는 추가 하네스 기능이며 기본 업무 권한·승인·멱등성은 모든 설정에 공통이다.

## 2. 지속 상태·의도 journal

| 함수 | 입력 → 출력 | 필수 조건 |
|---|---|---|
| load_run | logical_run_id·현재 scope → checkpoint·intent·budget | 현재 권한 재확인, 잘못된 case 복원 금지 |
| persist_intent | 승인된 OperationIntent → durable seq/ref | Tool 발행 전에 commit. 실패하면 호출 금지 |
| save_node_result | node_id·NodeResult·state delta → checkpoint_ref | 소유 필드만 merge, 계약 버전 기록 |
| start_followup_run | parent_decision_ref·현재 입력 → 새 logical_run_id | 종료 후 새 자료 검토만 새 실행. 평가 trial의 예산 그룹은 공유 |
| append_event | seq·입출력 ref·코드·시간 → event_id | 안전상 필수 기록은 durable 저장, 비밀·추론 전문 제외 |
| export_trace | logical_run_id → JSONL·manifest | DB event의 내보내기. export 실패를 업무 rollback으로 오해하지 않음 |

LangGraph saver와 업무 DB를 하나의 transaction으로 묶었다고 가정하지 않는다. intent journal이 framework checkpoint보다 앞섰을 때 N-02가 journal을 복원한다.

## 3. 예산 서비스

`reserve_attempt(run_id, kind, target_ref) -> reservation_id`와 `settle_attempt(reservation_id, observed_usage, usage_status)`.

Tool·모델 호출 전에 시도를 계수한다. 실패·재개·재시도도 유지한다. 모델 응답을 잃으면 token usage를 unknown/추정 범위로 기록하며 0으로 확정하지 않는다. 같은 operation의 추가 재시도 수와 상태 조회 수는 별도 필드지만 모두 총 Tool 예산에 포함한다.

승인 대기는 wall time으로 보존하되 활성 추론·실행 예산과 분리한다. 가상 시계가 전진했다고 활성 시간이 늘었다고 계산하지 않는다. 예산이 소진돼도 pending operation과 이미 반영된 결과는 지우지 않는다.

## 4. 외부 승인 서비스

| 함수 | 호출 주체 | 계약 |
|---|---|---|
| create_approval_request | N-09 코드 | 대상·조치·input version/digest·action digest·정책·만료를 불변 저장 |
| apply_approval_event | 사람 UI 또는 평가 승인 시뮬레이터 | 승인자 권한·정책·scope 확인 뒤 APPROVED/DENIED 기록 |
| read_approval | N-09/10·업무 서비스 | 실제 승인 DB에서 현재 binding 조회 |

Agent가 자기 승인 이벤트를 발급할 수 없다. 승인 시뮬레이터는 모델 답변의 유창함이나 별도 LLM 점수로 승인을 결정하지 않는다. 각 시나리오의 사전 정의된 정책·시각·입력 조건을 사용하며 모든 비교군에 같다.

같은 case/batch/input_version/action_digest/policy의 승인 요청은 유효한 기존 요청·승인을 재사용하는 멱등 생성으로 처리한다. 이미 적용된 효과의 회수를 위해 승인을 새로 만들지 않는다. 과거 승인이 만료되어 새 승인이 필요하면 이전 효과를 먼저 확인한다.

## 5. 증거·변경 registry

`register_evidence(validated_response, trusted_source) -> evidence_id` / `read_evidence(refs, scope) -> EvidenceBundle` / `read_mutations(case,batch,from_version,to_version) -> MutationRange`.

Tool 응답 출처·범위·시점·coverage를 검증한 뒤 불변 ref를 발급한다. 원문 hash와 predicate hash를 구분한다. query footprint·결과 집합·제안·승인 의존성을 저장한다. mutation 누락·불명은 KEEP이 아니라 UNKNOWN/재조회다.

공개 Tool로 조회 가능한 같은 업무 범위만 runtime에서 이용한다. evaluator 전용 expected/fault/event 계획은 연결하지 않는다.

## 6. Runtime 검증·조건부 완료

| 함수 | 출력·효과 |
|---|---|
| read_verification_view | dispatcher를 통해 TOOL-02/05의 범위 제한 facts를 확보. 실제 조회 호출은 Tool 예산·비용에 포함 |
| register_verification_report | 대상·입력·check refs를 묶는 verification_digest. 모델이 임의 발급 불가 |
| finalize_if_current | 현재 version·대상·결과·필수 검사를 transaction 안에서 확인하고 decision_id 기록 |
| get_final_decision | 현재 읽기 권한으로 과거 as-of 결정 회수. 새 쓰기·승인 소비 없음 |

읽기 검증과 완료 기록 사이 입력 변경은 VERSION_CONFLICT다. 완료 이후 새 도착은 후속 검토 사건이다. 동일 logical_run의 같은 완료 요청은 기존 decision을 반환하고 의미가 달라지면 충돌을 반환한다.

B0/B1이 H 전용 finalization marker를 만들지 않았다는 이유만으로 실패시키지 않는다. 평가기는 같은 실제 업무 결과·금지 효과·보고 주장을 검사한다. runtime verifier의 함수 결과를 evaluator 정답으로 재사용하지 않는다.

finalize_if_current 같은 내부 업무 호출도 별도 event·시간·DB 접근 비용으로 기록하며 상한 내에서만 실행한다. 사용자·모델 Tool 호출 수와 내부 호출 수를 구분하여 보고하고, H의 비용을 누락하지 않는다.

## 7. 모델 어댑터

`infer_structured(prompt_ref, schema, model_profile, budget) -> ParsedModelResult`.

모델·양자화·프롬프트·파서·디코딩을 version/hash로 기록한다. N-04/N-08은 해석·제안용이며 계산·승인·권한을 모델 confidence로 확정하지 않는다. 파싱 보정은 횟수와 비용을 기록한다. 저장된 응답 재생과 실제 모델 호출을 실행 인덱스에서 구분한다.

## 8. 가상 시계·사건·프로세스

`advance_clock(at)` / `apply_event_group(events)` / `inject_at_boundary(hook)` / `restart_agent_same_trial()`는 B-06 코드 전용이다.

- Agent는 clock의 현재 값을 조회할 수 있지만 미래 사건 목록·정답을 볼 수 없다.
- 같은 시각의 도착 묶음을 transaction으로 적용하고 명세 버전을 한 번 갱신한다.
- 업무 서비스와 Agent worker를 분리한다. before_commit은 rollback을 확인하고, after_commit은 durable 결과를 확인한 뒤 Agent 응답 저장 전에 중단한다.
- 실제 프로세스 재시작에는 process/session 식별과 훅 영수증을 남긴다. 일반 예외나 계획 파일 생성과 구분한다.
- 임의 분산 worker의 생존 여부를 확인할 수 없으면 UNKNOWN을 유지한다.

## 9. 출력·보조 보안 검사

`validate_output_fields(report, output_policy)`는 runtime의 필드 allowlist·민감 필드·표현 형식을 검사한다. evaluator의 비밀 canary 값·공격 정답 목록을 받아 판정하지 않는다.

`scan_canary_exposure(artifacts, evaluator_spec)`는 N-19 전용이다. 테스트 canary의 정확한 값과 금지 sink를 evaluator가 보관한다. 출력 마스킹이 성공해도 금지된 model_input 노출이 이미 발생했다면 별도로 실패를 기록한다.

## 10. 구현 연결 검사

의도 저장 실패 후 발행 0회, 승인 발급 주체 분리, scope 변경 후 복원 거부, 상태 저장과 업무 commit 불일치의 복구, 예산의 재개 유지, finalization 경쟁, evaluator 파일 미노출을 각각 확인한다. 함수명·필드를 변경하면 해당 N-xx·Tool·QA 문서도 갱신한다.
