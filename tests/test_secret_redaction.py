"""T28: lineChannelSecret（驗證 LINE webhook 簽章的 HMAC secret）不該出現在
GET /api/config 或 GET /api/debug-dump 的回應裡 —— 外洩等於能偽造合法
webhook 請求繞過簽章驗證。apiKeyCmd 則刻意保留（settings 表單會讀回填入）。"""
import json as _json

import pytest

pytestmark = pytest.mark.asyncio


async def _set_config(tmp_claude_home, extra: dict):
    import main
    main.CONFIG_FILE = tmp_claude_home / "claude-desktop-config.json"
    cfg = {"projectDir": "", "claudeHome": str(tmp_claude_home), **extra}
    main.CONFIG_FILE.write_text(_json.dumps(cfg), encoding="utf-8")


class TestConfigGetRedactsLineSecret:
    async def test_line_channel_secret_not_in_config_response(self, client, tmp_claude_home):
        await _set_config(tmp_claude_home, {"lineChannelSecret": "super-secret-hmac-key"})
        resp = await client.get("/api/config")
        body = await resp.json()
        assert "lineChannelSecret" not in body
        assert "super-secret-hmac-key" not in _json.dumps(body)

    async def test_api_key_cmd_still_readable_for_settings_prefill(self, client, tmp_claude_home):
        """apiKeyCmd 前端會讀回填入設定表單，不能被誤刪。"""
        await _set_config(tmp_claude_home, {"apiKeyCmd": "echo my-key"})
        resp = await client.get("/api/config")
        body = await resp.json()
        assert body.get("apiKeyCmd") == "echo my-key"


class TestDebugDumpRedactsSecrets:
    async def test_line_channel_secret_not_in_debug_dump(self, client, tmp_claude_home):
        await _set_config(tmp_claude_home, {"lineChannelSecret": "super-secret-hmac-key"})
        resp = await client.get("/api/debug-dump")
        text = await resp.text()
        assert "super-secret-hmac-key" not in text
        body = _json.loads(text)
        assert "lineChannelSecret" not in body["config"]

    async def test_api_key_cmd_redacted_in_debug_dump(self, client, tmp_claude_home):
        """debug-dump 是給人下載附檔用的診斷輸出，apiKeyCmd 本身可能夾帶 token，
        跟設定頁面的讀回填入是不同情境，這裡應該要被濾掉。"""
        await _set_config(tmp_claude_home, {"apiKeyCmd": "echo my-key"})
        resp = await client.get("/api/debug-dump")
        text = await resp.text()
        body = _json.loads(text)
        assert "apiKeyCmd" not in body["config"]
