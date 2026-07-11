"""2026-07-10 team 協作優化健檢：HR Agent 自動組隊產生的 plan 從未帶
execution_mode 欄位，即使 routes/teams.py 已經修好「execution_mode 會被
正確套用」的傳遞機制（見 test_team_run_execution_mode.py），HR 派發出來的
team 實際上還是永遠平行跑——因為 _run_hr_agent() 的 prompt/JSON schema
根本沒要求模型輸出這個欄位，套用端自然拿不到值，只能 fallback 成預設的
"parallel"。

修法：① prompt/schema 明確要求固定輸出 "execution_mode": "sequential"
（這個 team 本來就是設計成前一位輸出傳給下一位）；② 解析完 JSON 後再補一層
防呆，模型漏填時由後端直接補上 "sequential"，不依賴模型 100% 照 schema 輸出。
"""
import asyncio
import tempfile
from pathlib import Path

import pytest

import routes.agents as agents_module

pytestmark = pytest.mark.asyncio


class _FakeProc:
    def __init__(self, stdout: bytes):
        self._stdout = stdout

    async def communicate(self):
        return self._stdout, b""


def _make_agents_dir():
    d = Path(tempfile.mkdtemp())
    (d / "coder.md").write_text(
        "---\nname: coder\ndescription: 寫代碼\n---\n\n身體\n", encoding="utf-8"
    )
    return d


async def test_model_omits_execution_mode_gets_defaulted_to_sequential(monkeypatch):
    agents_dir = _make_agents_dir()
    import database
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    # simulate the model forgetting to include execution_mode despite the prompt
    fake_json = b'{"name": "auto-team", "description": "d", "members": [{"agent": "coder", "role": "r", "input_memory": [], "output_memory": []}]}'

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(fake_json)

    monkeypatch.setattr(agents_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    plan = await agents_module._run_hr_agent("build something")

    assert "error" not in plan
    assert plan["execution_mode"] == "sequential"


async def test_model_explicit_execution_mode_is_preserved(monkeypatch):
    agents_dir = _make_agents_dir()
    import database
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    fake_json = b'{"name": "auto-team", "description": "d", "execution_mode": "sequential", "members": [{"agent": "coder", "role": "r"}]}'

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(fake_json)

    monkeypatch.setattr(agents_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    plan = await agents_module._run_hr_agent("build something")

    assert plan["execution_mode"] == "sequential"


async def test_markdown_fenced_response_still_gets_default(monkeypatch):
    """Model wraps the JSON in ```json fences AND forgets execution_mode —
    both recovery paths (fence stripping, substring extraction) must still
    apply the sequential default."""
    agents_dir = _make_agents_dir()
    import database
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    fake_json = b'```json\n{"name": "auto-team", "description": "d", "members": [{"agent": "coder", "role": "r"}]}\n```'

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(fake_json)

    monkeypatch.setattr(agents_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    plan = await agents_module._run_hr_agent("build something")

    assert "error" not in plan
    assert plan["execution_mode"] == "sequential"
