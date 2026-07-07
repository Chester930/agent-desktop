"""T29: `with _db() as c:` 到處都在用，用的是 sqlite3.Connection 自己的
context manager（只 commit/rollback，不 close），洩漏連線 handle。
_db_ctx() 是唯一正確的替代寫法，用法相同但保證離開時一定 close()。"""
import sqlite3

import database


def test_db_ctx_closes_connection_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_INDEX_DB", tmp_path / "idx.db")
    database._init_db()

    with database._db_ctx() as c:
        c.execute("SELECT 1")

    # A closed sqlite3.Connection raises ProgrammingError on further use.
    import pytest
    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("SELECT 1")


def test_db_ctx_closes_connection_on_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_INDEX_DB", tmp_path / "idx.db")
    database._init_db()

    captured_conn = None
    try:
        with database._db_ctx() as c:
            captured_conn = c
            raise ValueError("boom")
    except ValueError:
        pass

    import pytest
    with pytest.raises(sqlite3.ProgrammingError):
        captured_conn.execute("SELECT 1")


def test_db_ctx_still_commits_like_plain_connection_context_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_INDEX_DB", tmp_path / "idx.db")
    database._init_db()

    with database._db_ctx() as c:
        c.execute("INSERT INTO sessions(id, title) VALUES ('s1', 'hello')")

    # Re-open a fresh connection to confirm the insert was actually committed.
    conn2 = database._db()
    try:
        row = conn2.execute("SELECT title FROM sessions WHERE id='s1'").fetchone()
        assert row["title"] == "hello"
    finally:
        conn2.close()
