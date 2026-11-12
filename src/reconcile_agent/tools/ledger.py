"""Payload-free durable attempt/budget ledger, separate from the business DB."""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
from datetime import datetime, timezone
from .errors import ToolError
from ..business.input_digest import fingerprint


class ToolCallLedger:
    def __init__(self, path: Path, *, max_attempts: int = 20):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10000:
            raise ToolError("CONTRACT_VIOLATION", "호출 예산 설정이 올바르지 않습니다.")
        self.path, self.max_attempts = Path(path), max_attempts
        if self.path.is_symlink():
            raise ToolError("AUDIT_UNAVAILABLE", "호출 기록 경로가 올바르지 않습니다.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        if fresh:
            with self.path.open('xb'): pass
            self.path.chmod(0o600)
        with self._connection() as db:
            if fresh:
                db.executescript('''
                    CREATE TABLE runtime_meta(version INTEGER NOT NULL);
                    INSERT INTO runtime_meta VALUES(1);
                    CREATE TABLE budgets(group_id TEXT PRIMARY KEY,binding TEXT NOT NULL,
                        max_attempts INTEGER NOT NULL,used INTEGER NOT NULL);
                    CREATE TABLE calls(call_id TEXT PRIMARY KEY,group_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,session_id TEXT NOT NULL,tool_name TEXT NOT NULL,
                        started_at TEXT NOT NULL,status TEXT NOT NULL,code TEXT,
                        elapsed_ms INTEGER,response_digest TEXT);
                    CREATE TABLE events(seq INTEGER PRIMARY KEY,call_id TEXT NOT NULL,
                        kind TEXT NOT NULL,at TEXT NOT NULL);
                ''')
            elif db.execute('SELECT version FROM runtime_meta').fetchone() != (1,):
                raise ToolError("AUDIT_UNAVAILABLE", "지원하지 않는 호출 기록 형식입니다.")

    @contextmanager
    def _connection(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA trusted_schema=OFF')
            yield db
        except (sqlite3.Error, OSError) as exc:
            raise ToolError("AUDIT_UNAVAILABLE", "호출 기록을 저장할 수 없습니다.") from exc
        finally:
            if db is not None: db.close()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def reserve(self, ctx, tool_name):
        binding = fingerprint({'principal':ctx.principal_id,'trial':ctx.trial_id,'config':ctx.config_hash})
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('INSERT OR IGNORE INTO budgets VALUES(?,?,?,0)',
                           (ctx.budget_group_id,binding,self.max_attempts))
                stored = db.execute('SELECT binding,max_attempts,used FROM budgets WHERE group_id=?',
                                    (ctx.budget_group_id,)).fetchone()
                if stored[:2] != (binding,self.max_attempts):
                    raise ToolError('SCOPE_DENIED','호출 예산의 실행 범위가 다릅니다.')
                if db.execute('SELECT 1 FROM calls WHERE call_id=?',(ctx.tool_call_id,)).fetchone():
                    raise ToolError('CALL_ID_CONFLICT','이미 사용한 호출 ID입니다.')
                if stored[2] >= stored[1]:
                    db.execute('INSERT INTO events(call_id,kind,at) VALUES(?,?,?)',
                               (ctx.tool_call_id,'BUDGET_REJECTED',self._now()))
                    db.commit()
                    raise ToolError('BUDGET_EXHAUSTED','호출 예산을 소진했습니다.')
                db.execute('UPDATE budgets SET used=used+1 WHERE group_id=?',(ctx.budget_group_id,))
                db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL)',
                           (ctx.tool_call_id,ctx.budget_group_id,ctx.logical_run_id,ctx.session_id,
                            tool_name,self._now(),'RESERVED'))
                db.execute('INSERT INTO events(call_id,kind,at) VALUES(?,?,?)',
                           (ctx.tool_call_id,'ATTEMPT_RESERVED',self._now()))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def event(self, call_id, kind):
        with self._connection() as db:
            db.execute('INSERT INTO events(call_id,kind,at) VALUES(?,?,?)',(call_id,kind,self._now()))

    def finish(self, call_id, status, code, elapsed_ms, response_digest):
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                changed = db.execute('UPDATE calls SET status=?,code=?,elapsed_ms=?,response_digest=? '
                                     'WHERE call_id=? AND status=?',
                                     (status,code,elapsed_ms,response_digest,call_id,'RESERVED')).rowcount
                if changed != 1: raise ToolError('AUDIT_UNAVAILABLE','호출 기록 상태가 올바르지 않습니다.')
                db.execute('INSERT INTO events(call_id,kind,at) VALUES(?,?,?)',
                           (call_id,'CALL_FINISHED',self._now()))
                db.commit()
            except BaseException:
                db.rollback()
                raise
