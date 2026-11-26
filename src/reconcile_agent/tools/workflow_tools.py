"""Narrow local contracts for Tools 3–6. Approval issuance is an operator service."""

from dataclasses import dataclass
import re
from typing import Callable

from .contracts import NodeResult, resolve_target
from .errors import ToolError
from ..business import reconciliation as business
from ..business.input_digest import fingerprint
from ..business.repository import BusinessRepository

BINDING_FIELDS = frozenset({"case_id", "batch_id", "expected_input_version", "expected_input_digest"})
_TARGET = frozenset({"case_id", "batch_id"})
_REQUIRED = {
    "precheck_reprocess": BINDING_FIELDS | {"action", "evidence_refs"},
    "execute_reprocess": BINDING_FIELDS | {"operation_id", "idempotency_key", "action_digest", "approval_id"},
    "get_operation": _TARGET | {"expected_input_version", "expected_action_digest"},
    "prepare_escalation": _TARGET | {"reason_codes", "evidence_refs"},
}
_OPTIONAL = {
    "get_operation": {"operation_id", "idempotency_key"},
    "prepare_escalation": {"operation_ref", "unresolved_items"},
}
WORKFLOW_PHASES = {
    "precheck_reprocess": ("B-03",),
    "execute_reprocess": ("B-04",),
    "get_operation": ("B-04", "B-05"),
    "prepare_escalation": ("B-05",),
}
REASONS = frozenset({
    "TRANSACTION_MISMATCH", "CUTOFF_PASSED", "UNRESOLVED_EFFECT", "APPROVAL_STALE",
    "ADVISOR_DISAGREEMENT", "BATCH_NOT_ELIGIBLE",
})


@dataclass(frozen=True)
class WorkflowToolServices:
    repository: BusinessRepository
    intent_check: Callable
    evidence_check: Callable
    save_draft: Callable
    authoritative_absence: bool = False
    before_commit: Callable[[], None] | None = None
    after_commit: Callable[[], None] | None = None


def _text(value):
    return type(value) is str and 1 <= len(value) <= 160 and value.isprintable() and value == value.strip()


def parse_workflow_request(name, payload):
    def invalid():
        raise ToolError("INVALID_ARGUMENT", "업무 도구의 요청 필드 또는 값이 올바르지 않습니다.")

    allowed = _REQUIRED[name] | _OPTIONAL.get(name, set())
    if type(payload) is not dict or not _REQUIRED[name] <= set(payload) <= allowed:
        invalid()
    if not _text(payload["case_id"]) or re.fullmatch(r"c_[0-9a-f]{12}", payload["case_id"]) is None:
        invalid()
    if not _text(payload["batch_id"]) or len(payload["batch_id"]) > 128:
        invalid()
    if "expected_input_version" in payload and (
        type(payload["expected_input_version"]) is not int or payload["expected_input_version"] < 1
    ):
        invalid()
    for field in ("expected_input_digest", "expected_action_digest", "action_digest"):
        if field in payload and (not _text(payload[field]) or re.fullmatch(r"sha256:[0-9a-f]{64}", payload[field]) is None):
            invalid()
    for field in ("operation_id", "idempotency_key", "approval_id", "operation_ref"):
        if field in payload and not _text(payload[field]):
            invalid()
    if name == "get_operation" and len({"operation_id", "idempotency_key"} & payload.keys()) != 1:
        invalid()
    if name == "precheck_reprocess" and payload["action"] != "REPROCESS":
        raise ToolError("INVALID_ACTION", "REPROCESS만 지원합니다.")
    for field in ("reason_codes", "evidence_refs", "unresolved_items"):
        if field in payload:
            items = payload[field]
            if type(items) is not list or len(items) > 16 or not all(_text(item) for item in items):
                invalid()
            if len(set(items)) != len(items):
                invalid()
    if name == "prepare_escalation" and (not payload["reason_codes"] or not set(payload["reason_codes"]) <= REASONS):
        invalid()
    if name == "precheck_reprocess" and not payload["evidence_refs"]:
        invalid()
    return payload.copy()


def run_workflow_tool(name, request, ctx, services: WorkflowToolServices):
    request = parse_workflow_request(name, request)
    scope = resolve_target(ctx, name, request["case_id"], request["batch_id"])
    if name == "execute_reprocess":
        services.intent_check(ctx, request)
    if name in {"precheck_reprocess", "prepare_escalation"}:
        services.evidence_check(ctx, request)
    write = name == "execute_reprocess" or (name == "get_operation" and services.authoritative_absence)
    with business.scoped_transaction(services.repository, scope, ctx.policy_version, ctx.config_hash,
                                     write=write) as (db, meta, policy):
        if name == "precheck_reprocess":
            data = business.precheck(db, scope, meta, policy, request)
        elif name == "execute_reprocess":
            data = business.execute(db, scope, meta, policy, request, services.before_commit)
        elif name == "get_operation":
            data = business.get_effect(db, scope, meta, policy, request,
                                       authoritative_absence=services.authoritative_absence)
        else:
            if "operation_ref" in request:
                operation = db.execute("SELECT 1 FROM operations WHERE operation_id=? AND case_id=? AND batch_id=?",
                                       (request["operation_ref"], scope.case_id, scope.batch_id)).fetchone()
                if operation is None:
                    raise ToolError("SCOPE_DENIED", "이관 작업 참조의 범위가 다릅니다.")
            data = {
                **request, "draft_id": "draft-" + fingerprint([ctx.trial_id, ctx.logical_run_id, request])[7:39],
                "status": "DRAFT_ONLY", "external_delivery": False,
            }
    if name == "prepare_escalation":
        services.save_draft(ctx, data)
    if name == "execute_reprocess" and not data["idempotent_replay"] and services.after_commit:
        services.after_commit()
    return NodeResult("success", data, {}, None)
