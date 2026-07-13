"""
engines/codex_engine.py — OpenAI Codex CLI 的 AgentEngine 實作。

2026-07-10／07-11 更新：已用真實 `codex` CLI（0.144.1，真實登入帳號）反覆
驗證過（`scripts/probe_codex_cli.py`、真實 Team Run 端對端測試、混用引擎
測試、錯誤路徑測試、sandbox 等級測試），下面「已驗證」段落是實測結果；
完整過程見 `docs/HANDOFF.md` 十二節。

已驗證（真實 CLI 輸出，非猜測）：
- `codex exec` 預設**要求在 git repo 裡執行**，不然會印
  `Not inside a trusted directory and --skip-git-repo-check was not
  specified.` 然後整個 turn 直接沒有任何 `item.completed` 事件、安靜結束
  （`output`/`session_id` 都是空字串，沒有丟例外，非常容易被誤判成「執行
  成功但沒有任何反應」）。Team Run 的 `cwd` 是使用者自己設定的工作目錄，
  不保證一定是 git repo，所以這裡**無條件**加 `--skip-git-repo-check`。
- 事件格式跟文件一致：`thread.started`（`thread_id`，UUID）、
  `turn.started`、`item.completed`（`item.type == "agent_message"` 時
  `item.text` 是文字內容）、`turn.completed`（含 `usage`）。
- **新發現、文件沒提到的 item type**：`item.type == "error"`——例如這次
  實測看到 `{"type":"item.completed","item":{"type":"error","message":
  "Exceeded skills context budget of 2%. ..."}}`。這種事件不代表整個
  turn 失敗（後面照樣接著 `item.completed`/`agent_message` 產出真正的
  回覆、`turn.completed` 正常結束），所以不當作 `RunResult.error`，改成
  跟一般文字一樣透過 `on_text` 送出（用 `[codex: ...]` 包起來），避免這類
  非致命訊息被靜默吃掉。
- Windows 上 `codex` 是 npm `.cmd` shim（`where codex` 會看到
  `codex.cmd`），沒有套用既有的 `wrap_cmd()`（helpers.py）的話
  `asyncio.create_subprocess_exec` 會直接 `FileNotFoundError`（這裡本來
  就有呼叫 `wrap_cmd`，這點原本就是對的，不是這次修的）。
- **Prompt 一律透過 stdin 傳（`stdin=PIPE`，CLI 引數位置填 `"-"`）**，不是
  當 CLI 引數傳——Windows 上 `wrap_cmd()` 會包一層 `cmd /c`，`cmd.exe` 對
  「引數裡包含換行字元」的處理是壞的，多行 prompt（真實 Team Run 一定是
  多行）當引數傳會被截斷/損壞，甚至讓 Codex 整個退回互動式人類可讀輸出、
  不是 `--json` 要求的 JSONL。改用官方文件記載的 stdin 方式後完全修復，
  已用真實端對端 Team Run 測試驗證過。
- `codex exec resume <SESSION_ID> "-"` 已驗證是**子指令**（不是 flag），
  且**不接受** `--sandbox`/`--cd`（`codex exec resume --help` 證實，塞了
  會直接整個失敗）；`--json`/`--skip-git-repo-check`/`--model` 這些則可以
  照樣加，程式碼已依此分開組 flag 集合。
- **Sandbox 等級**：`workspace-write` 已驗證可用（Write 正常，但 Windows
  上 Bash/shell 指令會失敗，`CreateProcessAsUserW failed: 5 (存取被拒)`
  ——這是 Codex CLI 本身在 Windows 上的已知限制，不是這個 app 的問題）。
  **`danger-full-access` 已驗證可用**：同樣情境下改用這個等級，Bash/shell
  指令可以正常執行（實測請 Codex 用 shell 指令寫檔案、再讀回內容，成功），
  代表 Windows 上如果需要 Codex 執行 Bash 指令，`danger-full-access` 是
  目前唯一能繞過該限制的做法（使用上要謹慎，這個等級完全沒有沙盒限制）。
- 混用引擎（同一個 team 裡部分成員 `engine: claude`、部分 `engine: codex`）
  已用真實帳號驗證過各自正確路由到對應 CLI。
- 錯誤路徑（`turn.failed`，例如不存在的 model 名稱）已驗證：`RunResult.error`
  正確帶出完整 API 錯誤內容，失敗前的非致命 `item.type=="error"` 警告跟
  `session_id` 都正確保留，不需要額外處理。

- **附件支援**：一開始誤判成「Codex 沒有附件參數」，經使用者提醒後查證
  `codex exec --help`／`codex exec resume --help` 才發現兩者都有
  `-i, --image <FILE>...`，原生支援圖片附件。前端的附件選擇器只允許
  `image/*` 跟幾種純文字格式（`.txt/.md/.py/.ts/.js/.json`，見
  `frontend/src/app/app.html`），所以圖片走 `-i` flag、文字類直接讀內容
  折進 prompt（不需要額外機制，Codex 本來就是純文字輸入）就能達到跟
  Claude（`--input-file`）對等的附件支援，不需要犧牲任何一邊。

尚未驗證（文件記載，還沒實測）：
- 認證：`CODEX_API_KEY=<key>` 環境變數，只在 `codex exec` 模式生效——這次
  實測用的是已經 `codex login` 過的憑證（`~/.codex/auth.json`），沒有走
  這個 env var 路徑，這部分還沒驗證過。

參考文件（2026-07 查證）：
- https://developers.openai.com/codex/noninteractive （非互動模式 exec）
- https://developers.openai.com/codex/cli/reference （全域 flags）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from helpers import safe_kill_process, wrap_cmd

from .base import RunResult

name = "codex"

DEFAULT_PERMISSION_MODE = "workspace-write"
VALID_PERMISSION_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})


def _codex_bin(bin_override: str = "") -> str:
    if bin_override:
        return bin_override
    for mod_name in ("main", "backend.main", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "CODEX_BIN"):
            return getattr(mod, "CODEX_BIN", "codex")
    return "codex"


def _normalize_sandbox_mode(permission_mode: str) -> str:
    # permission_mode 有可能是呼叫端沿用 Claude 那邊的字彙（例如
    # "acceptEdits"）——這種情況不強行對應，直接退回 Codex 自己的預設值，
    # 避免把不合法的 --sandbox 值餵給 CLI。同一個 team 混用 Claude/Codex
    # 成員時會發生這種情況，所以這裡要能安全 fallback，不能直接丟例外。
    if permission_mode in VALID_PERMISSION_MODES:
        return permission_mode
    return DEFAULT_PERMISSION_MODE


# 前端附件選擇器（frontend/src/app/app.html 的 <input accept=...>）只允許
# image/* 跟這幾種純文字格式，所以這裡窮舉比對副檔名就夠了，不需要真的去嗅探
# MIME type。
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})
_TEXT_EXTS = frozenset({".txt", ".md", ".py", ".ts", ".js", ".json"})


def _split_attachments(attachments: "list[str] | None") -> "tuple[list[str], list[str]]":
    """把附件路徑分成「圖片」（走 -i flag）跟「純文字」（讀內容折進 prompt）
    兩類。已驗證 codex exec 跟 codex exec resume 都原生支援 -i/--image，
    不需要在「附件」和「Codex」之間二選一。"""
    images: list[str] = []
    texts: list[str] = []
    for att in attachments or []:
        if not att or not Path(att).exists():
            continue
        ext = Path(att).suffix.lower()
        if ext in _IMAGE_EXTS:
            images.append(att)
        elif ext in _TEXT_EXTS:
            texts.append(att)
        # 其他副檔名：前端 accept 屬性本來就不允許選到，防禦性地靜默略過。
    return images, texts


def _inject_text_attachments(prompt: str, text_attachments: "list[str]") -> str:
    if not text_attachments:
        return prompt
    parts = [prompt]
    for att in text_attachments:
        try:
            content = Path(att).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        parts.append(f"\n\n[附件：{Path(att).name}]\n```\n{content}\n```\n")
    return "".join(parts)


async def run_turn(
    *,
    prompt: str,
    cwd: str,
    model: str,
    permission_mode: str,
    resume_session_id: "str | None",
    api_key: str,
    on_text,
    on_process=None,
    is_cancelled=None,
    attachments: "list[str] | None" = None,
    bin_override: str = "",   # 新增，預設空字串＝沿用原本 _codex_bin() 行為，
                              # Team Run／HR 派發的既有呼叫端不用改一行。
) -> RunResult:
    codex_bin = _codex_bin(bin_override)
    safe_cwd = cwd if (cwd and Path(cwd).is_dir()) else str(Path.home())
    sandbox_mode = _normalize_sandbox_mode(permission_mode)
    image_attachments, text_attachments = _split_attachments(attachments)
    prompt = _inject_text_attachments(prompt, text_attachments)

    # 已驗證（真實 CLI）：team run 的 prompt 是多行字串（agent frontmatter
    # body、memory context、任務描述用 "\n\n" 接起來）。Windows 上 codex 是
    # npm .cmd shim（`where codex` 看到 codex.cmd），wrap_cmd() 會包一層
    # `cmd /c` 執行——但 cmd.exe 對「一個引數裡包含換行字元」的處理是壞的，
    # 多行 prompt 傳進去會被截斷/錯誤斷行（實測看到模型只收到
    # "[Memory Context]" 這一行就結束，完全沒看到真正的任務內容，回覆
    # 「你想要我在這個 repo 做什麼？」）。而且這個情境下 codex 甚至會整個
    # 退回互動式的人類可讀輸出格式（不是 --json 要求的 JSONL），因為它把
    # 傳壞的引數解析成別的東西。
    #
    # 修法：不把 prompt 當 CLI 引數傳，改用官方文件記載的方式——引數位置
    # 填 "-"，實際 prompt 內容透過 stdin 送進去（「若引數是 "-"，從 stdin
    # 讀取指示」）。這樣完全不經過 cmd.exe 的命令列 tokenize，多行/特殊字元
    # 都不是問題。單行 prompt（例如這次驗證用的簡短測試句）不會踩到這個
    # bug，但 stdin 是對所有情況都安全的做法，統一都走這條路。
    if resume_session_id:
        cmd = [codex_bin, "exec", "resume", resume_session_id, "-", "--json", "--skip-git-repo-check"]
        if model:
            cmd += ["--model", model]
    else:
        # 已驗證：codex exec 預設要求在 git repo 裡執行，不然整個 turn 會
        # 安靜結束、不產生任何 item.completed 事件（output/session_id 都是
        # 空字串，不會丟例外，很容易誤判成「執行成功但沒反應」）。Team Run
        # 的 cwd 不保證是 git repo，所以無條件加這個 flag。
        cmd = [codex_bin, "exec", "-", "--json", "--sandbox", sandbox_mode, "--cd", safe_cwd, "--skip-git-repo-check"]
        if model:
            cmd += ["--model", model]

    # 已驗證（codex exec --help／codex exec resume --help）：兩個子指令都原生
    # 支援 -i/--image，可重複使用，圖片路徑直接原樣傳。
    for img in image_attachments:
        cmd += ["-i", img]

    env = {**os.environ}
    if api_key:
        env["CODEX_API_KEY"] = api_key

    output_parts: list[str] = []
    session_id = ""
    non_json_lines: list[str] = []
    proc = None
    try:
        cmd = wrap_cmd(cmd[0], cmd[1:])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE,
            cwd=safe_cwd,
            env=env,
        )
        if on_process:
            on_process(proc)

        if proc.stdin is not None:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

        async for line in proc.stdout:
            if is_cancelled and is_cancelled():
                safe_kill_process(proc)
                break

            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
                ev_type = ev.get("type")
                if ev_type == "thread.started" and ev.get("thread_id"):
                    session_id = ev["thread_id"]
                elif ev_type == "item.completed":
                    item = ev.get("item", {})
                    item_type = item.get("type")
                    if item_type == "agent_message":
                        chunk = item.get("text", "")
                        if chunk:
                            output_parts.append(chunk)
                            await on_text(chunk)
                    elif item_type == "error":
                        # 已驗證：文件沒提到的 item type，實測看過（例如
                        # skills context budget 超過）——不代表整個 turn
                        # 失敗（後面照樣會有正常的 agent_message／
                        # turn.completed），當成非致命訊息夾進輸出，不要
                        # 靜默吃掉。
                        msg = item.get("message", "")
                        if msg:
                            chunk = f"\n[codex: {msg}]\n"
                            output_parts.append(chunk)
                            await on_text(chunk)
                elif ev_type == "turn.failed":
                    err_text = json.dumps(ev.get("error", ev), ensure_ascii=False)
                    return RunResult(
                        output="".join(output_parts), session_id=session_id,
                        error=f"turn.failed: {err_text}",
                    )
            except json.JSONDecodeError:
                if len(non_json_lines) < 20:
                    non_json_lines.append(raw)
        await proc.wait()
    except Exception as e:
        return RunResult(output="".join(output_parts), session_id=session_id, error=str(e))

    # 已驗證：CLI 層級的失敗（例如 resume 子指令收到不支援的 flag）不會用
    # JSON 事件回報，只會印純文字錯誤訊息到 stdout/stderr 然後以非零結束碼
    # 結束——原本的解析器對這種情況完全沒反應，回傳一個看起來「成功但空白」
    # 的 RunResult（output=""、session_id=""、error=None），呼叫端沒辦法
    # 分辨「這一步真的什麼都沒做」還是「CLI 呼叫失敗了」。改成：process 以
    # 非零結束碼結束、而且完全沒有解析到任何 JSON 事件時，視為失敗。
    if proc is not None and proc.returncode not in (0, None) and not output_parts and not session_id:
        detail = " | ".join(non_json_lines) if non_json_lines else f"exit code {proc.returncode}"
        return RunResult(output="", session_id="", error=f"codex exec failed: {detail}")

    return RunResult(output="".join(output_parts), session_id=session_id)
