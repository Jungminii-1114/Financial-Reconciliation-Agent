# TOOL-01 get_case 정식 검증 보고서

> 보고서 버전 1.0 · 검증 완료(KST): **2026-11-12T13:54:51.660722+09:00** · 대상 코드: [`97d3400`](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/commit/97d340084b50ea7d4f7ab198520e15c2e4604a03)
>
> [사용 설명서](../../get-case-usage.md) · [Tool 명세](../../TOOL-01.md) · [원시 결과 JSON](results.json) · [전체 로그](unittest-full.txt) · [Tool 개별 로그](unittest-tool01.txt) · [CLI 실제 관측](cli-replay.json)

## 1. 판정 요약

**현재 로컬 Python TOOL-01과 최소 공통 호출 경로는 이 보고서에 명시한 검증을 통과했다.** 이는 은행 운영 인증, 완전한 보안 검증, Agent 성능 평가를 뜻하지 않는다.

| 실행 | 결과 | 실행 시간 | 의미 |
|---|---|---|---|
| 전체 회귀 검사 | 111개, exit=0 | 22.780 s | 기존 83개 + Tool 1 신규 28개 |
| Tool 1 독립 재실행 | 28/28 통과, 실패 0, 오류 0, 생략 0 | 2.459 s | 위 111개에 이미 포함된 28개를 다시 실행 |
| 기본 100행 CLI 재현 | PASS | 별도 성능 지표로 집계하지 않음 | 생성→적재→조회→도착 적용→새 세션 조회 |
| 모델 호출 | 0회 | 해당 없음 | 코드 assertion·SQLite·CLI로 판정 |

표의 전체 suite 시간은 Python 자식 프로세스 시작·종료를 포함한 wall time이다. 원시 로그의 unittest 내부 실행 시간과 약간 다를 수 있다.

111+28=139개의 독립 테스트가 아니다. 테스트 메서드, subtest, pilot 사례, CLI 명령 수는 서로 다른 단위다. 각 메서드의 소요 시간은 성능 목표나 p95 지연시간이 아니며 클래스 공통 fixture 생성 시간을 포함하지 않을 수 있다.

## 2. 검증 대상과 재현 환경

- 대상 단계: `ToolInvocation → dispatcher → 권한/scope → repository → 공개 응답 → runtime ledger`.
- 대상 코드는 검증 시작 당시 추적 파일에 수정이 없는 상태였다. 작성 중인 untracked 파일은 import·검증·이번 게시 범위에서 제외했다.
- 이 문서는 제품 Python 코드나 기존 테스트를 변경하지 않고 재실행한 결과다. 보고서 재현 스크립트는 별도 추가했다.
- 소스 파일별 SHA-256, runner SHA-256, 로그·CLI 아티팩트 SHA-256은 [results.json](results.json)에 있다. results.json 자기 자신의 hash는 재귀적으로 포함하지 않는다.

| 항목 | 값 |
|---|---|
| Python | 3.11.14 |
| SQLite | 3.53.0 |
| OS / release / architecture | Darwin / 25.2.0 / arm64 |
| 단위/통합 fixture | seed=42, rows_per_batch=3 |
| 공통 fixture 생성 | pilot와 benchmark. Tool 전 사례 조회는 pilot 10개이며 benchmark 98개 조회를 주장하지 않음 |
| 별도 CLI fixture | configs/synthetic-pilot.json, seed=42, rows_per_batch=100 |
| 기본 호출 예산 | 20회. 예산 경계 테스트는 명시적으로 1회로 변경 |
| 외부 의존성 | Python 표준 라이브러리, 로컬 SQLite. 은행 API·고객 실데이터·LLM 미사용 |

## 3. 방법과 판정 원칙

1. 전체 suite를 verbose 모드로 실행하여 회귀 여부와 종료 코드를 저장했다.
2. Tool 1 suite는 별도로 실행하면서 메서드별 성공/실패/오류/생략과 시간을 수집했다. subtest 수는 메서드 수에 더하지 않았다.
3. 기대 결과는 테스트 코드의 고정 assertion이다. 보고서의 실제 결과는 실행 수집기의 PASS와 해당 assertion 충족을 근거로 한다. 각 중간값이 verbose 로그에 모두 출력된다는 뜻은 아니다.
4. 정상·악성 메모·변조·실패 주입은 합성/통제 조건이다. mock 예외, 실제 SQLite 변경, 실제 CLI 자식 프로세스를 구별한다.
5. 현재 구현을 통과하도록 정답을 모델에 요청하거나 LLM judge 점수를 사용하지 않았다.

