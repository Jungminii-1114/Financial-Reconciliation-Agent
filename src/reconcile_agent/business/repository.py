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

from typing import Literal
from time import monotonic

from .errors import AdapterError

ProcessingState = Literal["FAILED", "RUNNING", "SUCCEEDED"]

@dataclass(frozen=True)
class BusinessScope:
    principal_id: str
    trial_id: str
    case_id: str
    batch_id: str

@dataclass(frozen=True)
class OperationRef:
    operation_id: str
    idempotency_key: str
    input_version: int
    action_digest: str


@dataclass(frozen=True)
class ExternalNote:
    note_id: str
    context_revision: int
    content: str
    origin: str
    known_at: str
    content_hash: str


# Repository가 반환하는 내부 조회 결과
@dataclass(frozen=True)
class CaseReadView:
    principal_id: str
    trial_id: str
    case_id: str
    batch_id: str

    business_now: str
    input_version: int
    input_digest: str
    ingestion_complete: bool
    processing_state: ProcessingState

    operation_refs: tuple[OperationRef, ...]
    external_notes: tuple[ExternalNote, ...]




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

    def get_case_view(self, *, requested_case_id: str, expected_scope: BusinessScope,
                      expected_policy_version=None, expected_config_hash=None, deadline=None) -> CaseReadView:
        """
        현재 호출 scope ↔ repository scope ↔ DB metadata 대조
        → 요청 case와 실제 대상 batch 검사
        → 입력·digest 검사
        → 처리 상태 조회
        → 작업 참조 조회
        → 외부 메모·hash 검사
        → CaseReadView 반환
        """

        return self._get_case_view(requested_case_id, expected_scope,
                                   expected_policy_version=expected_policy_version,
                                   expected_config_hash=expected_config_hash, deadline=deadline)

    def _get_case_view(self, requested_case_id, expected_scope, *,
                       expected_policy_version=None, expected_config_hash=None,
                       deadline=None):
        require(expected_scope == self._scope and requested_case_id == self._scope.case_id,
                "SCOPE_DENIED", "현재 대상 범위와 일치하지 않습니다.")
        deadline = deadline if deadline is not None else monotonic() + 2.0
        def check_deadline():
            require(monotonic() < deadline, "TOOL_TIMEOUT", "조회 제한 시간을 초과했습니다.")
        check_deadline()
        try:
            with connect(self._database, timeout=min(2.0, max(0.001, deadline-monotonic()))) as db, transaction(db):
                db.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
                check_business_version(db)
                meta, scope = metadata(db), self._scope
                require(all(meta.get(k) == getattr(scope, k) for k in
                            ("principal_id", "trial_id", "case_id", "batch_id")),
                        "SCOPE_DENIED", "현재 저장소의 실행·대상 범위와 일치하지 않습니다.")
                require(expected_config_hash is None or meta.get("runner_config_hash") == expected_config_hash,
                        "SCOPE_DENIED", "실행 설정이 일치하지 않습니다.")
                case = db.execute("SELECT target_batch_id,business_rule_version,policy_version FROM cases WHERE case_id=?",
                                  (scope.case_id,)).fetchone()
                require(case is not None, "CASE_UNAVAILABLE", "허용 범위에서 사건을 조회할 수 없습니다.")
                require(case["target_batch_id"] == scope.batch_id, "SCOPE_DENIED", "대상 배치가 일치하지 않습니다.")
                require(expected_policy_version is None or case["policy_version"] == expected_policy_version,
                        "SCOPE_DENIED", "현재 정책 버전이 일치하지 않습니다.")
                at = utc_time(meta["current_time"])
                require(meta["initial_observed_at"] <= at, "IMPORT_INVARIANT_ERROR", "현재 업무 시각이 올바르지 않습니다.")
                state = read_financial_state(db, scope.case_id, scope.batch_id, at)
                expected = input_digest(scope.case_id, scope.batch_id, case["business_rule_version"],
                                        state["internal"], state["external"], state["batch"]["ingestion_complete"])
                require(expected == state["batch"]["input_digest"], "IMPORT_INVARIANT_ERROR", "입력 digest가 다릅니다.")
                runtime = db.execute("SELECT run_status FROM batch_runtime WHERE case_id=? AND batch_id=?",
                                     (scope.case_id, scope.batch_id)).fetchone()
                require(runtime is not None, "IMPORT_INVARIANT_ERROR", "필수 배치 상태가 없습니다.")
                ops = db.execute("""SELECT operation_id,idempotency_key,input_version,action_digest FROM operations
                    WHERE case_id=? AND batch_id=? ORDER BY input_version,operation_id LIMIT 1025""",
                    (scope.case_id, scope.batch_id)).fetchall()
                require(len(ops) <= 1024, "OUTPUT_LIMIT_EXCEEDED", "작업 참조 개수 제한을 초과했습니다.")
                rows = db.execute("""SELECT note_id,context_revision,content_ref,content_hash,origin,known_at
                    FROM external_notes WHERE case_id=? AND known_at<=?
                    ORDER BY note_id,context_revision LIMIT 257""", (scope.case_id, at)).fetchall()
                require(len(rows) <= 256, "OUTPUT_LIMIT_EXCEEDED", "외부 메모 개수 제한을 초과했습니다.")
                notes = []
                for row in rows:
                    size = db.execute("SELECT length(CAST(content AS BLOB)),origin,known_at FROM content_objects WHERE content_ref=? AND case_id=?",
                                      (row["content_ref"], scope.case_id)).fetchone()
                    require(size is not None and size[1] == row["origin"] and size[2] == row["known_at"],
                            "CONTENT_INTEGRITY_ERROR", "원문 출처가 일치하지 않습니다.")
                    require(size[0] <= 16384, "OUTPUT_LIMIT_EXCEEDED", "외부 메모 크기 제한을 초과했습니다.")
                    content = read_content(db, row["content_ref"], scope.case_id, row["content_hash"])
                    notes.append(ExternalNote(row["note_id"], row["context_revision"], content,
                                              row["origin"], row["known_at"], row["content_hash"]))
                check_deadline()
                return CaseReadView(scope.principal_id, scope.trial_id, scope.case_id, scope.batch_id,
                                    at, state["batch"]["input_version"], expected,
                                    state["batch"]["ingestion_complete"], runtime[0],
                                    tuple(OperationRef(**dict(r)) for r in ops), tuple(notes))
        except AdapterError as exc:
            if monotonic() >= deadline:
                raise AdapterError("TOOL_TIMEOUT", "조회 제한 시간을 초과했습니다.") from exc
            raise
