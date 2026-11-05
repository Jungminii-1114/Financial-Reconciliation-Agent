# 금융 대사 Agent 하네스 — 설계 문서

> 설계 기준: **v0.1**, 구현 상태 갱신: **2026-11-05**. [자체 합성기 v1](data-generation/README.md)은 생성·독립 SQL 검사·시간 스냅샷·HTML/SQLite 열람까지 구현됐다. 아래 노드·업무 서비스·실제 결과 평가기는 아직 제안 명세다. [기존 교육용 예제](../examples/synthetic_data_starter/README.md)도 유지한다. 개별 문서의 설계 예시와 구현 상태를 구분한다.
>
> Engineering specifications are written in Korean; stable IDs and API names use English. Main README: [English](../README.md) · [한국어](../readme/README.ko.md).

## 읽는 순서

| 순서 | 문서 | 답하는 질문 |
|---|---|---|
| 시작 | [합성기 v1 사용 설명서](data-generation/README.md) · [설계](data-generation/design.md) · [Feature](data-generation/schemas.md) | 데이터 준비부터 직접 실행하려면? |
| 1 | [Overall Plan](overall-plan/README.md) | 어떤 문제를 어디까지 구현하는가? |
| 2 | [전체 구조](overall-plan/architecture.md) · [노드 지도](overall-plan/node-map.md) | 어떤 단계와 분기로 연결되는가? |
| 3 | [Big Node](big-nodes/README.md) | 단계별 실행 순서·조건부 엣지·종료 정책은 무엇인가? |
| 4 | [Small Node](small-nodes/README.md) | 함수 하나의 책임·입출력·실패 조건은 무엇인가? |
| 5 | [Tool](tools/README.md) | 실제 DB·업무 기능을 어떤 계약으로 호출하는가? |
| 6 | [공통 계약](contracts/README.md) | 상태·버전·승인·증거를 어떻게 저장하고 검증하는가? |
| 7 | [평가](evaluation/README.md) | 정답·실패·비용을 어떤 근거로 판정하는가? |
| 8 | [구현 순서](overall-plan/implementation-plan.md) · [결정 기록](overall-plan/decisions.md) | 지금 무엇부터 만들고 무엇을 확인해야 하는가? |

## 문서의 책임

- **Overall Plan:** 범위, 전체 연결, 개발 우선순위의 정본.
- **Big Node B-01~B-05:** 업무 처리 생명주기. B-06은 평가용 별도 실행 경로다.
- **Small Node N-01~N-19:** 단일 책임 함수의 계약. 노드가 다음 노드를 직접 호출하지 않으며 분기는 Big Node가 소유한다.
- **Tool TOOL-01~TOOL-06:** 업무 서비스가 제공하는 제한된 기능. LLM 도구 루프와 코드 기반 워크플로가 같은 구현을 공유한다.
- **하네스 내부 서비스:** 체크포인트·예산·승인 요청·완료 기록·가상 시계. 모델이 임의로 호출하는 업무 Tool에 포함하지 않는다.
- **공통 계약:** 필드·상태 의미의 정본. 개별 문서는 이를 링크하고 자기 책임만 추가한다.

노드 19개는 LLM 호출 19회나 서버 19개를 의미하지 않는다. 금액·시간·권한·승인·완료 판정은 코드로 수행한다. 별도 Tiny Node 계층은 두지 않는다.

## 참고한 문서 형식과 적용 차이

[ReportAnchor JS의 docs](https://github.com/Jungminii-1114/ReportAnchor/tree/0d5ebd3f723dff99d80a04878aeaa821b17d0d83/docs)를 **위 커밋 기준으로 읽고**, Overall / Big / Small 분리와 공통 반환 계약 형식을 참고했다.

ReportAnchor의 Big Node는 목적별 문서 업무를 선택하는 구조다. 이 프로젝트의 Big Node는 한 대사 업무의 접수→조사→승인→실행·복구→검증 단계를 나눈다. 의미 검증용 LLM Judge, 검색 파이프라인, 기존 노드 ID의 의미를 그대로 가져오지 않는다.

## 변경 관리

상태는 `설계 초안 → 명세 검토 → 구현 → 사례 확인 → 비교 평가`로 갱신하고 구현·테스트·로그 링크가 있을 때만 진행 상태를 올린다. 계약을 바꾸면 영향받는 노드·Tool·QA ID와 결정 기록을 같이 갱신한다. 현재 문서에서 사용한 기본 예산·평가 규모는 제안값이며 파일럿 후 동결한다.
