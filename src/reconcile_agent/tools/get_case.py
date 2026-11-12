"""
Tool-01
- 사건의 현재 상태를 안전하게 조회하는 함수

“내 권한으로 이 사건을 조회해서, 대상 배치·현재 입력 버전·자료 도착 여부·처리 상태·기존 작업 참조·외부 메모를 알려줘.”

[입력]: Tool_01은 case_id 필드만 받아서 처리 
{
    "case_id" : "c_2e8b2kdpo10c"
}
-> "case_id" 이외에 같이 dictionary 로 들어오는 입력은 거부해야 함.
 (요청 전체를 실패처리, 업무 조회는 실행 X)

 = = = = = = = = = = = = = = = = = = = = = = = = = = = = = =
 [호출 흐름]
업무 인자 {"case_id": "..."}
         +
서버가 제공하는 권한 문맥                   # 현재 호출 주체는 누구이고, 어떤 사건에 어떤 작업이 가능한가?
         ↓
dispatcher: 인자·권한·예산 검사
         ↓
get_case(): 조회 조립
         ↓
BusinessRepository.get_case_view(): SQLite 조회
         ↓
GetCaseData 생성·검증
         ↓
NodeResult 반환

= = = = = = = = = = = = = = = = = = = = = = = = = = = = = =


[출력]: 기존 네 필드 유지하기
{
  "status": "success",
  "data": {
    "case_id": "c_2e8b6b7c4702",
    "batch_id": "BATCH001",
    "input_version": 1,
    "input_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "decision_at": "2026-11-05T01:00:00.000000+00:00",
    "ingestion_complete": false,
    "processing_state": "FAILED",
    "operation_refs": [],
    "external_notes": []
  },
  "diagnostics": {
    "request_validation": {"status": "success"},
    "scope_check": {"status": "success"},
    "business_handler": {"status": "success"},
    "response_validation": {"status": "success"}
  },
  "outcome": null
}

[활용처]

RunnerConfig        : 로컬 실행 설정에 사용                  # 호출 주체인 `principal_id`, 실험 설정 `setting`, 반복 번호 `repeat_id`
TrialHandle         : 준비된 업무 DB와 repo 얻는 데 사용      # trial 경로 ID, 사건 배치 ID, 실행 설정
BusinessScope       : repo의 접근 대상을 고정               # principal_id, trial_id, case_id, batch_id
BusinessRepository  : 업무 DB 접근 및 조회 가능 -> DB 조회 기반으로 확장할 것


ctx: 신뢰된 실행 서버/코드가 제공하며, ["누가 호출하며 어디까지 허용되는지"를 제공]
services: 실행 코드/서버가 제공하며, ["조회에 사용할 repository"]등 실제 기능을 제공
request: 모델 또는 업무 요청 코드가 제공하며, 무엇을 조회할지 case_id를 제공함

"""


import re
from datetime import datetime
from dataclasses import asdict
from time import monotonic

from reconcile_agent.business.input_digest import canonical, sha256, utc_time
from dataclasses import dataclass

from reconcile_agent.business.repository import BusinessRepository
from reconcile_agent.tools.contracts import NodeResult, TrustedCallContext, StepDiagnostic, validate_context
from reconcile_agent.business.repository import BusinessScope, OperationRef, ExternalNote, ProcessingState, CaseReadView
from reconcile_agent.tools.errors import ToolError



@dataclass(frozen=True)
class GetCaseRequest:
    case_id: str

# 모델에 공개하는 data
@dataclass(frozen=True)
class GetCaseData:
    case_id: str
    batch_id: str
    input_version: int
    input_digest: str
    decision_at: str
    ingestion_complete: bool
    processing_state: ProcessingState

    operation_refs: tuple[OperationRef, ...]
    external_notes: tuple[ExternalNote, ...]


@dataclass(frozen=True)
class GetCaseLimits:
    max_response_bytes: int = 128 * 1024

    def __post_init__(self):
        if type(self.max_response_bytes) is not int or not 512 <= self.max_response_bytes <= 128 * 1024:
            raise ToolError("CONTRACT_VIOLATION", "응답 크기 설정이 올바르지 않습니다.")

@dataclass(frozen=True)
class GetCaseServices: 
    business_repository: BusinessRepository
    limits: GetCaseLimits


# TOOL-01 전용 처리 함수
def parse_get_case_request(payload: dict) -> GetCaseRequest:
    """
    정상 Case_id 통과, 추가 필드/잘못된 ID 거부하기
    """
    if type(payload) is not dict:
        raise ToolError("INVALID_ARGUMENT", "객체 형태의 인자가 필요합니다.")
    if set(payload) != {"case_id"}:
        raise ToolError("INVALID_ARGUMENT", "요청 필드가 올바르지 않습니다.")

    case_id = payload["case_id"]

    if (not isinstance(case_id, str) or re.fullmatch(r"c_[0-9a-f]{12}", case_id) is None):
        raise ToolError("INVALID_ARGUMENT", "case_id 형식이 올바르지 않습니다.")

    return GetCaseRequest(case_id=case_id)

def authorize_get_case(request: GetCaseRequest, ctx: TrustedCallContext) -> None:
    """
    [입력]
    요청/문맥

    [반환]
    없음

    Tool 권한 요청과 CAse 허용 여부를 확인하기
    """
    validate_context(ctx)
    if type(request) is not GetCaseRequest:
        raise ToolError("INVALID_ARGUMENT", "요청 타입이 올바르지 않습니다.")
    parse_get_case_request({"case_id": request.case_id})
    if "get_case" not in ctx.allowed_tools or not any(
        target.case_id == request.case_id for target in ctx.allowed_targets
    ):
        raise ToolError("SCOPE_DENIED", "허용되지 않은 호출입니다.")

