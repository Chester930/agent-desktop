"""可插拔 agent engine（engines/ package）的測試。

背景：使用者要求把「執行任務的 CLI（Claude / Codex）」抽成可切換的架構，
即使目前只有 Claude 側能用真實 CLI 驗證（這個環境沒有安裝 codex）。
ClaudeEngine 是既有 _agent_run_capture() 邏輯的忠實搬遷，用跟之前完全一樣
的 fake-subprocess 手法測；CodexEngine 是根據 OpenAI 官方文件寫的第一版
（見 engines/codex_engine.py 檔頭註解），測試資料直接取材自文件裡的範例
事件格式，跟 codex_engine.py 的解析邏輯做交叉驗證——這不能證明真實 CLI
的行為完全一致（需要真的 codex CLI 才能驗證），但至少證明解析邏輯跟
「我們以為 CLI 會輸出什麼」是一致的。
"""
import json

import pytest

from engines import claude_engine, codex_engine
from engines.registry import resolve_engine_name, resolve_engine_name_gated, get_engine, ENGINES, DEFAULT_ENGINE_NAME

pytestmark = pytest.mark.asyncio


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeStdin:
    """codex_engine.py 用 stdin=PIPE 傳 prompt（見該檔頭的說明：Windows 上
    多行 prompt 當 CLI 引數傳會被 cmd.exe 搞壞，改用官方文件記載的
    "-" + stdin 方式），這裡記錄寫入的內容供測試斷言。"""
    def __init__(self):
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self, lines, returncode=0):
        self.stdout = _FakeStdout(lines)
        self.stdin = _FakeStdin()
        self.returncode = returncode

    async def wait(self):
        return self.returncode


# ── registry.resolve_engine_name / get_engine ──────────────────────────────

class TestResolveEngineName:
    pytestmark = []

    def test_defaults_to_codex_when_nothing_specified(self):
        """2026-07-13：使用者確認兩邊 CLI 都能用時，預設改成 Codex。"""
        assert resolve_engine_name("", "") == DEFAULT_ENGINE_NAME == "codex"

    def test_frontmatter_takes_priority_over_request(self):
        assert resolve_engine_name("codex", "claude") == "codex"

    def test_request_used_when_frontmatter_empty(self):
        assert resolve_engine_name("", "codex") == "codex"

    def test_unknown_frontmatter_value_falls_through_to_request(self):
        assert resolve_engine_name("not-a-real-engine", "codex") == "codex"

    def test_unknown_everything_falls_back_to_default(self):
        assert resolve_engine_name("bogus", "also-bogus") == DEFAULT_ENGINE_NAME

    def test_get_engine_returns_default_for_unknown_name(self):
        assert get_engine("bogus") is ENGINES[DEFAULT_ENGINE_NAME]

    def test_get_engine_returns_requested_module(self):
        assert get_engine("codex") is codex_engine


# ── registry.resolve_engine_name_gated ──────────────────────────────────────
# 2026-07-12：Settings 新增「執行引擎範圍」鎖定（database.get_engine_mode()）。
# 鎖定成 'claude'/'codex' 時，agent frontmatter／request 層級的值完全不看，
# 直接收斂成鎖定的那個值——這是行為收斂，不是優先序調整。mode='both' 時必須
# 是對 resolve_engine_name() 100% 忠實的 passthrough，用同一組既有測試案例
# 參數化重跑一次來證明這件事，不是憑印象宣稱。

class TestResolveEngineNameGated:
    pytestmark = []

    def test_locked_claude_ignores_frontmatter_and_request(self):
        assert resolve_engine_name_gated("codex", "codex", "claude") == "claude"

    def test_locked_codex_ignores_frontmatter_and_request(self):
        assert resolve_engine_name_gated("claude", "claude", "codex") == "codex"

    def test_locked_claude_with_nothing_specified_still_claude(self):
        assert resolve_engine_name_gated("", "", "claude") == "claude"

    @pytest.mark.parametrize("frontmatter,request_engine,expected", [
        ("", "", DEFAULT_ENGINE_NAME),
        ("codex", "claude", "codex"),
        ("", "codex", "codex"),
        ("not-a-real-engine", "codex", "codex"),
        ("bogus", "also-bogus", DEFAULT_ENGINE_NAME),
    ])
    def test_mode_both_delegates_identically_to_resolve_engine_name(self, frontmatter, request_engine, expected):
        """跟 TestResolveEngineName 用同一組案例，證明 mode='both' 是真正的
        pass-through，不是巧合地算出一樣的結果。"""
        assert resolve_engine_name_gated(frontmatter, request_engine, "both") == expected
        assert resolve_engine_name_gated(frontmatter, request_engine, "both") == resolve_engine_name(frontmatter, request_engine)

    def test_unknown_mode_value_treated_as_both(self):
        """get_engine_mode() 自己已經正規化過，理論上不會把非法值傳進來，但
        這個函式本身也要 fail safe——未知 mode 值不應該意外鎖死某個引擎。"""
        assert resolve_engine_name_gated("codex", "", "not-a-real-mode") == "codex"