## 4. 항목별 상세 결과 — 28개

### T01-01. DB 조회·공개 응답

`test_round_trip_read_only_and_public_fields` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L49)

- **확인할 질문:** 현재 실패 배치를 정상 조회하면서 입력과 내부 정보를 바꾸거나 과다 공개하지 않는가?
- **고정/대조 조건:** 정상 trial, 기본 권한·한도. 호출 전 업무 DB 파일 바이트를 저장한다.
- **조작·수행:** dispatcher 조회 후 공개 키 집합·배치 상태·빈 작업 참조·JSON 변환·DB 바이트를 비교한다.
- **관측·통과 기준:** success, 처리 상태 FAILED, 수신 완료 true, 정의된 공개 필드만 존재, 작업 참조 0건, 업무 DB 바이트 동일.
- **실제 결과:** **PASSED**, assertion 충족, 63.075 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** DB 파일 바이트 동일성은 이 정상 호출 한 번에 대한 관측이며 모든 SQLite 환경의 일반적 파일 불변성을 증명하지 않는다.

### T01-02. 인자 검증

`test_parser_rejects_extra_fields_missing_and_wrong_types` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L63)

- **확인할 질문:** 권한 사칭이나 잘못된 타입을 업무 인자로 수용하는가?
- **고정/대조 조건:** 기본 case·문맥·서비스 유지.
- **조작·수행:** 빈 dict, 정수/bool case_id, 잘못된 ID, principal_id 추가, list 입력의 여섯 subtest를 실행한다.
- **관측·통과 기준:** 모두 INVALID_ARGUMENT, data=null, 응답 repr에 주입 문자열 미포함.
- **실제 결과:** **PASSED**, assertion 충족, 62.423 ms. subtest 6개, 실패 subtest 0개.
- **해석 범위:** dict 입력 검사다. 전송 계층의 중복 JSON 키·NaN decoder 검증까지 수행한 것은 아니다.

### T01-03. registry·예산

`test_unknown_tool_and_contract_are_counted` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L72)

- **확인할 질문:** 허용되지 않은 요청을 차단하면서도 시도 비용을 누락하지 않는가?
- **고정/대조 조건:** 동일 trial·ledger·예산 그룹.
- **조작·수행:** arbitrary_sql과 미지원 계약 버전을 각각 호출한 뒤 budgets.used를 조회한다.
- **관측·통과 기준:** UNKNOWN_TOOL / UNSUPPORTED_CONTRACT_VERSION, used=2.
- **실제 결과:** **PASSED**, assertion 충족, 56.457 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 등록 Tool은 get_case 하나다. 임의 SQL을 실제 실행한 실험은 아니다.

### T01-04. 호출 권한

`test_scope_denied_before_handler` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L78)

- **확인할 질문:** Tool·대상·principal·trial 변경 요청에서 데이터를 반환하는가?
- **고정/대조 조건:** 기본 trial·인자를 유지한다.
- **조작·수행:** 허용 Tool 없음, 허용 대상 없음, 다른 principal, 다른 trial의 네 subtest.
- **관측·통과 기준:** SCOPE_DENIED와 data=null.
- **실제 결과:** **PASSED**, assertion 충족, 62.864 ms. subtest 4개, 실패 subtest 0개.
- **해석 범위:** 이 테스트는 응답 코드·data를 assertion한다. 이름과 달리 handler 호출 0회를 spy로 직접 assertion하지는 않는다.

### T01-05. 대상 쌍 권한

`test_cross_pair_denied` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L86)

- **확인할 질문:** case와 batch를 독립 목록처럼 조합해 조회할 수 있는가?
- **고정/대조 조건:** 실제 repository 대상은 고정.
- **조작·수행:** 같은 case/다른 batch와 다른 case/같은 batch를 허용 쌍으로 제공한다.
- **관측·통과 기준:** SCOPE_DENIED.
- **실제 결과:** **PASSED**, assertion 충족, 59.217 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 실험에서 정의한 두 쌍의 조합 경계에 대한 검사다.

### T01-06. scope 구성

