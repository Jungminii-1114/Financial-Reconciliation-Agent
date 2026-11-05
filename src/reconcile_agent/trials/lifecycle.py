"""Prepare once, publish READY last, and resume without reimport or migration."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from ..business import BUSINESS_SCHEMA_VERSION
from ..business.connection import check_database, connect, create_business_schema, transaction
from ..business.errors import AdapterError, require
from ..business.input_digest import canonical, fingerprint, input_digest, normalize_row, sha256, utc_time
from ..business.repository import metadata, read_content, read_financial_state
from ..synthetic.files import AGENT_FILES, CONTROLLER_FILES, EVALUATOR_FILES
from . import IMPORTER_VERSION, TRIAL_FORMAT_VERSION
from .contracts import RunnerConfig, TrialHandle, validate_id
from .import_check import verify_initial_import
from .source import ValidatedDataset


DATABASES = {"business": "business/business.sqlite", "controller": "controller/controller.sqlite",
             "evaluator": "evaluator/evaluator.sqlite"}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def _write_json(path: Path, value) -> None:
    _write(path, canonical(value) + b"\n")


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _schema_digest(db) -> str:
    return fingerprint([list(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")])


def _add_content(db, case_id, key, text, origin, known_at):
    ref = "content_" + sha256(canonical([case_id, key]))
    value_hash = sha256(text.encode("utf-8"))
    db.execute("INSERT INTO content_objects VALUES (?,?,?,?,?,?)",
               (ref, case_id, text, value_hash, origin, utc_time(known_at)))
    return ref, value_hash


def insert_financial_row(db, case_id: str, row: dict, *, external: bool) -> None:
    row = normalize_row(row)
    fields = (["external_id"] if external else []) + ["transaction_id", "batch_id", "amount_krw", "currency",
                                                      "status", "event_time", "known_at", "row_revision"]
    table = "settlement_history" if external else "transaction_history"
    columns = ["case_id", *fields]
    db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
               [case_id, *[row[f] for f in fields]])


def _populate(db, fixture, trial_id, config):
    case_id, batch, policy = fixture["case_id"], fixture["batch"], fixture["policy"]
    at, batch_id = utc_time(batch["decision_at"]), batch["batch_id"]
    request_ref = "content_" + sha256(canonical([case_id, "request"]))
    db.execute("INSERT INTO cases VALUES (?,?,?,?,?)", (case_id, batch_id, policy["business_rule_version"],
                                                       policy["policy_version"], request_ref))
    _add_content(db, case_id, "request", fixture["request"], "external_request", at)
    db.execute("INSERT INTO case_policies VALUES (?,?,?,?,?,?)",
               (case_id, policy["policy_version"], policy["business_rule_version"], int(policy["approval_required"]),
                policy["approval_ttl_seconds"], utc_time(policy["reprocess_cutoff_at"]) if policy["reprocess_cutoff_at"] else None))
    initial_digest = input_digest(case_id, batch_id, policy["business_rule_version"], fixture["internal"],
                                  fixture["external"], batch["ingestion_complete"])
    db.execute("INSERT INTO batch_versions VALUES (?,?,?,?,?,?)",
               (case_id, batch_id, batch["input_version"], initial_digest, int(batch["ingestion_complete"]), at))
    db.execute("INSERT INTO batch_runtime VALUES (?,?,?,NULL)", (case_id, batch_id, batch["run_status"]))
    for external, rows in ((False, fixture["internal"]), (True, fixture["external"])):
        for row in rows:
            insert_financial_row(db, case_id, row, external=external)
    for note in fixture["notes"]:
        ref, content_hash = _add_content(db, case_id, "note:" + note["note_id"], note["content"], note["origin"], note["known_at"])
        db.execute("INSERT INTO external_notes VALUES (?,?,?,?,?,1,?,?)",
                   (note["note_id"], case_id, note["record_ref"], ref, content_hash, note["origin"], utc_time(note["known_at"])))
    meta = {"trial_id": trial_id, "case_id": case_id, "batch_id": batch_id,
            "principal_id": config.principal_id, "runner_config_hash": config.config_hash,
            "initial_observed_at": at, "current_time": at, "dataset_fingerprint": fixture["dataset_fingerprint"]}
    db.executemany("INSERT INTO trial_metadata VALUES (?,?)", sorted(meta.items()))


def _private_store(path, role, fixture, binding):
    with connect(path, create=True) as db:
        db.executescript("""CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
            CREATE TABLE documents(name TEXT PRIMARY KEY,payload TEXT NOT NULL,payload_hash TEXT NOT NULL) WITHOUT ROWID;""")
        db.execute("PRAGMA user_version=1")
        with transaction(db, write=True):
            db.executemany("INSERT INTO metadata VALUES (?,?)", sorted({**binding, "role": role}.items()))
            for name, raw in sorted(fixture["raw"].items()):
                if name.startswith(role + "_only/"):
                    db.execute("INSERT INTO documents VALUES (?,?,?)", (name.split("/", 1)[1], raw.decode("utf-8"), sha256(raw)))
            check_database(db)


def prepare_from_dataset(dataset: ValidatedDataset, case_id: str, trial_id: str,
                         output_dir: Path, config: RunnerConfig = RunnerConfig()) -> TrialHandle:
    validate_id(case_id, case=True)
    validate_id(trial_id)
    config.normalized()
    out = Path(output_dir).absolute()
    require(not out.exists() and not out.is_symlink(), "TRIAL_ALREADY_EXISTS", "새 경로가 필요합니다. 기존 실행은 resume을 사용하세요.")
    require(not out.resolve().is_relative_to(dataset.original.resolve()),
            "UNSAFE_PATH", "원본 데이터셋 안에는 trial을 만들 수 없습니다.")
    fixture = dataset.case(case_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".trial-stage-", dir=out.parent) as directory:
        stage = Path(directory)
        for folder in ("business", "controller", "evaluator", "operator"):
            (stage / folder).mkdir(mode=0o700)
        with connect(stage / DATABASES["business"], create=True) as db:
            create_business_schema(db)
            schema_hash = _schema_digest(db)
            with transaction(db, write=True):
                _populate(db, fixture, trial_id, config)
                report = verify_initial_import(db, fixture)
                db.execute("INSERT INTO import_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    "import_" + trial_id, trial_id, case_id, fixture["dataset_fingerprint"], fixture["fixture_schema_version"],
                    IMPORTER_VERSION, BUSINESS_SCHEMA_VERSION, report["initial_input_digest"],
                    report["financial_projection_digest"], canonical(report["row_counts"]).decode(), report["initial_observed_at"]))
        binding = {"trial_id": trial_id, "case_id": case_id, "dataset_fingerprint": fixture["dataset_fingerprint"],
                   "runner_config_hash": config.config_hash}
        for role in ("controller", "evaluator"):
            _private_store(stage / DATABASES[role], role, fixture, binding)
        for name, raw in fixture["raw"].items():
            _write(stage / "operator" / "source" / name, raw)
        _write_json(stage / "operator" / "runner_config.json", config.normalized())
        report.update({"trial_id": trial_id, "case_id": case_id, "importer_version": IMPORTER_VERSION})
        _write_json(stage / "operator" / "import_report.json", report)
        assets = {p.relative_to(stage).as_posix(): sha256(p.read_bytes()) for p in (stage / "operator").rglob("*") if p.is_file()}
        manifest = {"format_version": TRIAL_FORMAT_VERSION, "importer_version": IMPORTER_VERSION,
                    "business_schema_version": BUSINESS_SCHEMA_VERSION, "business_schema_digest": schema_hash,
                    **binding, "batch_id": fixture["batch"]["batch_id"], "principal_id": config.principal_id,
                    "initial_observed_at": report["initial_observed_at"], "initial_input_digest": report["initial_input_digest"],
                    "asset_sha256": assets}
        _write_json(stage / "operator" / "trial_manifest.json", manifest)
        # All stores are closed and independently checked before publication.
        _validate_bundle(stage, manifest, check_initial=True)
        for folder in sorted((p for p in stage.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            _sync_directory(folder)
        _sync_directory(stage)
        try:
            out.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise AdapterError("TRIAL_ALREADY_EXISTS", "다른 실행이 목적지를 먼저 예약했습니다.") from exc
        for item in sorted(stage.iterdir()):
            os.rename(item, out / item.name)
        # A process death during publication leaves an inert, non-resumable directory.
        _write_json(out / "operator" / "READY", {"format_version": TRIAL_FORMAT_VERSION,
                    "manifest_sha256": sha256((out / "operator" / "trial_manifest.json").read_bytes())})
        _sync_directory(out / "operator")
        _sync_directory(out)
        _sync_directory(out.parent)
    return TrialHandle(out, trial_id, case_id, fixture["batch"]["batch_id"], config)


def prepare_trial(dataset_root: Path, case_id: str, trial_id: str, output_dir: Path,
                  config: RunnerConfig = RunnerConfig()) -> TrialHandle:
    validate_id(case_id, case=True)
    validate_id(trial_id)
    config.normalized()
    require(not Path(output_dir).exists() and not Path(output_dir).is_symlink(),
            "TRIAL_ALREADY_EXISTS", "새 경로가 필요합니다. 기존 실행은 resume을 사용하세요.")
    with ValidatedDataset(dataset_root) as dataset:
        return prepare_from_dataset(dataset, case_id, trial_id, output_dir, config)


def _validate_bundle(root, manifest, *, check_initial=False):
    require(not root.is_symlink() and not any(p.is_symlink() for p in root.rglob("*")),
            "UNSAFE_PATH", "trial 내부 링크는 허용하지 않습니다.")
    require(manifest.get("format_version") == TRIAL_FORMAT_VERSION and
            manifest.get("business_schema_version") == BUSINESS_SCHEMA_VERSION and
            manifest.get("importer_version") == IMPORTER_VERSION,
            "BUSINESS_SCHEMA_MISMATCH", "지원하지 않는 trial 또는 importer 버전입니다.")
    required_assets = {"operator/runner_config.json", "operator/import_report.json"}
    for role, names in (("agent_view", AGENT_FILES), ("controller_only", CONTROLLER_FILES), ("evaluator_only", EVALUATOR_FILES)):
        required_assets.update(f"operator/source/{role}/{name}" for name in names)
    require(set(manifest["asset_sha256"]) == required_assets,
            "TRIAL_NOT_READY", "필수 불변 파일 목록이 다릅니다.")
    for name, expected in manifest["asset_sha256"].items():
        require(re.fullmatch(r"operator/(?:runner_config.json|import_report.json|source/(?:agent_view|controller_only|evaluator_only)/[a-z_]+\.(?:json|txt|csv))", name) is not None,
                "TRIAL_NOT_READY", "허용되지 않은 불변 파일 경로입니다.")
        path = root / name
        require(path.is_file() and sha256(path.read_bytes()) == expected,
                "TRIAL_NOT_READY", "불변 출처 파일이 누락되거나 변경됐습니다.")
    require(fingerprint(json.loads((root / "operator" / "runner_config.json").read_bytes())) == manifest["runner_config_hash"],
            "TRIAL_IDENTITY_MISMATCH", "runner 설정과 binding이 다릅니다.")
    binding = {k: manifest[k] for k in ("trial_id", "case_id", "dataset_fingerprint", "runner_config_hash")}
    for role, path in DATABASES.items():
        # rw permits SQLite's ordinary rollback-journal recovery after a storage
        # process crash. No application reinitialization or migration is run.
        with connect(root / path, writable=(role == "business")) as db, transaction(db):
            check_database(db, business=(role == "business"))
            if role != "business":
                require(db.execute("PRAGMA user_version").fetchone()[0] == 1,
                        "BUSINESS_SCHEMA_MISMATCH", "비공개 저장소 버전이 다릅니다.")
                require(dict(db.execute("SELECT key,value FROM metadata")) == {**binding, "role": role},
                        "TRIAL_IDENTITY_MISMATCH", "비공개 저장소가 다른 실행에 속합니다.")
                docs = list(db.execute("SELECT name,payload,payload_hash FROM documents"))
                expected_names = {n.split("/")[-1] for n in manifest["asset_sha256"] if f"/source/{role}_only/" in n}
                require({r["name"] for r in docs} == expected_names,
                        "TRIAL_NOT_READY", "비공개 명세 파일 목록이 다릅니다.")
                for doc in docs:
                    expected = manifest["asset_sha256"][f"operator/source/{role}_only/{doc['name']}"]
                    require(sha256(doc["payload"].encode()) == doc["payload_hash"] == expected,
                            "TRIAL_NOT_READY", "비공개 명세가 출처와 다릅니다.")
                continue
            require(_schema_digest(db) == manifest["business_schema_digest"],
                    "BUSINESS_SCHEMA_MISMATCH", "업무 테이블·인덱스 계약이 변경됐습니다.")
            meta = metadata(db)
            require(all(meta.get(k) == value for k, value in {**binding, "batch_id": manifest["batch_id"],
                        "principal_id": manifest["principal_id"], "initial_observed_at": manifest["initial_observed_at"]}.items()),
                    "TRIAL_IDENTITY_MISMATCH", "업무 DB가 다른 실행·입력에 속합니다.")
            require(utc_time(meta["current_time"]) == meta["current_time"] and meta["current_time"] >= meta["initial_observed_at"],
                    "IMPORT_INVARIANT_ERROR", "현재 가상 시각이 올바르지 않습니다.")
            versions = list(db.execute("SELECT * FROM batch_versions ORDER BY input_version"))
            require([r["input_version"] for r in versions] == list(range(1, len(versions)+1)) and versions,
                    "IMPORT_INVARIANT_ERROR", "입력 버전 이력이 연속되지 않습니다.")
            case = db.execute("SELECT * FROM cases WHERE case_id=?", (manifest["case_id"],)).fetchone()
            require(case is not None and db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1 and
                    versions[0]["input_digest"] == manifest["initial_input_digest"],
                    "IMPORT_INVARIANT_ERROR", "초기 입력 binding이 다릅니다.")
            # v1 supports financial events, not policy/request/note edits. Preserve
            # their original binding; a later format must explicitly version changes.
            policy = dict(db.execute("SELECT * FROM case_policies").fetchone())
            policy.pop("case_id")
            policy["approval_required"] = bool(policy["approval_required"])
            original_policy = json.loads((root / "operator/source/agent_view/policy.json").read_bytes())
            if original_policy["reprocess_cutoff_at"] is not None:
                original_policy["reprocess_cutoff_at"] = utc_time(original_policy["reprocess_cutoff_at"])
            require(policy == original_policy and db.execute("SELECT COUNT(*) FROM case_policies").fetchone()[0] == 1,
                    "IMPORT_INVARIANT_ERROR", "버전 없는 정책 변경을 허용하지 않습니다.")
            require(read_content(db, case["request_ref"], case["case_id"]) ==
                    (root / "operator/source/agent_view/request.txt").read_bytes().decode("utf-8"),
                    "CONTENT_INTEGRITY_ERROR", "접수 원문이 출처와 다릅니다.")
            expected_notes = json.loads((root / "operator/source/agent_view/external_notes.json").read_bytes())
            actual_notes = []
            for note in db.execute("SELECT * FROM external_notes ORDER BY note_id"):
                require(note["context_revision"] == 1, "IMPORT_INVARIANT_ERROR", "v1 메모 변경은 지원하지 않습니다.")
                actual_notes.append({"note_id": note["note_id"], "record_ref": note["record_ref"], "origin": note["origin"],
                    "known_at": note["known_at"], "content": read_content(db, note["content_ref"], case["case_id"], note["content_hash"])})
            require(actual_notes == sorted(({**n, "known_at": utc_time(n["known_at"])} for n in expected_notes), key=lambda n: n["note_id"]),
                    "CONTENT_INTEGRITY_ERROR", "메모 원문·출처·시각이 출처와 다릅니다.")
            for version in versions:
                require(version["known_at"] <= meta["current_time"], "IMPORT_INVARIANT_ERROR", "미래 입력 버전이 저장됐습니다.")
                view = read_financial_state(db, case["case_id"], case["target_batch_id"], version["known_at"])
                actual = input_digest(case["case_id"], case["target_batch_id"], case["business_rule_version"],
                                      view["internal"], view["external"], view["batch"]["ingestion_complete"])
                require(actual == version["input_digest"], "IMPORT_INVARIANT_ERROR", "입력 이력과 업무 digest가 다릅니다.")
            planned = json.loads((root / "operator/source/controller_only/scheduled_events.json").read_bytes())
            due = [g for g in planned if utc_time(g["at"]) <= meta["current_time"]]
            expected_receipts = [(g["group_id"], fingerprint(g), g["expected_input_version"],
                                  g["new_input_version"], utc_time(g["at"])) for g in due]
            observed_receipts = [tuple(r) for r in db.execute("""SELECT group_id,group_payload_digest,
                old_version,new_version,applied_at FROM applied_event_groups WHERE case_id=? ORDER BY new_version""",
                (case["case_id"],))]
            require(observed_receipts == expected_receipts and len(observed_receipts) == len(versions)-1,
                    "IMPORT_INVARIANT_ERROR", "현재 시각·입력 버전·실제 사건 영수증이 계획과 다릅니다.")
            receipt = list(db.execute("SELECT * FROM import_receipts"))
            original_report = json.loads((root / "operator/import_report.json").read_bytes())
            require(len(receipt) == 1 and receipt[0]["trial_id"] == manifest["trial_id"] and
                    receipt[0]["case_id"] == manifest["case_id"] and receipt[0]["dataset_fingerprint"] == manifest["dataset_fingerprint"] and
                    receipt[0]["initial_input_digest"] == manifest["initial_input_digest"] and
                    receipt[0]["initial_observed_at"] == manifest["initial_observed_at"] and
                    receipt[0]["business_schema_version"] == BUSINESS_SCHEMA_VERSION and receipt[0]["importer_version"] == IMPORTER_VERSION and
                    receipt[0]["fixture_schema_version"] == "reconcile-fixture-1" and
                    receipt[0]["financial_projection_digest"] == original_report["financial_projection_digest"] and
                    json.loads(receipt[0]["row_counts"]) == original_report["row_counts"],
                    "IMPORT_INVARIANT_ERROR", "초기 적재 영수증이 다릅니다.")
            if check_initial:
                require(len(versions) == 1, "IMPORT_INVARIANT_ERROR", "초기 상태에 후속 버전이 있습니다.")


def resume_trial(trial_dir: Path, expected_trial_id: str, case_id: str,
                 config: RunnerConfig = RunnerConfig()) -> TrialHandle:
    validate_id(expected_trial_id)
    validate_id(case_id, case=True)
    config.normalized()
    root = Path(trial_dir).absolute()
    require(root.is_dir() and not root.is_symlink(), "TRIAL_MISSING", "재개할 trial 디렉터리가 없습니다.")
    try:
        ready_path, manifest_path = root / "operator" / "READY", root / "operator" / "trial_manifest.json"
        require(ready_path.is_file() and manifest_path.is_file(), "TRIAL_NOT_READY", "READY 또는 manifest가 없습니다.")
        require(not any(p.is_symlink() for p in root.rglob("*")), "UNSAFE_PATH", "trial 내부 링크를 거부합니다.")
        ready, raw = json.loads(ready_path.read_bytes()), manifest_path.read_bytes()
        require(ready == {"format_version": TRIAL_FORMAT_VERSION, "manifest_sha256": sha256(raw)},
                "TRIAL_NOT_READY", "준비 완료 표시와 manifest가 다릅니다.")
        manifest = json.loads(raw)
        require(manifest["trial_id"] == expected_trial_id and manifest["case_id"] == case_id and
                manifest["runner_config_hash"] == config.config_hash and manifest["principal_id"] == config.principal_id,
                "SCOPE_DENIED", "실행 ID·사례·신뢰된 runner 설정이 다릅니다.")
        _validate_bundle(root, manifest)
        return TrialHandle(root, expected_trial_id, case_id, manifest["batch_id"], config)
    except AdapterError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AdapterError("TRIAL_NOT_READY", "trial 명세 또는 불변 파일을 확인할 수 없습니다.") from exc
