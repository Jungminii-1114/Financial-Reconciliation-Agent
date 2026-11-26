"""Run the local reconciliation MVP with the Python standard library."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reconcile_agent.business.errors import AdapterError
from reconcile_agent.business.input_digest import utc_time
from reconcile_agent.evaluation.mvp import evaluate
from reconcile_agent.harness.journal import locked_runtime
from reconcile_agent.harness.workflow import ReconciliationWorkflow
from reconcile_agent.synthetic.config import DatasetConfig
from reconcile_agent.synthetic.generator import generate_dataset
from reconcile_agent.synthetic.files import read_json, write_json
from reconcile_agent.tools.errors import ToolError
from reconcile_agent.trials.contracts import RunnerConfig
from reconcile_agent.trials.events import advance_trial
from reconcile_agent.trials.lifecycle import prepare_trial, resume_trial

FAULTS = {"before-commit": 71, "after-commit": 72}


def _counts(handle):
    with closing(sqlite3.connect(handle.business_db.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                ("operations", "reconciliation_results", "approvals", "completion_decisions")}


def _audit(handle):
    ledger = handle.root.with_name(handle.root.name + ".runtime") / "calls.sqlite"
    with closing(sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT session_id,tool_name,status,code FROM calls ORDER BY rowid")]


def _demo(output, model):
    output = output.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    dataset = output / "dataset"
    generate_dataset(dataset, DatasetConfig(rows_per_batch=3))
    catalog = {row["recipe"]: row["case_id"] for row in read_json(dataset / "catalog.json")["cases"]
               if row["suite"] == "core"}

    def worker(command, handle, *flags):
        return subprocess.run([sys.executable, __file__, command, "--trial", str(handle.root),
                               "--trial-id", handle.trial_id, "--case", handle.case_id, *flags],
                              capture_output=True, text=True, timeout=160)

    def expect(proc, status):
        try:
            data = json.loads(proc.stdout)
        except ValueError as exc:
            raise ToolError("DEMO_ASSERTION_FAILED", "MVP 작업자가 결과를 반환하지 않았습니다.") from exc
        if proc.returncode != 0 or data.get("status") != status:
            raise ToolError("DEMO_ASSERTION_FAILED", "MVP 데모의 실행 상태가 예상과 다릅니다.")
        return data

    cases = {}
    for recipe in ("clean", "delayed_arrival", "amount_swap", "before_commit", "after_commit"):
        handle = prepare_trial(dataset, catalog[recipe], recipe.replace("_", "-"), output / recipe)
        initial = expect(worker("run", handle, "--session", "session-1",
                                *("--model", model) if model else ()),
                         "FINAL" if recipe in {"delayed_arrival", "amount_swap"} else "PAUSED_FOR_APPROVAL")
        entry = {"initial": initial, "before_approval": _counts(handle)}
        if entry["before_approval"]["operations"] != 0:
            raise ToolError("DEMO_ASSERTION_FAILED", "승인 전에 업무 작업이 생성됐습니다.")
        if initial["status"] == "PAUSED_FOR_APPROVAL":
            entry["approval"] = expect(worker("approve", handle, "--actor", "demo-operator"), "APPROVED")
            if recipe in {"before_commit", "after_commit"}:
                fault = recipe.replace("_", "-")
                proc = worker("run", handle, "--session", "session-2", "--fault", fault)
                if proc.returncode != FAULTS[fault] or proc.stdout:
                    raise ToolError("DEMO_ASSERTION_FAILED", "작업 프로세스가 예정 경계에서 종료되지 않았습니다.")
                entry["worker_exit"] = proc.returncode
                entry["after_process_exit"] = _counts(handle)
                expected = 1 if recipe == "after_commit" else 0
                if (entry["after_process_exit"]["operations"] != expected or
                    entry["after_process_exit"]["completion_decisions"] != 0):
                    raise ToolError("DEMO_ASSERTION_FAILED", "업무 반영과 응답 경계가 예상과 다릅니다.")
            entry["final"] = expect(worker("run", handle, "--session", "session-3"), "FINAL")
        else:
            entry["final"] = initial
        entry["counts"] = _counts(handle)
        entry["audit"] = _audit(handle)
        if recipe == "after_commit":
            resumed = [event for event in entry["audit"] if event["session_id"] == "session-3"]
            if [event["tool_name"] for event in resumed] != ["get_operation", "get_case"]:
                raise ToolError("DEMO_ASSERTION_FAILED", "재개 과정에서 기존 결과부터 조회해야 합니다.")
        entry["evaluation"] = evaluate(handle, "run-1")
        if not entry["evaluation"]["passed"]:
            raise ToolError("DEMO_ASSERTION_FAILED", "독립 판정기가 사례를 통과시키지 못했습니다.")
        cases[recipe] = entry
    report = {"status": "DEMO_PASSED", "model": model, "scenarios": cases}
    write_json(output / "report.json", report)
    return {"status": report["status"], "report": str(output / "report.json"),
            "outcomes": {name: value["final"]["decision"]["outcome"] for name, value in cases.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="generate five cases and run real process stops")
    demo.add_argument("--out", type=Path, required=True)
    demo.add_argument("--model", help="optional installed local Ollama model")
    for name in ("prepare", "run", "approve", "advance", "evaluate", "inspect"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--trial", type=Path, required=True)
        cmd.add_argument("--trial-id", required=True)
        cmd.add_argument("--case", required=True)
        cmd.add_argument("--principal", default="local-runner")
        cmd.add_argument("--run-id", default="run-1")
        if name == "prepare":
            cmd.add_argument("--dataset", type=Path, required=True)
        if name in {"run", "inspect"}:
            cmd.add_argument("--session", default=None)
        if name == "run":
            cmd.add_argument("--model")
            cmd.add_argument("--fault", choices=FAULTS)
        if name == "approve":
            cmd.add_argument("--actor", required=True)
        if name == "advance":
            cmd.add_argument("--to", required=True)
        if name == "inspect":
            cmd.add_argument("--as-of", required=True)
            cmd.add_argument("--version", required=True, type=int)
            cmd.add_argument("--profile", choices=("EXISTENCE", "DUPLICATE", "BATCH_COMPARISON"))
            cmd.add_argument("--transaction-id", action="append", default=[])
            cmd.add_argument("--action-digest")
            cmd.add_argument("--result-ref")
            cmd.add_argument("--limit", type=int)
            cmd.add_argument("--cursor")
    args = parser.parse_args()
    try:
        if args.command == "demo":
            result = _demo(args.out, args.model)
        elif args.command == "prepare":
            handle = prepare_trial(args.dataset, args.case, args.trial_id, args.trial, RunnerConfig(args.principal))
            result = {"status": "READY", **handle.agent_admission()}
        else:
            handle = resume_trial(args.trial, args.trial_id, args.case, RunnerConfig(args.principal))
            with locked_runtime(handle) as journal:
                if args.command == "advance":
                    result = {"status": "ADVANCED", **advance_trial(handle, args.to)}
                elif args.command == "evaluate":
                    result = {"status": "EVALUATED", **evaluate(handle, args.run_id)}
                else:
                    fault = getattr(args, "fault", None)
                    flow = ReconciliationWorkflow(
                        handle, journal, args.run_id, getattr(args, "session", None) or "session-" + uuid4().hex,
                        before_commit=(lambda: os._exit(71)) if fault == "before-commit" else None,
                        after_commit=(lambda: os._exit(72)) if fault == "after-commit" else None,
                    )
                    if args.command == "approve":
                        result = flow.approve(args.actor)
                    elif args.command == "inspect":
                        query = {"profile": args.profile or ("EXISTENCE" if args.transaction_id else "BATCH_COMPARISON")}
                        if args.transaction_id:
                            query["transaction_ids"] = args.transaction_id
                        for field in ("action_digest", "result_ref", "limit", "cursor"):
                            value = getattr(args, field)
                            if value is not None:
                                query[field] = value
                        data = flow.call("query_records", "B-02", **flow.target, **query,
                                         as_of=args.as_of, expected_input_version=args.version)
                        result = {"status": "OBSERVED", "data": asdict(data)}
                    else:
                        result = flow.run(model=args.model)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 2 if result.get("status") == "UNRESOLVED" or result.get("passed") is False else 0
    except (AdapterError, ToolError) as exc:
        print(json.dumps({"status": "ERROR", "code": exc.code}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, subprocess.SubprocessError, sqlite3.Error):
        print(json.dumps({"status": "ERROR", "code": "LOCAL_RUNTIME_ERROR"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
