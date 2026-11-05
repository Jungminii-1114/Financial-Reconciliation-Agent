"""Strict configuration with no wall-clock defaults or external dependencies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime


def aware_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("시각은 offset이 포함된 ISO 8601 문자열이어야 합니다.")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("시각에 timezone offset이 필요합니다: " + value)
    return result


@dataclass(frozen=True)
class DatasetConfig:
    seed: int = 42
    rows_per_batch: int = 100
    profile: str = "pilot"
    decision_at: str = "2026-11-05T10:00:00+09:00"
    security_pairs_per_split: int | None = None

    def normalized(self) -> "DatasetConfig":
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError("seed는 0 이상 2**63 미만 정수여야 합니다.")
        if type(self.rows_per_batch) is not int or not 3 <= self.rows_per_batch <= 5000:
            raise ValueError("rows_per_batch는 3~5000 정수여야 합니다.")
        if not isinstance(self.profile, str) or self.profile not in {"pilot", "benchmark"}:
            raise ValueError("profile은 pilot 또는 benchmark여야 합니다.")
        at = aware_time(self.decision_at)
        pairs = self.security_pairs_per_split
        if pairs is None:
            pairs = 2 if self.profile == "pilot" else 20
        minimum = 2 if self.profile == "pilot" else 4
        if type(pairs) is not int or not minimum <= pairs <= 100:
            raise ValueError(f"이 profile의 security_pairs_per_split은 {minimum}~100이어야 합니다.")
        return DatasetConfig(self.seed, self.rows_per_batch, self.profile,
                             at.isoformat(timespec="seconds"), pairs)

    def to_dict(self) -> dict:
        return asdict(self.normalized())

    @classmethod
    def from_dict(cls, value: dict) -> "DatasetConfig":
        if not isinstance(value, dict):
            raise ValueError("설정 파일은 JSON object여야 합니다.")
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("알 수 없는 설정: " + ", ".join(sorted(unknown)))
        return cls(**value).normalized()
