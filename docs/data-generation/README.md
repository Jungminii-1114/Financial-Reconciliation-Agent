# 자체 합성 데이터 생성기 v1 — 사용 설명서

〖한국어 | [English](README.en.md)〗 · [프로젝트 홈](../../readme/README.ko.md) · [설계 문서 홈](../README.md)

**상태: 구현·로컬 검사 완료, 2026-11-05.** Python으로 대응 거래를 만들고, 사건을 주입하고, 독립 SQLite 검사기로 검사한 뒤 CSV·JSON·HTML·SQLite 파일을 출력한다. 은행 고객 정보나 GPU·API 키·유료 서비스가 필요하지 않다.

이전 [교육용 6사례](../../examples/synthetic_data_starter/README.md)는 작은 예제다. 이 문서는 새 `src/reconcile_agent/synthetic/` 구현의 사용 설명서다. 두 버전은 스키마가 다르므로 서로의 검사기로 검사하지 않는다.

## 1. SQLite를 몰라도 시작할 수 있다

SQLite는 별도의 DB 서버를 설치하지 않고 **하나의 파일에 표를 저장하는 DB**다. Python 표준 라이브러리에 포함된 `sqlite3`가 파일을 자동으로 만든다. 이 단계에서는 SQL을 작성하거나 DB 프로그램을 설치할 필요가 없다.

처음에는 생성된 HTML과 CSV만 확인해도 된다. SQLite는 같은 자료를 다음 업무 서비스 구현에 연결하거나 표 간 관계를 점검하는 데 사용한다. 현재 출력하는 DB는 **사람이 확인하는 입력 스냅샷**이며, 승인·작업·대사 결과를 처리하는 업무 DB 전체가 구현된 것은 아니다.

## 2. 처음 실행: 아래 두 명령

터미널의 현재 위치가 이 저장소의 최상위 폴더여야 한다. Python **3.10 이상**, 권장 **3.11**이 필요하다. 아래 명령은 Python 3.11 환경에서 확인했다.

```bash
python3.11 --version
python3.11 scripts/dataset.py build --config configs/synthetic-pilot.json --out generated_data/pilot_v1
```

`pip install`은 필요 없다. `python3`이 이미 3.10 이상인 환경에서는 `python3.11` 대신 `python3`을 사용할 수 있다. Colab에서도 저장소 루트에서 `!python scripts/dataset.py ...`로 실행할 수 있으며, GPU 활성화는 필요 없다. Colab 실행 자체는 이번 로컬 검사에 포함하지 않았다.

완료 시 **10사례·정상/공격 2쌍·11개 시점 검증 통과**와 파일 경로가 출력된다. `REPROCESS`는 재처리할 조건이 갖춰졌다는 명세상 제안이며 실제 업무 실행 완료를 뜻하지 않는다.

| 확인할 것 | 파일·위치 | 사용하는 방법 |
|---|---|---|
| 사람이 먼저 읽을 화면 | `generated_data/pilot_v1/operator_view/index.html` | 브라우저에서 연다. 사례를 클릭하면 처음 3행과 정답·시점이 펼쳐진다. |
| 거래 전체 | `cases/<case_id>/agent_view/internal.csv`, `external.csv` | 텍스트 편집기나 스프레드시트에서 확인한다. |
| 입력 DB 파일 | `operator_view/initial_snapshot.sqlite` | 자동 생성됨. 지금 당장 별도 프로그램으로 열 필요는 없다. |
| 검증 결과 | `operator_view/validation_report.json` | 사례 수·검사 시점·지문·사례별 행 수를 확인한다. |
| 사례 ID ↔ 의미 | `catalog.json` | 사람·평가자 전용. 모델에 전달하지 않는다. |

이미 존재하는 출력 경로는 덮어쓰지 않는다. 재실행할 때 `pilot_v2` 등 새 경로를 사용한다. 생성물은 `.gitignore`의 `/generated_data/`에 의해 Git에 올라가지 않는다. 코드는 Git에 보관하고 데이터는 설정과 시드로 재생성한다.

## 3. 무엇이 얼마나 생성되는가?

기본적으로 **사례마다 초기 내부 거래 100건**을 만들고 대응하는 외부 기록을 파생한다. 지연·중복·추가 거래에 따라 외부 건수나 후속 시점 건수는 달라진다.

| 프로필 | 업무 사례 | 보안 정상/공격 쌍 | 총 폴더 | 용도 |
|---|---:|---:|---:|---|
| `pilot` | 개발 6개 | 개발 2쌍 = 4조건 | 10 | 구조 이해·빠른 디버깅 |
| `benchmark` | 개발 6개 + 평가 후보 12개 | 개발 20쌍 + 평가 후보 20쌍 = 80조건 | 98 | 평가 후보 구성·시나리오 검토 |

`benchmark`라는 이름은 최종 벤치마크가 동결됐다는 뜻이 아니다. 보안 40쌍은 **서로 다른 공격 40종이 아니다.** 개발 2개·평가 후보 4개 공격 계열의 금액·표현 변형이다. 실제 평가에는 계열·조건·반복 실행을 구분해 집계한다.

```bash
python3.11 scripts/dataset.py build --config configs/synthetic-benchmark.json --out generated_data/benchmark_candidate_v1
```

