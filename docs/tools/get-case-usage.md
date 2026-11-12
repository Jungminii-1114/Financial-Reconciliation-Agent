# TOOL-01 get_case — 로컬 실행과 검증

**구현 2026-11-12.** 모델 업무 인자는 case_id 하나다. 로컬 runner가 별도로 TrustedCallContext와 범위 제한 repository를 제공한다. LLM·API 키·추가 패키지는 필요 없다. Python 3.10 이상, 로컬 검증은 Python 3.11이다.

[Tool 명세](TOOL-01.md) · [공통 호출 계약](../contracts/tool-call-contract.md) · [DB 어댑터](../data-adapter/README.md)

## 구현 범위

- 요청·권한·실제 trial/principal/case/batch/config/policy binding 검사.
- 하나의 읽기 트랜잭션에서 입력 digest, 처리 상태, 과거 버전을 포함한 작업 참조, 알려진 메모·원문 hash 검사.
- 공개 data allowlist와 네 필드 NodeResult, 오류·미수신 자료·실제 빈 결과 구분.
- 업무 DB와 별개인 runtime SQLite ledger에 호출 예산 원자적 예약·시도/진입/결과 기록. 실패·새 세션도 동일 예산 그룹을 사용한다.
- TOOL-01만 등록한 최소 dispatcher. 미등록 Tool은 거부한다. HTTP/MCP·LLM 루프·Agent checkpoint·승인·재처리·실제 인젝션 방어는 구현하지 않았다.

`ctx`는 신뢰된 로컬 코드의 문맥이지 인증 토큰이 아니다. `scope_version`은 로컬 문맥 식별자이며 외부 인증/권한 철회 서비스를 구현한 것이 아니다. 메모를 원문 그대로 출처와 함께 반환하는 것은 탐지·방어 성능을 입증하지 않는다.

## 바로 실행하기

저장소 루트에서 실행한다. 출력 경로가 이미 있으면 기존 데이터를 덮어쓰지 말고 준비된 경로를 재사용하거나 새 경로를 선택한다.

```bash
python3.11 scripts/dataset.py build --config configs/synthetic-pilot.json --out generated_data/pilot_v1
python3.11 scripts/trial.py prepare --dataset generated_data/pilot_v1 --case c_2e8b6b7c4702 --trial-id tool1-delay --out generated_data/trials/tool1-delay
python3.11 scripts/get_case.py --trial generated_data/trials/tool1-delay --trial-id tool1-delay --case c_2e8b6b7c4702 --ledger generated_data/tool_runtime/calls.sqlite
```

위 ID는 기본 seed=42 pilot의 delayed_arrival 사례다. 다른 seed에서는 dataset list로 실제 ID를 확인한다. 초기 응답은 `status=success`, `input_version=1`, `ingestion_complete=false`, `processing_state=FAILED`다. Tool이 WAIT를 결정하는 것은 아니다.

```bash
python3.11 scripts/trial.py advance --trial generated_data/trials/tool1-delay --trial-id tool1-delay --case c_2e8b6b7c4702 --to 2026-11-05T10:05:00+09:00
python3.11 scripts/get_case.py --trial generated_data/trials/tool1-delay --trial-id tool1-delay --case c_2e8b6b7c4702 --ledger generated_data/tool_runtime/calls.sqlite --session-id session-2
```

이후 v2·수신 완료 true가 된다. 같은 ledger와 trial은 세션을 바꿔도 기본 총 20회 예산을 공유한다. 예산 변경으로 기존 그룹을 초기화할 수 없다. ledger를 새로 만드는 행위는 별도 실험 설정이며 복구 비교에서 예산 우회로 사용하면 안 된다.

ledger는 trial 폴더 밖의 별도 경로에 둔다. CLI는 trial 내부 ledger 생성을 거부한다. 설정 H 등으로 적재했다면 `--principal`, `--setting`, `--repeat`을 적재 때와 동일하게 지정한다. CLI 성공은 0, 업무·설정 오류는 2다.

