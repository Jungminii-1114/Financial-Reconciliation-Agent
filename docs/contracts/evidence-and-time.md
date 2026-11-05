# 증거·빈 결과·시점·입력 버전

> v0.1 설계. [N-05](../small-nodes/N-05.md) · [N-06](../small-nodes/N-06.md) · [N-07](../small-nodes/N-07.md)

## 1. EvidenceRecord

| 필드 | 의미 |
|---|---|
| evidence_id | 저장소가 발급한 불변 참조 |
| case_id, batch_id | 조사 대상과 접근 범위 |
| query_profile, predicate | EXISTENCE / DUPLICATE / BATCH_COMPARISON과 정규화 조건 |
| as_of, input_version, input_digest | 당시 알려진 입력 스냅샷 |
| coverage | complete, observed_count, expected_scope, next_cursor |
| row_refs, structured_facts | 실제 조회·계산한 행 참조·정수 금액·ID별 결과 |
| result_digest, predicate_digest | 내용과 조회 범위를 각각 식별 |
| footprint | 영향받는 table·batch·transaction IDs·columns |
| external_content_refs | 원문 메모·오류 설명, 신뢰되지 않는 콘텐츠로 분리 |
| source_service, contract_version | 하네스가 검증한 응답 출처·버전 |

구조화 필드도 외부 문장이 스스로 선언한 값이면 권위 있는 관측이 아니다. trusted adapter가 실제 서비스의 envelope를 확인하여 등록한다. 모델이 출력한 evidence_id는 registry에 존재하고 현재 범위·시점에 유효해야 한다.

## 2. 지원하는 세 조회

| profile | 고정 조건과 결과 | 부재를 말할 수 있는 범위 |
|---|---|---|
| EXISTENCE | 지정 transaction_ids의 현재 외부 행 존재 여부와 refs | 요청한 IDs에 대해 조회가 완결된 경우만 |
| DUPLICATE | 지정 case/batch/input_version/action_kind의 작업/결과, action_digest의 동일 의미 여부 | 의미상 업무 키의 현재 DB 조회 범위. 다른 digest의 기존 효과를 숨기지 않음 |
| BATCH_COMPARISON | 전체 case/batch의 거래별 ID·금액·통화·호환 상태와 ingestion_complete | 전체 스냅샷 검사 완료 때만 배치 일치/불일치 |

BATCH_COMPARISON은 서버가 전체 허용 배치를 비교하고, 모델에는 요약·불일치 IDs·검증 가능한 refs를 준다. 반환 상세 행이 잘렸어도 집계가 완결됐는지 `summary_complete`를 별도로 표시한다. 일부 페이지만 읽고 전체가 일치한다고 말하지 않는다.

## 3. 선택적 재검증

`query predicate → result set(빈 집합 포함) → proposal → approval/verification` 의존성을 기록한다.

| 변경 | 관련 증거의 처리 |
|---|---|
| 빈 EXISTENCE 결과 뒤 같은 거래 외부 행 삽입 | 무효화·재조회. 기존 관측 행 버전만 검사하면 놓침 |
| 다른 batch의 행 변경 | 이 query footprint와 비중첩임을 코드로 증명하면 유지 |
| 관련 거래 amount/status/known_at 정정 | 관련 조회·시점 판단 무효화 |
| 배치 입력 추가·삭제·도착 완료 변화 | 배치 비교 무효화 |
| operation 상태만 변경 | DUPLICATE/operation 관측은 새로 조회. 금융 입력 버전만으로 캐시 유효성을 증명하지 않음 |
| 출처가 불분명한 변경·mutation gap | 영향 없음으로 추측하지 않고 해당 배치 근거 재조회 |

`revalidate_dependencies(refs, mutations)`는 KEEP / INVALIDATE / UNKNOWN을 반환한다. UNKNOWN도 새 쓰기의 근거로 사용하지 않는다. 최소 구현은 입력 버전 변경 때 전체 재조회이며, 최적화는 명세의 세 profile에만 적용한다.

**초기 캐시 정책:** EXISTENCE와 금융 입력만 비교하는 BATCH_COMPARISON은 입력 mutation으로 영향 분석한다. DUPLICATE는 operation 상태가 input_version과 별도로 바뀌므로 매 재검토에서 새로 조회한다. operation·승인·완료 직전 조건도 캐시로 대체하지 않는다. 결과 참조가 포함된 완료 검증은 N-13/14에서 현재 view를 다시 검사한다. operation 변화까지 선택적으로 캐시하는 최적화는 별도 revision ledger를 구현하기 전에는 도입하지 않는다.

증거를 유지할 수 있어도 approval은 현재 input_version/input_digest에 연결해야 한다. 승인이 자동 재사용된다는 뜻이 아니다. 비교 지표에서 재조회 절감과 재승인 요구를 구분한다.

## 4. 시점별 관측

- event_time: 거래·정정이 업무상 가리키는 발생 시각.
- known_at: 해당 시스템이 그 revision을 알게 된 시각.
- decision_at: 판단·검증을 기록한 가상 업무 시각.
- 현재 입력의 조회는 known_at ≤ decision_at이며, 정정 이력에서는 그 시점에 알려진 최신 revision을 선택한다.

예를 들어 09:58 거래의 외부 자료가 10:05 수신됐다면 10:00에는 보이지 않는다. 10:05 이후 완비되어도 10:00의 WAIT를 사후 정보로 오답 처리하지 않는다. 과거 조회는 조사·평가 용도이며 새 쓰기를 과거 승인으로 허용하지 않는다.

도착·정정·모의 마감 이후 도착을 각각 시나리오로 만든다. 가상 시계는 controller만 전진시키며 Agent는 미래 이벤트 목록을 볼 수 없다. 실제 추론 시간과 가상 업무 시간은 별도 기록한다.

## 5. 검증 사례

완전한 빈 조회·조회 실패·불완전 페이징을 서로 구분한다. 금액·건수·해시가 같아도 거래별 대응을 확인한다. mutation ledger가 누락되면 선택적 KEEP을 차단한다. 지연·정정 시점별 정답은 evaluator가 원본 이벤트 명세로 별도 계산한다.
