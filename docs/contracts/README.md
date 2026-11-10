# 공통 계약 — 필드·상태·불변식의 정본

| 문서 | 소유하는 계약 |
|---|---|
| [상태·타입](state-and-types.md) | IDs, CaseState, 업무·실행·효과 상태, 예산 |
| [업무 DB·데이터](business-data.md) | 원천 적재, 이력·키·승인·작업·결과 테이블 |
| [지속 상태·복구](persistence-and-recovery.md) | 의도 저장, commit 경계, 같은 키 재시도, 종료 재생 |
| [증거·시점](evidence-and-time.md) | 빈 결과, 조회 범위, 영향 분석, as-of와 입력 버전 |
| [보안 경계](security-boundary.md) | 신뢰 출처, 접근·승인·정보 노출·로그·공격 조건 |
| [NodeResult](../small-nodes/common-result-contract.md) | 모든 노드와 Tool의 반환 외피 |
| [Tool 공통 호출 계약](tool-call-contract.md) | 모델 인자·서버 문맥, 대상 쌍 권한, dispatcher, 호출 기록·오류·재시도·QA |

본문의 함수·타입은 설계 계약이다. 실제 Python 타입·DDL 마이그레이션·API 구현은 이후 추가하며, 문서 예시를 실행 결과로 표시하지 않는다. 공통 필드를 바꾸면 해당 필드를 쓰는 Small Node·Tool·QA와 같이 검토한다.
