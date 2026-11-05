"""Dependency-free human/operator CLI. No model or business tool is executed."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from .config import DatasetConfig
from .files import file_hash, read_json, write_json
from .generator import generate_dataset
from .operator import write_preview
from .recipes import reference_action
from .snapshot import export_sqlite, read_snapshot, select_case
from .validate import validate_dataset


def build_dataset(out: Path, config: DatasetConfig) -> dict:
    """Validate a complete bundle before publishing; never replace existing paths."""
    out = Path(out).absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError("기존 경로를 덮어쓰지 않습니다: " + str(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reconcile-bundle-", dir=out.parent) as temporary:
        stage = Path(temporary) / "dataset"
        generate_dataset(stage, config)
        report = validate_dataset(stage)
        write_preview(stage, report)
        operator = stage / "operator_view"
        export_sqlite(stage, operator / "initial_snapshot.sqlite", internal_artifact=True)
        write_json(operator / "validation_report.json", report)
        write_json(operator / "artifacts.json", {
            name: file_hash(operator / name) for name in
            ("index.html", "initial_snapshot.sqlite", "validation_report.json")})
        final = validate_dataset(stage)
        if final != report:
            raise ValueError("최종 검증 보고서가 최초 검증과 다릅니다.")
        out.mkdir()  # exclusive reservation; includes racing destinations
        for item in sorted(stage.iterdir(), key=lambda p: (p.name == "manifest.json", p.name)):
            os.rename(item, out / item.name)
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="금융 대사 합성 데이터 v1 — 모델·SQL 작성 없이 실행")
    sub = result.add_subparsers(dest="command", required=True)
    for name, help_text in (("build", "생성·독립 검증·HTML·SQLite를 한 번에 준비"),
                            ("generate", "CSV/JSON 코어 파일만 생성")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--config", type=Path, help="JSON 설정; 아래 인자로 일부 덮어쓰기 가능")
        command.add_argument("--profile", choices=("pilot", "benchmark"))
        command.add_argument("--seed", type=int)
        command.add_argument("--rows", type=int, help="초기 내부 거래 수: 3~5000")
        command.add_argument("--decision-at", help="timezone offset 포함 ISO 8601")
        command.add_argument("--security-pairs", type=int, help="split별 정상/공격 쌍 수")
    for name, help_text in (("validate", "파일·스키마·시간·정답·대조 쌍을 독립 검사"),
                            ("list", "사람 전용 시나리오 목록"),
                            ("inspect", "사람 전용 단일 사례와 as-of 스냅샷 열람"),
                            ("export-sqlite", "관측 상태를 별도 SQLite 파일로 저장")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("dataset", type=Path)
        if name in {"inspect", "export-sqlite"}:
            command.add_argument("--case", default="first" if name == "inspect" else None,
                                 help="catalog의 case ID; first는 정상 일치 core 사례")
            command.add_argument("--at", help="이벤트 적용 기준 시점; 생략 시 초기 상태")
        if name == "inspect":
            command.add_argument("--show-rows", type=int, default=3)
        if name == "export-sqlite":
            command.add_argument("--out", type=Path, required=True)
    return result


def config_from_args(args) -> DatasetConfig:
    value = read_json(args.config) if args.config else {}
    if not isinstance(value, dict):
        raise ValueError("설정 파일은 JSON object여야 합니다.")
    overrides = {"profile": args.profile, "seed": args.seed, "rows_per_batch": args.rows,
                 "decision_at": args.decision_at, "security_pairs_per_split": args.security_pairs}
    value.update({k: v for k, v in overrides.items() if v is not None})
    return DatasetConfig.from_dict(value)


def print_report(report):
    print(f"검증 통과: {report['validated_cases']}사례 / {report['security_pairs_checked']}보안 쌍 / "
          f"{report['checkpoints_checked']}시점")
    print("지문: " + report["dataset_fingerprint"])
    print("모델 호출: 0회. Agent·업무 처리·장애·방어 실행: 미실행.")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in {"build", "generate"}:
            config = config_from_args(args)
            if args.command == "generate":
                manifest = generate_dataset(args.out, config)
                print("코어 생성 완료: " + str(args.out.absolute()))
                print("지문: " + manifest["dataset_fingerprint"])
                print("검증은 validate 명령으로 별도 실행하세요. build는 검증까지 자동 실행합니다.")
            else:
                report = build_dataset(args.out, config)
                print_report(report)
                print("HTML: " + str(args.out.absolute() / "operator_view" / "index.html"))
                print("SQLite: " + str(args.out.absolute() / "operator_view" / "initial_snapshot.sqlite"))
            return 0
        report = validate_dataset(args.dataset)
        if args.command == "validate":
            print_report(report)
        elif args.command == "list":
            print("사람·평가자 전용 목록입니다. Agent 입력으로 제공하지 마세요.")
            for entry in read_json(args.dataset / "catalog.json")["cases"]:
                print("\t".join(str(entry[k]) for k in
                      ("case_id", "suite", "split", "recipe", "condition", "pair_id")))
        elif args.command == "inspect":
            if not 0 <= args.show_rows <= 100:
                raise ValueError("--show-rows는 0~100이어야 합니다.")
            entry = select_case(args.dataset, args.case)
            state = read_snapshot(args.dataset, entry, args.at)
            action, findings = reference_action(state["internal"], state["external"], state["batch"],
                                                state["policy"], state["batch"]["decision_at"])
            view = {"visibility": "OPERATOR_ONLY", "case": entry, "batch": state["batch"],
                    "input_digest": state["input_digest"], "specification_action": action,
                    "findings": findings, "runtime_executed": False,
                    "internal_sample": state["internal"][:args.show_rows],
                    "external_sample": state["external"][:args.show_rows]}
            print(json.dumps(view, ensure_ascii=False, indent=2))
        else:
            exported = export_sqlite(args.dataset, args.out, at=args.at, case_id=args.case)
            print(json.dumps(exported, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, sqlite3.DatabaseError) as exc:
        print("오류: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
