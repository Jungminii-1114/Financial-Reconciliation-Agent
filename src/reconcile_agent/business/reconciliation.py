"""Scoped simulated reconciliation. Only action/result tables can be written here."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from time import monotonic

from .connection import check_business_version, connect, transaction
from .errors import AdapterError, require
from .input_digest import action_digest, canonical, fingerprint, input_digest, utc_time
from .repository import BusinessScope, metadata, read_financial_state


@contextmanager
def scoped_transaction(repository, scope: BusinessScope, policy_version, config_hash,
                       *, write=False, deadline=None):
    require(scope == repository._scope, "SCOPE_DENIED", "저장소의 대상 범위가 다릅니다.")
    deadline = monotonic() + 5 if deadline is None else deadline
    require(monotonic() < deadline, "TOOL_TIMEOUT", "업무 처리 제한 시간을 초과했습니다.")
    try:
        with connect(repository._database, writable=write,
                     timeout=min(2.0, max(0.001, deadline - monotonic()))) as db:
            if write:
                db.execute("PRAGMA synchronous=FULL")
            db.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            with transaction(db, write=write):
                check_business_version(db)
                meta = metadata(db)
                require(all(meta.get(k) == getattr(scope, k)
                            for k in ("case_id", "batch_id", "principal_id", "trial_id"))
                        and meta.get("runner_config_hash") == config_hash,
                        "SCOPE_DENIED", "업무 DB의 대상 또는 실행 설정이 다릅니다.")
                policy = db.execute("""SELECT p.* FROM cases c JOIN case_policies p
                    ON p.case_id=c.case_id AND p.policy_version=c.policy_version
                    AND p.business_rule_version=c.business_rule_version
                    WHERE c.case_id=? AND c.target_batch_id=? AND c.policy_version=?""",
                                    (scope.case_id, scope.batch_id, policy_version)).fetchone()
                require(policy is not None, "SCOPE_DENIED", "업무 정책 또는 대상이 다릅니다.")
                yield db, meta, policy
                require(monotonic() < deadline, "TOOL_TIMEOUT", "업무 처리 제한 시간을 초과했습니다.")
    except AdapterError as exc:
        if exc.code == "STORAGE_ERROR" and monotonic() >= deadline:
            raise AdapterError("TOOL_TIMEOUT", "업무 처리 제한 시간을 초과했습니다.") from exc
        raise


def financial_snapshot(db, scope, meta, policy, as_of=None):
    at = meta["current_time"] if as_of is None else utc_time(as_of)
    require(meta["initial_observed_at"] <= at <= meta["current_time"],
            "ASOF_OUT_OF_RANGE", "허용된 업무 시각 범위를 벗어났습니다.")
    state = read_financial_state(db, scope.case_id, scope.batch_id, at)
    batch = state["batch"]
    expected = input_digest(scope.case_id, scope.batch_id, policy["business_rule_version"],
                            state["internal"], state["external"], batch["ingestion_complete"])
    require(expected == batch["input_digest"], "IMPORT_INVARIANT_ERROR", "입력 digest가 다릅니다.")
    return state, at


def row_ref(table, row):
    key = row["external_id"] if table == "settlement_history" else row["transaction_id"]
    return f'{table}:{key}:{row["row_revision"]}'


def compare_records(state):
    internal = {row["transaction_id"]: row for row in state["internal"]}
    external = {}
    for row in state["external"]:
        external.setdefault(row["transaction_id"], []).append(row)
    shared = internal.keys() & external.keys()
    mismatch = {tid for tid in shared if any(
        (r["amount_krw"], r["currency"]) != (internal[tid]["amount_krw"], internal[tid]["currency"])
        for r in external[tid])}
    pending = {tid for tid in shared if internal[tid]["status"] != "POSTED"
               or any(row["status"] != "SETTLED" for row in external[tid])}
    duplicates = {tid for tid, rows in external.items() if len(rows) != 1}
    return {
        "internal_count": len(internal),
        "external_count": len(state["external"]),
        "matched_count": len(shared - mismatch - pending - duplicates),
        "mismatched_ids": tuple(sorted(mismatch)),
        "missing_external_ids": tuple(sorted(internal.keys() - external.keys())),
        "unexpected_external_ids": tuple(sorted(external.keys() - internal.keys())),
        "duplicate_transaction_ids": tuple(sorted(duplicates)),
        "unsettled_transaction_ids": tuple(sorted(pending)),
        "ingestion_complete": state["batch"]["ingestion_complete"],
    }


def rule_action(facts, cutoff, at):
    if not facts["ingestion_complete"]:
        return "WAIT", "INPUT_NOT_COMPLETE"
    if not facts["internal_count"] or facts["matched_count"] != facts["internal_count"] or (
        facts["internal_count"] != facts["external_count"] or facts["unexpected_external_ids"]
    ):
        return "ESCALATE", "TRANSACTION_MISMATCH"
    if cutoff and at > cutoff:
        return "ESCALATE", "CUTOFF_PASSED"
    return "REPROCESS", "COMPLETE_MATCHING_INPUT"


def binding_for(scope, batch, policy):
    return {
        "case_id": scope.case_id, "batch_id": scope.batch_id,
        "expected_input_version": batch["input_version"], "expected_input_digest": batch["input_digest"],
        "action_digest": action_digest(scope.case_id, scope.batch_id, batch["input_version"],
                                       batch["input_digest"], policy["business_rule_version"],
                                       policy["policy_version"]),
    }


def require_binding(scope, state, policy, request):
    actual = binding_for(scope, state["batch"], policy)
    require(all(actual[key] == request[key] for key in actual if key != "action_digest"),
            "VERSION_CONFLICT", "현재 입력 버전 또는 digest가 계획과 다릅니다.")
    if "action_digest" in request:
        require(actual["action_digest"] == request["action_digest"],
                "IDEMPOTENCY_CONFLICT", "업무 action binding이 일치하지 않습니다.")
    return actual


def current_operation(db, scope, version):
    return db.execute("""SELECT * FROM operations WHERE case_id=? AND batch_id=?
        AND input_version=? AND action_kind='REPROCESS'""",
                      (scope.case_id, scope.batch_id, version)).fetchone()


def precheck(db, scope, meta, policy, request):
    state, at = financial_snapshot(db, scope, meta, policy)
    binding = require_binding(scope, state, policy, request)
    action, reason = rule_action(compare_records(state), policy["reprocess_cutoff_at"], at)
    existing = current_operation(db, scope, state["batch"]["input_version"])
    runtime = db.execute("SELECT run_status,last_operation_id FROM batch_runtime WHERE case_id=? AND batch_id=?",
                         (scope.case_id, scope.batch_id)).fetchone()
    require(runtime is not None, "IMPORT_INVARIANT_ERROR", "배치 업무 상태가 없습니다.")
    if existing:
        reason = "ALREADY_APPLIED" if existing["state"] == "SUCCEEDED" else "UNRESOLVED_EFFECT"
    elif runtime["run_status"] != "FAILED":
        # A successfully reconciled older version can be followed by a new version.
        previous = db.execute("""SELECT input_version FROM operations WHERE operation_id=?
            AND case_id=? AND batch_id=? AND state='SUCCEEDED'""",
                              (runtime["last_operation_id"], scope.case_id, scope.batch_id)).fetchone()
        if not (runtime["run_status"] == "SUCCEEDED" and previous
                and previous["input_version"] < state["batch"]["input_version"]):
            reason = "BATCH_NOT_ELIGIBLE"
    return {**binding, "eligible": action == "REPROCESS" and reason == "COMPLETE_MATCHING_INPUT",
            "reason_codes": (reason,), "approval_requirement": "REQUIRED", "checked_at": at}


def grant_approval(db, scope, meta, policy, request, actor):
    checked = precheck(db, scope, meta, policy, request)
    require(checked["eligible"], "NOT_ELIGIBLE", "현재 입력은 재처리할 수 없습니다.")
    at = checked["checked_at"]
    expires_at = utc_time((datetime.fromisoformat(at) + timedelta(seconds=policy["approval_ttl_seconds"])).isoformat())
    approval_id = "apr-" + fingerprint([scope.trial_id, checked["action_digest"], actor, at])[7:39]
    db.execute("""INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,'APPROVED',NULL)
        ON CONFLICT(approval_id) DO NOTHING""",
               (approval_id, scope.case_id, scope.batch_id, checked["expected_input_version"],
                checked["expected_input_digest"], checked["action_digest"], policy["policy_version"],
                actor, expires_at))
    return {**checked, "approval_id": approval_id, "expires_at": expires_at, "granted_by": actor}


def _approved(db, scope, policy, request, at):
    approval = db.execute("SELECT * FROM approvals WHERE approval_id=? AND case_id=? AND batch_id=?",
                          (request["approval_id"], scope.case_id, scope.batch_id)).fetchone()
    require(approval is not None, "APPROVAL_REQUIRED", "해당 범위의 승인이 필요합니다.")
    require(approval["status"] == "APPROVED" and approval["consumed_by"] is None and
            approval["granted_by"] and approval["granted_by"] != scope.principal_id and
            approval["expires_at"] and at < approval["expires_at"] and
            approval["policy_version"] == policy["policy_version"] and
            approval["input_version"] == request["expected_input_version"] and
            approval["input_digest"] == request["expected_input_digest"] and
            approval["action_digest"] == request["action_digest"],
            "APPROVAL_STALE", "승인의 대상·정책·유효 기간 또는 소비 상태가 일치하지 않습니다.")
    return approval


def paired_result(state):
    # Called only after a complete one-to-one comparison.
    external = {row["transaction_id"]: row for row in state["external"]}
    return [(row["transaction_id"], row_ref("transaction_history", row),
             row_ref("settlement_history", external[row["transaction_id"]]),
             row["amount_krw"], "MATCHED") for row in state["internal"]]


def verify_effect(db, scope, meta, policy, op):
    require(op["state"] == "SUCCEEDED" and op["result_ref"] and op["committed_at"],
            "EFFECT_UNKNOWN", "확정된 업무 처리 결과가 없습니다.")
    state, at = financial_snapshot(db, scope, meta, policy, op["committed_at"])
    binding = binding_for(scope, state["batch"], policy)
    approval = db.execute("SELECT * FROM approvals WHERE approval_id=?", (op["approval_id"],)).fetchone()
    approved = approval is not None and (
        approval["case_id"], approval["batch_id"], approval["input_version"], approval["input_digest"],
        approval["action_digest"], approval["policy_version"], approval["status"], approval["consumed_by"]
    ) == (
        scope.case_id, scope.batch_id, op["input_version"], binding["expected_input_digest"],
        op["action_digest"], policy["policy_version"], "CONSUMED", op["operation_id"]
    )
    require(binding["expected_input_version"] == op["input_version"] and
            binding["action_digest"] == op["action_digest"] and approved and
            approval["granted_by"] and approval["granted_by"] != scope.principal_id and
            approval["expires_at"] and at < approval["expires_at"] and
            rule_action(compare_records(state), policy["reprocess_cutoff_at"], at)[0] == "REPROCESS",
            "VERIFICATION_FAILED", "결과의 입력 또는 승인 근거가 일치하지 않습니다.")
    found = [tuple(row) for row in db.execute("""SELECT transaction_id,internal_ref,external_ref,amount_krw,match_status
        FROM reconciliation_results WHERE result_id=? AND operation_id=? ORDER BY transaction_id""",
        (op["result_ref"], op["operation_id"]))]
    require(found == paired_result(state) and db.execute("""SELECT COUNT(*) FROM reconciliation_results
        WHERE operation_id=? OR result_id=?""", (op["operation_id"], op["result_ref"])).fetchone()[0] == len(found),
        "VERIFICATION_FAILED", "거래별 결과가 원천과 일치하지 않습니다.")
    return {"operation_id": op["operation_id"], "idempotency_key": op["idempotency_key"],
            "case_id": scope.case_id, "batch_id": scope.batch_id, "input_version": op["input_version"],
            "action_digest": op["action_digest"], "operation_state": "SUCCEEDED", "observed_effect": "APPLIED",
            "result_ref": op["result_ref"], "committed_at": at, "verified_row_count": len(found)}


def execute(db, scope, meta, policy, request, before_commit=None):
    by_key = db.execute("SELECT * FROM operations WHERE idempotency_key=? AND case_id=? AND batch_id=?",
                        (request["idempotency_key"], scope.case_id, scope.batch_id)).fetchone()
    op = by_key or current_operation(db, scope, request["expected_input_version"])
    if op is not None:
        require(op["input_version"] == request["expected_input_version"] and
                op["action_digest"] == request["action_digest"], "IDEMPOTENCY_CONFLICT", "기존 작업 의미가 다릅니다.")
        effect = verify_effect(db, scope, meta, policy, op)
        batch = read_financial_state(db, scope.case_id, scope.batch_id, op["committed_at"])["batch"]
        require(batch["input_digest"] == request["expected_input_digest"],
                "VERSION_CONFLICT", "기존 작업의 입력 digest가 다릅니다.")
        return {**effect, "idempotent_replay": True}
    checked = precheck(db, scope, meta, policy, request)
    require(checked["eligible"], "NOT_ELIGIBLE", "현재 입력은 재처리할 수 없습니다.")
    at = checked["checked_at"]
    _approved(db, scope, policy, request, at)
    state, _ = financial_snapshot(db, scope, meta, policy)
    op_id = request["operation_id"]
    result_ref = "result-" + op_id
    db.execute("""INSERT INTO operations VALUES (?,?,?,?,?,'REPROCESS',?,?,'SUCCEEDED',?,?)""",
               (op_id, request["idempotency_key"], scope.case_id, scope.batch_id,
                request["expected_input_version"], request["action_digest"], request["approval_id"],
                result_ref, at))
    db.executemany("INSERT INTO reconciliation_results VALUES (?,?,?,?,?,?,?)",
                   ((result_ref, op_id, *row) for row in paired_result(state)))
    db.execute("UPDATE approvals SET status='CONSUMED',consumed_by=? WHERE approval_id=?", (op_id, request["approval_id"]))
    db.execute("UPDATE batch_runtime SET run_status='SUCCEEDED',last_operation_id=? WHERE case_id=? AND batch_id=?",
               (op_id, scope.case_id, scope.batch_id))
    effect = verify_effect(db, scope, meta, policy, current_operation(db, scope, request["expected_input_version"]))
    if before_commit:
        before_commit()
    return {**effect, "idempotent_replay": False}


def get_effect(db, scope, meta, policy, request, *, authoritative_absence=False):
    key = "operation_id" if "operation_id" in request else "idempotency_key"
    op = db.execute(f"SELECT * FROM operations WHERE {key}=? AND case_id=? AND batch_id=?",
                    (request[key], scope.case_id, scope.batch_id)).fetchone()
    if op is None and key == "idempotency_key" and authoritative_absence:
        op = current_operation(db, scope, request["expected_input_version"])
    if op is None:
        return {"operation_state": "NOT_FOUND", "observed_effect": "NOT_APPLIED" if authoritative_absence else "UNKNOWN",
                "safe_to_retry_same_key": authoritative_absence, "authoritative": authoritative_absence}
    require(op["input_version"] == request["expected_input_version"] and
            op["action_digest"] == request["expected_action_digest"],
            "IDEMPOTENCY_CONFLICT", "작업의 입력 버전 또는 action binding이 다릅니다.")
    if op["state"] != "SUCCEEDED":
        return {"operation_state": op["state"], "observed_effect": "UNKNOWN",
                "safe_to_retry_same_key": False, "authoritative": False}
    return {**verify_effect(db, scope, meta, policy, op),
            "safe_to_retry_same_key": False, "authoritative": True,
            "current_input_version": financial_snapshot(db, scope, meta, policy)[0]["batch"]["input_version"]}


def finalize(db, scope, meta, policy, run_id, request, outcome, reason, effect=None,
             *, evidence_ref=None, draft_id=None):
    state, at = financial_snapshot(db, scope, meta, policy)
    binding = require_binding(scope, state, policy, request)
    require(request["as_of"] == at, "VERSION_CONFLICT", "결정 시각이 변경됐습니다.")
    action, _ = rule_action(compare_records(state), policy["reprocess_cutoff_at"], at)
    op_id = None
    if outcome == "REPROCESSED":
        op = current_operation(db, scope, binding["expected_input_version"])
        require(op is not None and effect and effect["operation_id"] == op["operation_id"],
                "VERIFICATION_FAILED", "현재 입력의 완료 근거가 없습니다.")
        effect = verify_effect(db, scope, meta, policy, op)
        op_id = op["operation_id"]
    else:
        require((outcome == "WAITING_FOR_INPUT" and action == "WAIT") or
                (outcome == "ESCALATED" and (action == "ESCALATE" or reason in {
                    "UNRESOLVED_EFFECT", "ADVISOR_DISAGREEMENT", "BATCH_NOT_ELIGIBLE"
                })), "VERIFICATION_FAILED", "종료 결정이 현재 입력과 일치하지 않습니다.")
    decision = {
        "decision_id": "decision-" + fingerprint([scope.trial_id, run_id])[7:39],
        "logical_run_id": run_id, "case_id": scope.case_id, "batch_id": scope.batch_id,
        "decision_at": at, "input_version": binding["expected_input_version"],
        "input_digest": binding["expected_input_digest"], "outcome": outcome, "reason": reason,
        "operation_id": op_id, "result_ref": effect["result_ref"] if op_id else None,
        "evidence_ref": evidence_ref, "draft_id": draft_id,
    }
    digest = fingerprint(decision)
    db.execute("""INSERT INTO completion_decisions VALUES (?,?,?,?,?,?,?,?,?,?)""",
               (decision["decision_id"], run_id, scope.case_id, scope.batch_id, outcome, at,
                decision["input_version"], op_id, digest, canonical(decision).decode()))
    return {**decision, "verification_digest": digest}
