"""Read-back checks separate from insertion. No evaluator answer is used here."""

from __future__ import annotations

from ..business.connection import check_database
from ..business.errors import require
from ..business.input_digest import financial_projection, fingerprint, input_digest, utc_time
from ..business.repository import read_content, read_financial_state


EXECUTION_TABLES = ("approvals", "operations", "reconciliation_results", "completion_decisions")


def verify_initial_import(db, fixture: dict) -> dict:
    case_id, batch = fixture["case_id"], fixture["batch"]
    at = utc_time(batch["decision_at"])
    case = db.execute("SELECT * FROM cases").fetchall()
    require(len(case) == 1 and case[0]["case_id"] == case_id and
            case[0]["target_batch_id"] == batch["batch_id"] and
            case[0]["business_rule_version"] == fixture["policy"]["business_rule_version"] and
            case[0]["policy_version"] == fixture["policy"]["policy_version"],
            "IMPORT_INVARIANT_ERROR", "적재 대상 또는 규칙 binding이 다릅니다.")
    observed = read_financial_state(db, case_id, batch["batch_id"], at)
    original = financial_projection(fixture["internal"], fixture["external"], batch["ingestion_complete"])
    actual = financial_projection(observed["internal"], observed["external"], observed["batch"]["ingestion_complete"])
    require(actual == original and observed["batch"]["input_version"] == batch["input_version"],
            "IMPORT_INVARIANT_ERROR", "원본 금융 입력과 DB 역조회 결과가 다릅니다.")
    wanted_digest = input_digest(case_id, batch["batch_id"], case[0]["business_rule_version"],
                                 fixture["internal"], fixture["external"], batch["ingestion_complete"])
    require(observed["batch"]["input_digest"] == wanted_digest,
            "IMPORT_INVARIANT_ERROR", "업무 입력 digest가 다릅니다.")
    policy = dict(db.execute("SELECT * FROM case_policies").fetchone())
    policy.pop("case_id")
    policy["approval_required"] = bool(policy["approval_required"])
    expected_policy = dict(fixture["policy"])
    if expected_policy["reprocess_cutoff_at"] is not None:
        expected_policy["reprocess_cutoff_at"] = utc_time(expected_policy["reprocess_cutoff_at"])
    require(policy == expected_policy and
            db.execute("SELECT run_status FROM batch_runtime").fetchone()[0] == batch["run_status"],
            "IMPORT_INVARIANT_ERROR", "정책 또는 배치 실행 상태가 다릅니다.")
    require(read_content(db, case[0]["request_ref"], case_id) == fixture["request"],
            "IMPORT_INVARIANT_ERROR", "접수 원문이 달라졌습니다.")
    notes = []
    for row in db.execute("SELECT * FROM external_notes ORDER BY note_id"):
        notes.append({"note_id": row["note_id"], "record_ref": row["record_ref"],
                      "origin": row["origin"], "known_at": row["known_at"],
                      "content": read_content(db, row["content_ref"], case_id)})
        require(row["context_revision"] == 1, "IMPORT_INVARIANT_ERROR", "초기 메모 revision 오류입니다.")
    expected_notes = [{**n, "known_at": utc_time(n["known_at"])} for n in fixture["notes"]]
    require(notes == sorted(expected_notes, key=lambda n: n["note_id"]),
            "IMPORT_INVARIANT_ERROR", "메모 원문·출처·시각이 다릅니다.")
    require(all(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
                for table in (*EXECUTION_TABLES, "input_mutations", "applied_event_groups")),
            "IMPORT_INVARIANT_ERROR", "초기 상태에 실행 결과나 미래 사건이 섞였습니다.")
    check_database(db, business=True)
    return {"passed": True, "initial_input_digest": wanted_digest,
            "financial_projection_digest": fingerprint(original),
            "row_counts": {"internal": len(observed["internal"]), "external": len(observed["external"])},
            "initial_observed_at": at, "model_requests": 0, "agent_runtime_executed": False}
