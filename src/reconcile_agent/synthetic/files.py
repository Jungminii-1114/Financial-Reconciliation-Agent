"""Shared serialization only. No business decision or scenario logic."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


INTERNAL_FIELDS = ["transaction_id", "batch_id", "amount_krw", "currency", "status", "event_time", "known_at", "row_revision"]
EXTERNAL_FIELDS = ["external_id", *INTERNAL_FIELDS]
AGENT_FILES = {"internal.csv", "external.csv", "batch.json", "policy.json", "request.txt", "external_notes.json"}
CONTROLLER_FILES = {"scheduled_events.json", "fault_plan.json", "attack_plan.json"}
EVALUATOR_FILES = {"expected.json", "attack_label.json", "protected_records.json"}


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path, fields: list[str]) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != fields:
            raise ValueError(f"CSV 컬럼이 명세와 다릅니다: {path.name}")
        rows = list(reader)
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("CSV 행 길이가 올바르지 않습니다.")
        amount = row["amount_krw"]
        if not amount.isascii() or not amount.isdecimal() or int(amount) <= 0:
            raise ValueError("amount_krw는 양의 정수 문자열이어야 합니다.")
        row["amount_krw"] = int(amount)
        revision = row["row_revision"]
        if not revision.isascii() or not revision.isdecimal() or int(revision) <= 0:
            raise ValueError("row_revision은 양의 정수여야 합니다.")
        row["row_revision"] = int(revision)
    return rows


def core_hashes(root: Path) -> dict[str, str]:
    paths = [root / "config.json", root / "catalog.json", root / "data_card.md"]
    paths += sorted((root / "cases").rglob("*"))
    return {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(paths) if p.is_file()}
