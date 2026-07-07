"""T32: MemoryAgent.get_archival_memory() 原本在 exists()/read_text() 檢查
後又對同一個檔案呼叫 stat()（TOCTOU），若檔案在兩次呼叫之間被刪除
（例如並行的 team run 正在寫入/覆蓋 memory 檔），會直接拋出未被接住的
FileNotFoundError，讓整個 context 組裝失敗。"""
import time

from memory_agent import MemoryAgent


def test_safe_mtime_falls_back_when_file_vanishes(tmp_path):
    agent = MemoryAgent(global_mem_dir=tmp_path / "global")
    f = tmp_path / "gone.md"
    f.write_text("content", encoding="utf-8")
    f.unlink()  # simulate the race: file vanished between read and stat

    before = time.time()
    result = agent._safe_mtime(f)
    assert result >= before  # fell back to time.time(), didn't raise


def test_get_archival_memory_survives_vanishing_project_file(tmp_path, monkeypatch):
    global_mem = tmp_path / "global"
    agent_mem = tmp_path / "agent"
    (agent_mem / "projects").mkdir(parents=True)
    proj_file = agent_mem / "projects" / "myproj.md"
    proj_file.write_text("some experience notes", encoding="utf-8")

    agent = MemoryAgent(global_mem_dir=global_mem, agent_mem_dir=agent_mem, cwd_slug="myproj")

    # Monkeypatch Path.stat to simulate the file vanishing right after _read_md
    # succeeded (exists()+read_text(), which itself calls stat() once) but
    # before the explicit _safe_mtime() call runs.
    real_stat = type(proj_file).stat
    call_count = {"n": 0}

    def flaky_stat(self, *a, **kw):
        if self == proj_file:
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise FileNotFoundError("simulated race")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(type(proj_file), "stat", flaky_stat)

    archival = agent.get_archival_memory(agent_id="test-agent")
    assert len(archival) == 1
    assert archival[0]["content"] == "some experience notes"
    assert isinstance(archival[0]["mtime"], float)