`test_ambiguous_case_denied` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L90)

- **확인할 질문:** 하나의 case에 두 batch가 허용되면 임의로 하나를 고르는가?
- **고정/대조 조건:** 같은 case에 서로 다른 batch 두 개를 제공한다.
- **조작·수행:** resolve_case_scope가 포함된 dispatcher 호출.
- **관측·통과 기준:** CONTRACT_VIOLATION.
- **실제 결과:** **PASSED**, assertion 충족, 62.778 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** v1의 case당 단일 대상 배치 정책을 검사한다.

### T01-07. DB binding

`test_config_and_policy_rechecked` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L94)

- **확인할 질문:** 서버 문맥의 설정·정책과 실제 DB가 어긋나도 조회하는가?
- **고정/대조 조건:** 실제 DB 설정·정책은 고정.
- **조작·수행:** config_hash, policy_version을 각각 다른 값으로 교체한다.
- **관측·통과 기준:** 각각 SCOPE_DENIED.
- **실제 결과:** **PASSED**, assertion 충족, 57.949 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 외부 인증 플랫폼의 권한 철회·scope_version 서버 조회를 구현/검증한 것은 아니다.

### T01-08. 문맥 타입

`test_context_cannot_use_mutable_permissions` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L98)

- **확인할 질문:** 서버 문맥의 불변 컬렉션 계약이 유지되는가?
- **고정/대조 조건:** 정상 문맥에서 allowed_tools만 교체.
- **조작·수행:** frozenset 대신 mutable set을 전달한다.
- **관측·통과 기준:** SCOPE_DENIED.
- **실제 결과:** **PASSED**, assertion 충족, 56.955 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 타입·구조 검사다. frozen dataclass가 OS 격리나 인증을 제공한다는 의미가 아니다.

### T01-09. 응답 검증

`test_response_type_validation_rejects_bool_version` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L101)

- **확인할 질문:** bool이 int의 하위 타입이라는 이유로 버전 true를 허용하는가?
- **고정/대조 조건:** 실제 repository 결과를 먼저 확보한다.
- **조작·수행:** repository 반환 객체의 input_version만 True로 바꾸는 patch를 주입한다.
- **관측·통과 기준:** CONTRACT_VIOLATION.
- **실제 결과:** **PASSED**, assertion 충족, 63.971 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 서비스 반환 객체 오염 시험이며 DB의 bool 저장 실험은 아니다.

### T01-10. 입력 무결성

`test_input_tampering_is_not_trusted_digest` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L106)

- **확인할 질문:** 저장된 digest를 무조건 신뢰하지 않고 실제 입력과 대조하는가?
- **고정/대조 조건:** 정상 적재 trial·원래 digest 유지.
- **조작·수행:** settlement_history의 모든 amount_krw를 1씩 증가시킨 뒤 호출한다.
- **관측·통과 기준:** IMPORT_INVARIANT_ERROR.
- **실제 결과:** **PASSED**, assertion 충족, 58.864 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 자유 텍스트 프롬프트 인젝션이 아닌 금융 입력 무결성 검사다.

### T01-11. 필수 상태 조회

`test_missing_runtime_is_error_not_default` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L111)

- **확인할 질문:** 필수 runtime 행이 없으면 FAILED 같은 기본값으로 숨기는가?
- **고정/대조 조건:** case·입력은 유지.
- **조작·수행:** batch_runtime 행을 삭제한다.
- **관측·통과 기준:** IMPORT_INVARIANT_ERROR.
- **실제 결과:** **PASSED**, assertion 충족, 60.249 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 필수 상태 누락 한 유형에 대한 검사다.

### T01-12. 조회 불가 분류

`test_missing_case_only_after_scope_admission` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L115)

- **확인할 질문:** 이미 허용된 사건이 사라졌을 때 구체적인 조회 불가를 구분하는가?
- **고정/대조 조건:** 삭제 전에 신뢰 문맥을 생성한다.
- **조작·수행:** cases 행을 삭제하고 기존 문맥으로 호출한다.
- **관측·통과 기준:** CASE_UNAVAILABLE.
- **실제 결과:** **PASSED**, assertion 충족, 57.593 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 권한 밖 사건의 존재 여부를 알아내는 공격을 포괄적으로 검증한 것은 아니다.

### T01-13. 시점 경계

