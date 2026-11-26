"""Local web operator workspace for the existing reconciliation CLI and SQLite state."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .business.connection import connect, transaction
from .business.input_digest import fingerprint
from .business.reconciliation import compare_records
from .evaluation.mvp import evaluate
from .synthetic.config import DatasetConfig
from .synthetic.files import read_json
from .synthetic.generator import generate_dataset
from .trials.lifecycle import prepare_trial, resume_trial


SCENARIOS = {
    "clean": ("정상 재처리", "자료가 모두 도착했고 내부 거래와 외부 정산이 일치합니다."),
    "delayed_arrival": ("정산 자료 지연", "외부 정산 한 건이 늦게 도착합니다. v1과 v2를 비교합니다."),
    "amount_swap": ("총액은 같고 거래는 불일치", "두 거래의 금액이 바뀌어 합계만 보면 이상이 없습니다."),
    "before_commit": ("반영 전 중단", "커밋 전에 작업 프로세스를 종료하고 같은 의도로 재개합니다."),
    "after_commit": ("반영 후 응답 유실", "DB 커밋 직후 프로세스를 종료하고 기존 결과를 회수합니다."),
}
FAULTS = {"before_commit": ("before-commit", 71), "after_commit": ("after-commit", 72)}
APPROVER = "web-demo-operator"


class WorkspaceError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


class WebWorkspace:
    def __init__(self, root: Path, worker: Path, *, model: str | None = None):
        self.root, self.worker, self.model = root, worker, model
        self.dataset = root / "dataset"
        if not self.dataset.exists():
            generate_dataset(self.dataset, DatasetConfig(rows_per_batch=3))
        self.catalog = {row["recipe"]: row["case_id"] for row in read_json(self.dataset / "catalog.json")["cases"]
                        if row["suite"] == "core" and row["recipe"] in SCENARIOS}
        if set(self.catalog) != set(SCENARIOS):
            raise WorkspaceError(500, "DATASET_UNAVAILABLE")
        self.locks = {key: Lock() for key in SCENARIOS}

    def list_cases(self):
        return [{"id": key, "title": title, "description": description, "case_id": self.catalog[key]}
                for key, (title, description) in SCENARIOS.items()]

    def _case_folder(self, key):
        if key not in SCENARIOS:
            raise WorkspaceError(404, "CASE_NOT_FOUND")
        return self.root / "trials" / key

    def _load_handle(self, key):
        folder = self._case_folder(key)
        pointer = folder / "active"
        if not pointer.exists():
            return None
        trial_id = pointer.read_text(encoding="ascii").strip()
        if len(trial_id) != 32 or any(value not in "0123456789abcdef" for value in trial_id):
            raise WorkspaceError(500, "TRIAL_UNAVAILABLE")
        return resume_trial(folder / trial_id, trial_id, self.catalog[key])

    def _prepare(self, key):
        folder = self._case_folder(key)
        folder.mkdir(parents=True, exist_ok=True)
        trial_id = uuid4().hex
        prepare_trial(self.dataset, self.catalog[key], trial_id, folder / trial_id)
        pointer = folder / (".active-" + trial_id)
        pointer.write_text(trial_id + "\n", encoding="ascii")
        pointer.replace(folder / "active")

    def _observe(self, key, handle):
        title, description = SCENARIOS[key]
        state = {"id": key, "title": title, "description": description, "case_id": self.catalog[key],
                 "trial_id": None, "stage": "NEW", "available": ["start"], "model": self.model}
        if handle is not None:
            state = self._observe_trial(state, handle)
        return {**state, "revision": fingerprint(state)}

    def _observe_trial(self, state, handle):
        snapshot = handle.business_repository().snapshot()
        batch = snapshot["batch"]
        version = batch["input_version"]
        run_id = f"web-v{version}"
        runtime = handle.root.with_name(handle.root.name + ".runtime")
        run = {}
        ledger_path = runtime / "calls.sqlite"
        journal_path = runtime / "journal.sqlite"
        audit, budget = [], {"used": 0, "max_attempts": 20}
        if ledger_path.is_file():
            with connect(ledger_path) as db, transaction(db):
                audit = [dict(row) for row in db.execute("""SELECT call_id,run_id,session_id,tool_name,
                    started_at,status,code,elapsed_ms FROM calls ORDER BY rowid""")]
                saved = db.execute("SELECT used,max_attempts FROM budgets WHERE group_id=?", (handle.trial_id,)).fetchone()
                if saved:
                    budget = dict(saved)
        if journal_path.is_file():
            with connect(journal_path) as db, transaction(db):
                row = db.execute("SELECT record FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if row:
                    run = json.loads(row[0])
        with connect(handle.business_db) as db, transaction(db):
            row = db.execute("""SELECT report_facts,verification_digest FROM completion_decisions
                WHERE logical_run_id=? AND case_id=? AND batch_id=?""", (run_id, handle.case_id, handle.batch_id)).fetchone()
            decision = {**json.loads(row[0]), "verification_digest": row[1]} if row else None
            ops = [dict(r) for r in db.execute("""SELECT operation_id,input_version,state,result_ref,committed_at
                FROM operations WHERE case_id=? AND batch_id=? ORDER BY input_version""", (handle.case_id, handle.batch_id))]
            approvals = [dict(r) for r in db.execute("""SELECT approval_id,input_version,granted_by,expires_at,status
                FROM approvals WHERE case_id=? AND batch_id=? ORDER BY input_version,expires_at,approval_id""",
                (handle.case_id, handle.batch_id))]
            result_count = db.execute("""SELECT COUNT(*) FROM reconciliation_results r JOIN operations o
                ON r.operation_id=o.operation_id WHERE o.case_id=? AND o.batch_id=?""",
                (handle.case_id, handle.batch_id)).fetchone()[0]
            decision_count = db.execute("SELECT COUNT(*) FROM completion_decisions").fetchone()[0]

        outstanding = next((item for item in reversed(audit) if item["run_id"] == run_id
                            and item["tool_name"] == "execute_reprocess"), None)
        approved = next((a for a in approvals if a["approval_id"] == run.get("approval_id")
                         and a["status"] == "APPROVED" and a["expires_at"] > snapshot["as_of"]), None)
        if decision:
            stage, available = "FINAL", []
            if state["id"] == "delayed_arrival" and version == 1 and decision["outcome"] == "WAITING_FOR_INPUT":
                available.append("advance")
        elif run.get("intent") and outstanding and (outstanding["status"] == "RESERVED"
              or (ops and ops[-1]["input_version"] == version)):
            stage, available = "INTERRUPTED", ["resume"]
        elif run.get("proposed") and approved:
            stage, available = "APPROVED", ["interrupt" if state["id"] in FAULTS else "run"]
        elif run.get("proposed"):
            stage, available = "PAUSED_FOR_APPROVAL", ["approve"]
        else:
            stage, available = "READY", ["run"]
        summary = compare_records(snapshot)
        visible_rows = ("transaction_id", "amount_krw", "currency", "status", "row_revision")
        return {**state, "stage": stage, "available": [*available, "start"], "trial_id": handle.trial_id,
                "batch_id": handle.batch_id, "run_id": run_id, "as_of": snapshot["as_of"],
                "input_version": version, "input_digest": batch["input_digest"],
                "processing_state": snapshot["processing_state_current"], "summary": summary,
                "internal_total_krw": sum(r["amount_krw"] for r in snapshot["internal"]),
                "external_total_krw": sum(r["amount_krw"] for r in snapshot["external"]),
                "internal": [{field: r[field] for field in visible_rows} for r in snapshot["internal"]],
                "external": [{**{field: r[field] for field in visible_rows}, "external_id": r["external_id"]}
                             for r in snapshot["external"]],
                "model": run.get("model", self.model), "model_advice": (run.get("advice") or {}).get("action"),
                "decision": decision, "evaluation": evaluate(handle, run_id) if decision else None,
                "approvals": approvals, "operations": ops, "result_count": result_count,
                "decision_count": decision_count, "audit": audit, "budget": budget}

    def case(self, key):
        self._case_folder(key)
        if not self.locks[key].acquire(timeout=0.75):
            raise WorkspaceError(409, "RUN_BUSY")
        try:
            return self._observe(key, self._load_handle(key))
        finally:
            self.locks[key].release()

    def act(self, key, action, revision):
        self._case_folder(key)
        if not self.locks[key].acquire(blocking=False):
            raise WorkspaceError(409, "RUN_BUSY")
        try:
            handle = self._load_handle(key)
            before = self._observe(key, handle)
            if before["revision"] != revision:
                raise WorkspaceError(409, "STATE_CHANGED")
            if action not in before["available"]:
                raise WorkspaceError(409, "ACTION_UNAVAILABLE")
            if action == "start":
                self._prepare(key)
                handle = self._load_handle(key)
                result = self._work(handle, "run", "web-v1",
                                    ["--model", self.model] if self.model else [])
            elif action == "approve":
                result = self._work(handle, "approve", before["run_id"], ["--actor", APPROVER])
            elif action == "advance":
                to = datetime.fromisoformat(before["as_of"]) + timedelta(minutes=5)
                self._work(handle, "advance", before["run_id"], ["--to", to.isoformat()])
                result = self._work(handle, "run", f'web-v{before["input_version"] + 1}',
                                    ["--model", self.model] if self.model else [])
            elif action == "interrupt":
                boundary, exit_code = FAULTS[key]
                result = self._work(handle, "run", before["run_id"], ["--fault", boundary], expected_exit=exit_code)
            else:
                flags = ["--model", self.model] if before["stage"] == "READY" and self.model else []
                result = self._work(handle, "run", before["run_id"], flags)
            return {"case": self._observe(key, handle), "last_action": result}
        finally:
            self.locks[key].release()

    def _work(self, handle, command, run_id, flags, *, expected_exit=None):
        args = [sys.executable, str(self.worker), command, "--trial", str(handle.root),
                "--trial-id", handle.trial_id, "--case", handle.case_id, "--run-id", run_id, *flags]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=140)
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError(504, "WORKER_TIMEOUT") from exc
        if expected_exit is not None and proc.returncode == expected_exit and not proc.stdout:
            return {"status": "WORKER_INTERRUPTED", "exit_code": proc.returncode}
        try:
            result = json.loads(proc.stdout)
        except ValueError as exc:
            raise WorkspaceError(500, "WORKER_UNAVAILABLE") from exc
        if proc.returncode and result.get("status") != "UNRESOLVED":
            raise WorkspaceError(409, result.get("code", "WORKER_FAILED"))
        if expected_exit is not None:
            # The actual process exited normally or rejected the write, so no stop can be reported.
            raise WorkspaceError(409, "FAULT_NOT_OBSERVED")
        # Keep the browser response limited to operation status and public reason codes.
        return {key: result[key] for key in ("status", "reason", "recovery", "redelivered") if key in result}
