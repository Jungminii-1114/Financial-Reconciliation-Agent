# 업무 데이터·DB 계약

> 업무 서비스 계약 v0.1 / 저장소 schema 1 구현. [적재 어댑터](../data-adapter/README.md) · [기존 예제](../../examples/synthetic_data_starter/README.md) · [상태 타입](state-and-types.md) · [복구](persistence-and-recovery.md)

**2026-11-05 구현 연결:** [자체 합성기 v1 스키마](../data-generation/schemas.md)는 내부/외부 모두 known_at·row_revision을 제공한다. 합성기 SQLite 6개 표는 **열람용 입력 스냅샷**이다. 별도 [업무 DB 적재 어댑터](../data-adapter/README.md)가 아래 10개 표와 보조 표를 준비하고, 초기 적재·역검사·명시적 재개·시점별 읽기·입력 사건을 구현했다. 승인·재처리·완료 서비스는 아직 없다. 이전 교육용 형식의 `load_teaching_fixture`는 미구현이며 v1은 버전을 검사해 별도로 처리한다. 열람용 digest는 업무 승인 digest로 재사용하지 않는다.

## 1. 기존 파일을 실행 환경으로 옮기는 어댑터

현재 `prepare_trial(dataset_root, case_id, trial_id, output_dir, config)`가 v1을 지원한다. `agent_view`만 업무 DB의 초기 입력으로 적재하고, controller/evaluator 파일은 각각 별도 저장소로 보낸다. Agent의 파일 루트를 전체 fixture 폴더로 설정하지 않는다. 아래 표는 이전 교육용 형식의 후속 변환 설계이며 현재 구현과 혼동하지 않는다.

| 현재 필드 | 실행 환경의 의미·변환 |
|---|---|
| internal.csv: transaction_id, batch_id, amount_krw, currency, status, event_time | 문자열 ID·정수 KRW 보존. POSTED는 내부 유효 상태 |
| external.csv: 위 필드 + external_id, known_at | 외부 수신 시각 보존. SETTLED는 내부 POSTED와 호환되는 외부 상태 |
| batch.json: decision_at, ingestion_complete, run_status, input_version | 초기 가상 시각·도착 완결성·배치 실행 상태·입력 버전 |
| internal에 known_at 없음 | 초기 스냅샷의 관측 시각을 적재 명세에 명시하여 known_at으로 사용. 원본에 수신 시각이 있었다고 주장하지 않음 |
| request.txt | 원본 한국어 접수문. 권한 정보와 분리한 untrusted 콘텐츠 |
| fault_plan / scheduled_events | 컨트롤러 저장소 전용. 모델 입력 금지 |
| expected.json / teaching_index | 평가 정답 전용. 초기 문맥·검색 대상 금지 |

`POSTED == SETTLED`라는 문자열 비교를 하지 않는다. 모의 규칙의 호환 상태 쌍인지 검사한다. 같은 BATCH001이 case_004와 case_005에 있으므로 모든 키에 case_id를 결합한다.

## 2. 업무 DB 테이블

표의 이름·제약과 [schema 1 DDL](../../src/reconcile_agent/business/schema.sql)이 저장소 정본이다. 새 실험마다 별도 업무 SQLite 파일을 만들고, 복구 세션에서는 같은 파일을 계속 사용한다. DDL이 존재한다고 승인·실행의 서비스 프로토콜까지 구현된 것은 아니다.

| 테이블 | 주요 필드 | PK·유일성·참조 |
|---|---|---|
| cases | case_id, target_batch_id, business_rule_version, policy_version, request_ref | PK case_id. 인증 scope는 별도 서버 컨텍스트에서 확인 |
| batch_versions | case_id, batch_id, input_version, input_digest, ingestion_complete, known_at | PK(case_id,batch_id,input_version), 버전 단조 증가 |
| transaction_history | case_id, batch_id, transaction_id, row_revision, amount_krw, currency, status, event_time, known_at, superseded_known_at | PK(case_id,transaction_id,row_revision). 현재 버전의 거래 ID 유일 |
| settlement_history | case_id, batch_id, external_id, transaction_id, row_revision, amount_krw, currency, status, event_time, known_at, superseded_known_at | PK(case_id,external_id,row_revision). 중복 transaction_id는 삭제하지 말고 검증 대상으로 유지 |
| input_mutations | mutation_id, case_id, batch_id, old_version, new_version, table_name, affected_ids, changed_fields, known_at | 변경 영향 추적용. transaction 내부에서 입력과 함께 기록 |
| external_notes | note_id, case_id, record_ref, content_ref, content_hash, context_revision, origin, known_at | PK(case_id,note_id,context_revision). 원문과 출처 보존. 메모의 trust를 자체 선언으로 승격하지 않음 |
| approvals | approval_id, case_id, batch_id, input_version, input_digest, action_digest, policy_version, granted_by, expires_at, status, consumed_by | PK approval_id. 한 승인은 한 논리 작업만 소비 |
| operations | operation_id, idempotency_key, case_id, batch_id, input_version, action_kind, action_digest, approval_id, state, result_ref, committed_at | PK operation_id, UNIQUE idempotency_key, UNIQUE(case_id,batch_id,input_version,action_kind) |
| reconciliation_results | result_id, operation_id, transaction_id, internal_ref, external_ref, amount_krw, match_status | PK(result_id,transaction_id), operation 참조 |
| completion_decisions | decision_id, logical_run_id, case_id, batch_id, outcome, decision_at, input_version, operation_id, verification_digest, report_facts | PK decision_id, UNIQUE logical_run_id. 조건부 검사 후 기록, 입력 변경과 같은 직렬화 경계 사용 |