빠르게 3건씩 확인하려면 다음처럼 일부 설정만 덮어쓴다.

```bash
python3.11 scripts/dataset.py build --config configs/synthetic-pilot.json --rows 3 --seed 42 --out generated_data/tiny_v1
```

## 4. 사례를 조회하고 시간 이후의 상태 확인하기

```bash
python3.11 scripts/dataset.py validate generated_data/pilot_v1
python3.11 scripts/dataset.py list generated_data/pilot_v1
python3.11 scripts/dataset.py inspect generated_data/pilot_v1 --case first
```

`first`는 정상 일치 업무 사례다. `list`에서 `delayed_arrival` 행의 `c_...` ID를 찾아 아래 `<CASE_ID>`를 실제 값으로 바꾼다. 꺾쇠괄호까지 입력하지 않는다.

```bash
python3.11 scripts/dataset.py inspect generated_data/pilot_v1 --case <CASE_ID> --at 2026-11-05T10:04:59+09:00
python3.11 scripts/dataset.py inspect generated_data/pilot_v1 --case <CASE_ID> --at 2026-11-05T10:05:00+09:00
```

기본 기준 시각은 `2026-11-05 10:00 KST`다. 지연 사례는 10:04:59까지 마지막 외부 행이 보이지 않고, 10:05:00에 도착하며 `input_version`이 1에서 2로 바뀐다. 원본 파일은 바꾸지 않고 해당 시점의 사본을 재구성한다. 초기 스냅샷 이전은 복원하지 않는다.

정답·분류가 포함되는 `list`, `inspect`, HTML은 사람 전용이다. 미래의 Agent에는 전체 디렉터리 대신 허용된 사례·시점의 자료만 Tool로 제공해야 한다.

## 5. SQLite 내보내기도 자동으로

`build`가 만든 DB는 모든 사례의 **초기 상태**를 포함한다. 시간 이후의 상태나 한 사례만 따로 저장하려면 다음을 실행한다.

```bash
python3.11 scripts/dataset.py export-sqlite generated_data/pilot_v1 --out generated_data/pilot_initial.sqlite
python3.11 scripts/dataset.py export-sqlite generated_data/pilot_v1 --case <CASE_ID> --at 2026-11-05T10:05:00+09:00 --out generated_data/one_case_after.sqlite
```

출력 DB는 기존 데이터셋 폴더 **바깥**에 저장한다. 기존 파일을 덮어쓰지 않는다. 이 명령은 시간 이벤트에 따른 입력 자료를 복원할 뿐, 장애를 일으키거나 재처리 결과·승인·완료 기록을 생성하지 않는다. 보호 문자열·공격 라벨·정답은 DB에 넣지 않는다.

## 6. 설정값

| 설정 | 의미 | 기본값·제약 |
|---|---|---|
| `seed` | 재현 가능한 난수 생성의 기준 | 42, 0 이상 `2**63` 미만 정수 |
| `rows_per_batch` | 사례마다 초기 내부 거래 건수 | 100, 3~5000 |
| `profile` | 포함할 사건 구성 | `pilot` 또는 `benchmark` |
| `decision_at` | 최초 관측·판단 시각 | `2026-11-05T10:00:00+09:00`, offset 필수 |
| `security_pairs_per_split` | 포함된 split별 정상/공격 쌍 수 | pilot 2, benchmark 20; 각각 최소 2/4, 최대 100 |

CLI의 `--rows`, `--seed`, `--profile`, `--decision-at`, `--security-pairs`는 JSON의 해당 값만 덮어쓴다. JSON에서 쌍 수를 명시했다면 `--profile`만 바꿔도 그 쌍 수는 유지되므로, 전체 프로필 변경은 별도 설정 파일 사용을 권장한다. 잘못된 키·음수·실수·시간대 없는 시각은 거부한다.

같은 코드·설정·시드라면 코어 CSV/JSON과 manifest가 재현된다. 소스 코드도 지문에 포함하므로 코드 수정 후에는 지문이 달라진다. 코어와 별도로 HTML·SQLite·검증 보고서의 해시를 저장한다.

## 7. 다음 문서

| 문서 | 내용 |
|---|---|
| [설계·생성 방법](design.md) | 대응 거래 생성 → 통제된 변형 → 시점별 정답 → 독립 검증 |
| [Feature·파일·SQLite 스키마](schemas.md) | 모든 데이터 필드와 공개 범위, DB 6개 테이블 |
| [사건·보안 쌍 카탈로그](scenarios.md) | 업무 18종, 보안 6계열, 네 핵심 기능과의 연결 |
| [검증·테스트·한계](validation.md) | 잘못된 데이터/정답을 잡는 검사, 실행 결과, 남은 일 |

[버전 지정 업무 DB 적재 어댑터](../data-adapter/README.md)는 초기 적재·DB 상태 재개·시점별 읽기·입력 사건 적용까지 구현됐다. 다음은 R0·TOOL-01/02·독립 결과 검사기이며, 승인·멱등 실행·실제 Agent 중단/재개·보안 통제는 이후 단계다. [전체 구현 순서](../overall-plan/implementation-plan.md)에서 관리한다.
