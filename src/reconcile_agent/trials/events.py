"""Trusted controller input events. No Agent termination, approval, or model calls.

Input revisions, version, mutations, event receipt and virtual clock commit in the
same business transaction. Controller plans remain in their private read-only DB.
"""

from __future__ import annotations

import json

from ..business.connection import connect, transaction
from ..business.errors import require
from ..business.input_digest import canonical, fingerprint, input_digest, utc_time
from ..business.repository import metadata, read_financial_state
from .contracts import TrialHandle
from .lifecycle import insert_financial_row, resume_trial


def _validate_row(row, *, external, at, batch_id):
    fields = {"transaction_id", "batch_id", "amount_krw", "currency", "status", "event_time", "known_at", "row_revision"}
    if external:
        fields.add("external_id")
    require(set(row) == fields and row["batch_id"] == batch_id and row["currency"] == "KRW" and
            type(row["amount_krw"]) is int and 0 < row["amount_krw"] <= 10**12 and
            type(row["row_revision"]) is int and 0 < row["row_revision"] <= 100000 and
            row["status"] in ({"SETTLED", "PENDING"} if external else {"POSTED"}) and
            utc_time(row["event_time"]) <= utc_time(row["known_at"]) == at,
            "EVENT_PAYLOAD_CONFLICT", "이벤트 행의 타입·범위·시각 계약이 다릅니다.")


