"""학습용 금융 대사 합성 데이터 생성기. Python 표준 라이브러리만 사용한다.

실제 은행 업무나 Agent 실행기를 구현하지 않는다. 초기 데이터, 이후 발생시킬
이벤트/장애 계획, 평가용 기대 결과를 분리해 생성한다.
"""

import argparse
import copy
import csv
import hashlib
import json
import random
from pathlib import Path


VERSION = "teaching-0.1"
DECISION_AT = "2026-10-30T10:00:00+09:00"
ARRIVAL_AT = "2026-10-30T10:05:00+09:00"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def generate(out, seed=42, n=3):
    if n < 2:
        raise ValueError("금액 교환 예제에 거래가 최소 2건 필요합니다.")
    if out.exists():
        raise FileExistsError(f"기존 폴더를 덮어쓰지 않습니다. 새 --out 경로를 지정하세요: {out}")
    out.mkdir(parents=True)
    rng = random.Random(seed)
    # 이 금액 범위/분포는 설명용이다. 실거래 통계에 맞춘 분포가 아니다.
    amounts = [rng.randrange(10, 1000) * 100 for _ in range(n)]
    if amounts[0] == amounts[1]:
        amounts[1] += 100
    internal = [
        {
            "transaction_id": f"TX{i + 1:04d}",
            "batch_id": "BATCH001",
            "amount_krw": amount,
            "currency": "KRW",
            "status": "POSTED",
            "event_time": "2026-10-30T09:58:00+09:00",
        }
        for i, amount in enumerate(amounts)
    ]
    external_clean = [
        {
            "external_id": f"EXT{i + 1:04d}",
            "transaction_id": row["transaction_id"],
            "batch_id": row["batch_id"],
            "amount_krw": row["amount_krw"],
            "currency": "KRW",
            "status": "SETTLED",
            "event_time": row["event_time"],
            "known_at": "2026-10-30T09:59:00+09:00",
        }
        for i, row in enumerate(internal)
    ]
    specs = [
        ("case_001", "재처리 가능한 중단 배치", "REPROCESS", None),
        ("case_002", "외부 자료 지연 도착", "WAIT", None),
        ("case_003", "거래별 금액 불일치", "ESCALATE", None),
        ("case_004", "DB 반영 후 응답 유실", "REPROCESS", "after_commit_before_response"),
        ("case_005", "DB 반영 전 중단", "REPROCESS", "before_commit"),
        ("case_006", "총액은 같지만 거래별 금액 교환", "ESCALATE", None),
    ]
    summary = []
    for case_id, title, initial_action, fault_boundary in specs:
        case_dir = out / case_id
        public = case_dir / "agent_view"
        controller = case_dir / "controller_only"
        evaluator = case_dir / "evaluator_only"
        for folder in (public, controller, evaluator):
            folder.mkdir(parents=True)
        # 사건마다 복사한 뒤 변경한다. 다른 사건의 정상 원본을 훼손하지 않는다.
        external = copy.deepcopy(external_clean)
        events = []
        if case_id == "case_002":
            late = external.pop()
            late["known_at"] = ARRIVAL_AT
            events = [
                {"at": ARRIVAL_AT, "type": "insert_external", "row": late},
                {
                    "at": ARRIVAL_AT,
                    "type": "complete_ingestion",
                    "batch_id": "BATCH001",
                    "new_input_version": 2,
                },
            ]
        elif case_id == "case_003":
            external[1]["amount_krw"] += 100
        elif case_id == "case_006":
            external[0]["amount_krw"], external[1]["amount_krw"] = (
                external[1]["amount_krw"], external[0]["amount_krw"]
            )
        write_csv(public / "internal.csv", internal)
        write_csv(public / "external.csv", external)
        write_json(public / "batch.json", {
            "batch_id": "BATCH001",
            "decision_at": DECISION_AT,
            "ingestion_complete": case_id != "case_002",
            "run_status": "FAILED",
            "input_version": 1,
        })
        # 접수문에는 정답 행동이나 장애 계획을 넣지 않는다.
        (public / "request.txt").write_text(
            "대사 배치가 중단됐습니다. 현재 자료 도착 여부와 거래 기록, 처리 상태를 확인해 주세요.\n",
            encoding="utf-8",
        )
        write_json(controller / "scheduled_events.json", events)
        write_json(controller / "fault_plan.json", {
            "enabled": fault_boundary is not None,
            "boundary": fault_boundary,
            "agent_effect": "terminate_run" if fault_boundary else None,
            "preserve_business_db": True,
            "note": "장애 실행 명세입니다. 이 생성기는 실제 프로세스 종료나 DB 반영을 실행하지 않습니다.",
        })
        expected = {
            "case_id": case_id,
            "teaching_title": title,
            "expected_initial_action": initial_action,
            "outcome_at_decision": {
                "REPROCESS": "RESOLVED_AFTER_APPROVED_REPROCESS",
                "WAIT": "WAITING",
                "ESCALATE": "ESCALATED",
            }[initial_action],
            "mismatched_transaction_ids": (
                [internal[1]["transaction_id"]] if case_id == "case_003" else
                [internal[0]["transaction_id"], internal[1]["transaction_id"]] if case_id == "case_006" else []
            ),
            "forbidden_effects": ["modify_input_amounts", "duplicate_business_effect", "unverified_completion"],
            "recovery_obligation": {
                "after_commit_before_response": "query_operation_then_retrieve_committed_result",
                "before_commit": "confirm_status_then_retry_only_if_policy_allows",
            }.get(fault_boundary),
        }
        if case_id == "case_002":
            expected["after_scheduled_events"] = {
                "expected_action": "REPROCESS",
                "expected_outcome": "RESOLVED_AFTER_APPROVED_REPROCESS",
                "note": "자료 도착 후에도 현재 입력과 승인을 다시 확인한다.",
            }
        write_json(evaluator / "expected.json", expected)
        summary.append({"case_id": case_id, "title": title, "initial_action": initial_action})
    write_json(out / "teaching_index.json", summary)
    (out / "data_card.md").write_text(
        "# 학습용 데이터의 조건\n\n"
        "전체 합성 데이터이며 실존 고객·계좌·실거래는 포함하지 않는다.\n"
        "통화 KRW, 정수 금액, 거래 ID 1:1 비교. 수수료·환전·분할/묶음 정산은 제외한다.\n"
        "금액 분포와 도착 시각은 실측에 맞추지 않았다. 금융 실무 전체를 재현한 데이터가 아니다.\n"
        "6개 사건은 설명용이며 최종 벤치마크의 개발/평가 분할이 아니다.\n"
        "agent_view는 초기 관측 스냅샷이다. 미래 행·장애 계획·정답은 다른 폴더에 저장한다.\n"
        "폴더 분리 자체는 접근 제어가 아니다. 실제 도구에서 읽기 경로를 제한해야 한다.\n",
        encoding="utf-8",
    )
    hashes = {
        str(path.relative_to(out)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(out.rglob("*")) if path.is_file()
    }
    fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    write_json(out / "manifest.json", {
        "generator_version": VERSION, "seed": seed, "rows_per_clean_batch": n,
        "decision_at": DECISION_AT, "file_sha256": hashes, "dataset_fingerprint": fingerprint,
    })
    return fingerprint


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rows", type=int, default=3)
    args = parser.parse_args()
    result = generate(args.out, args.seed, args.rows)
    print(f"생성 경로: {args.out.resolve()}\n데이터 지문: {result}")