`test_future_note_hidden` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L120)

- **확인할 질문:** 현재 알려지지 않은 메모가 공개되는가?
- **고정/대조 조건:** 현재의 정상 메모는 유지.
- **조작·수행:** known_at=2099년인 미래 메모 행을 추가한다.
- **관측·통과 기준:** 반환 note_id에 future가 없다.
- **실제 결과:** **PASSED**, assertion 충족, 117.630 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 성공 응답 data 접근을 통해 미래 메모 제외를 확인한다. 모든 미래 금융 행 조합을 시험하지는 않는다.

### T01-14. 입력 사건·기존 작업 참조

`test_real_arrival_changes_version_and_preserves_old_reference` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L125)

- **확인할 질문:** 새 자료가 도착해도 과거 버전의 미확정 작업을 찾을 수 있는가?
- **고정/대조 조건:** 3행 delayed_arrival trial. 최초 조회는 수신 미완료.
- **조작·수행:** 승인/UNKNOWN 작업 행을 검사 코드로 주입하고 advance_trial로 도착을 적용한다.
- **관측·통과 기준:** 초기 수신 false, 이후 success·v2·수신 true, 과거 operation_ref의 input_version=1 유지.
- **실제 결과:** **PASSED**, assertion 충족, 130.159 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 승인·작업 행은 seeded storage다. 실제 승인/재처리 서비스나 Agent 중단·복구를 실행하지 않았다.

### T01-15. 메모 출처·원문 무결성

`test_security_notes_remain_untrusted_and_hash_checked` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L142)

- **확인할 질문:** 외부 메모를 바꾸지 않고 반환하면서 바이트 변조는 발견하는가?
- **고정/대조 조건:** 실제 pilot 보안 사례 한 개를 적재한다.
- **조작·수행:** 정상 조회 후 content_objects의 외부 메모 원문을 changed로 바꾸되 hash는 유지한다.
- **관측·통과 기준:** 첫 응답 success·origin/원문 일치·승인 행 0건, 변조 후 CONTENT_INTEGRITY_ERROR.
- **실제 결과:** **PASSED**, assertion 충족, 115.334 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 메모 주입 지시를 LLM이 거부하는지나 탐지 precision/recall은 평가하지 않는다.

### T01-16. 공개 응답 크기

`test_output_limit_blocks_whole_response` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L157)

- **확인할 질문:** 성공 응답이 제한보다 크면 조용히 일부 금융 사실을 자르는가?
- **고정/대조 조건:** 동일 trial, 응답 상한만 512 bytes로 낮춘다.
- **조작·수행:** 완전한 성공 응답이 한도를 넘는 조건으로 호출.
- **관측·통과 기준:** OUTPUT_LIMIT_EXCEEDED, data=null.
- **실제 결과:** **PASSED**, assertion 충족, 58.578 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 메모당 16 KiB·참조 1,024개 각각의 정확한 상한 경계 시험은 아니다.

### T01-17. 발행 전 감사 실패

`test_audit_failure_prevents_handler` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L164)

- **확인할 질문:** 사전 기록이 실패해도 업무 조회를 실행하는가?
- **고정/대조 조건:** ledger.reserve와 repository 진입을 patch/spy한다.
- **조작·수행:** reserve가 AUDIT_UNAVAILABLE을 발생시키도록 한다.
- **관측·통과 기준:** AUDIT_UNAVAILABLE, repository.get_case_view 호출 0회.
- **실제 결과:** **PASSED**, assertion 충족, 55.885 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 실제 디스크 full/파일시스템 장애가 아닌 지정 함수 예외 주입이다.

### T01-18. 실행 후 감사 실패

`test_post_handler_audit_failure_does_not_expose_result` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L169)

- **확인할 질문:** 후속 기록 실패를 정상 공개 성공으로 숨기는가?
- **고정/대조 조건:** 정상 DB·handler.
- **조작·수행:** ledger.finish에서 가짜 비밀 문자열을 포함한 OSError를 주입한다.
- **관측·통과 기준:** error/data=null, 응답 repr에 주입 문자열 없음.
- **실제 결과:** **PASSED**, assertion 충족, 60.565 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 읽기 Tool의 후속 기록 오류다. 쓰기 commit 이후 효과 복구를 검증하지 않는다.

### T01-19. 지속 예산

