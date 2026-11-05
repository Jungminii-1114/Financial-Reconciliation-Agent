"""Explicit SQLite lifecycle: never silently create a missing runtime database."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import APPLICATION_ID, BUSINESS_SCHEMA_VERSION
from .errors import AdapterError, require


@contextmanager
def connect(path: Path, *, writable=False, create=False, timeout=2.0):
    path = Path(path)
    require(not path.is_symlink(), "UNSAFE_PATH", "DB 링크는 허용하지 않습니다.")
    if create:
        # The caller owns a private staging directory. Exclusive reservation prevents reuse.
        with path.open("xb"):
            pass
        path.chmod(0o600)
    require(path.is_file(), "TRIAL_MISSING", "기존 DB 파일이 필요합니다.")
    db = None
    try:
        mode = "rw" if writable or create else "ro"
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=" + mode,
                             isolation_level=None, timeout=timeout)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        require(db.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
                "STORAGE_CONFIGURATION_ERROR", "외래 키 활성화에 실패했습니다.")
        if not writable and not create:
            db.execute("PRAGMA query_only=ON")
        yield db
    except sqlite3.OperationalError as exc:
        code = "SQLITE_BUSY" if "locked" in str(exc).lower() or "busy" in str(exc).lower() else "STORAGE_ERROR"
        raise AdapterError(code, "SQLite 작업을 완료하지 못했습니다.") from exc
    except sqlite3.DatabaseError as exc:
        raise AdapterError("STORAGE_ERROR", "SQLite 제약 또는 무결성 검사에 실패했습니다.") from exc
    finally:
        if db is not None:
            db.close()


@contextmanager
def transaction(db, *, write=False):
    require(not db.in_transaction, "TRANSACTION_STATE_ERROR", "중첩 트랜잭션은 지원하지 않습니다.")
    db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


def create_business_schema(db) -> None:
    require(not db.in_transaction, "TRANSACTION_STATE_ERROR", "DDL은 적재 트랜잭션 이전에 수행합니다.")
    db.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    db.execute(f"PRAGMA application_id={APPLICATION_ID}")
    db.execute(f"PRAGMA user_version={BUSINESS_SCHEMA_VERSION}")


def check_business_version(db) -> None:
    require(db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID and
            db.execute("PRAGMA user_version").fetchone()[0] == BUSINESS_SCHEMA_VERSION,
            "BUSINESS_SCHEMA_MISMATCH", "지원하는 업무 DB 버전이 아닙니다.")


def check_database(db, *, business=False) -> None:
    if business:
        check_business_version(db)
    require([r[0] for r in db.execute("PRAGMA integrity_check")] == ["ok"] and
            not db.execute("PRAGMA foreign_key_check").fetchall(),
            "IMPORT_INVARIANT_ERROR", "DB 무결성 검사에 실패했습니다.")
