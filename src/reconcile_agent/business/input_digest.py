"""Versioned business identity, distinct from the operator snapshot digest."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .errors import AdapterError


def utc_time(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError()
        return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, TypeError, OverflowError) as exc:
        raise AdapterError("INVALID_TIMESTAMP", "offset이 있는 ISO 시각이 필요합니다.") from exc


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(value) -> str:
    return "sha256:" + sha256(canonical(value))


def normalize_row(row: dict) -> dict:
    result = dict(row)
    result["event_time"] = utc_time(result["event_time"])
    result["known_at"] = utc_time(result["known_at"])
    return result


def financial_projection(internal: list[dict], external: list[dict], complete: bool) -> dict:
    """No case identity: suitable for paired financial-input comparisons only."""
    return {"internal": sorted((normalize_row(r) for r in internal), key=lambda r: r["transaction_id"]),
            "external": sorted((normalize_row(r) for r in external), key=lambda r: r["external_id"]),
            "ingestion_complete": complete}


def input_digest(case_id: str, batch_id: str, rule_version: str,
                 internal: list[dict], external: list[dict], complete: bool) -> str:
    return fingerprint({"schema": "business-input-v1", "case_id": case_id,
                        "batch_id": batch_id, "business_rule_version": rule_version,
                        **financial_projection(internal, external, complete)})


def action_digest(case_id: str, batch_id: str, version: int, digest: str,
                  rule_version: str, policy_version: str) -> str:
    return fingerprint({"schema": "business-action-v1", "action": "REPROCESS",
                        "case_id": case_id, "batch_id": batch_id, "input_version": version,
                        "input_digest": digest, "business_rule_version": rule_version,
                        "policy_version": policy_version})
