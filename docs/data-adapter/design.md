# 적재·재개·시간 사건 설계

[사용 설명서](README.md) · [업무 DB 계약](../contracts/business-data.md) · [검증](validation.md)

## 1. 구현 경계

입력은 `reconcile-fixture-1` / `synthetic-1.0.0`으로 고정한다. 이전 교육용 `teaching-0.1`은 조용히 추측·변환하지 않고 미지원 오류로 반환한다. 어댑터는 해당 버전의 파일·정답 일관성을 검증하는 **operator** 코드이며, 업무 mapper가 정답 행동이나 recipe를 읽어 데이터를 고치지 않는다.

새 trial은 한 사례와 별도 업무 DB를 가진다. baseline·모델·반복을 바꾸면 새 trial을 만든다. 같은 trial의 재개는 기존 업무 DB를 유지한다. 서로 다른 trial 사이의 거래 ID가 같아도 독립 DB와 case 복합 키로 구분한다.

## 2. 초기 적재 프로토콜

1. 입력/실행 ID와 runner 설정을 검증한다. 기존 목적지·데이터셋 내부 출력은 거부한다.
2. 데이터셋을 비공개 임시 스냅샷으로 복사한다. 링크·특수 파일·미허용 루트 항목을 거부한다. 단일 파일은 64 MiB로 제한한다.
3. 스냅샷의 버전·manifest·파일 hash·시점·사건·정답 일관성을 기존 독립 검사기로 확인한다.
4. 정확한 catalog ID로 한 사례를 고른다. 선택 사례의 바이트를 다시 hash 확인하고 **그 바이트를 파싱**한다. 원본이 이후 변경돼도 이미 검증한 스냅샷을 사용한다.
5. 새 staging에 schema를 만들고 FK를 켠다. DDL은 적재 트랜잭션 이전에만 실행한다.
6. `BEGIN IMMEDIATE`로 초기 입력·정책·원문·출처를 저장한다. 독립 역조회와 비교한 다음 영수증을 같은 트랜잭션에 저장한다.
7. 계획과 정답을 각각의 별도 SQLite에 저장하고, 선택 사례의 원본 바이트와 설정·검사 보고서를 operator 경로에 보관한다.
8. 세 DB의 무결성·identity·source binding·schema·역조회 결과를 검사하고 닫는다.
9. 새 목적지를 배타적으로 예약하고 staging 내용을 옮긴다. 불변 파일과 디렉터리를 동기화한 뒤 **READY를 마지막에 발행**한다.

한 DB 안의 적재는 원자적이다. 여러 DB와 파일 묶음은 하나의 분산 트랜잭션이 아니며, READY·manifest 검사로 실행 가능 상태를 구분한다. 발행 중 프로세스가 종료되면 불완전한 목적지는 재개·재초기화를 거부한다. 이미 실행 중인 폴더를 자동 삭제하거나 덮어쓰지 않는다.

## 3. 업무 schema 1

DDL 정본: [schema.sql](../../src/reconcile_agent/business/schema.sql). `application_id=FRA1`, `user_version=1`. 저장된 schema digest와 버전을 재개 때 확인한다.

| 구분 | 테이블 | 역할 |
|---|---|---|
| 기존 계약 | cases | 사건·대상·규칙/정책 버전·요청 ref |
| 기존 계약 | batch_versions | 입력 버전·도착 완료·금융 digest·알려진 시각 |
| 기존 계약 | transaction_history / settlement_history | 내부/외부 revision과 유효 구간 |
| 기존 계약 | input_mutations | 전후 버전·변경 표·영향 ID·필드·시각 |
| 기존 계약 | external_notes | 원문 ref/hash·origin·known_at·context revision |
| 기존 계약 | approvals / operations | 후속 승인·실행 서비스용. 초기 0건 |
| 기존 계약 | reconciliation_results / completion_decisions | 후속 결과·완료 서비스용. 초기 0건 |
| 채택한 보완 | case_policies | approval_required·TTL·cutoff의 손실 없는 보관 |
| 채택한 보완 | batch_runtime | 입력 버전과 별개인 FAILED/RUNNING/SUCCEEDED |
| 채택한 보완 | import_receipts | 출처·적재기/DB 버전·초기 digest·건수 |
| 채택한 보완 | content_objects | 원문·hash·출처를 같은 업무 DB 트랜잭션으로 저장 |
| 채택한 보완 | trial_metadata | 실행 identity·설정 hash·초기/현재 가상 시각 |
| 채택한 보완 | applied_event_groups | 이미 반영한 사건 ID·payload hash·전후 버전·시각 |

