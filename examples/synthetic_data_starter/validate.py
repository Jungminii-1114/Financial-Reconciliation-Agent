"""생성기와 분리된 파일 기반 검사. 업무 규칙은 이 학습 예제에만 적용된다."""

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(root):
    manifest = read_json(root / "manifest.json")
    cases = sorted(root.glob("case_*"))
    require(len(cases) == 6, "학습 예제는 6개 사건이어야 합니다.")
    report = []
    for case in cases:
        public = case / "agent_view"
        require({p.name for p in public.iterdir()} == {
            "internal.csv", "external.csv", "batch.json", "request.txt"
        }, f"{case.name}: 관측 폴더에 허용하지 않은 파일이 있습니다.")
        internal = read_csv(public / "internal.csv")
        external = read_csv(public / "external.csv")
        batch = read_json(public / "batch.json")
        expected = read_json(case / "evaluator_only" / "expected.json")
        require(len(internal) == manifest["rows_per_clean_batch"], "내부 거래 건수가 다릅니다.")
        for rows, id_column in ((internal, "transaction_id"), (external, "external_id")):
            ids = [r[id_column] for r in rows]
            require(len(ids) == len(set(ids)), f"{case.name}: 기본키가 중복됩니다.")
            for row in rows:
                require(row["currency"] == "KRW" and int(row["amount_krw"]) > 0,
                        f"{case.name}: 통화 또는 금액 오류")
                require(row["batch_id"] == batch["batch_id"], "다른 배치의 행이 섞였습니다.")
                require(datetime.fromisoformat(row["event_time"]) <= datetime.fromisoformat(batch["decision_at"]),
                        "미래 거래가 초기 관측에 노출됐습니다.")
        for row in external:
            known_at = datetime.fromisoformat(row["known_at"])
            require(known_at <= datetime.fromisoformat(batch["decision_at"]),
                    "미래에 수신할 외부 자료가 초기 관측에 노출됐습니다.")
            require(known_at >= datetime.fromisoformat(row["event_time"]), "발생 전 자료 수신 오류")
        left = {r["transaction_id"]: int(r["amount_krw"]) for r in internal}
        right = {}
        for row in external:
            require(row["transaction_id"] not in right, "1:1 대응을 위반한 외부 중복 거래")
            right[row["transaction_id"]] = int(row["amount_krw"])
        require(set(right) <= set(left), "이 예제에는 알 수 없는 외부 거래 ID가 없어야 합니다.")
        mismatches = sorted(k for k in left.keys() & right.keys() if left[k] != right[k])
        # 정답 파일의 라벨을 신뢰하지 않고, 관측 데이터와 명시한 모의 규칙으로 재계산한다.
        if not batch["ingestion_complete"]:
            action = "WAIT"
        elif set(left) != set(right) or mismatches:
            action = "ESCALATE"
        else:
            require(batch["run_status"] == "FAILED", "재처리 예제는 실패 배치를 전제로 합니다.")
            action = "REPROCESS"
        require(action == expected["expected_initial_action"], f"{case.name}: 기대 행동 라벨이 데이터와 다릅니다.")
        require(mismatches == sorted(expected["mismatched_transaction_ids"]), "금액 불일치 정답 오류")
        events = read_json(case / "controller_only" / "scheduled_events.json")
        arrivals = [e for e in events if e["type"] == "insert_external"]
        if action == "WAIT":
            require(len(arrivals) == 1, "지연 사건의 도착 계획이 없습니다.")
            require(arrivals[0]["row"]["transaction_id"] not in right, "미래 행이 이미 노출됐습니다.")
            require(datetime.fromisoformat(arrivals[0]["at"]) > datetime.fromisoformat(batch["decision_at"]),
                    "도착 계획이 미래 시각이 아닙니다.")
            after = dict(right)
            after[arrivals[0]["row"]["transaction_id"]] = int(arrivals[0]["row"]["amount_krw"])
            require(after == left, "자료 도착 후 정상 입력이 된다는 명세와 다릅니다.")
            require(any(e["type"] == "complete_ingestion" for e in events), "도착 완료 이벤트 누락")
        if case.name == "case_006":
            require(sum(left.values()) == sum(right.values()) and len(mismatches) == 2,
                    "금액 교환은 합계를 유지하고 정확히 두 거래를 틀리게 해야 합니다.")
        fault = read_json(case / "controller_only" / "fault_plan.json")
        if fault["enabled"]:
            require(fault["boundary"] in {"before_commit", "after_commit_before_response"}, "장애 경계 오류")
            require(fault["preserve_business_db"], "이 예제는 업무 DB 유지가 전제입니다.")
            require(expected["recovery_obligation"] is not None, "복구 평가 명세 누락")
        report.append({"case_id": case.name, "initial_action": action,
                       "visible_external_rows": len(external), "mismatched_rows": len(mismatches)})
    # 같은 정상 입력에 DB 반영 전/후라는 서로 다른 장애 계획만 적용한다.
    for name in ("internal.csv", "external.csv", "batch.json", "request.txt"):
        require((root / "case_004" / "agent_view" / name).read_bytes() ==
                (root / "case_005" / "agent_view" / name).read_bytes(), "장애 쌍의 초기 조건이 다릅니다.")
    actual = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file() and p != root / "manifest.json"
    }
    require(actual == manifest["file_sha256"], "파일 추가·누락·변경 또는 해시 불일치")
    fingerprint = hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()
    require(fingerprint == manifest["dataset_fingerprint"], "데이터 지문 불일치")
    return {"validated_cases": len(report), "dataset_fingerprint": fingerprint, "cases": report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.dataset), ensure_ascii=False, indent=2))