`test_budget_persists_across_sessions_and_reopen` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L174)

- **확인할 질문:** 세션이나 ledger 객체를 바꿔 호출 예산을 초기화하는가?
- **고정/대조 조건:** 동일 SQLite ledger, max_attempts=1.
- **조작·수행:** 첫 호출 후 같은 파일을 다시 열고 새 session으로 호출.
- **관측·통과 기준:** 첫 success, 두 번째 BUDGET_EXHAUSTED.
- **실제 결과:** **PASSED**, assertion 충족, 59.143 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 같은 Python 검사 프로세스에서 객체를 재구성했다. Agent 프로세스 강제 종료 실험은 아니다.

### T01-20. 예산 원자성

`test_concurrent_budget_reservation` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L181)

- **확인할 질문:** 남은 1회 예산을 여러 호출이 동시에 소비하는가?
- **고정/대조 조건:** 동일 ledger·그룹·max_attempts=1.
- **조작·수행:** ThreadPoolExecutor의 3개 worker로 동시에 호출한다.
- **관측·통과 기준:** success 1건, BUDGET_EXHAUSTED 2건.
- **실제 결과:** **PASSED**, assertion 충족, 64.578 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 이 한 번의 소규모 동시성 실행을 대규모 부하·다중 프로세스 안전성으로 일반화하지 않는다.

### T01-21. 호출 ID 중복

`test_call_id_reuse_is_not_another_attempt` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L188)

- **확인할 질문:** 동일 호출 식별자의 재사용이 예산/작업 시도로 중복 집계되는가?
- **고정/대조 조건:** 동일 ctx/tool_call_id와 ledger.
- **조작·수행:** 동일 호출을 두 번 전달한다.
- **관측·통과 기준:** 첫 success, 두 번째 CALL_ID_CONFLICT, used=1.
- **실제 결과:** **PASSED**, assertion 충족, 83.389 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** tool_call_id 중복 검사다. 업무 operation_id/idempotency_key의 멱등 효과 검증과 다르다.

### T01-22. 오류·로그 노출

`test_unknown_exception_is_payload_free` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L194)

- **확인할 질문:** 미분류 예외의 내부 경로·가짜 비밀이 공개되는가?
- **고정/대조 조건:** 정상 권한·인자.
- **조작·수행:** repository에서 가짜 비밀/경로를 담은 RuntimeError를 주입한다.
- **관측·통과 기준:** INTERNAL_ERROR, 응답 repr과 ledger 파일 바이트에 CANARY_SECRET 없음.
- **실제 결과:** **PASSED**, assertion 충족, 57.577 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 정의된 평문 문자열과 두 sink만 검사한다. 포괄적 유출 탐지가 아니다.

### T01-23. 읽기 deadline

`test_deadline_expired_before_sql` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L200)

- **확인할 질문:** 이미 만료된 조회를 시작하는가?
- **고정/대조 조건:** 정상 repository·대상 scope.
- **조작·수행:** deadline=monotonic()-1로 직접 get_case_view 호출.
- **관측·통과 기준:** AdapterError TOOL_TIMEOUT.
- **실제 결과:** **PASSED**, assertion 충족, 54.933 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 긴 쿼리 interrupt, 실제 잠금 대기, dispatcher 전체 hard timeout을 시험하지 않는다.

### T01-24. 선택적 H 단계 게이트

`test_phase_gate_optional` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L206)

- **확인할 질문:** 기본 호출은 허용하면서 명시적 비허용 단계는 차단하는가?
- **고정/대조 조건:** 동일 Tool/업무 권한.
- **조작·수행:** phase=None 호출과 phase=B-03 호출 비교.
- **관측·통과 기준:** 기본 success, B-03 PHASE_DENIED.
- **실제 결과:** **PASSED**, assertion 충족, 59.897 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 전체 R0/B0/B1/H 설정의 공정성 비교 실험은 아니다.

### T01-25. pilot 연결 범위

`test_entire_pilot_can_be_observed` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L211)

- **확인할 질문:** 정상·불일치·보안 입력을 포함한 pilot 모든 사례를 관측할 수 있는가?
- **고정/대조 조건:** 공유 Fixtures의 3행 pilot 전체.
- **조작·수행:** catalog의 10개 사례를 각각 독립 trial로 적재해 호출한다.
- **관측·통과 기준:** 모든 사례의 호출 status=success.
- **실제 결과:** **PASSED**, assertion 충족, 285.259 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 해당 메서드는 각 사례의 모든 업무 필드를 독립 정답과 대조하지 않는다. 대사 판단 정확도가 아니다.

