"""First runnable vertical slice of the reconciliation agent."""

import json
from dataclasses import asdict

from ..business import reconciliation as business
from ..business.errors import require
from ..business.input_digest import fingerprint
from ..business.repository import BusinessScope
from ..tools.contracts import ToolInvocation
from ..tools.dispatcher import RuntimeServices, dispatch_tool
from ..tools.errors import ToolError
from ..tools.get_case import GetCaseLimits, GetCaseServices
from ..tools.query_records import QueryRecordsLimits, QueryRecordsServices
from ..tools.runner import context_for_trial
from ..tools.workflow_tools import BINDING_FIELDS, WORKFLOW_PHASES, WorkflowToolServices
from ..trials.contracts import validate_id
from .advisor import ollama_advice

_ALLOWED_TOOLS = frozenset({"get_case", "query_records", *WORKFLOW_PHASES})


class ReconciliationWorkflow:
    """Construction and all invocations require the trusted local runtime lease."""

    def __init__(self, handle, journal, run_id, session_id, *, before_commit=None, after_commit=None):
        self.handle, self.journal = handle, journal
        self.run_id, self.session_id = validate_id(run_id), validate_id(session_id)
        self.repo = handle.business_repository()
        self.scope = BusinessScope(handle.principal_id, handle.trial_id, handle.case_id, handle.batch_id)
        self.target = {"case_id": handle.case_id, "batch_id": handle.batch_id}
        self.services = RuntimeServices(
            GetCaseServices(self.repo, GetCaseLimits()), journal.ledger,
            QueryRecordsServices(self.repo, QueryRecordsLimits(), journal.cursor_key),
            WorkflowToolServices(self.repo, journal.check_intent, journal.check_evidence, journal.save_draft,
                                 True, before_commit, after_commit),
        )

    def context(self):
        return context_for_trial(self.handle, logical_run_id=self.run_id, session_id=self.session_id,
                                 allowed_tools=_ALLOWED_TOOLS)

    def call(self, name, phase, **args):
        result = dispatch_tool(ToolInvocation("tool-call-1", name, args), self.context(), self.services, phase=phase)
        if result.status != "success":
            diag = next(step for step in result.diagnostics.values() if step.status == "failed")
            raise ToolError(diag.error_code, diag.reason, retryable=bool(diag.retryable))
        return result.data

    def db(self, *, write=False):
        ctx = self.context()
        return business.scoped_transaction(self.repo, self.scope, ctx.policy_version, ctx.config_hash, write=write)

    def prior_decision(self):
        with self.db() as (db, meta, _):
            row = db.execute("""SELECT * FROM completion_decisions WHERE case_id=? AND batch_id=?
                AND logical_run_id=?""", (self.handle.case_id, self.handle.batch_id, self.run_id)).fetchone()
            if row is None:
                return None
            record = json.loads(row["report_facts"])
            require(fingerprint(record) == row["verification_digest"] and all(
                record[key] == row[key] for key in ("decision_id", "logical_run_id", "case_id", "batch_id",
                                                     "outcome", "decision_at", "input_version", "operation_id")
            ), "VERIFICATION_FAILED", "저장된 결정과 검증 digest가 일치하지 않습니다.")
            return {"status": "FINAL", "redelivered": True,
                    "historical": record["decision_at"] != meta["current_time"],
                    "decision": {**record, "verification_digest": row["verification_digest"]}}

    def approve(self, actor):
        validate_id(actor)
        require(actor != self.handle.principal_id, "APPROVAL_REQUIRED", "실행 주체와 다른 검토자 ID가 필요합니다.")
        state = self.journal.load(self.run_id)
        require(state and state.get("proposed"), "APPROVAL_REQUIRED", "먼저 해당 실행의 재처리 제안을 생성하세요.")
        require(self.prior_decision() is None, "NOT_ELIGIBLE", "이미 종료된 실행입니다.")
        with self.db(write=True) as (db, meta, policy):
            approval = business.grant_approval(db, self.scope, meta, policy, state["proposed"], actor)
        state["approval_id"] = approval["approval_id"]
        if state.get("intent"):
            state["intent"]["approval_id"] = approval["approval_id"]
        self.journal.save(self.run_id, state)
        return {"status": "APPROVED", "approval": approval}

    def _binding(self, case):
        return {**self.target, "expected_input_version": case.input_version,
                "expected_input_digest": case.input_digest}

    def _check_operation(self, intent):
        return self.call("get_operation", "B-04", **self.target,
                         idempotency_key=intent["idempotency_key"],
                         expected_input_version=intent["expected_input_version"],
                         expected_action_digest=intent["action_digest"])

    def _finalize(self, case, outcome, reason, effect=None, evidence_ref=None, draft_id=None):
        with self.db(write=True) as (db, meta, policy):
            decision = business.finalize(db, self.scope, meta, policy, self.run_id,
                                         {**self._binding(case), "as_of": case.decision_at}, outcome, reason, effect,
                                         evidence_ref=evidence_ref, draft_id=draft_id)
        return {"status": "FINAL", "redelivered": False, "historical": False, "decision": decision}

    def _start_intent(self, state, checked):
        key = "key-" + fingerprint([self.handle.trial_id, checked["action_digest"]])[7:]
        intent = {**{name: checked[name] for name in BINDING_FIELDS},
                  "action_digest": checked["action_digest"], "operation_id": "op-" + key[4:36],
                  "idempotency_key": key, "approval_id": state["approval_id"]}
        state["intent"] = intent
        self.journal.save(self.run_id, state)
        return intent

    def run(self, *, model=None):
        prior = self.prior_decision()
        if prior:
            return prior
        state = self.journal.load(self.run_id) or {}
        if "model" in state and model is not None and model != state["model"]:
            raise ToolError("SCOPE_DENIED", "재개 세션은 기존 판단 설정을 사용해야 합니다.")
        model = state.setdefault("model", model)
        self.journal.save(self.run_id, state)
        intent = state.get("intent")
        if intent:
            try:
                effect = self._check_operation(intent)
            except ToolError as exc:
                return {"status": "UNRESOLVED", "reason": exc.code}
            if effect["observed_effect"] == "APPLIED":
                case = self.call("get_case", "B-04", case_id=self.handle.case_id)
                if case.input_version == effect["input_version"]:
                    output = self._finalize(case, "REPROCESSED", "RECOVERED_COMMITTED_EFFECT", effect,
                                            state.get("evidence_ref"))
                    output["recovery"] = "RETRIEVED_EXISTING_RESULT"
                    return output
                state["previous_effect"] = effect
                for key in ("intent", "approval_id", "proposed"):
                    state.pop(key, None)
                self.journal.save(self.run_id, state)
            elif not effect["safe_to_retry_same_key"]:
                return {"status": "UNRESOLVED", "reason": "EFFECT_UNKNOWN"}

        case = self.call("get_case", "B-01", case_id=self.handle.case_id)
        observed = self.call("query_records", "B-02", **self.target, profile="BATCH_COMPARISON",
                             as_of=case.decision_at, expected_input_version=case.input_version)
        if not observed.coverage.summary_complete or observed.input_digest != case.input_digest:
            raise ToolError("VERSION_CONFLICT", "사건 조회와 거래 관측의 입력이 다릅니다.")
        self.journal.register_evidence(self.run_id, observed)
        existing = next((op for op in case.operation_refs if op.input_version == case.input_version), None)
        if existing:
            effect = self.call("get_operation", "B-05", **self.target, operation_id=existing.operation_id,
                               expected_input_version=existing.input_version,
                               expected_action_digest=existing.action_digest)
            if effect["observed_effect"] == "APPLIED":
                return self._finalize(case, "REPROCESSED", "VERIFIED_EXISTING_EFFECT", effect, observed.result_digest)
            return {"status": "UNRESOLVED", "reason": "EFFECT_UNKNOWN"}
        with self.db() as (db, meta, policy):
            if meta["current_time"] != observed.as_of:
                raise ToolError("VERSION_CONFLICT", "관측 후 업무 시각이 바뀌었습니다.")
            cutoff = policy["reprocess_cutoff_at"]
        facts = asdict(observed.structured_facts)
        action, reason = business.rule_action(facts, cutoff, observed.as_of)
        # The advisory model receives only typed counts/flags and returns one literal.
        if model:
            cached = state.get("advice")
            if cached and cached["evidence_ref"] == observed.result_digest:
                advice = cached["action"]
            else:
                advice = ollama_advice(model, {
                    "internal_count": facts["internal_count"], "external_count": facts["external_count"],
                    "matched_count": facts["matched_count"], "ingestion_complete": facts["ingestion_complete"],
                    "discrepancy_count": sum(len(facts[key]) for key in (
                        "mismatched_ids", "missing_external_ids", "unexpected_external_ids",
                        "duplicate_transaction_ids", "unsettled_transaction_ids")),
                    "cutoff_passed": bool(cutoff and observed.as_of > cutoff),
                })
                state["advice"] = {"evidence_ref": observed.result_digest, "action": advice}
                self.journal.save(self.run_id, state)
            if advice != action:
                action, reason = "ESCALATE", "ADVISOR_DISAGREEMENT"
        if action == "WAIT":
            return self._finalize(case, "WAITING_FOR_INPUT", reason, evidence_ref=observed.result_digest)
        if action == "ESCALATE":
            return self._escalate(case, reason, observed.result_digest)
        checked = self.call("precheck_reprocess", "B-03", **self._binding(case),
                            action="REPROCESS", evidence_refs=[observed.result_digest])
        if not checked["eligible"]:
            return self._escalate(case, "BATCH_NOT_ELIGIBLE", observed.result_digest)
        proposed = {key: checked[key] for key in (*BINDING_FIELDS, "action_digest")}
        if state.get("proposed") != proposed:
            state.pop("approval_id", None)
            state.pop("intent", None)
        state["proposed"] = proposed
        state["evidence_ref"] = observed.result_digest
        self.journal.save(self.run_id, state)
        if not state.get("approval_id"):
            response = {"status": "PAUSED_FOR_APPROVAL", "proposal": checked}
            if model:
                response["advisor"] = {"model": model, "proposed_action": advice}
            return response
        intent = state.get("intent") or self._start_intent(state, checked)
        try:
            self.call("execute_reprocess", "B-04", **intent)
        except ToolError as exc:
            if exc.code in {"APPROVAL_REQUIRED", "APPROVAL_STALE"}:
                return {"status": "PAUSED_FOR_APPROVAL", "reason": exc.code, "proposal": checked}
            return {"status": "UNRESOLVED", "reason": exc.code}
        effect = self._check_operation(intent)
        if effect["observed_effect"] != "APPLIED":
            return {"status": "UNRESOLVED", "reason": "EFFECT_UNKNOWN"}
        return self._finalize(case, "REPROCESSED", "VERIFIED_COMMITTED_EFFECT", effect, observed.result_digest)

    def _escalate(self, case, reason, evidence_ref):
        draft = self.call("prepare_escalation", "B-05", **self.target,
                          reason_codes=[reason], evidence_refs=[evidence_ref])
        return self._finalize(case, "ESCALATED", reason, evidence_ref=evidence_ref, draft_id=draft["draft_id"])
