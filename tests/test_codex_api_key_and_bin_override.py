"""2026-07-13 續篇五：Settings 補齊 Codex 對等設定（執行路徑、API Key）。

涵蓋三件事：
1. `codex_engine._codex_bin(bin_override)`——additive 參數，有覆寫值時優先
   採用，沒有時 fallback 回原本讀 `main.CODEX_BIN` 的行為。
2. `main._resolve_codex_api_key()`——跟既有 `_resolve_api_key()` 對稱，只是
   讀 `codexApiKeyCmd` 這個獨立的 config key，兩者完全不共用邏輯。
3. `codexApiKeyCmd` 的 `GET`/`PUT /api/config` round trip（比照既有
   `test_config_put_engineMode_round_trip` 的手法）。

三個既有 api_key 判斷點（main.py::_resolve_agent_engine_and_key、
routes/teams.py::_agent_run_capture、routes/agents.py::_run_hr_agent）的
「Codex 引擎現在真的收到 _resolve_codex_api_key() 的值」則各自新增在對應的
既有測試檔（test_team_run_mixed_engine.py／test_hr_dispatch_execution_mode.py）
旁邊，反向驗證既有的 leak-prevention 測試（那些測試只驗證了「Anthropic key
不會誤植進 Codex」，沒驗證「Codex 自己的 key 真的有被正確傳遞」，這裡補上）。
"""
import json as _json

import pytest

from engines import codex_engine

pytestmark = pytest.mark.asyncio


class TestCodexBinOverride:
    def test_override_takes_precedence(self):
        assert codex_engine._codex_bin("my-custom-codex") == "my-custom-codex"

    def test_empty_override_falls_back_to_main_codex_bin(self, monkeypatch):
        import sys
        fake_main = type(sys)("fake_main_for_codex_bin_test")
        fake_main.CODEX_BIN = "codex-from-main"
        monkeypatch.setitem(sys.modules, "main", fake_main)
        assert codex_engine._codex_bin("") == "codex-from-main"

    def test_no_override_no_main_module_falls_back_to_literal_codex(self, monkeypatch):
        import sys
        for mod_name in ("main", "backend.main", "__main__"):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)
        assert codex_engine._codex_bin() == "codex"


async def _set_config(tmp_claude_home, extra: dict):
    import main
    main.CONFIG_FILE = tmp_claude_home / "claude-desktop-config.json"
    cfg = {"projectDir": "", "claudeHome": str(tmp_claude_home), **extra}
    main.CONFIG_FILE.write_text(_json.dumps(cfg), encoding="utf-8")


class TestResolveCodexApiKey:
    async def test_empty_cmd_returns_empty_string(self, tmp_claude_home):
        await _set_config(tmp_claude_home, {"codexApiKeyCmd": ""})
        import main
        assert main._resolve_codex_api_key() == ""

    async def test_runs_configured_command_and_trims_output(self, tmp_claude_home):
        await _set_config(tmp_claude_home, {"codexApiKeyCmd": "echo my-codex-key"})
        import main
        assert main._resolve_codex_api_key() == "my-codex-key"

    async def test_does_not_read_apiKeyCmd(self, tmp_claude_home):
        """兩個 resolver 完全分開讀各自的 config key，不會互相 fallback。"""
        await _set_config(tmp_claude_home, {"apiKeyCmd": "echo anthropic-key", "codexApiKeyCmd": ""})
        import main
        assert main._resolve_codex_api_key() == ""
        assert main._resolve_api_key() == "anthropic-key"


class TestCodexApiKeyCmdConfigRoundTrip:
    async def test_config_get_defaults_codexApiKeyCmd_to_empty(self, client, tmp_path):
        import main
        main.CONFIG_FILE = tmp_path / "claude-desktop-config.json"
        resp = await client.get("/api/config")
        body = await resp.json()
        assert body["codexApiKeyCmd"] == ""

    async def test_config_put_codexApiKeyCmd_round_trip(self, client, tmp_path):
        import main
        main.CONFIG_FILE = tmp_path / "claude-desktop-config.json"
        put_resp = await client.put("/api/config", json={"codexApiKeyCmd": "op read op://vault/item/credential"})
        assert put_resp.status == 200

        get_resp = await client.get("/api/config")
        body = await get_resp.json()
        assert body["codexApiKeyCmd"] == "op read op://vault/item/credential"

    async def test_config_put_codexApiKeyCmd_does_not_affect_apiKeyCmd(self, client, tmp_path):
        import main
        main.CONFIG_FILE = tmp_path / "claude-desktop-config.json"
        await client.put("/api/config", json={"apiKeyCmd": "echo claude-cmd"})
        await client.put("/api/config", json={"codexApiKeyCmd": "echo codex-cmd"})

        get_resp = await client.get("/api/config")
        body = await get_resp.json()
        assert body["apiKeyCmd"] == "echo claude-cmd"
        assert body["codexApiKeyCmd"] == "echo codex-cmd"
