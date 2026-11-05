"""Read-only, case-bound input access for future Tool 1/2 implementations.

Scope is constructed by trusted application code, not authenticated by this library.
No arbitrary SQL, private-store path, or expected-action label is exposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .connection import check_business_version, connect, transaction
from .errors import require
from .input_digest import input_digest, sha256, utc_time


@dataclass(frozen=True)
class BusinessScope:
    principal_id: str
    trial_id: str
    case_id: str
    batch_id: str


def metadata(db) -> dict[str, str]:
    return dict(db.execute("SELECT key,value FROM trial_metadata"))


def read_financial_state(db, case_id: str, batch_id: str, at: str) -> dict:
    batch = db.execute("""SELECT input_version,input_digest,ingestion_complete,known_at
        FROM batch_versions WHERE case_id=? AND batch_id=? AND known_at<=?
        ORDER BY input_version DESC LIMIT 1""", (case_id, batch_id, at)).fetchone()
    require(batch is not None, "ASOF_OUT_OF_RANGE", "사용 가능한 초기 관측 이전입니다.")
    result = {"batch": dict(batch)}
    result["batch"]["ingestion_complete"] = bool(batch["ingestion_complete"])
    for kind, table, extra, order in (
        ("internal", "transaction_history", "", "transaction_id"),
        ("external", "settlement_history", "external_id,", "external_id"),
    ):
        # Every identifier below is a code-owned constant, never a caller SQL fragment.
        result[kind] = [dict(r) for r in db.execute(f"""SELECT {extra}transaction_id,batch_id,
            amount_krw,currency,status,event_time,known_at,row_revision FROM {table}
            WHERE case_id=? AND batch_id=? AND known_at<=?
            AND (superseded_known_at IS NULL OR ?<superseded_known_at)
            ORDER BY {order}""", (case_id, batch_id, at, at))]
    return result


def read_content(db, ref: str, case_id: str, expected_hash: str | None = None) -> str:
    row = db.execute("SELECT content,content_hash FROM content_objects WHERE content_ref=? AND case_id=?",
                     (ref, case_id)).fetchone()
    require(row is not None and sha256(row["content"].encode("utf-8")) == row["content_hash"] and
            (expected_hash is None or expected_hash == row["content_hash"]),
            "CONTENT_INTEGRITY_ERROR", "원문 참조의 범위 또는 해시가 맞지 않습니다.")
    return row["content"]


class BusinessRepository:
    def __init__(self, database: Path, scope: BusinessScope):
        self._database = Path(database)
        self._scope = scope

    def snapshot(self, *, as_of: str | None = None) -> dict:
        """Return a consistent input snapshot; an operator API, not a model Tool yet."""
        with connect(self._database) as db, transaction(db):
            check_business_version(db)
            meta = metadata(db)
            scope = self._scope
            require(all(meta.get(k) == getattr(scope, k) for k in
                        ("principal_id", "trial_id", "case_id", "batch_id")),
                    "SCOPE_DENIED", "현재 저장소의 실행·대상 범위와 일치하지 않습니다.")
            at = utc_time(as_of) if as_of is not None else meta["current_time"]
            require(meta["initial_observed_at"] <= at <= meta["current_time"],
                    "ASOF_OUT_OF_RANGE", "초기 관측부터 현재 가상 시각까지만 조회할 수 있습니다.")
            case = dict(db.execute("SELECT * FROM cases WHERE case_id=?", (scope.case_id,)).fetchone())
            state = read_financial_state(db, scope.case_id, scope.batch_id, at)
            expected = input_digest(scope.case_id, scope.batch_id, case["business_rule_version"],
                                    state["internal"], state["external"], state["batch"]["ingestion_complete"])
            require(expected == state["batch"]["input_digest"],
                    "IMPORT_INVARIANT_ERROR", "입력과 저장된 업무 digest가 다릅니다.")
            policy = dict(db.execute("SELECT * FROM case_policies WHERE case_id=? AND policy_version=?",
                                     (scope.case_id, case["policy_version"])).fetchone())
            policy.pop("case_id")
            policy["approval_required"] = bool(policy["approval_required"])
            notes = []
            for stored in db.execute("SELECT * FROM external_notes WHERE case_id=? AND known_at<=? ORDER BY note_id,context_revision",
                                     (scope.case_id, at)):
                note = dict(stored)
                note.pop("case_id")
                note["content"] = read_content(db, note["content_ref"], scope.case_id, note["content_hash"])
                notes.append(note)
            runtime = db.execute("SELECT run_status FROM batch_runtime WHERE case_id=? AND batch_id=?",
                                 (scope.case_id, scope.batch_id)).fetchone()
            return {"case_id": scope.case_id, "batch_id": scope.batch_id, "as_of": at,
                    "batch": state["batch"], "internal": state["internal"], "external": state["external"],
                    "policy": policy, "request": read_content(db, case["request_ref"], scope.case_id),
                    "external_notes": notes, "processing_state_current": runtime[0]}