def _apply_group(db, group: dict, meta: dict) -> bool:
    """Private transaction helper. True=new application, False=receipt replay."""
    require(set(group) == {"group_id", "at", "expected_input_version", "new_input_version", "events"} and
            isinstance(group["events"], list) and bool(group["events"]),
            "EVENT_PAYLOAD_CONFLICT", "이벤트 묶음 스키마가 다릅니다.")
    case_id, batch_id = meta["case_id"], meta["batch_id"]
    at, payload_digest = utc_time(group["at"]), fingerprint(group)
    previous = db.execute("SELECT * FROM applied_event_groups WHERE case_id=? AND group_id=?",
                          (case_id, group["group_id"])).fetchone()
    if previous:
        require(previous["group_payload_digest"] == payload_digest,
                "EVENT_PAYLOAD_CONFLICT", "같은 사건 ID의 내용이 달라졌습니다.")
        return False
    current = db.execute("SELECT * FROM batch_versions WHERE case_id=? AND batch_id=? ORDER BY input_version DESC LIMIT 1",
                         (case_id, batch_id)).fetchone()
    old, new = group["expected_input_version"], group["new_input_version"]
    require(type(old) is int and type(new) is int and old == current["input_version"] and new == old+1 and
            current["known_at"] < at, "EVENT_VERSION_CONFLICT", "현재 입력과 사건의 전후 버전·순서가 다릅니다.")
    complete = bool(current["ingestion_complete"])
    mutations = []
    for index, event in enumerate(group["events"]):
        kind = event.get("type")
        if kind in {"insert_internal", "insert_external"}:
            require(set(event) == {"type", "row"}, "EVENT_PAYLOAD_CONFLICT", "삽입 이벤트 계약이 다릅니다.")
            row, external = event["row"], kind == "insert_external"
            _validate_row(row, external=external, at=at, batch_id=batch_id)
            insert_financial_row(db, case_id, row, external=external)
            table = "settlement_history" if external else "transaction_history"
            affected = {"transaction_ids": [row["transaction_id"]], "external_ids": [row["external_id"]] if external else []}
            changed = sorted(row)
        elif kind == "set_ingestion_complete":
            require(set(event) == {"type", "value"} and type(event["value"]) is bool,
                    "EVENT_PAYLOAD_CONFLICT", "도착 완료 이벤트 계약이 다릅니다.")
            complete = event["value"]
            table, affected, changed = "batch_versions", {"batch_ids": [batch_id]}, ["ingestion_complete"]
        elif kind in {"update_external", "delete_external"}:
            keys = {"type", "external_id", "expected_revision"}
            if kind == "update_external":
                keys |= {"new_revision", "patch"}
            require(set(event) == keys and type(event["expected_revision"]) is int,
                    "EVENT_PAYLOAD_CONFLICT", "정정 이벤트 계약이 다릅니다.")
            stored = db.execute("SELECT * FROM settlement_history WHERE case_id=? AND external_id=? AND superseded_known_at IS NULL",
                                (case_id, event["external_id"])).fetchone()
            require(stored is not None and stored["row_revision"] == event["expected_revision"] and stored["known_at"] < at,
                    "EVENT_REVISION_CONFLICT", "정정할 활성 revision이 다릅니다.")
            row = {k: stored[k] for k in ("external_id", "transaction_id", "batch_id", "amount_krw", "currency", "status", "event_time", "known_at", "row_revision")}
            db.execute("UPDATE settlement_history SET superseded_known_at=? WHERE case_id=? AND external_id=? AND row_revision=?",
                       (at, case_id, event["external_id"], stored["row_revision"]))
            changed = ["superseded_known_at"]
            if kind == "update_external":
                require(isinstance(event["patch"], dict) and "known_at" in event["patch"] and
                        set(event["patch"]) <= {"amount_krw", "status", "known_at"} and
                        type(event["new_revision"]) is int and event["new_revision"] == stored["row_revision"]+1,
                        "EVENT_REVISION_CONFLICT", "정정 필드 또는 새 revision이 다릅니다.")
                row.update(event["patch"])
                row["row_revision"] = event["new_revision"]
                _validate_row(row, external=True, at=at, batch_id=batch_id)
                insert_financial_row(db, case_id, row, external=True)
                changed = sorted([*event["patch"], "row_revision"])
            table = "settlement_history"
            affected = {"transaction_ids": [row["transaction_id"]], "external_ids": [row["external_id"]]}
        else:
            require(False, "EVENT_PAYLOAD_CONFLICT", "지원하지 않는 사건 종류입니다.")
        mutations.append((f"{case_id}:{group['group_id']}:{index}", case_id, batch_id, old, new, table,
                          canonical(affected).decode(), canonical(changed).decode(), at))
    view = read_financial_state(db, case_id, batch_id, at)
    rule = db.execute("SELECT business_rule_version FROM cases WHERE case_id=?", (case_id,)).fetchone()[0]
    digest = input_digest(case_id, batch_id, rule, view["internal"], view["external"], complete)
    db.execute("INSERT INTO batch_versions VALUES (?,?,?,?,?,?)", (case_id, batch_id, new, digest, int(complete), at))
    db.executemany("INSERT INTO input_mutations VALUES (?,?,?,?,?,?,?,?,?)", mutations)
    db.execute("INSERT INTO applied_event_groups VALUES (?,?,?,?,?,?)", (case_id, group["group_id"], payload_digest, old, new, at))
    return True


def advance_trial(handle: TrialHandle, to_at: str) -> dict:
    """Code-only controller API. Not registered as a model Tool."""
    handle = resume_trial(handle.root, handle.trial_id, handle.case_id, handle.config)
    target = utc_time(to_at)
    with connect(handle.controller_db) as controller, transaction(controller):
        groups = json.loads(controller.execute("SELECT payload FROM documents WHERE name='scheduled_events.json'").fetchone()[0])
    with connect(handle.business_db, writable=True) as db, transaction(db, write=True):
        meta = metadata(db)
        require(target >= meta["current_time"], "CLOCK_BACKWARDS", "가상 시계는 뒤로 이동할 수 없습니다.")
        applied, replayed = [], []
        for group in groups:
            if utc_time(group["at"]) <= target:
                (applied if _apply_group(db, group, meta) else replayed).append(group["group_id"])
        db.execute("UPDATE trial_metadata SET value=? WHERE key='current_time'", (target,))
        version = db.execute("SELECT MAX(input_version) FROM batch_versions").fetchone()[0]
    return {"trial_id": handle.trial_id, "case_id": handle.case_id, "current_time": target,
            "input_version": version, "applied_group_ids": applied, "replayed_group_ids": replayed,
            "faults_executed": False, "agent_runtime_executed": False}
