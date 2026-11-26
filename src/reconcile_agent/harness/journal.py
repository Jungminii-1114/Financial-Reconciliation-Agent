"""Local single-worker lease and durable intent/evidence storage."""

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from secrets import token_hex

from ..business.connection import connect, transaction
from ..business.errors import require
from ..business.input_digest import canonical, fingerprint
from ..tools.errors import ToolError
from ..tools.ledger import ToolCallLedger


@contextmanager
def locked_runtime(handle):
    root = handle.root.with_name(handle.root.name + ".runtime")
    root.mkdir(mode=0o700, exist_ok=True)
    require(not root.is_symlink() and root.is_dir(), "UNSAFE_PATH", "실행 기록 경로가 올바르지 않습니다.")
    lock_fd = os.open(root / "worker.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ToolError("RUN_BUSY", "이 실행의 다른 프로세스가 이미 작동 중입니다.") from exc
        yield Journal(root, handle)
    finally:
        os.close(lock_fd)


class Journal:
    def __init__(self, root: Path, handle):
        self.path = root / "journal.sqlite"
        binding = fingerprint({**handle.agent_admission(), "config": handle.config.config_hash})
        with connect(self.path, writable=True, create=not self.path.exists()) as db:
            db.execute("PRAGMA synchronous=FULL")
            with transaction(db, write=True):
                db.execute("""CREATE TABLE IF NOT EXISTS journal_meta (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), binding TEXT NOT NULL, cursor_key TEXT NOT NULL)""")
                db.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, record TEXT NOT NULL)")
                db.execute("""CREATE TABLE IF NOT EXISTS evidence (
                    run_id TEXT NOT NULL, ref TEXT NOT NULL, input_version INTEGER NOT NULL, input_digest TEXT NOT NULL,
                    PRIMARY KEY(run_id, ref), FOREIGN KEY(run_id) REFERENCES runs(run_id))""")
                db.execute("""CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id))""")
                db.execute("INSERT OR IGNORE INTO journal_meta VALUES (1,?,?)", (binding, token_hex(32)))
                stored = db.execute("SELECT binding,cursor_key FROM journal_meta WHERE singleton=1").fetchone()
                require(stored["binding"] == binding, "SCOPE_DENIED", "실행 기록이 다른 trial에 속합니다.")
                self.cursor_key = bytes.fromhex(stored["cursor_key"])
        self.ledger = ToolCallLedger(root / "calls.sqlite")

    def load(self, run_id):
        with connect(self.path) as db:
            row = db.execute("SELECT record FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @contextmanager
    def _write(self):
        with connect(self.path, writable=True) as db:
            db.execute("PRAGMA synchronous=FULL")
            with transaction(db, write=True):
                yield db

    def save(self, run_id, state):
        with self._write() as db:
            db.execute("""INSERT INTO runs VALUES(?,?) ON CONFLICT(run_id)
                DO UPDATE SET record=excluded.record""", (run_id, canonical(state).decode()))

    def check_intent(self, ctx, request):
        state = self.load(ctx.logical_run_id)
        if not state or state.get("intent") != request:
            raise ToolError("INTENT_REQUIRED", "이 실행에 연결된 작업 의도가 없습니다.")

    def register_evidence(self, run_id, data):
        with self._write() as db:
            db.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?,?)",
                       (run_id, data.result_digest, data.input_version, data.input_digest))

    def check_evidence(self, ctx, request):
        with connect(self.path) as db:
            for ref in request["evidence_refs"]:
                row = db.execute("SELECT input_version,input_digest FROM evidence WHERE run_id=? AND ref=?",
                                 (ctx.logical_run_id, ref)).fetchone()
                if row is None or ("expected_input_version" in request and tuple(row) != (
                    request["expected_input_version"], request["expected_input_digest"]
                )):
                    raise ToolError("EVIDENCE_STALE", "승인 요청에 연결되는 관측 근거가 없습니다.")

    def save_draft(self, ctx, data):
        with self._write() as db:
            db.execute("INSERT OR IGNORE INTO drafts VALUES(?,?,?)",
                       (data["draft_id"], ctx.logical_run_id, canonical(data).decode()))