총 16개 표다. `external_notes`의 키는 `(case_id,note_id,context_revision)`으로 확정하고 `known_at`을 보존한다. 승인·작업 등의 **테이블/키 제약**이 있는 것과 승인·멱등 실행 프로토콜이 완성된 것은 다르다.

외부 transaction_id에는 내부 FK나 UNIQUE를 강제하지 않는다. 미대응 ID와 중복은 지우지 않고 검증 입력으로 남겨야 한다. 외부 행의 유일성은 external_id로 관리한다. 활성 revision만 유일하게 만드는 partial unique index를 사용한다.

## 4. 시간과 원문

- 시각은 UTC·고정 microsecond 자리 ISO 문자열로 정규화한다. SQL에 들어가는 조회 시각도 같은 형식이다.
- event_time은 발생, known_at은 그 값의 수신, current_time은 현재 가상 시각이다.
- 정정의 유효 구간은 `[known_at,superseded_known_at)`이다. 삭제는 구간 종료이며 물리 삭제가 아니다.
- 초기 시각 이전과 현재 가상 시각 이후의 조회를 거부한다.
- 원문은 UTF-8 바이트의 hash와 내용을 보존한다. 공격 문구를 제거하거나 승인·권한·정책으로 해석하지 않는다.
- v1의 시간 사건은 금융 입력에만 적용한다. 메모·요청·정책 변경은 지원하지 않으며 재개 검사에서 버전 없는 변경을 거부한다.
- `processing_state_current`는 현재 상태다. 과거 금융 입력과 함께 반환돼도 당시 실행 상태라고 해석하지 않는다.

## 5. digest 계약

`business-input-v1`은 case/batch·업무 규칙 버전·도착 완료와 정렬된 현재 금융 행/revision/시각을 canonical JSON으로 묶는다. 입력 버전은 별도 binding으로 사용한다. 금액은 정수이며 메모·평가 정답·공격 라벨·미래 사건·operation 상태는 포함하지 않는다.

`business-action-v1`은 REPROCESS·대상·input_version/input_digest·업무/정책 버전을 묶는다. 해시는 `sha256:<hex>` 형식이다. 이를 생성했다고 승인이나 인증 서명이 생기는 것은 아니다.

정상/공격의 **금융 projection**은 같지만 case_id가 달라 업무 digest는 달라질 수 있다. 기존 `snapshot.py` 열람용 digest는 업무 승인 식별값으로 재사용하지 않는다.

## 6. 입력 사건과 시계

`advance_trial(handle, to_at)`은 신뢰된 컨트롤러 코드용이며 모델 Tool로 등록하지 않는다. 미래 계획은 controller DB에만 있다. 목표 시각까지의 사건을 순서대로 적용한다.

각 묶음은 expected_version→new_version을 확인하고, 삽입/정정/구간 종료/도착 완료, 새 금융 digest, batch_versions, input_mutations, applied_event_groups를 같은 업무 트랜잭션으로 기록한다. 한 번의 advance에서 발생한 실패는 해당 호출의 모든 변경과 시계 전진을 rollback한다.

이미 반영한 같은 ID·같은 payload는 영수증 회수이며 재적용하지 않는다. 같은 ID·다른 내용은 충돌이다. 동시 전달은 `BEGIN IMMEDIATE`와 유일 키로 직렬화한다. 시계도 같은 업무 DB에 저장해 입력 반영과 시각이 따로 commit되는 문제를 피했다. 컨트롤러 DB는 이 버전에서는 불변 계획만 보관하며 별도 전달 상태 갱신이 필요하지 않다.