# ── ClaudeEngine.run_turn ────────────────────────────────────────────────────

async def test_claude_engine_extracts_text_and_session_id(monkeypatch):
    lines = [
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"Hello "}]}}\n',
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"world"}]}}\n',
        b'{"type":"result","session_id":"sid-123"}\n',
    ]

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(lines)

    monkeypatch.setattr(claude_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    received = []

    async def on_text(chunk):
        received.append(chunk)

    result = await claude_engine.run_turn(
        prompt="hi", cwd="/tmp", model="haiku", permission_mode="acceptEdits",
        resume_session_id=None, api_key="", on_text=on_text,
    )

    assert result.output == "Hello world"
    assert result.session_id == "sid-123"
    assert result.error is None
    assert received == ["Hello ", "world"]


async def test_claude_engine_falls_back_to_default_permission_mode_for_unknown_value(monkeypatch):
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(claude_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await claude_engine.run_turn(
        prompt="hi", cwd="/tmp", model="haiku", permission_mode="workspace-write",  # codex vocabulary, not claude's
        resume_session_id=None, api_key="", on_text=lambda c: None,
    )

    cmd = captured["args"]
    assert cmd[cmd.index("--permission-mode") + 1] == claude_engine.DEFAULT_PERMISSION_MODE == "acceptEdits"


async def test_claude_engine_resume_flag(monkeypatch):
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(claude_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await claude_engine.run_turn(
        prompt="hi", cwd="/tmp", model="", permission_mode="acceptEdits",
        resume_session_id="sid-abc", api_key="", on_text=lambda c: None,
    )

    cmd = captured["args"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "sid-abc"


# ── CodexEngine.run_turn ──────────────────────────────────────────────────────
# 測試資料取材自 engines/codex_engine.py 檔頭引用的官方文件範例事件格式。

async def test_codex_engine_extracts_text_and_thread_id(monkeypatch):
    lines = [
        b'{"type":"thread.started","thread_id":"0199a213-81c0-7800-8aa1-bbab2a035a53"}\n',
        b'{"type":"turn.started"}\n',
        b'{"type":"item.completed","item":{"id":"item_1","type":"reasoning","status":"completed"}}\n',
        b'{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"Hello from codex"}}\n',
        b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n',
    ]

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(lines)

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    received = []

    async def on_text(chunk):
        received.append(chunk)

    result = await codex_engine.run_turn(
        prompt="hi", cwd="/tmp", model="gpt-5.4", permission_mode="workspace-write",
        resume_session_id=None, api_key="", on_text=on_text,
    )

    assert result.output == "Hello from codex"
    assert result.session_id == "0199a213-81c0-7800-8aa1-bbab2a035a53"
    assert result.error is None
    assert received == ["Hello from codex"]
    # non-agent_message items (reasoning, command_execution, ...) must not leak into output
    assert "reasoning" not in result.output


async def test_codex_engine_turn_failed_becomes_error(monkeypatch):
    lines = [
        b'{"type":"thread.started","thread_id":"sid-x"}\n',
        b'{"type":"turn.failed","error":{"message":"sandbox denied"}}\n',
    ]

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(lines)

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await codex_engine.run_turn(
        prompt="hi", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id=None, api_key="", on_text=lambda c: None,
    )

    assert result.error is not None
    assert "sandbox denied" in result.error


async def test_codex_engine_real_invalid_model_scenario(monkeypatch):
    """2026-07-11：用真實 codex CLI（已登入帳號）實測「不存在的 model 名稱」
    這個錯誤路徑，鎖定觀察到的真實行為，避免之後回歸：
    (1) 失敗前會先出現一到多個非致命的 item.completed/error 警告（例如
        model metadata fallback、skills budget），這些要照樣經 on_text 送出、
        不能被吞掉；
    (2) 最終的 turn.failed 事件裡 error.message 欄位本身是「一段 JSON 文字」
        （OpenAI API 回傳的 400 錯誤被 Codex CLI 原封不動塞進字串），不是
        一個乾淨的 error 物件——RunResult.error 只需要把整段內容原樣保留
        供除錯查看，不需要也不應該試圖再往下解析這層巢狀 JSON；
    (3) thread.started 給的 session_id 即使最後失敗仍要保留（呼叫端可能想
        用來 resume 或除錯），不能因為失敗就清空。
    """
    real_error_message = (
        '{"type":"error","status":400,"error":{"type":"invalid_request_error",'
        '"message":"The \'this-model-definitely-does-not-exist-xyz123\' model '
        'is not supported when using Codex with a ChatGPT account."}}'
    )
    lines = [
        b'{"type":"thread.started","thread_id":"sid-real-invalid-model"}\n',
        b'{"type":"turn.started"}\n',
        json.dumps({
            "type": "item.completed",
            "item": {"type": "error", "message": "Model metadata not found, defaulting to fallback."},
        }).encode("utf-8") + b"\n",
        json.dumps({
            "type": "item.completed",
            "item": {"type": "error", "message": "Exceeded skills context budget of 2%."},
        }).encode("utf-8") + b"\n",
        json.dumps({
            "type": "turn.failed",
            "error": {"message": real_error_message},
        }).encode("utf-8") + b"\n",
    ]

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(lines)

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    received = []

    async def on_text(chunk):
        received.append(chunk)

    result = await codex_engine.run_turn(
        prompt="hello", cwd="/tmp", model="this-model-definitely-does-not-exist-xyz123",
        permission_mode="workspace-write", resume_session_id=None, api_key="", on_text=on_text,
    )

    assert len(received) == 2
    assert "Model metadata not found" in received[0]
    assert "Exceeded skills context budget" in received[1]
    assert result.session_id == "sid-real-invalid-model"
    assert result.error is not None
    assert "this-model-definitely-does-not-exist-xyz123" in result.error
    assert "invalid_request_error" in result.error


async def test_codex_engine_uses_resume_subcommand(monkeypatch):
    """2026-07-10：prompt 不再出現在指令列引數裡（見下方 stdin 測試的說明），
    改成用 "-" 佔位、實際內容透過 stdin 傳，所以這裡只驗證子指令結構，
    prompt 內容的斷言移到 test_codex_engine_sends_prompt_via_stdin。"""
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        proc = _FakeProc([])
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await codex_engine.run_turn(
        prompt="continue please", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id="sid-abc", api_key="", on_text=lambda c: None,
    )

    # wrap_cmd() 在真的裝了 codex（Windows npm .cmd shim）的機器上會把整個
    # cmd 包成 ["cmd", "/c", "<path>\\codex.CMD", "exec", "resume", ...]，
    # 所以只斷言 "exec"/"resume"/"sid-abc" 這幾個字彙有依序出現，不要求它們
    # 一定在最前面。
    cmd = list(captured["args"])
    exec_idx = cmd.index("exec")
    assert cmd[exec_idx:exec_idx + 3] == ["exec", "resume", "sid-abc"]
    assert "-" in cmd
    assert "continue please" not in cmd  # 不再是 CLI 引數


async def test_codex_engine_sends_prompt_via_stdin(monkeypatch):
    """2026-07-10 用真實 codex CLI 驗證發現：Windows 上 codex 是 npm .cmd
    shim，wrap_cmd() 會包一層 cmd /c；cmd.exe 對「引數裡包含換行字元」的
    處理是壞的——team run 的真實 prompt 幾乎都是多行字串（memory context、
    任務描述用 "\n\n" 接起來），當 CLI 引數傳會被截斷/錯誤斷行，實測看到
    模型只收到 prompt 的第一行就結束，完全沒看到真正的任務內容，且 codex
    甚至整個退回互動式人類可讀輸出（不是 --json 要求的 JSONL）。改用官方
    文件記載的方式：引數位置填 "-"，實際 prompt 透過 stdin 送進去，完全不
    經過 cmd.exe 的命令列 tokenize。"""
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        proc = _FakeProc([])
        captured["proc"] = proc
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    multiline_prompt = "[Memory Context]\n測試內容\n\n---\n## 任務\n\n請回覆 ok"
    await codex_engine.run_turn(
        prompt=multiline_prompt, cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id=None, api_key="", on_text=lambda c: None,
    )

    assert captured["kwargs"]["stdin"] == codex_engine.asyncio.subprocess.PIPE
    assert captured["proc"].stdin.written == multiline_prompt.encode("utf-8")
    assert captured["proc"].stdin.closed is True


async def test_codex_engine_resume_omits_sandbox_and_cd_flags(monkeypatch):
    """2026-07-10 用真實 codex CLI 驗證發現：`codex exec resume` 子指令完全
    不接受 --sandbox／--cd（`codex exec resume --help` 的選項列表裡沒有這
    兩個 flag），塞了會直接 `error: unexpected argument '--sandbox' found`
    整個失敗——resumed session 沿用建立當下的 sandbox/cwd，沒辦法在 resume
    時更換。"""
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await codex_engine.run_turn(
        prompt="continue please", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id="sid-abc", api_key="", on_text=lambda c: None,
    )

    cmd = list(captured["args"])
    assert "--sandbox" not in cmd
    assert "--cd" not in cmd
    assert "--json" in cmd
    assert "--skip-git-repo-check" in cmd


async def test_codex_engine_fresh_call_still_includes_sandbox_and_cd(monkeypatch):
    """對照組：非 resume 的一般呼叫仍然要帶 --sandbox／--cd（只有 resume
    子指令不接受這兩個 flag）。"""
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await codex_engine.run_turn(
        prompt="hi", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id=None, api_key="", on_text=lambda c: None,
    )

    cmd = list(captured["args"])
    assert "--sandbox" in cmd
    assert "--cd" in cmd


async def test_codex_engine_nonzero_exit_with_no_json_becomes_error(monkeypatch):
    """2026-07-10 用真實 codex CLI 驗證發現：CLI 層級的失敗（例如 resume
    收到不支援的 flag）不會用 JSON 事件回報，只印純文字錯誤訊息到
    stdout/stderr 然後以非零結束碼結束——原本的解析器對這種情況完全沒反應，
    回傳一個「看起來成功但空白」的 RunResult（output=""、session_id=""、
    error=None），呼叫端沒辦法分辨「這一步真的什麼都沒做」還是「CLI 呼叫
    失敗了」。改成：process 以非零結束碼結束、且完全沒有解析到任何 JSON
    事件時，視為失敗。"""
    lines = [
        b"error: unexpected argument '--sandbox' found\n",
        b"tip: to pass '--sandbox' as a value, use '-- --sandbox'\n",
        b"Usage: codex exec resume --json <SESSION_ID> <PROMPT>\n",
    ]

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(lines, returncode=2)

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await codex_engine.run_turn(
        prompt="hi", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id="sid-abc", api_key="", on_text=lambda c: None,
    )

    assert result.error is not None
    assert "unexpected argument" in result.error


async def test_codex_engine_zero_exit_with_no_output_is_not_an_error(monkeypatch):
    """對照組：process 正常結束（returncode 0）但沒有任何輸出，不應該被
    誤判成失敗——例如一個空的 turn。"""
    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc([], returncode=0)

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await codex_engine.run_turn(
        prompt="hi", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id=None, api_key="", on_text=lambda c: None,
    )

    assert result.error is None


async def test_codex_engine_normalizes_unknown_sandbox_value_to_default(monkeypatch):
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    # "acceptEdits" is Claude vocabulary, not a valid --sandbox value —
    # this happens for real when a team mixes Claude/Codex members and the
    # run-level permission_mode default was set for the Claude member.
    await codex_engine.run_turn(
        prompt="hi", cwd="/tmp", model="", permission_mode="acceptEdits",
        resume_session_id=None, api_key="", on_text=lambda c: None,
    )

    cmd = captured["args"]
    assert cmd[cmd.index("--sandbox") + 1] == codex_engine.DEFAULT_PERMISSION_MODE == "workspace-write"


async def test_codex_engine_passes_through_danger_full_access(monkeypatch):
    """2026-07-11：用真實已登入帳號實測過 danger-full-access——這是目前
    Windows 上唯一能讓 Codex 執行 Bash/shell 指令的 sandbox 等級（
    workspace-write 底下 shell 指令會因 CreateProcessAsUserW 被拒絕失敗，
    見 codex_engine.py 檔頭），所以 --sandbox 要原樣把它傳給 CLI、不能被
    _normalize_sandbox_mode() 誤判成不合法值而退回預設。"""
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await codex_engine.run_turn(
        prompt="hi", cwd="/tmp", model="", permission_mode="danger-full-access",
        resume_session_id=None, api_key="", on_text=lambda c: None,
    )

    cmd = captured["args"]
    assert cmd[cmd.index("--sandbox") + 1] == "danger-full-access"


async def test_codex_engine_image_attachments_use_dash_i_flag(monkeypatch, tmp_path):
    """2026-07-11：一開始誤判 Codex 沒有附件參數，經使用者提醒後查證
    `codex exec --help`/`codex exec resume --help` 才發現兩者都原生支援
    -i/--image（可重複），不需要在附件跟 Codex 之間二選一。"""
    img1 = tmp_path / "screenshot.png"
    img1.write_bytes(b"fake-png-bytes")
    img2 = tmp_path / "photo.jpg"
    img2.write_bytes(b"fake-jpg-bytes")

    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await codex_engine.run_turn(
        prompt="describe these images", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id=None, api_key="", on_text=lambda c: None,
        attachments=[str(img1), str(img2)],
    )

    cmd = list(captured["args"])
    image_flag_values = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "-i"]
    assert str(img1) in image_flag_values
    assert str(img2) in image_flag_values


async def test_codex_engine_text_attachment_content_reaches_stdin(monkeypatch, tmp_path):
    """純文字附件（.txt/.md/.py/.ts/.js/.json，前端 accept 屬性允許的非圖片
    格式）沒有對應的 CLI flag，直接讀內容折進 prompt、透過 stdin 送出——
    Codex 本來就是純文字輸入，不需要額外機制。"""
    txt = tmp_path / "notes.txt"
    txt.write_text("這是附件內容", encoding="utf-8")

    created_proc = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        proc = _FakeProc([])
        created_proc["proc"] = proc
        return proc

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await codex_engine.run_turn(
        prompt="summarize this", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id=None, api_key="", on_text=lambda c: None,
        attachments=[str(txt)],
    )

    written = created_proc["proc"].stdin.written.decode("utf-8")
    assert "summarize this" in written
    assert "這是附件內容" in written
    assert "notes.txt" in written


async def test_codex_engine_resume_also_supports_image_flag(monkeypatch, tmp_path):
    """已驗證 codex exec resume --help 也列出 -i/--image，跟 fresh call 一樣
    支援，resume 分支不能漏掉這個 flag。"""
    img = tmp_path / "shot.png"
    img.write_bytes(b"fake")

    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await codex_engine.run_turn(
        prompt="continue", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id="sid-abc", api_key="", on_text=lambda c: None,
        attachments=[str(img)],
    )

    cmd = list(captured["args"])
    assert "-i" in cmd
    assert cmd[cmd.index("-i") + 1] == str(img)


async def test_codex_engine_nonexistent_attachment_silently_skipped(monkeypatch):
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await codex_engine.run_turn(
        prompt="hi", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id=None, api_key="", on_text=lambda c: None,
        attachments=["/does/not/exist.png"],
    )

    cmd = list(captured["args"])
    assert "-i" not in cmd


async def test_codex_engine_sets_codex_api_key_env_var(monkeypatch):
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return _FakeProc([])

    monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await codex_engine.run_turn(
        prompt="hi", cwd="/tmp", model="", permission_mode="workspace-write",
        resume_session_id=None, api_key="sk-test-123", on_text=lambda c: None,
    )

    assert captured["env"].get("CODEX_API_KEY") == "sk-test-123"
    assert "ANTHROPIC_API_KEY" not in captured["env"] or captured["env"].get("ANTHROPIC_API_KEY") != "sk-test-123"


# ── helpers._agent_dict() engine field ────────────────────────────────────────

class TestAgentDictEngineField:
    pytestmark = []

    def test_agent_dict_parses_engine_field(self, tmp_path):
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "backend"))
        import helpers

        agent_file = tmp_path / "codex-agent.md"
        agent_file.write_text(
            "---\nname: codex-agent\ndescription: uses codex\nengine: codex\n---\n\nbody\n",
            encoding="utf-8",
        )
        d = helpers._agent_dict(agent_file)
        assert d["engine"] == "codex"

    def test_agent_dict_defaults_engine_to_empty_string(self, tmp_path):
        import helpers

        agent_file = tmp_path / "plain-agent.md"
        agent_file.write_text(
            "---\nname: plain-agent\ndescription: no engine specified\n---\n\nbody\n",
            encoding="utf-8",
        )
        d = helpers._agent_dict(agent_file)
        assert d["engine"] == ""