### T01-26. 응답-감사 연결

`test_ledger_hash_matches_actual_success_and_error_response` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L219)

- **확인할 질문:** 기록한 hash가 실제 직렬화된 공개 응답과 일치하는가?
- **고정/대조 조건:** 동일 trial/ledger, 서로 다른 호출 ID.
- **조작·수행:** 정상 요청과 bad 추가 필드 오류 요청을 각각 실행한다.
- **관측·통과 기준:** 두 응답 모두 실제 UTF-8 JSON SHA-256과 calls.response_digest 일치.
- **실제 결과:** **PASSED**, assertion 충족, 63.175 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** hash 일치는 내용의 진실성이나 서명 인증을 뜻하지 않는다.

### T01-27. CLI·경로 경계

`test_cli_uses_existing_trial_and_external_runtime_ledger` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L228)

- **확인할 질문:** 명령행이 준비된 trial을 읽고 ledger를 역할 저장소 밖에 두는가?
- **고정/대조 조건:** 3행 trial, Python 자식 프로세스.
- **조작·수행:** 외부 ledger로 실행 후 trial/operator 내부 ledger 경로로 다시 실행한다.
- **관측·통과 기준:** 첫 exit=0·success, 내부 경로 exit=2·파일 미생성.
- **실제 결과:** **PASSED**, assertion 충족, 165.477 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 임의 OS 침해나 모든 symlink/경로 공격을 시험한 것은 아니다.

### T01-28. 오류 응답 크기

