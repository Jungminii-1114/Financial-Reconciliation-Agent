"""Operator CLI. It is intentionally not a model-accessible Tool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..business.connection import connect, transaction
from ..business.errors import AdapterError, require
from ..business.repository import metadata, read_financial_state
from .contracts import RunnerConfig, validate_id
from .events import advance_trial
from .lifecycle import _write_json, prepare_from_dataset, resume_trial
from .source import ValidatedDataset


def _config(args) -> RunnerConfig:
    return RunnerConfig(args.principal, args.setting, args.repeat)


def inspect_trial(handle) -> dict:
    with connect(handle.business_db) as db, transaction(db):
        meta = metadata(db)
        require(all(meta.get(k) == getattr(handle, k) for k in ("trial_id", "case_id", "batch_id", "principal_id")),
                "SCOPE_DENIED", "현재 실행 범위가 다릅니다.")
        view = read_financial_state(db, handle.case_id, handle.batch_id, meta["current_time"])
        processing_state = db.execute("SELECT run_status FROM batch_runtime WHERE case_id=? AND batch_id=?",
                                     (handle.case_id, handle.batch_id)).fetchone()[0]
        counts = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in
                  ("approvals", "operations", "reconciliation_results", "completion_decisions", "input_mutations", "applied_event_groups")}
    return {**handle.agent_admission(), "current_time": meta["current_time"],
            "input_version": view["batch"]["input_version"], "input_digest": view["batch"]["input_digest"],
            "ingestion_complete": view["batch"]["ingestion_complete"], "processing_state": processing_state,
            "row_counts": {"internal": len(view["internal"]), "external": len(view["external"])},
            "execution_counts": counts, "model_requests": 0, "agent_runtime_executed": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="검증된 합성 입력을 격리된 업무 trial DB로 적재합니다.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "prepare-all"):
        p = sub.add_parser(name)
        p.add_argument("--dataset", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        if name == "prepare":
            p.add_argument("--case", required=True, help="정확한 c_ ID 또는 operator 편의 선택 first")
            p.add_argument("--trial-id", required=True)
        else:
            p.add_argument("--prefix", default="trial")
    for name in ("inspect", "resume", "advance"):
        p = sub.add_parser(name)
        p.add_argument("--trial", type=Path, required=True)
        p.add_argument("--trial-id", required=(name != "inspect"))
        p.add_argument("--case", required=(name != "inspect"))
        if name == "advance":
            p.add_argument("--to", required=True, help="offset이 있는 목표 가상 시각")
    for p in sub.choices.values():
        p.add_argument("--principal", default="local-runner")
        p.add_argument("--setting", default="development")
        p.add_argument("--repeat", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        config = _config(args)
        config.normalized()
        if args.command in {"prepare", "prepare-all"}:
            with ValidatedDataset(args.dataset) as dataset:
                if args.command == "prepare":
                    case_id = args.case
                    if case_id == "first":
                        # Human selection only. Import mapping never branches on recipe or answer.
                        case_id = next(e["case_id"] for e in dataset.entries if e["suite"] == "core" and e["recipe"] == "clean")
                    handle = prepare_from_dataset(dataset, case_id, args.trial_id, args.out, config)
                    result = {"status": "READY", "trial_dir": str(handle.root), **inspect_trial(handle)}
                else:
                    validate_id(args.prefix)
                    require(not args.out.exists() and not args.out.is_symlink(),
                            "TRIAL_ALREADY_EXISTS", "일괄 적재도 새 출력 경로만 허용합니다.")
                    require(not args.out.resolve().is_relative_to(dataset.original.resolve()),
                            "UNSAFE_PATH", "원본 데이터셋 안에는 출력할 수 없습니다.")
                    args.out.mkdir(mode=0o700, parents=True)
                    entries = []
                    for index, entry in enumerate(dataset.entries, 1):
                        trial_id = f"{args.prefix}-{index:03d}"
                        handle = prepare_from_dataset(dataset, entry["case_id"], trial_id, args.out / trial_id, config)
                        entries.append({**handle.agent_admission(), "trial_dir": handle.root.name})
                    result = {"status": "READY", "trials": len(entries), "dataset_fingerprint": dataset.manifest["dataset_fingerprint"],
                              "index": entries, "model_requests": 0, "agent_runtime_executed": False}
                    _write_json(args.out / "trial_index.json", result)
        else:
            trial_id, case_id = args.trial_id, args.case
            if args.command == "inspect":
                # Operator convenience, not an authorization mechanism.
                raw = json.loads((args.trial / "operator" / "trial_manifest.json").read_bytes())
                trial_id, case_id = trial_id or raw["trial_id"], case_id or raw["case_id"]
            handle = resume_trial(args.trial, trial_id, case_id, config)
            if args.command == "advance":
                result = advance_trial(handle, args.to)
            else:
                result = {"status": "RESUMABLE", **inspect_trial(handle)}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except AdapterError as exc:
        error = {"code": exc.code, "message": exc.message}
    except (OSError, ValueError, KeyError, TypeError):
        error = {"code": "IO_OR_FORMAT_ERROR", "message": "경로·파일·설정 형식을 확인하세요. 원문 payload는 출력하지 않습니다."}
    print(json.dumps({"error": error}, ensure_ascii=False, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
