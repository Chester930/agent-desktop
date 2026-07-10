"""
probe_codex_cli.py — 在真的有安裝 `codex` CLI 的機器上，驗證
engines/codex_engine.py 的解析邏輯有沒有跟真實輸出對上。

背景：engines/codex_engine.py 是根據 OpenAI 官方文件寫的第一版（這個開發
環境沒有安裝 codex，沒辦法像驗證 Claude CLI 的 `acceptEdits` 那樣直接用
真實 CLI 測）。這支腳本比照那次驗證用的手法（permtest/accepttest：直接
呼叫 CLI、印出原始輸出、人工比對）——不是自動化測試，是給人跑一次、把
結果回報回來讓我校正用的診斷工具。

用法：
    cd backend
    python scripts/probe_codex_cli.py                       # 用預設 prompt/sandbox
    python scripts/probe_codex_cli.py --prompt "..." --sandbox workspace-write
    python scripts/probe_codex_cli.py --test-resume          # 額外測 resume 子指令

會做的事：
1. 在一個乾淨的暫存目錄裡，用 codex_engine.run_turn() 實際跑一輪（會真的
   呼叫 codex CLI、花真的額度），同時把 codex 吐出的每一行原始 JSON 印出來
   （不透過 codex_engine.py 的解析邏輯，是 raw stdout）。
2. 再印出 codex_engine.run_turn() 實際解析出來的結果（output/session_id/error）。
3. 比對：有沒有出現 codex_engine.py 完全沒處理過的事件型別（`thread.started`／
   `turn.started`／`turn.completed`／`turn.failed`／`item.started`／
   `item.completed` 以外的 `type`）——有的話會印出來，代表官方文件沒提到、
   或這個版本的 CLI 行為跟文件不一致，需要回報。
4. （加 --test-resume 才會做）用第一輪拿到的 session id 呼叫
   `codex exec resume <id> "..."`，確認 resume 子指令真的能接續對話、
   還有 --json/--sandbox/--model/--cd 這些 flag 放在 resume 子指令底下
   到底有沒有效果（目前 codex_engine.py 是「假設有效果」，這是文件沒寫清楚
   的部分，見 codex_engine.py 檔頭註解）。

把這支腳本的完整輸出（尤其是「未知事件型別」那段，如果有的話）貼給我，
我會照著校正 codex_engine.py。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines import codex_engine  # noqa: E402
from helpers import wrap_cmd  # noqa: E402

KNOWN_EVENT_TYPES = {
    "thread.started", "turn.started", "turn.completed", "turn.failed",
    "item.started", "item.completed",
}
# 已驗證看過的 item.completed / item.started 底下的 item.type（agent_message
# 是唯一被解析成文字輸出的；error 已驗證存在、非致命、會轉成 [codex: ...]
# 文字；其餘是文件提過但還沒實測看到的型別，先列進「已知」避免誤報）。
KNOWN_ITEM_TYPES = {
    "agent_message", "error", "command_execution", "reasoning",
    "file_change", "mcp_tool_call", "web_search", "plan_update",
}


async def _run_and_report(prompt: str, cwd: str, sandbox: str, model: str, resume_id: "str | None") -> "str | None":
    print(f"\n{'='*70}")
    print(f"呼叫：codex exec {'resume ' + resume_id if resume_id else ''} --json --sandbox {sandbox} --cd {cwd} {('--model ' + model) if model else ''}")
    print(f"prompt: {prompt!r}")
    print(f"{'='*70}\n")

    unknown_types: set[str] = set()
    raw_lines: list[str] = []

    async def on_text(chunk: str) -> None:
        pass  # collected via result.output instead; this is just for streaming display

    codex_bin = codex_engine._codex_bin()
    cmd = [codex_bin, "exec"]
    if resume_id:
        cmd += ["resume", resume_id, prompt]
    else:
        cmd += [prompt]
    cmd += ["--json", "--sandbox", sandbox, "--cd", cwd, "--skip-git-repo-check"]
    if model:
        cmd += ["--model", model]

    print(f"實際指令：{' '.join(cmd)}\n")
    print("--- 原始輸出（逐行）---")

    # Windows 上 npm 全域安裝的 CLI 通常是 .cmd shim（`where codex` 會看到
    # codex.cmd），asyncio.create_subprocess_exec 不像 shell 那樣會自動解析
    # PATHEXT／幫忙包一層 cmd.exe，直接執行 .cmd 會是 FileNotFoundError
    # （WinError 2）。這正是這個 codebase 既有的 wrap_cmd()（helpers.py）
    # 存在的原因，codex_engine.py 內部呼叫也用了同一個 helper；這裡的原始
    # 輸出展示段落一樣要套用，才能在 Windows 上真的執行 codex 驗證。
    wrapped_cmd = wrap_cmd(cmd[0], cmd[1:])
    proc = await asyncio.create_subprocess_exec(
        *wrapped_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=cwd,
    )
    async for line in proc.stdout:
        raw = line.decode("utf-8", errors="replace").rstrip()
        if not raw:
            continue
        raw_lines.append(raw)
        print(raw)
        try:
            ev = json.loads(raw)
            ev_type = ev.get("type")
            if ev_type and ev_type not in KNOWN_EVENT_TYPES:
                unknown_types.add(ev_type)
            if ev_type in ("item.completed", "item.started"):
                item_type = ev.get("item", {}).get("type")
                if item_type and item_type not in KNOWN_ITEM_TYPES:
                    unknown_types.add(f"item.type={item_type}")
        except json.JSONDecodeError:
            print(f"  ⚠ 這一行不是合法 JSON（codex_engine.py 的解析器會直接跳過它）")
    await proc.wait()

    print("--- 原始輸出結束 ---\n")

    if unknown_types:
        print(f"⚠️  出現 codex_engine.py 完全沒處理過的事件型別：{sorted(unknown_types)}")
        print("   這些事件目前會被靜默忽略，可能漏掉重要資訊，需要回報校正。\n")
    else:
        print("✓ 沒有出現未知事件型別，跟 codex_engine.py 目前處理的集合一致。\n")

    # 再用 codex_engine.run_turn() 實際跑一次（正式的解析路徑），確認跟上面
    # 手動解析的結果一致
    print("--- codex_engine.run_turn() 的解析結果 ---")
    result = await codex_engine.run_turn(
        prompt=prompt, cwd=cwd, model=model, permission_mode=sandbox,
        resume_session_id=resume_id, api_key="", on_text=on_text,
    )
    print(f"output      = {result.output!r}")
    print(f"session_id  = {result.session_id!r}")
    print(f"error       = {result.error!r}")
    print("--- 結束 ---\n")

    return result.session_id or None


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default="請直接回覆「codex probe ok」這幾個字，不要做任何其他事。")
    parser.add_argument("--sandbox", default="workspace-write", choices=["read-only", "workspace-write", "danger-full-access"])
    parser.add_argument("--model", default="")
    parser.add_argument("--test-resume", action="store_true", help="額外測試 codex exec resume 子指令")
    args = parser.parse_args()

    tmp_dir = tempfile.mkdtemp(prefix="codex_probe_")
    print(f"暫存工作目錄：{tmp_dir}（結束後會自動刪除）")

    try:
        session_id = await _run_and_report(args.prompt, tmp_dir, args.sandbox, args.model, None)

        if args.test_resume:
            if not session_id:
                print("⚠️  第一輪沒有拿到 session_id（thread_id），無法測試 resume，略過。")
            else:
                await _run_and_report(
                    "請回覆「resume 也 ok」這幾個字。",
                    tmp_dir, args.sandbox, args.model, session_id,
                )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\n完成。把上面的完整輸出貼給 Claude，尤其是「未知事件型別」那幾行（如果有的話）。")


if __name__ == "__main__":
    asyncio.run(main())
