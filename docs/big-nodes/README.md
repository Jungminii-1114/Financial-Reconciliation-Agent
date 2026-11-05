# Big Node — 실행 단계·분기 목록

> 6종, 설계 초안 / 미구현. [Overall Plan](../overall-plan/README.md) · [노드 지도](../overall-plan/node-map.md)

| Big Node | English | 역할 | Small Node |
|---|---|---|---|
| [B-01](B-01.md) | Case Intake and Restore | 접수·현재 권한·지속 상태 복원 | [N-01](../small-nodes/N-01.md) · [N-02](../small-nodes/N-02.md) · [N-03](../small-nodes/N-03.md) |
| [B-02](B-02.md) | Evidence Investigation | 허용 조사·선택적 재검증·시점별 근거 | [N-04](../small-nodes/N-04.md) · [N-05](../small-nodes/N-05.md) · [N-06](../small-nodes/N-06.md) · [N-07](../small-nodes/N-07.md) |
| [B-03](B-03.md) | Decision and Approval | 조치 제안·외부 승인·의도 저장 | [N-08](../small-nodes/N-08.md) · [N-09](../small-nodes/N-09.md) · [N-10](../small-nodes/N-10.md) |
| [B-04](B-04.md) | Execution and Recovery | 작업 발행·미확정 상태 관측·복구 | [N-11](../small-nodes/N-11.md) · [N-12](../small-nodes/N-12.md) |
| [B-05](B-05.md) | Verification and Delivery | 업무 결과 검증·조건부 종료·전달 | [N-13](../small-nodes/N-13.md) · [N-14](../small-nodes/N-14.md) · [N-15](../small-nodes/N-15.md) |
| [B-06](B-06.md) | Scenario Replay and Evaluation | 격리된 사건 재생·독립 평가 | [N-16](../small-nodes/N-16.md) · [N-17](../small-nodes/N-17.md) · [N-18](../small-nodes/N-18.md) · [N-19](../small-nodes/N-19.md) |

B-01~B-05는 한 대사 업무의 생명주기이며 사전 정의된 조사·재개 루프를 허용한다. B-06은 evaluator 전용 경로다. B0/B1 평가 설정 이름과 B-01 노드 ID를 구분한다.

각 Big Node는 입력·출력, 사용 노드, 그래프, 분기, 대기·종료, 상태·권한·예산, 연결 사례를 관리한다. Small Node의 함수 세부 계약과 Tool의 DB 동작은 각각 [Small Node](../small-nodes/README.md), [Tool](../tools/README.md)에 둔다.
