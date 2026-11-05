"""Validate a private byte snapshot once; import many trials from that snapshot.

The host filesystem/runner is trusted. Source changes during copying are caught by
manifest validation; this is not a sandbox against a compromised OS account.
"""

from __future__ import annotations

import csv
import io
import json
import os
import stat
import tempfile
from pathlib import Path

from ..business.errors import AdapterError, require
from ..business.input_digest import sha256
from ..synthetic import GENERATOR_VERSION, SCHEMA_VERSION
from ..synthetic.files import (AGENT_FILES, CONTROLLER_FILES, EVALUATOR_FILES,
                               EXTERNAL_FIELDS, INTERNAL_FIELDS, read_json)
from ..synthetic.validate import validate_dataset


class ValidatedDataset:
    """Context-managed operator object; private fixture contents stay off model paths."""
    def __init__(self, root: Path):
        self.original = Path(root).absolute()
        self._temporary = None

    def __enter__(self):
        require(self.original.is_dir() and not self.original.is_symlink(),
                "FIXTURE_INTEGRITY_ERROR", "데이터셋 디렉터리가 없거나 링크입니다.")
        self._temporary = tempfile.TemporaryDirectory(prefix="reconcile-import-source-")
        self.root = Path(self._temporary.name) / "snapshot"
        self.root.mkdir(mode=0o700)
        try:
            allowed = {"manifest.json", "config.json", "catalog.json", "data_card.md", "cases", "operator_view"}
            require({p.name for p in self.original.iterdir()} <= allowed,
                    "FIXTURE_INTEGRITY_ERROR", "데이터셋에 미허용 루트 파일이 있습니다.")
            for path in self.original.rglob("*"):
                info = path.lstat()
                require(not stat.S_ISLNK(info.st_mode) and
                        (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)),
                        "FIXTURE_INTEGRITY_ERROR", "링크 또는 특수 파일은 허용하지 않습니다.")
                target = self.root / path.relative_to(self.original)
                if path.is_dir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                else:
                    require(info.st_size <= 64 * 1024**2, "FIXTURE_TOO_LARGE", "단일 입력 파일 크기 한도를 넘었습니다.")
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(descriptor, "rb") as source, target.open("xb") as destination:
                        data = source.read(64 * 1024**2 + 1)
                        require(len(data) <= 64 * 1024**2, "FIXTURE_TOO_LARGE", "단일 입력 파일 크기 한도를 넘었습니다.")
                        destination.write(data)
                    target.chmod(0o600)
            self.manifest = read_json(self.root / "manifest.json")
            require(self.manifest.get("schema_version") == SCHEMA_VERSION and
                    self.manifest.get("generator_version") == GENERATOR_VERSION,
                    "UNSUPPORTED_FIXTURE_VERSION", "reconcile-fixture-1 / synthetic-1.0.0만 지원합니다.")
            self.validation = validate_dataset(self.root)
            self.entries = read_json(self.root / "catalog.json")["cases"]
            return self
        except BaseException as exc:
            self._temporary.cleanup()
            self._temporary = None
            if isinstance(exc, AdapterError) or not isinstance(exc, Exception):
                raise
            raise AdapterError("FIXTURE_INTEGRITY_ERROR", "불변 스냅샷의 입력 검증에 실패했습니다.") from exc

    def __exit__(self, *args):
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None

    def case(self, case_id: str) -> dict:
        require(self._temporary is not None, "FIXTURE_CLOSED", "검증 스냅샷이 닫혔습니다.")
        entry = next((e for e in self.entries if e["case_id"] == case_id), None)
        require(entry is not None, "CASE_NOT_FOUND", "catalog에 정확히 일치하는 사례가 없습니다.")
        base = self.root / "cases" / case_id
        raw = {}
        for folder, names in (("agent_view", AGENT_FILES), ("controller_only", CONTROLLER_FILES),
                              ("evaluator_only", EVALUATOR_FILES)):
            for name in sorted(names):
                path = base / folder / name
                data = path.read_bytes()
                require(sha256(data) == self.manifest["file_sha256"][path.relative_to(self.root).as_posix()],
                        "FIXTURE_INTEGRITY_ERROR", "검증 이후 스냅샷이 변경됐습니다.")
                raw[f"{folder}/{name}"] = data
        def rows(name, fields):
            reader = csv.DictReader(io.StringIO(raw["agent_view/" + name].decode("utf-8"), newline=""))
            require(reader.fieldnames == fields, "FIXTURE_INTEGRITY_ERROR", "CSV 헤더가 다릅니다.")
            result = list(reader)
            for row in result:
                row["amount_krw"] = int(row["amount_krw"])
                row["row_revision"] = int(row["row_revision"])
            return result
        return {"case_id": case_id, "internal": rows("internal.csv", INTERNAL_FIELDS),
                "external": rows("external.csv", EXTERNAL_FIELDS),
                "batch": json.loads(raw["agent_view/batch.json"]), "policy": json.loads(raw["agent_view/policy.json"]),
                "notes": json.loads(raw["agent_view/external_notes.json"]),
                "request": raw["agent_view/request.txt"].decode("utf-8"), "raw": raw,
                "dataset_fingerprint": self.manifest["dataset_fingerprint"],
                "fixture_schema_version": self.manifest["schema_version"]}