def resolve_case_scope(request:GetCaseRequest, ctx: TrustedCallContext) -> BusinessScope:
    # 현재 v1의 case당 단일 대상 배치 정책으로 scope 구성
    # 허용 대상이 없거나 모호하면 거부
    """
    	요청 case의 허용 대상 쌍을 선택하고, 현재 principal·trial과 함께 BusinessScope 생성
    """

    targets=[target for target in ctx.allowed_targets if target.case_id == request.case_id]

    if not targets:
        raise ToolError("SCOPE_DENIED", "허용되지 않은 사건입니다.")
    
    if len(targets) != 1:
        raise ToolError("CONTRACT_VIOLATION", "사건의 대상 배치를 하나로 결정할 수 없습니다.")

    target = targets[0]

    return BusinessScope(
        principal_id=ctx.principal_id,
        trial_id=ctx.trial_id,
        case_id=target.case_id,
        batch_id=target.batch_id
    )

def build_get_case_data(view: CaseReadView) -> GetCaseData:

    return GetCaseData(
        case_id=view.case_id,
        batch_id=view.batch_id,
        input_version=view.input_version,
        input_digest=view.input_digest,
        decision_at=view.business_now,
        ingestion_complete=view.ingestion_complete,
        processing_state=view.processing_state,
        operation_refs=view.operation_refs,
        external_notes=view.external_notes
    )

def validate_get_case_data(data: GetCaseData, *, request: GetCaseRequest, expected_scope: BusinessScope) -> None:
    def require_output(condition):
        if not condition:
            raise ToolError("CONTRACT_VIOLATION", "조회 응답 계약이 올바르지 않습니다.")
    require_output(type(data) is GetCaseData)
    require_output(data.case_id == request.case_id == expected_scope.case_id and data.batch_id == expected_scope.batch_id)
    require_output(type(data.input_version) is int and data.input_version > 0)
    require_output(type(data.ingestion_complete) is bool)
    require_output(isinstance(data.input_digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", data.input_digest) is not None)
    require_output(data.processing_state in ("FAILED", "RUNNING", "SUCCEEDED"))
    try:
        at = utc_time(data.decision_at)
        require_output(type(data.operation_refs) is tuple and len(data.operation_refs) <= 1024)
        require_output(type(data.external_notes) is tuple and len(data.external_notes) <= 256)
        seen = set()
        for op in data.operation_refs:
            require_output(type(op) is OperationRef and op.operation_id not in seen)
            seen.add(op.operation_id)
            require_output(all(isinstance(v, str) and 0 < len(v) <= 256 for v in
                               (op.operation_id, op.idempotency_key)))
            require_output(type(op.input_version) is int and 0 < op.input_version <= data.input_version)
            require_output(re.fullmatch(r"sha256:[0-9a-f]{64}", op.action_digest) is not None)
        seen = set()
        for note in data.external_notes:
            require_output(type(note) is ExternalNote and (note.note_id, note.context_revision) not in seen)
            seen.add((note.note_id, note.context_revision))
            require_output(isinstance(note.note_id, str) and 0 < len(note.note_id) <= 256)
            require_output(type(note.context_revision) is int and note.context_revision > 0)
            require_output(isinstance(note.origin, str) and 0 < len(note.origin) <= 256)
            require_output(utc_time(note.known_at) <= at)
            require_output(isinstance(note.content, str) and len(note.content.encode("utf-8")) <= 16384)
            require_output(note.content_hash == sha256(note.content.encode("utf-8")))
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError("CONTRACT_VIOLATION", "조회 응답 계약이 올바르지 않습니다.") from exc

def get_case(request: GetCaseRequest, ctx: TrustedCallContext, services: GetCaseServices) -> NodeResult[GetCaseData]: 
    """
        authorize_get_case()
        → resolve_case_scope()
        → repository.get_case_view()
        → build_get_case_data()
        → validate_get_case_data()
        → NodeResult 생성
    """

    authorize_get_case(request, ctx)
    repository = services.business_repository

    expected_scope = resolve_case_scope(request, ctx)

    deadline = monotonic() + 2.0
    view = repository.get_case_view(
        requested_case_id=request.case_id, expected_scope=expected_scope,
        expected_policy_version=ctx.policy_version, expected_config_hash=ctx.config_hash,
        deadline=deadline,
    )
    
    data = build_get_case_data(view)

    validate_get_case_data(data, request=request, expected_scope=expected_scope)
    

    result = NodeResult(
        status="success",
        data=data,
        diagnostics={
            "scope_check": StepDiagnostic(status="success"),
            "business_handler": StepDiagnostic(status="success")
        },
        outcome=None
    )
    serialize_get_case_result(result, limits=services.limits)
    return result


def serialize_get_case_result(result: NodeResult[GetCaseData], *, limits: GetCaseLimits) -> bytes:
    if type(limits.max_response_bytes) is not int or limits.max_response_bytes < 512:
        raise ToolError("CONTRACT_VIOLATION", "응답 크기 설정이 올바르지 않습니다.")
    if type(result) is not NodeResult or result.status not in ("success", "error") or result.outcome is not None:
        raise ToolError("CONTRACT_VIOLATION", "응답 외피가 올바르지 않습니다.")
    if result.status == "success" and type(result.data) is not GetCaseData:
        raise ToolError("CONTRACT_VIOLATION", "응답 data 타입이 올바르지 않습니다.")
    if result.status == "error" and result.data is not None:
        raise ToolError("CONTRACT_VIOLATION", "오류 응답에 data를 포함할 수 없습니다.")
    payload = canonical(asdict(result))
    if len(payload) > limits.max_response_bytes:
        raise ToolError("OUTPUT_LIMIT_EXCEEDED", "응답 크기 제한을 초과했습니다.")
    return payload
