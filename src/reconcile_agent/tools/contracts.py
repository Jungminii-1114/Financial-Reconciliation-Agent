from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal, Mapping, TypeVar

T = TypeVar("T")    # 업무 데이터 타입이 들어갈 자리

ProcessingState = Literal["FAILED", "RUNNING", "SUCCEEDED"]
NodeStatus = Literal["success", "partial", "error"]

@dataclass(frozen=True)
class TargetScope:
    case_id: str
    batch_id: str

@dataclass(frozen=True)
class TrustedCallContext:
    principal_id: str
    trial_id: str
    logical_run_id: str
    session_id: str
    tool_call_id: str
    budget_group_id: str

    allowed_targets: frozenset[TargetScope]
    allowed_tools: frozenset[str]

    scope_version: str
    policy_version: str
    server_now: datetime
    config_hash: str

@dataclass(frozen=True)
class StepDiagnostic:
    status: Literal["success", "failed", "skipped"]
    error_code: str | None = None
    reason: str | None = None
    retryable: bool | None = None
    elapsed_ms: int | None = None

@dataclass(frozen=True)
class NodeResult(Generic[T]):
    status: NodeStatus
    data: T | None
    diagnostics: dict[str, StepDiagnostic]
    outcome: str | None


@dataclass(frozen=True)
class ToolInvocation:
    contract_version: str
    tool_name: str
    arguments: dict[str, object]


def validate_context(ctx: TrustedCallContext) -> None:
    from .errors import ToolError
    import re
    valid = type(ctx) is TrustedCallContext
    if valid:
        valid = all(isinstance(getattr(ctx, k), str) and
                    re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", getattr(ctx, k))
                    for k in ("principal_id", "trial_id", "logical_run_id", "session_id", "tool_call_id",
                              "budget_group_id", "scope_version", "policy_version", "config_hash"))
        valid = valid and type(ctx.allowed_targets) is frozenset and type(ctx.allowed_tools) is frozenset
        valid = valid and all(type(t) is TargetScope and isinstance(t.case_id, str) and
                              re.fullmatch(r"c_[0-9a-f]{12}", t.case_id) and isinstance(t.batch_id, str) and
                              0 < len(t.batch_id) <= 128 for t in ctx.allowed_targets)
        valid = valid and all(isinstance(t, str) and 0 < len(t) <= 64 for t in ctx.allowed_tools)
        valid = valid and isinstance(ctx.server_now, datetime) and ctx.server_now.tzinfo is not None and ctx.server_now.utcoffset() is not None
    if not valid:
        raise ToolError("SCOPE_DENIED", "서버 호출 문맥이 올바르지 않습니다.")
