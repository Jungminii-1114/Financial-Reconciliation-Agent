"""Local Python dispatcher. Only get_case is registered; no model or HTTP transport."""
from dataclasses import dataclass
from time import monotonic
from .contracts import NodeResult, StepDiagnostic, ToolInvocation, TrustedCallContext, validate_context
from .errors import ToolError, map_tool_error
from .get_case import GetCaseServices, get_case, parse_get_case_request, serialize_get_case_result
from .ledger import ToolCallLedger
from ..business.input_digest import canonical, sha256


@dataclass(frozen=True)
class RuntimeServices:
    get_case_services: GetCaseServices
    ledger: ToolCallLedger


def dispatch_tool(invocation: ToolInvocation, ctx: TrustedCallContext,
                  services: RuntimeServices, *, phase=None) -> NodeResult:
    diagnostics = {k: StepDiagnostic('skipped') for k in
                   ('context_check','budget_reservation','request_validation','scope_check',
                    'business_handler','response_validation')}
    active, reserved, start = 'context_check', False, monotonic()
    try:
        validate_context(ctx)
        diagnostics[active] = StepDiagnostic('success')
        active = 'budget_reservation'
        services.ledger.reserve(ctx, 'get_case' if getattr(invocation,'tool_name',None)=='get_case' else 'unregistered')
        reserved = True
        diagnostics[active] = StepDiagnostic('success')
        active = 'request_validation'
        if type(invocation) is not ToolInvocation or invocation.contract_version != 'tool-call-1':
            raise ToolError('UNSUPPORTED_CONTRACT_VERSION','지원하지 않는 호출 계약입니다.')
        if invocation.tool_name != 'get_case':
            raise ToolError('UNKNOWN_TOOL','등록되지 않은 업무 도구입니다.')
        try:
            payload = canonical(invocation.arguments)
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise ToolError('INVALID_ARGUMENT','JSON 업무 인자가 올바르지 않습니다.') from exc
        if len(payload) > 32*1024:
            raise ToolError('INVALID_ARGUMENT','업무 인자 크기 제한을 초과했습니다.')
        request = parse_get_case_request(invocation.arguments)
        diagnostics[active] = StepDiagnostic('success')
        active = 'scope_check'
        from .get_case import authorize_get_case, resolve_case_scope
        authorize_get_case(request,ctx)
        scope = resolve_case_scope(request,ctx)
        # Local context is code-owned. Repository rechecks actual DB binding before content reads.
        if scope != services.get_case_services.business_repository._scope:
            raise ToolError('SCOPE_DENIED','업무 저장소의 호출 범위가 다릅니다.')
        if phase is not None and phase not in ('B-01','B-02','B-04','B-05'):
            raise ToolError('PHASE_DENIED','현재 단계에서 허용되지 않은 도구입니다.')
        diagnostics[active] = StepDiagnostic('success')
        services.ledger.event(ctx.tool_call_id,'HANDLER_STARTED')
        active = 'business_handler'
        result = get_case(request,ctx,services.get_case_services)
        diagnostics[active] = StepDiagnostic('success')
        active = 'response_validation'
        from .get_case import validate_get_case_data
        validate_get_case_data(result.data,request=request,expected_scope=scope)
        diagnostics[active] = StepDiagnostic('success')
        result = NodeResult(result.status,result.data,diagnostics,result.outcome)
        encoded = serialize_get_case_result(result,limits=services.get_case_services.limits)
        services.ledger.event(ctx.tool_call_id,'RESPONSE_VALIDATED')
    except Exception as exc:
        error = map_tool_error(exc)
        diagnostics[active] = StepDiagnostic('failed',error.code,error.message,error.retryable)
        result = NodeResult('error',None,diagnostics,None)
        encoded = canonical({'status':'error','code':error.code})
    try:
        encoded = serialize_get_case_result(result, limits=services.get_case_services.limits)
    except ToolError:
        code = next((d.error_code for d in result.diagnostics.values() if d.status == 'failed'), 'OUTPUT_LIMIT_EXCEEDED')
        result = NodeResult('error', None, {'response_validation': StepDiagnostic(
            'failed', code, '공개 응답을 반환할 수 없습니다.', False)}, None)
        encoded = serialize_get_case_result(result, limits=services.get_case_services.limits)
    if reserved:
        try:
            code = next((d.error_code for d in result.diagnostics.values() if d.status=='failed'),None)
            services.ledger.finish(ctx.tool_call_id,result.status,code,
                                  int((monotonic()-start)*1000),sha256(encoded))
        except Exception:
            result = NodeResult('error',None,{'audit': StepDiagnostic(
                'failed','AUDIT_UNAVAILABLE','호출 결과 기록에 실패했습니다.',False)},None)
    return result
