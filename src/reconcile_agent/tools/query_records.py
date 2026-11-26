"""
제한된 거래/중복/배치 비교 조회
"""

import hmac
import re
from dataclasses import asdict, dataclass, replace
from time import monotonic
from typing import Any, Literal

from reconcile_agent.business.errors import AdapterError, require
from reconcile_agent.business.input_digest import canonical, fingerprint, utc_time
from reconcile_agent.business.reconciliation import (
    compare_records, current_operation, financial_snapshot, row_ref, scoped_transaction, verify_effect,
)
from reconcile_agent.business.repository import BusinessRepository, OperationRef
from reconcile_agent.tools.contracts import NodeResult, TrustedCallContext, resolve_target
from reconcile_agent.tools.errors import ToolError


# 공통 TrustedCallContext·NodeResult·ToolError는 기존 모듈을 재사용한다.
# dataclass·Literal은 런타임 검증을 수행하지 않는다. 아래 요청 검증 함수를 사용한다.
QueryProfile = Literal["EXISTENCE", "DUPLICATE", "BATCH_COMPARISON"]
ReadTable = Literal[
    "transaction_history", "settlement_history", "operations", "reconciliation_results"
]

_MAX_TRANSACTION_IDS = 50
_MAX_PAGE_SIZE = 50
_REQUIRED_FIELDS = frozenset({"case_id", "batch_id", "profile", "as_of", "expected_input_version"})
_ALLOWED_FIELDS = _REQUIRED_FIELDS | {"transaction_ids", "result_ref", "action_digest", "limit", "cursor"}
_AS_OF_FORMAT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


@dataclass(frozen=True)
class QueryRecordsRequest:
    """모델 업무 인자. ctx·DB 경로·임의 SQL은 요청에 포함하지 않는다."""

    case_id: str
    batch_id: str
    profile: QueryProfile
    as_of: str  # offset이 있는 ISO 8601. 업무 DB의 현재 시각 이하인지 검사할 예정
    expected_input_version: int

    # EXISTENCE에서만 필수, 1~50개. 다른 profile은 이 필드를 거부할 예정
    transaction_ids: tuple[str, ...] | None = None
    # BATCH_COMPARISON에서만 선택적으로 사용
    result_ref: str | None = None
    # DUPLICATE에서만 사용. 다른 action_digest의 기존 작업을 숨기는 필터가 아님
    action_digest: str | None = None

    limit: int = _MAX_PAGE_SIZE  # 상세 행 페이지 크기. 전체 배치 집계 범위를 줄이지 않음
    cursor: str | None = None  # 서버가 발급하고 scope/query/snapshot에 묶을 예정


@dataclass(frozen=True)
class QueryCoverage:
    """필수 조회의 완결성과 상세 페이지의 완결성을 구분한다."""

    complete: bool
    summary_complete: bool
    details_complete: bool
    next_cursor: str | None


@dataclass(frozen=True)
class QueryPredicate:
    """서버가 검증·정규화한 profile별 조건. 요청 원문을 그대로 신뢰하지 않는다."""

    transaction_ids: tuple[str, ...] | None = None
    result_ref: str | None = None
    action_digest: str | None = None


@dataclass(frozen=True)
class QueryFootprint:
    """이후 변경 영향 분석에서 어떤 범위를 조회했는지 확인하는 정보."""

    case_id: str
    batch_id: str
    table: ReadTable  # 서버가 정하는 관측 범위. 모델이 테이블을 선택하는 인자가 아님
    # None은 배치 전체, tuple은 명시적으로 한정한 거래 집합을 나타내도록 설계
    transaction_ids: tuple[str, ...] | None = None
    additional_tables: tuple[ReadTable, ...] = ()


@dataclass(frozen=True)
class ExistenceFacts:
    """지정 거래 ID의 외부 정산 기록 존재 여부. 없는 것과 조회 실패는 구분한다."""

    matched_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateFacts:
    """동일 업무 키의 기존 작업 참조. 거래 행 중복은 배치 비교에서 다룬다."""

    operation_refs: tuple[OperationRef, ...]
    # action_digest가 주어졌을 때 의미가 일치하는 작업 ID의 보조 목록
    # operation_refs에는 의미가 다른 기존 작업도 유지해야 한다.
    same_action_operation_ids: tuple[str, ...] | None
    # 이 타입만으로 NOT_APPLIED나 재시도 허가를 확정하지 않는다. 효과 조회는 TOOL-05.


@dataclass(frozen=True)
class BatchComparisonFacts:
    """허용된 전체 배치 집계. 합계가 같아도 거래별 불일치를 드러낸다."""

    internal_count: int
    external_count: int
    matched_count: int
    mismatched_ids: tuple[str, ...]
    missing_external_ids: tuple[str, ...]
    unexpected_external_ids: tuple[str, ...]
    duplicate_transaction_ids: tuple[str, ...]
    unsettled_transaction_ids: tuple[str, ...]
    ingestion_complete: bool
    result_verified: bool | None = None


