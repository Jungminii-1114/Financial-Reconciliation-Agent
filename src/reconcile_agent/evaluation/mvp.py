"""Independent SQL check of a recorded decision and its actual business effects."""

import json

from ..business.connection import check_business_version, connect, transaction
from ..business.input_digest import fingerprint


def evaluate(handle, run_id: str) -> dict:
    with connect(handle.business_db) as db, transaction(db):
        check_business_version(db)
        decided = db.execute("""SELECT d.*,p.reprocess_cutoff_at FROM completion_decisions d
            JOIN cases c ON c.case_id=d.case_id AND c.target_batch_id=d.batch_id
            JOIN case_policies p ON p.case_id=c.case_id AND p.policy_version=c.policy_version
            WHERE d.logical_run_id=? AND d.case_id=? AND d.batch_id=?""",
                             (run_id, handle.case_id, handle.batch_id)).fetchone()
        if decided is None:
            return {"passed": False, "checks": {"durable_decision": False}}
        report = json.loads(decided["report_facts"])
        at, version = decided["decision_at"], decided["input_version"]
        case, batch = handle.case_id, handle.batch_id
        input_view = db.execute("""SELECT input_version,input_digest,ingestion_complete FROM batch_versions
            WHERE case_id=? AND batch_id=? AND known_at<=? ORDER BY input_version DESC LIMIT 1""",
                                (case, batch, at)).fetchone()
        # No runtime comparer or paired-result builder is imported here. The second query
        # checks the actual persisted effect using joins in both directions.
        cte = """WITH
            i AS (SELECT * FROM transaction_history WHERE case_id=:case AND batch_id=:batch
                AND known_at<=:at AND (superseded_known_at IS NULL OR :at<superseded_known_at)),
            e AS (SELECT * FROM settlement_history WHERE case_id=:case AND batch_id=:batch
                AND known_at<=:at AND (superseded_known_at IS NULL OR :at<superseded_known_at))
        """
        binding = {"case": case, "batch": batch, "at": at}
        valid, count = db.execute(cte + """SELECT
            NOT EXISTS (SELECT 1 FROM i WHERE
                (SELECT COUNT(*) FROM e WHERE e.transaction_id=i.transaction_id)=0
                OR (SELECT COUNT(*) FROM e WHERE e.transaction_id=i.transaction_id)>1
                OR EXISTS (SELECT 1 FROM e WHERE e.transaction_id=i.transaction_id
                    AND (e.amount_krw!=i.amount_krw OR e.currency!=i.currency OR e.status!='SETTLED'))
                OR i.status!='POSTED')
            AND NOT EXISTS (SELECT 1 FROM e LEFT JOIN i USING(transaction_id) WHERE i.transaction_id IS NULL),
            (SELECT COUNT(*) FROM i)""", binding).fetchone()
        if not input_view:
            return {"passed": False, "checks": {"decision_snapshot": False}}
        operations = list(db.execute("""SELECT * FROM operations WHERE case_id=? AND batch_id=?
            AND input_version=? AND action_kind='REPROCESS' AND committed_at<=?""", (case, batch, version, at)))
        cutoff = decided["reprocess_cutoff_at"]
        already_effective = any(op["state"] == "SUCCEEDED" and (not cutoff or op["committed_at"] <= cutoff)
                                for op in operations)
        if not input_view["ingestion_complete"]:
            expected = "WAITING_FOR_INPUT"
        elif not valid or not count or (cutoff and at > cutoff and not already_effective):
            expected = "ESCALATED"
        else:
            expected = "REPROCESSED"
        durable = (
            fingerprint(report) == decided["verification_digest"]
            and all(report.get(k) == decided[k] for k in ("decision_id", "logical_run_id", "case_id", "batch_id",
                                                          "outcome", "operation_id", "input_version", "decision_at"))
        )
        checks = {"durable_decision": durable,
                  "decision_snapshot": input_view["input_version"] == version and
                  input_view["input_digest"] == report.get("input_digest"),
                  "expected_outcome": decided["outcome"] == expected}
        if decided["outcome"] == "REPROCESSED":
            op = operations[0] if len(operations) == 1 else None
            checks["single_bound_effect"] = bool(op and op["state"] == "SUCCEEDED" and
                                                 op["operation_id"] == decided["operation_id"] and
                                                 op["result_ref"] == report.get("result_ref") and op["result_ref"])
            if op and op["result_ref"]:
                approval = db.execute("""SELECT 1 FROM approvals a JOIN cases c ON c.case_id=a.case_id
                    WHERE a.approval_id=? AND a.case_id=? AND a.batch_id=? AND a.input_version=?
                    AND a.input_digest=? AND a.action_digest=? AND a.status='CONSUMED'
                    AND a.consumed_by=? AND a.granted_by IS NOT NULL AND a.granted_by!=?
                    AND a.expires_at>? AND a.policy_version=c.policy_version""",
                    (op["approval_id"], case, batch, version, input_view["input_digest"],
                     op["action_digest"], op["operation_id"], handle.principal_id, op["committed_at"])).fetchone()
                checks["consumed_bound_approval"] = approval is not None
                binding.update({"op": op["operation_id"], "result": op["result_ref"], "at": op["committed_at"]})
                matches, total = db.execute(cte + """SELECT
                    (SELECT COUNT(*) FROM i JOIN e USING(transaction_id) JOIN reconciliation_results r
                        USING(transaction_id) WHERE r.operation_id=:op AND r.result_id=:result
                        AND r.internal_ref='transaction_history:'||i.transaction_id||':'||i.row_revision
                        AND r.external_ref='settlement_history:'||e.external_id||':'||e.row_revision
                        AND r.amount_krw=i.amount_krw AND r.amount_krw=e.amount_krw AND r.match_status='MATCHED'),
                    (SELECT COUNT(*) FROM reconciliation_results WHERE result_id=:result OR operation_id=:op)""",
                                              binding).fetchone()
                checks["exact_result_rows"] = matches == total == count
        else:
            checks["no_effect_for_decision_snapshot"] = not operations and decided["operation_id"] is None
        return {"passed": all(checks.values()), "expected_outcome": expected, "observed_outcome": decided["outcome"],
                "checks": checks}
