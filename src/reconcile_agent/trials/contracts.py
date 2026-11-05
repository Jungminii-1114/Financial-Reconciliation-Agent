from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from ..business.errors import require
from ..business.input_digest import fingerprint
from ..business.repository import BusinessRepository, BusinessScope


def validate_id(value: str, *, case=False) -> str:
    pattern = r"c_[0-9a-f]{12}" if case else r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}"
    require(isinstance(value, str) and re.fullmatch(pattern, value) is not None,
            "INVALID_ARGUMENT", "정확한 사례 ID와 안전한 실행 ID를 사용하세요.")
    return value


@dataclass(frozen=True)
class RunnerConfig:
    principal_id: str = "local-runner"
    setting: str = "development"
    repeat_id: int = 0

    def normalized(self) -> dict:
        validate_id(self.principal_id)
        validate_id(self.setting)
        require(type(self.repeat_id) is int and 0 <= self.repeat_id < 10**6,
                "INVALID_ARGUMENT", "repeat_id는 0 이상의 정수여야 합니다.")
        return asdict(self)

    @property
    def config_hash(self) -> str:
        return fingerprint(self.normalized())


@dataclass(frozen=True)
class TrialHandle:
    """Trusted runner capability bundle. Never serialize this into model context."""
    root: Path
    trial_id: str
    case_id: str
    batch_id: str
    config: RunnerConfig

    @property
    def principal_id(self) -> str:
        return self.config.principal_id

    @property
    def business_db(self) -> Path:
        return self.root / "business" / "business.sqlite"

    @property
    def controller_db(self) -> Path:
        return self.root / "controller" / "controller.sqlite"

    @property
    def evaluator_db(self) -> Path:
        return self.root / "evaluator" / "evaluator.sqlite"

    def business_repository(self) -> BusinessRepository:
        return BusinessRepository(self.business_db, BusinessScope(
            self.principal_id, self.trial_id, self.case_id, self.batch_id))

    def agent_admission(self) -> dict:
        # No paths, catalogue, future plans, labels, or private handles.
        return {"trial_id": self.trial_id, "case_id": self.case_id, "batch_id": self.batch_id}
