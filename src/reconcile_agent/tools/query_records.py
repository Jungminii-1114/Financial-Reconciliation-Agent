"""
제한된 거래/중복/배치 비교 조회
"""

from dataclasses import dataclass
from typing import Literal

from reconcile_agent.business.repository import BusinessRepository, OperationRef


# 공통 TrustedCallContext·NodeResult·ToolError는 기존 모듈을 재사용한다.
# 이 파일은 타입 구조만 정의한다. dataclass·Literal은 런타임 검증을 수행하지 않는다.
QueryProfile = Literal["EXISTENCE", "DUPLICATE", "BATCH_COMPARISON"]
ReadTable = Literal[
    "transaction_history", "settlement_history", "operations", "reconciliation_results"
]


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

    limit: int = 50  # 상세 행 페이지 크기. 전체 배치 집계 범위를 줄이지 않음
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
    same_action_operation_ids: tuple[str, ...]
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

    max_transaction_ids: int = 50
    max_page_size: int = 50
    max_response_bytes: int = 128 * 1024
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class QueryRecordsServices:
    """호출 코드가 주입하는 의존성. 모델 입력으로 직렬화하지 않는다."""

    business_repository: BusinessRepository
    limits: QueryRecordsLimits
