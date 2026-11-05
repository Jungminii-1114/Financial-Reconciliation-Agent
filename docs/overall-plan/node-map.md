# 전체 노드 지도와 구현 단계

> [전체 구조](architecture.md) · [Big Node](../big-nodes/README.md) · [Small Node](../small-nodes/README.md)

## 노드와 우선순위

| Small Node | Big Node | 함수 | 우선순위 |
|---|---|---|---|
| [N-01](../small-nodes/N-01.md) | B-01 | `admit_case` | P0 |
| [N-02](../small-nodes/N-02.md) | B-01 | `restore_run` | P0 |
| [N-03](../small-nodes/N-03.md) | B-01 | `build_case_context` | P0 |
| [N-04](../small-nodes/N-04.md) | B-02 | `plan_investigation` | P0 |
| [N-05](../small-nodes/N-05.md) | B-02 | `collect_evidence` | P0 |
| [N-06](../small-nodes/N-06.md) | B-02 | `revalidate_dependencies` | P1 |
| [N-07](../small-nodes/N-07.md) | B-02 | `assemble_asof_evidence` | P1 |
| [N-08](../small-nodes/N-08.md) | B-03 | `propose_action` | P0 |
| [N-09](../small-nodes/N-09.md) | B-03 | `prepare_approval` | P0 |
| [N-10](../small-nodes/N-10.md) | B-03 | `persist_operation_intent` | P0 |
| [N-11](../small-nodes/N-11.md) | B-04 | `execute_operation` | P0 |
| [N-12](../small-nodes/N-12.md) | B-04 | `resolve_uncertain_operation` | P1 |
| [N-13](../small-nodes/N-13.md) | B-05 | `verify_business_outcome` | P1 |
| [N-14](../small-nodes/N-14.md) | B-05 | `finalize_case` | P1 |
| [N-15](../small-nodes/N-15.md) | B-05 | `deliver_result` | P0 |
| [N-16](../small-nodes/N-16.md) | B-06 | `load_evaluation_case` | P0 |
| [N-17](../small-nodes/N-17.md) | B-06 | `replay_events_and_faults` | P0/P1 |
| [N-18](../small-nodes/N-18.md) | B-06 | `generate_counterexamples` | P1 |
| [N-19](../small-nodes/N-19.md) | B-06 | `grade_and_aggregate` | P0 |

## 업무 Tool의 주 사용 노드

| Tool | 주 호출 노드 | 역할 |
|---|---|---|
| [TOOL-01](../tools/TOOL-01.md) get_case | N-01/03/12 | 범위·현재 입력·작업 참조 |
| [TOOL-02](../tools/TOOL-02.md) query_records | N-05/13 | 3가지 profile의 거래·작업·결과 관측 |
| [TOOL-03](../tools/TOOL-03.md) precheck_reprocess | N-09 | 현재 계획·승인 요청용 검사 |
| [TOOL-04](../tools/TOOL-04.md) execute_reprocess | N-11 | 승인된 대사 결과 생성 |
| [TOOL-05](../tools/TOOL-05.md) get_operation | N-12/13 | 효과·결과·권위 있는 재시도 조건 |
| [TOOL-06](../tools/TOOL-06.md) prepare_escalation | N-15 | 검증된 이관 초안 |

LLM을 직접 사용하는 기본 단계는 N-04와 N-08이다. 도구 6개는 공유 업무 기능이고, Big Node는 이를 사용하는 고정 실행 경로다. 호출 횟수는 조사·복구 분기에 따라 달라지므로 노드 수와 같지 않다.

처음에는 R0의 결정적 경로와 B0/B1을 먼저 구축한다. P1의 세부 모듈을 켜고 끄며 H를 비교한다. evaluator 경로·정답 접근은 어떤 수행 모델에도 허용하지 않는다.