`test_small_response_limit_also_bounds_error_envelope` · [검증 당시 테스트 코드](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/97d340084b50ea7d4f7ab198520e15c2e4604a03/tests/test_get_case.py#L240)

- **확인할 질문:** 성공뿐 아니라 오류 외피도 크기 제한을 지키는가?
- **고정/대조 조건:** 응답 상한 512 bytes.
- **조작·수행:** 호출 결과를 같은 제한으로 직렬화한다.
- **관측·통과 기준:** 전체 직렬화 바이트 길이 ≤512.
- **실제 결과:** **PASSED**, assertion 충족, 59.853 ms. subtest 0개, 실패 subtest 0개.
- **해석 범위:** 최소 한도의 오류 fallback 확인이다. 모든 가능한 오류 조합에 대한 전수 검사는 아니다.

## 5. 기본 100행 pilot의 별도 CLI 재현

[cli-replay.json](cli-replay.json)은 실제 실행한 명령·종료 코드·공개 필드 관측을 담는다. `<tmp>`는 실제 임시 경로를 치환한 표기이며 repo/개인 경로를 공개하지 않는다. 명령의 실행 순서는 JSON 배열 순서다.

| 관측 | 도착 전 | 도착 후 |
|---|---|---|
| Tool status | success | success |
| input_version | 1 | 2 |
| ingestion_complete | False | True |
| processing_state | FAILED | FAILED |
| 업무 가상 시각(UTC) | 2026-11-05T01:00:00.000000+00:00 | 2026-11-05T01:05:00.000000+00:00 |
| 작업 참조 수 | 0 | 0 |
| 메모 수 | 1 | 1 |
| 해당 읽기 전후 업무 DB hash 동일 | True | True |

- 도착 후 현재 외부 행 수는 **100개**다. 도착 전 행 수는 이 CLI 스크립트에서 별도 수집하지 않았으므로 관측값으로 기재하지 않는다.
- 같은 ledger의 used=2, session 목록=['session-1', 'session-2'], 호출 상태=['success', 'success']다. session을 바꾸어도 기존 그룹의 예산 계수를 유지했다.
- v1/v2의 실제 input_digest는 JSON에 보존했다. 해시 변화가 거래별 정합성 판정이나 업무 성공을 뜻하지 않는다.
- 처리 상태 FAILED가 유지되는 것이 맞다. 조회·자료 도착만 수행했고 재처리는 실행하지 않았다.

## 6. 공통 계약 QA와의 연결 및 미검증 범위

아래는 [공통 호출 계약](../../../contracts/tool-call-contract.md)의 CALL ID와 실제 검사 범위를 연결한다. 부분 충족을 전체 충족으로 승격하지 않는다.

| 계약 항목 | 관련 검사 | 이번 근거와 남은 부분 |
|---|---|---|
| CALL-01 | T01-01 | 정상 외피·공개 키·읽기 불변성 확인 |
| CALL-02~03 | T01-02~03 | 추가 인자·미등록 Tool·버전 확인. 모든 사칭 필드 조합 전수 검사는 아님 |
| CALL-04 | T01-02 | 정수/bool/잘못된 입력 확인. 중복 JSON 키 decoder는 미검증 |
| CALL-05~07 | T01-04~08, 12 | 대상 쌍·실행 문맥·정책·조회 불가 확인. 실제 OAuth/권한 철회는 미구현 |
| CALL-08 | T01-14와 repository 구조 | 순차 도착 후 관측 확인. 도착/조회 동시 실행의 일관성 전용 테스트는 없음 |
| CALL-09~10 | T01-01, 11~12, 22 | 빈 작업 참조·실패 구분·배치 FAILED 조회. 실제 SQL 오류 전체 유형은 미검증 |
| CALL-11~12 | T01-01, 09, 15, 22 | 출처·키 allowlist·특정 오류 노출 경로 확인. 모델의 공격 탐지/거부율은 미평가 |
| CALL-13~14 | T01-16, 28 | 응답/오류 외피 512-byte 한도 확인. 개별 메모/개수 경계·TOOL-02 partial은 미검증 |
| CALL-15 | T01-23 | 시작 전 만료만 확인. 긴 SQL 취소·실제 잠금·전체 hard deadline은 미검증 |
| CALL-16~17 | T01-03, 19~21 | 시도 계수·객체 재개·3-thread/1-budget 확인. 실제 Agent 중단 복구는 미검증 |
| CALL-18 | T01-17~18 | reserve/finish 예외 주입 확인. 실제 저장장치 장애는 미검증 |
| CALL-19~21 | 해당 없음 | 쓰기 응답 유실·commit 후 복구·업무 멱등 효과는 TOOL-04/05 구현 후 검증 |
| CALL-22 | T01-19~20 일부 | 예산 거부는 확인. 미확정 업무 효과를 가진 Agent의 이관은 미검증 |
| CALL-23 | 해당 없음 | TOOL-06 초안 저장은 미구현 |
| CALL-24 | T01-24 일부 | 선택 phase 게이트만 확인. R0/B0/B1/H 동등 조건 비교는 미수행 |

## 7. 재현 절차

보고서의 기준 코드를 분리해서 재현하려면 별도 checkout에서 `97d3400`를 사용한다. 현재 개발 폴더의 코드를 강제로 되돌릴 필요는 없다.

```bash
# 현재 checkout에서 기본 검사를 재실행
python3.11 -m unittest discover -s tests -v
python3.11 -m unittest discover -s tests -p test_get_case.py -v

# 이후 추가한 보고서 runner로 같은 종류의 증거를 새 디렉터리에 수집
# 출력 디렉터리는 아직 없어야 한다. generated_data는 Git에서 제외된다.
python3.11 scripts/verify_tool01.py --out generated_data/tool01_verification_new
```

runner는 현재 checkout의 revision·소스 hash를 새 결과에 기록한다. 결과 시각·시간·새 UUID는 달라질 수 있다. 이 게시본을 덮어쓰지 말고 새 실행 결과와 구분한다. 111개는 이 게시본의 코드 기준 개수이며 미래 테스트 추가 후에는 달라질 수 있다.

## 8. 결론과 다음 검증

현재 근거로는 **고정된 로컬 합성 환경에서 TOOL-01의 관측·권한 경계·명시적 실패·예산 기록이 해당 assertion을 만족한다**고 말할 수 있다. 은행 실환경 적용, 프롬프트 인젝션 방어 완료, Agent 프로세스 복구 성공률, 업무 재처리 정확도, 보편적 무결성/규제 준수는 주장하지 않는다.

다음 검증 우선순위는 도착/조회 경쟁 조건, 긴 쿼리·잠금 deadline, 메모/참조 상한 경계, 이후 Tool 2의 조회 coverage다. 새 실패나 코드 변경이 있을 때 관련 검사를 추가하고 보고서를 버전별로 갱신한다.
