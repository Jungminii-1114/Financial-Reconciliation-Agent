"""Operator-only as-of reconstruction and SQLite export; no SQL knowledge required."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .config import aware_time
from .files import EXTERNAL_FIELDS, INTERNAL_FIELDS, digest, read_csv, read_json
from .recipes import reference_replay


def select_case(root: Path, case_id: str = "first") -> dict:
    entries = read_json(Path(root) / "catalog.json")["cases"]
    if case_id == "first":
        return next(e for e in entries if e["suite"] == "core" and e["recipe"] == "clean")
    matches = [entry for entry in entries if entry["case_id"] == case_id]
    if len(matches) != 1:
        raise ValueError("catalog에 없는 case ID입니다: " + case_id)
    return matches[0]


def read_snapshot(root: Path, entry: dict, at: str | None = None) -> dict:
    base = Path(root) / "cases" / entry["case_id"]
    public = base / "agent_view"
    batch = read_json(public / "batch.json")
    at = aware_time(at or batch["decision_at"]).isoformat(timespec="seconds")
    if aware_time(at) < aware_time(batch["decision_at"]):
        raise ValueError("초기 스냅샷 이전의 상태는 이 fixture로 복원하지 않습니다.")
    initial = {"internal": read_csv(public / "internal.csv", INTERNAL_FIELDS),
               "external": read_csv(public / "external.csv", EXTERNAL_FIELDS),
               "batch": batch, "policy": read_json(public / "policy.json"),
               "groups": read_json(base / "controller_only" / "scheduled_events.json")}
    state = reference_replay(initial, at)
    state["external_notes"] = read_json(public / "external_notes.json")
    state["case_id"] = entry["case_id"]
    state["input_digest"] = digest({"batch": {k: v for k, v in state["batch"].items() if k != "decision_at"},
                                   "internal": sorted(state["internal"], key=lambda r: r["transaction_id"]),
                                   "external": sorted(state["external"], key=lambda r: r["external_id"])})
    return state


def export_sqlite(root: Path, output: Path, at: str | None = None,
                  case_id: str | None = None, internal_artifact=False) -> dict:
    root, output = Path(root).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("기존 SQLite 파일을 덮어쓰지 않습니다: " + str(output))
    if output.resolve().is_relative_to(root):
        allowed = root / "operator_view" / "initial_snapshot.sqlite"
        if not internal_artifact or output.resolve() != allowed:
            raise ValueError("별도 export 파일은 데이터셋 폴더 바깥에 저장하세요.")
    entries = [select_case(root, case_id)] if case_id else read_json(root / "catalog.json")["cases"]
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".snapshot-", suffix=".sqlite", dir=output.parent)
    os.close(fd)
    temp = Path(temp)
    counts = {"cases": 0, "internal_rows": 0, "external_rows": 0}
    try:
        with closing(sqlite3.connect(temp)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript('''
                CREATE TABLE batches (
                    case_id TEXT NOT NULL, batch_id TEXT NOT NULL,
                    input_version INTEGER NOT NULL CHECK(input_version>0),
                    ingestion_complete INTEGER NOT NULL CHECK(ingestion_complete IN (0,1)),
                    run_status TEXT NOT NULL, as_of TEXT NOT NULL, input_digest TEXT NOT NULL,
                    PRIMARY KEY(case_id,batch_id));
                CREATE TABLE internal_transactions (
                    case_id TEXT NOT NULL, transaction_id TEXT NOT NULL, batch_id TEXT NOT NULL,
                    amount_krw INTEGER NOT NULL CHECK(amount_krw>0), currency TEXT NOT NULL,
                    status TEXT NOT NULL, event_time TEXT NOT NULL, known_at TEXT NOT NULL,
                    row_revision INTEGER NOT NULL CHECK(row_revision>0),
                    PRIMARY KEY(case_id,transaction_id), FOREIGN KEY(case_id,batch_id) REFERENCES batches(case_id,batch_id));
                CREATE TABLE external_settlements (
                    case_id TEXT NOT NULL, external_id TEXT NOT NULL, transaction_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL, amount_krw INTEGER NOT NULL CHECK(amount_krw>0),
                    currency TEXT NOT NULL, status TEXT NOT NULL, event_time TEXT NOT NULL,
                    known_at TEXT NOT NULL, row_revision INTEGER NOT NULL CHECK(row_revision>0),
                    PRIMARY KEY(case_id,external_id), FOREIGN KEY(case_id,batch_id) REFERENCES batches(case_id,batch_id));
                CREATE TABLE external_notes (
                    case_id TEXT NOT NULL, note_id TEXT NOT NULL, record_ref TEXT NOT NULL,
                    origin TEXT NOT NULL, known_at TEXT NOT NULL, content TEXT NOT NULL,
                    PRIMARY KEY(case_id,note_id));
                CREATE TABLE case_policies (
                    case_id TEXT PRIMARY KEY, business_rule_version TEXT NOT NULL,
                    policy_version TEXT NOT NULL, approval_required INTEGER NOT NULL,
                    approval_ttl_seconds INTEGER NOT NULL, reprocess_cutoff_at TEXT);
                CREATE TABLE export_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE INDEX settlement_transaction ON external_settlements(case_id,transaction_id);
            ''')
            for entry in entries:
                state = read_snapshot(root, entry, at)
                cid, batch, policy = entry["case_id"], state["batch"], state["policy"]
                db.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?)", (cid, batch["batch_id"], batch["input_version"],
                           int(batch["ingestion_complete"]), batch["run_status"], batch["decision_at"], state["input_digest"]))
                db.executemany("INSERT INTO internal_transactions VALUES (?,?,?,?,?,?,?,?,?)",
                               [[cid, *[row[k] for k in INTERNAL_FIELDS]] for row in state["internal"]])
                db.executemany("INSERT INTO external_settlements VALUES (?,?,?,?,?,?,?,?,?,?)",
                               [[cid, *[row[k] for k in EXTERNAL_FIELDS]] for row in state["external"]])
                db.executemany("INSERT INTO external_notes VALUES (?,?,?,?,?,?)", [
                    (cid, note["note_id"], note["record_ref"], note["origin"], note["known_at"], note["content"])
                    for note in state["external_notes"]])
                db.execute("INSERT INTO case_policies VALUES (?,?,?,?,?,?)", (cid, policy["business_rule_version"],
                           policy["policy_version"], int(policy["approval_required"]), policy["approval_ttl_seconds"], policy["reprocess_cutoff_at"]))
                counts["cases"] += 1
                counts["internal_rows"] += len(state["internal"])
                counts["external_rows"] += len(state["external"])
            metadata = {"visibility": "OPERATOR_ONLY", "runtime_executed": "false",
                        "dataset_fingerprint": read_json(root / "manifest.json")["dataset_fingerprint"],
                        "as_of_mode": at or "INITIAL_SNAPSHOTS"}
            db.executemany("INSERT INTO export_metadata VALUES (?,?)", metadata.items())
            if db.execute("PRAGMA foreign_key_check").fetchall() or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite 무결성 검사 실패")
        # Exclusive destination reservation prevents accidental overwrite, including races.
        with output.open("xb") as reservation:
            reservation.write(temp.read_bytes())
    finally:
        temp.unlink(missing_ok=True)
    return {"output": str(output), **counts, "runtime_executed": False}