batch의 작업 진행 상태는 입력 버전과 분리한다. 대사 결과가 생성됐다는 이유만으로 input_version을 증가시켜 자기 작업의 승인을 낡게 만들지 않는다.

schema 1에서는 `case_policies`, `batch_runtime`, `import_receipts`, `content_objects`, `trial_metadata`, `applied_event_groups` 보조 표를 채택해 총 16개 표를 사용한다. 원문을 같은 DB에 보관하고 권위 있는 가상 시각을 입력 사건과 함께 commit한다. 정확한 책임·채택 이유는 [적재 설계](../data-adapter/design.md)를 따른다. controller/evaluator는 별도 metadata/documents DB이며 runtime 연결에 ATTACH하지 않는다.

## 3. Agent 상태 저장소

| 테이블·아티팩트 | 역할 | 중단 후 사용 |
|---|---|---|
| run_checkpoints | 노드 결과·진행 단계·계약 버전 | 동일 logical_run_id의 마지막 상태 |
| operation_intents | operation_id·업무 키·승인·입력·dispatch 상태 | 쓰기 전 반드시 durable 저장. 체크포인트보다 복구 우선 |
| evidence_registry | 구조화 관측·content_ref·범위·시점·해시 | 모델이 발급하지 않은 유효 refs 복원 |
| dependency_records | query footprint → evidence → proposal/approval | 변경 영향 계산 |
| budget_ledger | 호출 예약·확정·미확정 usage | 재개 후 누적 제한 |
| run_events | seq·node/tool·입출력 참조·에러·효과·시각 | JSONL은 이 기록의 내보내기이며 원본 저장을 대체하지 않음 |

LangGraph 체크포인트가 업무 DB와 원자적으로 저장된다고 가정하지 않는다. framework 저장이 뒤처져도 operation_intents를 먼저 복원하여 실제 작업을 조회한다.

## 4. 금액·시간·버전

- amount_krw는 양의 정수, currency는 KRW. float·암묵적 환율·소수점 반올림 금지.
- 시간은 offset이 포함된 ISO 8601 문자열로 직렬화하고, 내부 비교는 timezone-aware datetime으로 한다. 모든 예시의 KST를 UTC라고 가정하지 않는다.
- schema 1의 DB 시각과 조회 바인딩은 UTC·고정 microsecond 자리로 정규화한다. 원본 바이트는 operator 자료로 별도 보존한다.
- 소급 정정은 이전 행을 지우지 않고 새 revision과 known_at을 만든다. 과거 as-of에서는 당시 revision이 조회돼야 한다.
- 알려진 입력 행·재무 필드·도착 완결성의 변화가 input_version과 input_digest를 바꾼다. 메모만 바뀌면 context_revision/content_hash를 갱신한다. 메모는 쓰기 권한의 근거가 될 수 없다.
- 기존 case_002의 같은 시각 insert+complete_ingestion은 하나의 도착 묶음으로 적용하고 명세의 new_input_version=2를 한 번 부여한다. 이벤트마다 임의로 두 번 증가시키지 않는다.

## 5. 입력 digest의 정의

`canonical_json`은 키·행 정렬, UTF-8, 정수 금액, 통일된 시각 형식을 사용한다. digest에는 대상 case/batch, 금융 입력의 현재 record/revision/값, ingestion_complete, 업무 규칙 버전을 포함한다. operation 상태·LLM 문장·로그 시각은 제외한다.

action_kind는 이 버전에서 REPROCESS 하나다. idempotency_key와 별도로 같은 case/batch/input_version/action_kind의 실제 효과를 유일하게 제한한다. 키나 정책 digest를 바꿔 같은 입력을 두 번 반영하는 우회를 허용하지 않는다.

action_digest에는 `REPROCESS`, 대상, input_version/input_digest, business_rule_version, policy_version을 포함한다. 임의의 사용자 문장을 그대로 hash하여 동일 업무가 다른 키로 중복 수행되게 하지 않는다. 해시는 버전 식별 수단이며 인증 서명을 대신하지 않는다.

## 6. 검사할 불변식

1. 한 논리 작업이 여러 번 호출돼도 같은 입력 버전에 업무 효과는 1회다.
2. 반영·작업 성공·결과·승인 소비는 같은 업무 트랜잭션에서 함께 commit/rollback된다.
3. 입력 원천 금액은 재처리 과정에서 변경되지 않는다.
4. 결과는 해당 operation과 입력의 거래별 ID·금액·호환 상태를 만족한다.
5. 다른 case/batch의 결과 참조를 완료 근거로 사용할 수 없다.
6. 과거 결과 조회는 현재의 읽기 권한을 검사하되, 이미 적용된 쓰기의 승인을 다시 소비하지 않는다.
7. 같은 키의 다른 action_digest·대상은 IDEMPOTENCY_CONFLICT다.

검사기는 원천과 결과를 직접 읽어 비교한다. Agent의 match_count나 성공 문장을 정답으로 복사하지 않는다.
