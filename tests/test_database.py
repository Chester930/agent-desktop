"""T9: database.py 的 _init_db 自我修復邏輯不應對任何 sqlite3.Error 都刪除重建索引 DB，
只有真的偵測到檔案損毀時才重建；重建呼叫本身失敗時也要正確拋出而非被吃掉。"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

import database


@pytest.fixture
def isolated_index_db(monkeypatch, tmp_path):
    db_path = tmp_path / "idx.db"
    monkeypatch.setattr(database, "_INDEX_DB", db_path)
    return db_path


def test_corrupted_file_triggers_rebuild(isolated_index_db):
    isolated_index_db.write_bytes(b"not a sqlite file at all, garbage bytes")

    database._init_db()

    conn = database._db()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_transient_operational_error_is_not_swallowed(monkeypatch, isolated_index_db):
    """A locked/busy DB (OperationalError) must propagate, not trigger deletion of
    the user's session index — only genuine corruption should do that."""

    def boom():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(database, "_db", boom)

    with pytest.raises(sqlite3.OperationalError):
        database._init_db()

    # the (nonexistent) index file must NOT have been touched/created as a side effect
    assert not isolated_index_db.exists()


def test_rebuild_failure_propagates_instead_of_being_silenced(monkeypatch, isolated_index_db):
    isolated_index_db.write_bytes(b"garbage")

    call_count = {"n": 0}
    real_db = database._db

    def flaky_db():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise sqlite3.DatabaseError("file is not a database")
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(database, "_db", flaky_db)

    with pytest.raises(sqlite3.DatabaseError):
        database._init_db()


def test_db_connection_closed_when_setup_fails(isolated_index_db):
    """_db() must not leak an open handle when PRAGMA setup fails on a corrupted file
    (an open handle on Windows would block the caller's unlink+rebuild)."""
    isolated_index_db.write_bytes(b"garbage, not sqlite")

    with pytest.raises(sqlite3.DatabaseError):
        database._db()

    # If the handle leaked, deleting the file would fail on Windows.
    isolated_index_db.unlink()
    assert not isolated_index_db.exists()