## 코드의 위치

| 파일 | 역할 |
|---|---|
| [contracts.py](../../src/reconcile_agent/tools/contracts.py) | 공통 타입·문맥 검증 |
| [get_case.py](../../src/reconcile_agent/tools/get_case.py) | 요청·권한·응답 검증과 조립 |
| [repository.py](../../src/reconcile_agent/business/repository.py) | 같은 스냅샷의 업무 관측 |
| [errors.py](../../src/reconcile_agent/tools/errors.py) | payload 없는 오류 매핑 |
| [dispatcher.py](../../src/reconcile_agent/tools/dispatcher.py) | 제한된 Tool 등록·시도·반환 연결 |
| [ledger.py](../../src/reconcile_agent/tools/ledger.py) | 지속 호출 예산과 payload-free 감사 기록 |
| [runner.py](../../src/reconcile_agent/tools/runner.py) | 로컬 실험 문맥 생성 |

작성 중이던 주석·docstring은 유지했다. 최초 주석의 `c_2e8b2kdpo10c`는 현재 ID 규칙에 맞지 않아 거부되는 설명용 문자열이다. 실제 실행은 위 정상 ID를 사용한다. frozen dataclass와 타입 힌트만으로 검증하지 않고 별도 검사 함수를 사용한다.

## 상한·시간·실패 처리

- 인자 32 KiB, 응답 전체 UTF-8 JSON 기본 최대 128 KiB. 금융 정보·JSON 중간을 잘라 반환하지 않는다.
- 작업 참조 최대 1,024개, 현재까지 알려진 메모 revision 최대 256개, 메모당 UTF-8 16 KiB. 초과는 명시적 오류다. 모든 알려진 revision을 반환하며 과거 메모를 현재 승인으로 해석하지 않는다.
- 읽기 deadline 제안 2초. SQLite progress handler와 남은 잠금 대기·완료 시각 검사를 사용한다. Python digest 계산까지 강제 중단하는 별도 프로세스 격리는 없다. ledger IO의 잠금 대기는 별도 최대 2초이며 전체 dispatcher의 2초 hard deadline을 보장하지 않는다.
- 호출 ledger에는 외부 메모·인자 원문을 저장하지 않는다. 최종 공개 응답의 hash와 오류 코드·실행 시간·호출 식별자를 저장한다. 호출 반환은 모델/사용자의 실제 수신 확인이 아니며 delivery를 성공으로 기록하지 않는다.
- 기록 실패는 공개 성공으로 숨기지 않는다. 중단된 RESERVED 호출을 자동 재발행하지 않으며 미완료 기록은 유지한다. 이는 Agent 복구 서비스 구현이 아니다.

## 검증

```bash
python3.11 -m unittest discover -s tests
python3.11 -m unittest discover -s tests -p test_get_case.py
```

추가 Tool 검사는 실제 생성 fixture·trial을 대상으로 정상/오류 필드, 대상 쌍·실행 설정, 입력 변조, 미래 메모, 지연 도착, 과거 작업 참조, 메모 원문 hash, 작은 응답 상한, 사전/사후 감사 실패, 동시 예산 예약·세션 재개·호출 ID 재사용·CLI를 확인한다.

작업 참조 검사의 승인/작업 행은 검사 코드가 주입한 저장 상태다. 실제 재처리 서비스 실행·모델 성능·공격 방어율로 보고하지 않는다. 공통 계약의 CALL-19~24 쓰기·Agent 복구·전체 비교군 평가를 모두 완료한 것은 아니다.

검증 기록: 기존 83개와 신규 Tool 검사 28개를 합쳐 **111개 테스트 통과**. 기본 100행 pilot의 지연 사례를 별도 CLI로 적재·조회·advance·다른 session 재조회하여 `v1/미완료 → v2/완료`를 확인했다. 실제 모델 호출은 0회다.