StructuredFacts = ExistenceFacts | DuplicateFacts | BatchComparisonFacts


@dataclass(frozen=True)
class QueryRecordsData:
    """NodeResult.data에 넣을 공개 응답. profile과 facts 타입의 대응은 검증할 예정."""

    query_profile: QueryProfile
    predicate: QueryPredicate
    input_version: int
    input_digest: str
    as_of: str
    structured_facts: StructuredFacts
    row_refs: tuple[str, ...]
    coverage: QueryCoverage
    footprint: QueryFootprint
    result_digest: str
    external_content_refs: tuple[str, ...]  # 구조화 사실과 분리한 비신뢰 메모 참조


@dataclass(frozen=True)
class QueryRecordsLimits:
    """서버 설정의 초기 제안값. 요청 인자로 변경하지 않는다."""

    max_transaction_ids: int = _MAX_TRANSACTION_IDS
    max_page_size: int = _MAX_PAGE_SIZE
    max_response_bytes: int = 128 * 1024
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if (
            type(self.max_transaction_ids) is not int
            or not 1 <= self.max_transaction_ids <= _MAX_TRANSACTION_IDS
            or type(self.max_page_size) is not int
            or not 1 <= self.max_page_size <= _MAX_PAGE_SIZE
            or type(self.max_response_bytes) is not int
            or not 512 <= self.max_response_bytes <= 128 * 1024
            or type(self.timeout_seconds) not in (int, float)
            or not 0 < self.timeout_seconds <= 60
        ):
            raise ToolError("CONTRACT_VIOLATION", "조회 한도 설정이 올바르지 않습니다.")


@dataclass(frozen=True)
class QueryRecordsServices:
    """호출 코드가 주입하는 의존성. 모델 입력으로 직렬화하지 않는다."""

    business_repository: BusinessRepository
    limits: QueryRecordsLimits
    cursor_key: bytes | None = None


def parse_query_records_request(
    payload: dict[str, Any], *, limits: QueryRecordsLimits
) -> QueryRecordsRequest:
    """디코딩한 JSON 인자를 검증하고 정규화된 업무 요청을 반환한다."""
    if type(payload) is not dict:
        raise ToolError("INVALID_ARGUMENT", "객체 형태의 인자가 필요합니다.")
    fields = set(payload)
    if not _REQUIRED_FIELDS <= fields <= _ALLOWED_FIELDS:
        raise ToolError("INVALID_ARGUMENT", "요청 필드가 올바르지 않습니다.")

    transaction_ids = None
    if "transaction_ids" in payload:
        raw_ids = payload["transaction_ids"]
        if type(raw_ids) is not list:
            raise ToolError("INVALID_ARGUMENT", "transaction_ids는 JSON 배열이어야 합니다.")
        if len(raw_ids) > limits.max_transaction_ids:
            raise ToolError("INVALID_ARGUMENT", "transaction_ids가 허용 개수를 초과했습니다.")
        transaction_ids = tuple(raw_ids)

    request = QueryRecordsRequest(
        case_id=payload["case_id"],
        batch_id=payload["batch_id"],
        profile=payload["profile"],
        as_of=payload["as_of"],
        expected_input_version=payload["expected_input_version"],
        transaction_ids=transaction_ids,
        result_ref=payload.get("result_ref"),
        action_digest=payload.get("action_digest"),
        limit=payload.get("limit", limits.max_page_size),
        cursor=payload.get("cursor"),
    )
    return _normalize_query_records_request(request, limits=limits)


def validate_query_records_request(
    request: QueryRecordsRequest, *, limits: QueryRecordsLimits
) -> None:
    """직접 생성한 요청에도 같은 값·profile 규칙을 적용한다."""
    _normalize_query_records_request(request, limits=limits)


def _is_request_text(value: object) -> bool:
    return type(value) is str and bool(value) and value.isprintable() and value == value.strip()


