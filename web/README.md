# Financial Reconciliation — Web UI Prototype

다운로드한 `financial_reconciliation_agent.tsx`의 화면 구성을 유지하며 실행 환경과 인터랙션을 보완한 React 프로토타입이다. Python Agent와 연결할 화면 설계를 검토하기 위한 웹이다.

**현재 브라우저는 Python·SQLite API에 연결되지 않는다.** Cases·Agent·Recovery·역할·감사 항목은 로컬 UI 예시이며, Evaluations만 저장소에 기록된 실제 검증 자료의 정적 사본을 표시한다. 모의 승인·복구 버튼은 React 상태만 바꾼다.

## 실행

Node.js 20.19 이상과 npm이 필요하다. 저장소 루트에서:

```bash
cd web
npm ci
npm run dev
```

다운로드 ZIP을 사용할 때는 압축을 푼 `Financial_Reconciliation_Web` 폴더에서 `cd web`을 생략하고 실행한다.

터미널에 표시되는 localhost 주소를 연다. TSX 파일을 직접 더블클릭해서 여는 형태는 아니다.

```bash
npm run build
npm run preview
```

잠금 파일 `package-lock.json`을 포함했다. 원본 다운로드 파일은 보관하고 수정본은 이 디렉터리에서 관리한다.

## 화면과 데이터

| 화면 | 현재 동작 |
|---|---|
| Overview | 5개 예시 사례에서 계산한 건수·상태 비율 |
| Cases | case/batch/digest 검색, 상태 필터, 선택한 사건 상세 표시 |
| Agent Workspace | 타이머로 진행하는 UI 데모. 일치 입력 예시 `case_1026`에서 Approver만 모의 승인 가능 |
| Recovery Lab | Start → Inject → Resume 순서의 UI 상태 전환. 선택 사건·설정 변경 시 초기화 |
| Evaluations | 기존 83개 + Tool 검사 28개 = 111개 회귀 검사, CLI v1→v2 관측값의 정적 표시 |
| Audit Trail | 예시 호출 목록. Python runtime ledger의 실제 로그가 아님 |
| Settings | 실제 여섯 Tool 계약 이름·구현 상태와 로컬 데모 역할 표시 |

표시된 역할 선택은 서버 인증이 아니다. Agent의 reasoning 텍스트는 고정된 UI 설명이다. `case_1024` 등의 웹 예시 ID·digest는 실제 pilot fixture와 별개다. 현재 Python 데이터 계약의 금액은 정수 KRW이며, 실제 재처리는 원천 금액·수수료를 수정하는 기능이 아니다.

## 실제 검증 근거

- [Tool 1 정식 보고서](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/f6fe614/docs/tools/reports/tool-01/validation-report.md)
- [원시 결과](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/f6fe614/docs/tools/reports/tool-01/results.json)
- [CLI 재현 관측](https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/f6fe614/docs/tools/reports/tool-01/cli-replay.json)

`src/data/results.json`, `src/data/cli-replay.json`은 보고서 커밋 `f6fe614`의 동일 파일 사본이다. 대상 구현 커밋은 `97d3400`이다. 시간 경과나 화면 새로고침에 따라 자동 갱신되지 않는다. 보고서가 바뀌면 사본도 함께 갱신해야 한다.

111개에는 28개 Tool 검사가 이미 포함되어 있다. 이는 Agent 정확도·복구율·인젝션 방어율이 아니다. Agent 런타임·승인/실행 서비스·실제 공격 방어와 브라우저 API 연결은 후속 개발 범위다.

## 검토 결과

[REVIEW.md](REVIEW.md)에 수정 사항과 브라우저 검증 범위를 기록했다. 모바일에서는 상단 메뉴 버튼으로 화면을 전환한다. 검색 버튼 또는 Cmd/Ctrl+K로 Cases 검색창을 열 수 있다. 승인 모달은 Escape로 닫을 수 있다.
