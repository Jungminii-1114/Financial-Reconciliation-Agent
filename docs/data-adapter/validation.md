# 적재 어댑터 검증과 한계

[사용 설명서](README.md) · [설계](design.md) · [실행 기록 JSON](verification_report.json)

## 1. 검사 명령과 범위

```bash
python3.11 -m unittest discover -s tests
```

기존 합성기 검사와 어댑터 검사를 함께 실행한다. 핵심은 출력 파일 존재뿐 아니라 **원본 보존·역검사·실패 원자성·재개 무변경·시간별 정답·역할 분리**다. 작은 3행 fixture에서 고의 오류와 경합을 검사하고, 기본 100행 생성물은 별도 CLI 실행으로 적재한다.

2026-11-05 로컬 실행 결과:

| 검사 | 결과 |
|---|---|
| 기존 합성기 39개 + 새 어댑터/사건/CLI 44개 | 전체 **83개 통과** |
| 3행 benchmark 후보의 전체 입력 이력 | 98조건·107시점의 findings/버전 일치 |
| 기본 100행 pilot의 실제 CLI 일괄 적재 후 재개·역조회 | 10개 trial, 내부 1,000행·외부 999행 |
| 기본 100행 benchmark 후보의 실제 CLI 일괄 적재 후 재개·역조회 | 98개 trial, 내부 9,800행·외부 9,797행 |
| 기본 지연 도착 demo | 외부 99→100, v1→v2, 사건 재전달 후 v2, 과거 조회는 99 |
| 모델 호출·Agent/업무 재처리 실행·인젝션 방어 실행 | 모두 0회/미실행 |

pilot과 benchmark에는 공유되는 개발 조건이 있어 두 trial 수를 독립 시나리오 수로 합산하지 않는다. benchmark의 표현·수치 변형도 독립 공격 계열은 아니다. 실행 환경·생성 데이터 지문·현재 코드 hash는 기계 판독 보고서에 기록한다.

## 2. IMP QA와 구현 증거

| QA | 검사 | 증거 |
|---|---|---|
| IMP-01/02 | 정상/지연 건수·완결성·초기 버전·실행 표 0건 | [test_trial_import.py](../../tests/test_trial_import.py) |
| IMP-03/04/05 | +100원 오류·합계 유지 교환·중복·미대응 ID·PENDING을 보존 | 같은 파일의 discrepancies 검사 |
| IMP-06 | 반영 전/후 계획은 같게 적재하고 실제 effect는 둘 다 0건 | pre/postcommit plan 검사 |
| IMP-07/08 | 정상/공격 금융 입력 동일·메모 원문 유지·업무 DB에 정답/계획/canary 없음 | security pair·private material 검사 |
| IMP-09/16 | 미지원 버전·고의 오답·링크·경로 이탈·잘못된 금액/FK | source 및 DB constraint 검사 |
| IMP-10 | 재초기화 거부·기존 DB 바이트 보존 | duplicate/concurrent prepare 검사 |
| IMP-11 | 잘못된 mapper 역검사, 실제 프로세스 READY 전 종료, rollback journal 복구 | readback·process exit·SQLite crash 검사 |
| IMP-12 | 재개에서 현재 입력·실행 상태·가상 시각 보존 | resume/event 검사, 수동 저장한 승인/operation 보존 검사 |
| IMP-13 | 도착 묶음의 version 한 번 증가·재전달/동시 전달 한 번 반영 | [test_trial_events.py](../../tests/test_trial_events.py) |
| IMP-14 | 정정/삭제 뒤 과거 as-of의 원래 revision 보존 | correction/duplicate removal 검사 |
| IMP-15 | 다른 trial/case/principal/config 및 교체한 private role 거부 | scope·schema·role 검사 |
| IMP-17 | hash가 맞아도 mapper의 +1원 오류를 역조회가 발견 | independent readback 검사 |
| IMP-18 | 새 trial에서 초기 금융 projection/digest 재현·실행 파일 격리 | same source/new trial 검사 |

추가로 시계 역행·미래 조회 거부, 입력 그룹 중간 실패의 전체 rollback, 같은 사건 ID의 다른 payload 거부, 잠금 상한, UTC offset 동등성, 원문/정책의 버전 없는 변조를 확인한다. [CLI 검사](../../tests/test_trial_cli.py)는 prepare→inspect→resume→advance와 일괄 준비를 실제 하위 프로세스에서 실행한다.

98개 benchmark 후보 조건을 각각 독립 trial로 적재하고, 사건을 적용한 뒤 과거 107개 시점의 거래별 findings·입력 버전을 원본 정답과 대조한다. 테스트만 비공개 정답을 읽으며 runtime 저장소와 mapper에는 기대 행동을 전달하지 않는다.

## 3. 결과의 의미

확인한 것은 schema·원본 mapping·역조회·금융 이력·원자적 사건 적용·명시적 재개의 저장소 동작이다. 수동으로 심은 승인/operation의 보존 검사는 업무 서비스가 실제로 승인·실행했다는 결과가 아니다. 실제 프로세스 종료 시험도 **어댑터 발행/SQLite 저장소 복구**이며 Agent 복구율을 뜻하지 않는다.

현재까지 모델 호출은 0회다. Agent·승인 발급/소비 서비스·멱등 재처리 API·보안 탐지·N-19 실험 채점은 구현 전이다. 최종 평가셋 동결, 실은행 분포 대표성, Colab 실행·장시간 부하·OS 보안 격리를 검증했다고 주장하지 않는다.

구체적인 최신 테스트 개수·기본 100행 일괄 적재 건수는 [실행 기록](verification_report.json)에 기록한다. 테스트 수를 독립 금융 시나리오 수나 Agent 성능으로 세지 않는다.
