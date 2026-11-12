# 업무 Tool — 6개 기능의 상세 API 계약

> TOOL-01 로컬 구현 / TOOL-02~06 설계 단계. [문서 홈](../README.md) · [Small Node](../small-nodes/README.md) · [내부 서비스](internal-services.md)

**구현 전 먼저 읽기:** [공통 호출 계약 tool-call-1](../contracts/tool-call-contract.md). 모델 업무 인자와 서버 권한 문맥, 네 필드 응답, 대상 쌍 검사, 호출 기록·오류·재시도·최소 QA를 정의한다. TOOL-01과 최소 공통 호출 경로는 구현됐으며 나머지 다섯 handler는 미구현이다. [TOOL-01 사용 설명서](get-case-usage.md).

| ID | 모델 등록 이름 | 효과 | 책임 |
|---|---|---|---|
| [TOOL-01](TOOL-01.md) | `get_case` | 업무 읽기 | 사건·대상·현재 입력 조회 |
| [TOOL-02](TOOL-02.md) | `query_records` | 업무 읽기 | 제한된 거래·중복·배치 비교 조회 |
| [TOOL-03](TOOL-03.md) | `precheck_reprocess` | 업무 읽기 | 재처리 적합성·승인 binding 사전 확인 |
| [TOOL-04](TOOL-04.md) | `execute_reprocess` | 업무 쓰기 | 승인된 대사 결과 생성·멱등 재요청 |
| [TOOL-05](TOOL-05.md) | `get_operation` | 업무 읽기 | 작업 효과·결과·재시도 조건 조회 |
| [TOOL-06](TOOL-06.md) | `prepare_escalation` | 범위 제한 아티팩트 작성, 업무 입력 변경 없음 | 근거가 있는 이관 초안 구성 |

## Tool과 노드의 차이

Tool은 외부 업무 기능의 안정된 계약이다. Small Node는 이를 호출하거나 모델·코드 검사를 수행하는 처리 단위다. Big Node는 단계와 분기를 관리한다. H의 고정 그래프와 B0/B1의 도구 루프는 위 6개 구현을 공유하며 권한·기본 서버 보호를 달리하지 않는다.

## 호출 외피와 승인

모든 호출은 서버가 만든 TrustedCallContext를 받는다. 인증 주체·scope·시간·예산·정책을 모델 JSON에서 받지 않는다. 실제 OAuth 플랫폼은 선택 후속 범위이며 초기 로컬 실험에서도 호출 주체와 권한은 명시적으로 binding한다.

TOOL-03은 승인 발급 기능이 아니다. 외부 승인 UI/시뮬레이터가 승인 레코드를 만들며 TOOL-04는 새 쓰기 직전에 현재 입력·승인을 재검사한다. operation_id와 업무 키는 코드가 생성·지속 저장한다.

## 공통 정책

- 요청·응답 schema, case/batch·버전·출처를 검사한다. 지원 밖 필드를 무시하여 위험 인자를 숨기지 않고 거부한다.
- NodeResult의 실행 상태와 실제 업무 효과·업무 결과를 구분한다.
- 호출 전 예산 예약, 실패·조회·재시도도 누적 계수. 알려지지 않은 usage는 unknown으로 기록한다.
- 범위 오류·승인 오류·버전 오류는 넓은 권한·새 키·무검증 동작으로 폴백하지 않는다.
- 쓰기 타임아웃은 효과 UNKNOWN이다. TOOL-05 조회 없이 새 쓰기로 반복하지 않는다.
- 원문 메모·Tool 자유 설명은 구조화 권위 관측과 분리한다.
- 실행·결과·승인·금지 효과는 코드로 판정하고 외부 문장·모델 confidence를 권한으로 사용하지 않는다.

각 상세 문서는 함수·선택 HTTP 경로, 요청/응답 예시, 서버 순서·원자성·멱등성, 오류·재시도, 최소 사례를 포함한다. HTTP 어댑터·MCP transport를 붙여도 이 업무 계약은 유지한다.