def _normalize_query_records_request(
    request: QueryRecordsRequest, *, limits: QueryRecordsLimits
) -> QueryRecordsRequest:
    """요청의 불변조건을 검사한 뒤 UTC 시각과 정렬된 ID를 가진 새 객체를 반환한다."""
    if type(request) is not QueryRecordsRequest:
        raise ToolError("INVALID_ARGUMENT", "요청 객체 타입이 올바르지 않습니다.")
    if type(request.profile) is not str or request.profile not in ("EXISTENCE", "DUPLICATE", "BATCH_COMPARISON"):
        raise ToolError("INVALID_QUERY_PROFILE", "지원하지 않는 거래 조회 profile입니다.")
    if type(request.case_id) is not str or re.fullmatch(r"c_[0-9a-f]{12}", request.case_id) is None:
        raise ToolError("INVALID_ARGUMENT", "case_id 형식이 올바르지 않습니다.")
    if not _is_request_text(request.batch_id) or len(request.batch_id) > 128:
        raise ToolError("INVALID_ARGUMENT", "batch_id는 1~128자의 유효한 문자열이어야 합니다.")
    if type(request.expected_input_version) is not int or request.expected_input_version < 1:
        raise ToolError("INVALID_ARGUMENT", "expected_input_version은 양의 정수여야 합니다.")
    if type(request.limit) is not int or not 1 <= request.limit <= limits.max_page_size:
        raise ToolError("INVALID_ARGUMENT", "limit가 허용된 상세 페이지 범위를 벗어났습니다.")
    if request.cursor is not None and not _is_request_text(request.cursor):
        raise ToolError("INVALID_ARGUMENT", "cursor는 null 또는 유효한 문자열이어야 합니다.")
    if request.result_ref is not None and (request.profile != "BATCH_COMPARISON" or not _is_request_text(request.result_ref)):
        raise ToolError("INVALID_ARGUMENT", "result_ref는 배치 비교에서만 허용되는 문자열입니다.")
    if request.action_digest is not None and (request.profile != "DUPLICATE" or type(request.action_digest) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", request.action_digest) is None):
        raise ToolError("INVALID_ARGUMENT", "action_digest는 기존 작업 조회에서만 허용되는 digest입니다.")

    transaction_ids = request.transaction_ids
    if request.profile == "EXISTENCE":
        if type(transaction_ids) is not tuple or not 1 <= len(transaction_ids) <= limits.max_transaction_ids:
            raise ToolError("INVALID_ARGUMENT", "transaction_ids 개수가 허용 범위를 벗어났습니다.")
        if not all(_is_request_text(transaction_id) for transaction_id in transaction_ids):
            raise ToolError("INVALID_ARGUMENT", "거래 ID는 비어 있지 않은 유효한 문자열이어야 합니다.")
        if len(set(transaction_ids)) != len(transaction_ids):
            raise ToolError("INVALID_ARGUMENT", "중복된 거래 ID는 허용되지 않습니다.")
        transaction_ids = tuple(sorted(transaction_ids))
    elif transaction_ids is not None:
        raise ToolError("INVALID_ARGUMENT", "transaction_ids는 존재 조회에서만 허용됩니다.")

    timestamp_error = "as_of는 시간대가 있는 ISO 8601 시각이어야 합니다."
    if type(request.as_of) is not str or _AS_OF_FORMAT.fullmatch(request.as_of) is None:
        raise ToolError("INVALID_ARGUMENT", timestamp_error)
    try:
        as_of = utc_time(request.as_of)
    except AdapterError as exc:
        raise ToolError("INVALID_ARGUMENT", timestamp_error) from exc

    return replace(request, as_of=as_of, transaction_ids=transaction_ids)


def _page(refs, request, binding, key):
    def sign(position):
        if type(key) is not bytes or len(key) < 32:
            raise ToolError("CONTRACT_VIOLATION", "페이지 cursor 키가 설정되지 않았습니다.")
        return hmac.digest(key, canonical([binding, position]), "sha256").hex()

    start = 0
    if request.cursor is not None:
        match = re.fullmatch(r"([1-9][0-9]{0,7})\.([0-9a-f]{64})", request.cursor)
        if match is None:
            raise ToolError("INVALID_CURSOR", "페이지 cursor가 올바르지 않습니다.")
        start = int(match[1])
        if start >= len(refs) or not hmac.compare_digest(match[2], sign(start)):
            raise ToolError("INVALID_CURSOR", "페이지 cursor의 조회 조건이 다릅니다.")
    stop = start + request.limit
    cursor = f"{stop}.{sign(stop)}" if stop < len(refs) else None
    return tuple(refs[start:stop]), QueryCoverage(True, True, cursor is None, cursor)


def _query_facts(db, meta, scope, policy, request, state):
    if request.profile == "EXISTENCE":
        ids = set(request.transaction_ids)
        rows = [row for row in state["external"] if row["transaction_id"] in ids]
        found = {row["transaction_id"] for row in rows}
        facts = ExistenceFacts(tuple(sorted(found)), tuple(sorted(ids - found)))
        return facts, [row_ref("settlement_history", r) for r in rows], QueryFootprint(
            scope.case_id, scope.batch_id, "settlement_history", request.transaction_ids), (
                "records_observed" if found else "no_matching_records")
    if request.profile == "DUPLICATE":
        op = current_operation(db, scope, request.expected_input_version)
        if op is not None:
            require(op["state"] == "SUCCEEDED" and op["committed_at"], "SNAPSHOT_INCOMPLETE",
                    "기존 작업의 과거 상태를 확인할 수 없습니다.")
        refs = ()
        if op is not None and op["committed_at"] <= request.as_of:
            refs = (OperationRef(op["operation_id"], op["idempotency_key"], op["input_version"], op["action_digest"]),)
        same = None if request.action_digest is None else tuple(
            item.operation_id for item in refs if item.action_digest == request.action_digest)
        return DuplicateFacts(refs, same), [item.operation_id for item in refs], QueryFootprint(
            scope.case_id, scope.batch_id, "operations"), ("records_observed" if refs else "no_matching_records")

    verified = None
    tables = ("settlement_history",)
    if request.result_ref is not None:
        op = current_operation(db, scope, request.expected_input_version)
        require(op is not None and op["result_ref"] == request.result_ref and op["committed_at"]
                and op["committed_at"] <= request.as_of, "WRONG_RESULT_BINDING",
                "시점과 대상에 연결되는 결과 참조가 없습니다.")
        verify_effect(db, scope, meta, policy, op)
        verified = True
        tables += ("operations", "reconciliation_results")
    refs = [row_ref("transaction_history", r) for r in state["internal"]]
    refs.extend(row_ref("settlement_history", r) for r in state["external"])
    return BatchComparisonFacts(**compare_records(state), result_verified=verified), refs, QueryFootprint(
        scope.case_id, scope.batch_id, "transaction_history", additional_tables=tables), "records_observed"


def query_records(request: QueryRecordsRequest, ctx: TrustedCallContext,
                  services: QueryRecordsServices) -> NodeResult[QueryRecordsData]:
    request = _normalize_query_records_request(request, limits=services.limits)
    scope = resolve_target(ctx, "query_records", request.case_id, request.batch_id)
    deadline = monotonic() + services.limits.timeout_seconds
    with scoped_transaction(services.business_repository, scope, ctx.policy_version, ctx.config_hash,
                            deadline=deadline) as (db, meta, policy):
        state, at = financial_snapshot(db, scope, meta, policy, request.as_of)
        batch = state["batch"]
        require(batch["input_version"] == request.expected_input_version,
                "VERSION_CONFLICT", "조회 시점의 입력 버전이 요청과 다릅니다.")
        predicate = QueryPredicate(request.transaction_ids, request.result_ref, request.action_digest)
        facts, refs, footprint, outcome = _query_facts(db, meta, scope, policy, request, state)
        # Include the ordered reference set so newly committed same-time operations invalidate cursors.
        identity = fingerprint([asdict(scope), ctx.policy_version, ctx.config_hash, request.profile,
                                asdict(predicate), at, batch["input_version"], batch["input_digest"],
                                request.limit, refs])
        page_refs, coverage = _page(refs, request, identity, services.cursor_key)
        note_refs = tuple(row[0] for row in db.execute("""SELECT content_ref FROM external_notes
            WHERE case_id=? AND known_at<=? ORDER BY note_id,context_revision LIMIT 257""", (scope.case_id, at)))
        require(len(note_refs) <= 256, "OUTPUT_LIMIT_EXCEEDED", "외부 메모 참조 한도를 초과했습니다.")
    # The evidence digest represents the full observation; it stays stable across detail pages.
    digest = fingerprint([identity, asdict(facts), asdict(footprint), note_refs])
    data = QueryRecordsData(request.profile, predicate, batch["input_version"], batch["input_digest"],
                            at, facts, page_refs, coverage, footprint, digest, note_refs)
    validate_query_records_data(data, request)
    return NodeResult("success", data, {}, outcome)


def validate_query_records_data(data: QueryRecordsData, request: QueryRecordsRequest) -> None:
    expected = {"EXISTENCE": ExistenceFacts, "DUPLICATE": DuplicateFacts, "BATCH_COMPARISON": BatchComparisonFacts}
    if (type(data) is not QueryRecordsData or data.query_profile != request.profile
        or type(data.structured_facts) is not expected[request.profile]
        or (data.footprint.case_id, data.footprint.batch_id) != (request.case_id, request.batch_id)
        or data.input_version != request.expected_input_version or data.as_of != request.as_of
        or data.predicate != QueryPredicate(request.transaction_ids, request.result_ref, request.action_digest)
        or not data.coverage.complete or not data.coverage.summary_complete
        or data.coverage.details_complete != (data.coverage.next_cursor is None)
        or len(data.row_refs) > request.limit):
        raise ToolError("CONTRACT_VIOLATION", "거래 조회 결과가 요청 범위와 일치하지 않습니다.")
    if isinstance(data.structured_facts, ExistenceFacts):
        records = data.structured_facts.matched_ids + data.structured_facts.missing_ids
        if tuple(sorted(records)) != request.transaction_ids:
            raise ToolError("CONTRACT_VIOLATION", "존재 조회 결과가 요청 거래 집합을 포함하지 않습니다.")
