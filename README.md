# Financial-Reconciliation-Agent

금융 대사 예외처리 Agent의 복구·재검증·완료 검증 하네스를 개발하는 개인 프로젝트입니다.

현재 이 저장소에는 **합성 데이터 생성·검사 학습용 예제**가 포함돼 있습니다. 실제 Agent, 업무 API, 승인·복구 하네스와 장애 실행기는 이후 구현할 범위입니다.

## 합성 데이터 첫 예제

- [사용 설명서](examples/synthetic_data_starter/README.md)
- [데이터 생성기](examples/synthetic_data_starter/generate.py)
- [독립된 파일 기반 데이터 검사기](examples/synthetic_data_starter/validate.py)
- [생성된 샘플 데이터](examples/synthetic_data_starter/sample_data)
- [검사 기록](examples/synthetic_data_starter/verification_report.json)

원화 정수 금액, 거래 ID 1:1 대사, 수수료·환전 없는 모의 업무를 가정합니다. 정상 데이터에서 출발해 자료 지연·금액 불일치·총액 유지 반례와 DB 반영 전후 중단 계획 등 6개 학습용 사건을 구성합니다.

## 빠른 실행

Python 3.10 이상을 권장합니다. 예제는 표준 라이브러리만 사용하므로 별도 패키지나 GPU가 필요하지 않습니다. 저장소 루트에서 실행합니다.

먼저 포함된 샘플을 검사합니다.

```bash
python3 examples/synthetic_data_starter/validate.py examples/synthetic_data_starter/sample_data
```

같은 시드로 새 데이터를 생성하고 검사할 수 있습니다. 출력 폴더가 이미 있으면 덮어쓰지 않으므로 새 이름을 사용하세요.

```bash
python3 examples/synthetic_data_starter/generate.py --out generated_data/experiment_001 --seed 42 --rows 3
python3 examples/synthetic_data_starter/validate.py generated_data/experiment_001
```

## 예제 구조

```text
examples/synthetic_data_starter/
├── README.md
├── generate.py
├── validate.py
├── verification_report.json
└── sample_data/
    ├── manifest.json
    ├── data_card.md
    ├── teaching_index.json
    └── case_001/ ... case_006/
        ├── agent_view/        초기 시점의 관측 데이터
        ├── controller_only/   미래 이벤트·장애 계획
        └── evaluator_only/    기대 행동·금지 효과
```

## 현재 예제의 범위

- 전체 샘플은 직접 생성한 합성 데이터이며 실존 고객·계좌·실거래를 포함하지 않습니다.
- 6개 사건은 설명용이며 정식 개발셋·최종 평가셋을 완성했다는 뜻이 아닙니다.
- 장애 계획 파일을 생성하지만 실제 DB 반영, 프로세스 중단, 재개는 실행하지 않습니다.
- 폴더 분리는 접근 제어가 아닙니다. 실제 Agent 도구를 연결할 때 정답과 미래 정보의 접근을 제한해야 합니다.
- SAML-D 원본 데이터나 변환기는 포함하지 않습니다.
- 데이터 정합성 검사는 모의 규칙의 검증이며 실제 은행 업무 전반의 타당성이나 성능 개선을 입증하지 않습니다.