`input_mutations.affected_ids`는 JSON object다. 행 변경은 `transaction_ids`와 `external_ids` 배열, 도착 상태 변경은 `batch_ids` 배열을 저장한다. `changed_fields`는 JSON 문자열 배열이다. 같은 그룹의 여러 mutation은 동일한 old/new_version을 가지며, 묶음 단위로 version이 한 번 증가한다. 이후 N-06은 이 객체와 query footprint를 비교할 수 있다.

이 설계는 초기 가이드의 “컨트롤러 시계 저장소” 제안에서 구체화한 변경이다. 컨트롤러가 시계를 전진시킬 권한을 가지되, 권위 있는 현재 시각은 업무 입력과 원자적으로 보존한다. Agent는 다음 사건을 읽을 수 없다.

## 7. 재개 프로토콜

READY·불변 manifest·원본/설정/보고서 hash를 확인하고 요청한 trial/case/config와 대조한다. 업무 schema hash·버전·FK/무결성, 출처 binding·초기 영수증, 연속된 입력 버전·각 시점의 금융 digest와 사건 수, 정책·원문 출처를 검사한다. private store도 role·trial·source binding과 원문 hash를 확인한다.

이 검사는 현재 DB 파일의 전체 hash를 초기 파일과 비교하지 않는다. 승인 소비·작업 효과·현재 입력·가상 시각이 정상적으로 바뀐 것은 보존한다. 재개에서 앱 INSERT·초기화·migration을 수행하지 않는다. 업무 DB는 `mode=rw`로 기존 파일만 열어 SQLite 자체의 crash rollback을 허용하고, 일반 입력 조회는 `mode=ro`와 query_only로 연다.

## 8. 역할·권한의 실효 범위

`RunnerConfig`와 `BusinessScope`는 신뢰된 서버 코드가 구성하는 binding이다. 이 라이브러리는 문자열 principal을 실제 로그인 사용자로 인증하지 않는다. 미래의 dispatcher가 인증된 context에서 scope를 만들고 모델 JSON으로 덮어쓰지 못하게 해야 한다.

`BusinessRepository`는 업무 DB만 열고 고정 SQL과 매개변수를 사용한다. 정답/계획 DB의 ATTACH, 임의 SQL, 파일 탐색을 제공하지 않는다. 전체 TrialHandle은 runner 전용이며 agent_admission에는 ID만 들어간다.

파일 권한과 별도 DB는 우발적 노출을 줄이지만 동일 계정의 악성 Python·OS 침해를 막는 보안 샌드박스가 아니다. 실제 서비스의 scope·승인·출력 통제와 인젝션 탐지 평가는 후속 범위다.

## 9. 코드 위치

| 코드 | 책임 |
|---|---|
| [business/schema.sql](../../src/reconcile_agent/business/schema.sql) | schema 1의 DDL·키·기본 제약 |
| [business/connection.py](../../src/reconcile_agent/business/connection.py) | 기존 DB 연결·FK·트랜잭션·무결성 |
| [business/input_digest.py](../../src/reconcile_agent/business/input_digest.py) | UTC 정규화·금융/조치 digest |
| [business/repository.py](../../src/reconcile_agent/business/repository.py) | 범위가 제한된 일관된 as-of 읽기 |
| [trials/source.py](../../src/reconcile_agent/trials/source.py) | 검증된 불변 입력 스냅샷 |
| [trials/lifecycle.py](../../src/reconcile_agent/trials/lifecycle.py) | 새 trial·READY·영수증·resume |
| [trials/import_check.py](../../src/reconcile_agent/trials/import_check.py) | 삽입과 분리한 원본/DB 역대조 |
| [trials/events.py](../../src/reconcile_agent/trials/events.py) | 입력 사건·revision·시계·사건 재전달 |
| [trials/cli.py](../../src/reconcile_agent/trials/cli.py) | 사람용 준비·점검·재개·사건 명령 |

SQLite 근거: [Python sqlite3의 연결·트랜잭션](https://docs.python.org/3.11/library/sqlite3.html), [SQLite 외래 키 활성화](https://www.sqlite.org/foreignkeys.html), [BEGIN IMMEDIATE와 잠금](https://www.sqlite.org/lang_transaction.html). 실제 동작은 별도의 실패·경합 테스트로 확인한다.
