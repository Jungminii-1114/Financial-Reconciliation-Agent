"""Local Python tool entry point with scope, call budget and audit around every call."""

from dataclasses import asdict, dataclass
from time import monotonic
from typing import Callable

from .contracts import NodeResult, StepDiagnostic, ToolInvocation, TrustedCallContext, resolve_target, validate_context
from .errors import ToolError, map_tool_error
from .get_case import (
    GetCaseServices, authorize_get_case, get_case, parse_get_case_request, resolve_case_scope,
    serialize_get_case_result, validate_get_case_data,
)
from .ledger import ToolCallLedger
from .query_records import (
    QueryRecordsData, QueryRecordsServices, parse_query_records_request, query_records, validate_query_records_data,
)
from .workflow_tools import (
    WORKFLOW_PHASES, WorkflowToolServices, parse_workflow_request, run_workflow_tool,
)
from ..business.input_digest import canonical, sha256


@dataclass(frozen=True)
class RuntimeServices:
    get_case_services: GetCaseServices
    ledger: ToolCallLedger
    query_records_services: QueryRecordsServices | None = None
    workflow_services: WorkflowToolServices | None = None


@dataclass(frozen=True)
class _Tool:
    parse: Callable
    scope: Callable
    handler: Callable
    encode: Callable
    phases: tuple[str, ...]
    validate: Callable | None = None


def _serialize(result, *, data_type, limit):
    if (type(result) is not NodeResult or result.status not in ("success", "error")
        or (result.status == "success" and type(result.data) is not data_type)
        or (result.status == "error" and (result.data is not None or result.outcome is not None))):
        raise ToolError("CONTRACT_VIOLATION", "업무 도구 응답 계약이 다릅니다.")
    encoded = canonical(asdict(result))
    if len(encoded) > limit:
        raise ToolError("OUTPUT_LIMIT_EXCEEDED", "응답 크기 제한을 초과했습니다.")
    return encoded


def _registry(services):
    case = services.get_case_services

    def get_case_scope(req, ctx):
        authorize_get_case(req, ctx)
        return resolve_case_scope(req, ctx)

    tools = {"get_case": _Tool(
        parse_get_case_request, get_case_scope, lambda req, ctx: get_case(req, ctx, case),
        lambda result: serialize_get_case_result(result, limits=case.limits),
        ("B-01", "B-02", "B-04", "B-05"),
        lambda data, req, scope: validate_get_case_data(data, request=req, expected_scope=scope),
    )}

    def bind_scope(ctx, name, case_id, batch_id, repository):
        scope = resolve_target(ctx, name, case_id, batch_id)
        if scope != repository._scope:
            raise ToolError("SCOPE_DENIED", "업무 저장소의 호출 범위가 다릅니다.")
        return scope

    query = services.query_records_services
    if query is not None:
        tools["query_records"] = _Tool(
            lambda raw: parse_query_records_request(raw, limits=query.limits),
            lambda req, ctx: bind_scope(ctx, "query_records", req.case_id, req.batch_id, query.business_repository),
            lambda req, ctx: query_records(req, ctx, query),
            lambda result: _serialize(result, data_type=QueryRecordsData, limit=query.limits.max_response_bytes),
            ("B-02", "B-05"),
            lambda data, req, scope: validate_query_records_data(data, req),
        )

    workflow = services.workflow_services
    if workflow is not None:
        for name, phases in WORKFLOW_PHASES.items():
            tools[name] = _Tool(
                lambda raw, n=name: parse_workflow_request(n, raw),
                lambda req, ctx, n=name: bind_scope(ctx, n, req["case_id"], req["batch_id"], workflow.repository),
                lambda req, ctx, n=name: run_workflow_tool(n, req, ctx, workflow),
                lambda result: _serialize(result, data_type=dict, limit=128 * 1024),
                phases,
            )
    return tools


def dispatch_tool(invocation: ToolInvocation, ctx: TrustedCallContext,
                  services: RuntimeServices, *, phase=None) -> NodeResult:
    diagnostics = {key: StepDiagnostic("skipped") for key in (
        "context_check", "budget_reservation", "request_validation", "scope_check",
        "business_handler", "response_validation",
    )}
    active, reserved, start = "context_check", False, monotonic()
    tool = None
    try:
        validate_context(ctx)
        diagnostics[active] = StepDiagnostic("success")
        active = "budget_reservation"
        name = getattr(invocation, "tool_name", None)
        registry = _registry(services)
        tool = registry.get(name) if type(name) is str else None
        services.ledger.reserve(ctx, name if tool is not None else "unregistered")
        reserved = True
        diagnostics[active] = StepDiagnostic("success")
        active = "request_validation"
        if type(invocation) is not ToolInvocation or invocation.contract_version != "tool-call-1":
            raise ToolError("UNSUPPORTED_CONTRACT_VERSION", "지원하지 않는 호출 계약입니다.")
        if tool is None:
            raise ToolError("UNKNOWN_TOOL", "등록되지 않은 업무 도구입니다.")
        try:
            payload = canonical(invocation.arguments)
        except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
            raise ToolError("INVALID_ARGUMENT", "JSON 업무 인자가 올바르지 않습니다.") from exc
        if len(payload) > 32 * 1024:
            raise ToolError("INVALID_ARGUMENT", "업무 인자 크기 제한을 초과했습니다.")
        request = tool.parse(invocation.arguments)
        diagnostics[active] = StepDiagnostic("success")
        active = "scope_check"
        scope = tool.scope(request, ctx)
        # Local context is code-owned. Repository rechecks actual DB binding before content reads.
        if name == "get_case" and scope != services.get_case_services.business_repository._scope:
            raise ToolError("SCOPE_DENIED", "업무 저장소의 호출 범위가 다릅니다.")
        if phase is not None and phase not in tool.phases:
            raise ToolError("PHASE_DENIED", "현재 단계에서 허용되지 않은 도구입니다.")
        diagnostics[active] = StepDiagnostic("success")
        services.ledger.event(ctx.tool_call_id, "HANDLER_STARTED")
        active = "business_handler"
        result = tool.handler(request, ctx)
        diagnostics[active] = StepDiagnostic("success")
        active = "response_validation"
        if tool.validate:
            tool.validate(result.data, request, scope)
        diagnostics[active] = StepDiagnostic("success")
        result = NodeResult(result.status, result.data, diagnostics, result.outcome)
        encoded = tool.encode(result)
        services.ledger.event(ctx.tool_call_id, "RESPONSE_VALIDATED")
    except Exception as exc:
        error = map_tool_error(exc)
        diagnostics[active] = StepDiagnostic("failed", error.code, error.message, error.retryable)
        result = NodeResult("error", None, diagnostics, None)
        if tool is not None:
            try:
                encoded = tool.encode(result)
            except ToolError:
                diagnostics = {active: StepDiagnostic("failed", error.code, "업무 호출을 완료할 수 없습니다.", error.retryable)}
                result = NodeResult("error", None, diagnostics, None)
                encoded = tool.encode(result)
        else:
            encoded = canonical(asdict(result))

    if reserved:
        try:
            code = next((step.error_code for step in diagnostics.values() if step.status == "failed"), None)
            services.ledger.finish(ctx.tool_call_id, result.status, code,
                                   int((monotonic() - start) * 1000), sha256(encoded))
        except Exception:
            result = NodeResult("error", None, {"audit": StepDiagnostic(
                "failed", "AUDIT_UNAVAILABLE", "호출 결과 기록에 실패했습니다.", False)}, None)
    return result
