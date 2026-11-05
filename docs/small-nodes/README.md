# Small Node — 단일 책임과 계약 목록

> 19종, 설계 초안 / 미구현. [문서 홈](../README.md) · [Big Node](../big-nodes/README.md) · [공통 반환](common-result-contract.md)

| ID | 함수명 | 단일 책임 | 소속 | LLM |
|---|---|---|---|---|
| [N-01](N-01.md) | `admit_case` | 접수·권한·지원 범위 확인 | [B-01](../big-nodes/B-01.md) | 사용하지 않음 |
| [N-02](N-02.md) | `restore_run` | 체크포인트·미확정 의도 복원 | [B-01](../big-nodes/B-01.md) | 사용하지 않음 |
| [N-03](N-03.md) | `build_case_context` | 현재 조사 문맥 구성 | [B-01](../big-nodes/B-01.md) | 사용하지 않음 |
| [N-04](N-04.md) | `plan_investigation` | 제한된 조사 계획 제안 | [B-02](../big-nodes/B-02.md) | 선택한 수행 모델 1회/조사 라운드 |
| [N-05](N-05.md) | `collect_evidence` | 허용 조회 실행·증거 등록 | [B-02](../big-nodes/B-02.md) | 사용하지 않음 |
| [N-06](N-06.md) | `revalidate_dependencies` | 변경 영향·부정 증거 재검증 | [B-02](../big-nodes/B-02.md) | 사용하지 않음 |
| [N-07](N-07.md) | `assemble_asof_evidence` | 판단 시점의 근거 구성 | [B-02](../big-nodes/B-02.md) | 사용하지 않음 |
| [N-08](N-08.md) | `propose_action` | 조치 제안과 근거 연결 | [B-03](../big-nodes/B-03.md) | 선택한 수행 모델 1회/조사 라운드 |
| [N-09](N-09.md) | `prepare_approval` | 재처리 계획·외부 승인 요청 | [B-03](../big-nodes/B-03.md) | 사용하지 않음 |
| [N-10](N-10.md) | `persist_operation_intent` | 실행 직전 검사·의도 영속화 | [B-03](../big-nodes/B-03.md) | 사용하지 않음 |
| [N-11](N-11.md) | `execute_operation` | 승인된 재처리 발행·응답 관측 | [B-04](../big-nodes/B-04.md) | 사용하지 않음 |
| [N-12](N-12.md) | `resolve_uncertain_operation` | 미확정 효과·입력 관계 구분 | [B-04](../big-nodes/B-04.md) | 사용하지 않음 |
| [N-13](N-13.md) | `verify_business_outcome` | 거래별 결과·실행 근거 검증 | [B-05](../big-nodes/B-05.md) | 사용하지 않음 |
| [N-14](N-14.md) | `finalize_case` | 현재 조건부 종료 결정 | [B-05](../big-nodes/B-05.md) | 사용하지 않음 |
| [N-15](N-15.md) | `deliver_result` | 확정 결과·근거·한계 전달 | [B-05](../big-nodes/B-05.md) | 기본은 결정적 템플릿, 추가 LLM 서식화는 P2 |
| [N-16](N-16.md) | `load_evaluation_case` | 평가 사례·정답·실행 격리 준비 | [B-06](../big-nodes/B-06.md) | 사용하지 않음 |
| [N-17](N-17.md) | `replay_events_and_faults` | 가상 시계·장애·공격 재생 | [B-06](../big-nodes/B-06.md) | 수행 모델 실행을 시작하지만 평가 모델 호출은 하지 않음 |
| [N-18](N-18.md) | `generate_counterexamples` | 금융 반례·정상 변환 생성 | [B-06](../big-nodes/B-06.md) | 사용하지 않음 |
| [N-19](N-19.md) | `grade_and_aggregate` | 독립 결과 판정·지표 집계 | [B-06](../big-nodes/B-06.md) | 기본 평가 0회. 의미 품질 보조는 별도 P2 |

N-01~15는 업무 실행, N-16~19는 evaluator 전용이다. 저장·예산·출처·Tool dispatcher는 [횡단 내부 서비스](../tools/internal-services.md)이며 별도 Small Node 수에 포함하지 않는다. 각 함수의 실제 구현 위치는 구현 시 정한다.

입력·출력·내부 함수·오류·예산·상태·최소 QA는 개별 문서가, 실행 순서·조건부 엣지는 Big Node가 소유한다. 노드는 다음 노드를 직접 호출하지 않는다. 일부 단순 단계를 하나의 LangGraph 노드 내부 함수로 합쳐도 문서의 책임과 검증 경계는 유지한다.
