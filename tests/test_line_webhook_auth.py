"""T31: LINE webhook 簽章驗證原本在 lineChannelSecret 未設定時直接放行
（fail-open）——設定到一半的期間，任何人都能偽造 webhook payload 觸發
_line_run_claude（實際執行 Claude CLI）。改成沒設定 secret 就一律拒絕。"""
import base64
import hashlib
import hmac
import json

import pytest

import main

pytestmark = pytest.mark.asyncio


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


class TestVerifyLineSignatureFailsClosed:
    pytestmark = []

    def test_unconfigured_secret_rejects(self, tmp_claude_home):
        main.CONFIG_FILE = tmp_claude_home / "claude-desktop-config.json"
        main.CONFIG_FILE.write_text(json.dumps({"projectDir": ""}), encoding="utf-8")
        assert main._verify_line_signature(b"anything", "any-sig") is False

    def test_configured_secret_correct_signature_accepts(self, tmp_claude_home):
        main.CONFIG_FILE = tmp_claude_home / "claude-desktop-config.json"
        main.CONFIG_FILE.write_text(json.dumps({"lineChannelSecret": "s3cret"}), encoding="utf-8")
        body = b'{"events":[]}'
        assert main._verify_line_signature(body, _sign("s3cret", body)) is True

    def test_configured_secret_wrong_signature_rejects(self, tmp_claude_home):
        main.CONFIG_FILE = tmp_claude_home / "claude-desktop-config.json"
        main.CONFIG_FILE.write_text(json.dumps({"lineChannelSecret": "s3cret"}), encoding="utf-8")
        body = b'{"events":[]}'
        assert main._verify_line_signature(body, "forged-signature") is False


class TestLineWebhookEndpointFailsClosed:
    async def test_webhook_rejected_when_secret_unconfigured(self, client, tmp_claude_home):
        main.CONFIG_FILE = tmp_claude_home / "claude-desktop-config.json"
        main.CONFIG_FILE.write_text(json.dumps({"projectDir": ""}), encoding="utf-8")

        resp = await client.post(
            "/api/line/webhook",
            data=json.dumps({"events": [{
                "type": "message",
                "message": {"type": "text", "text": "forged prompt injection attempt"},
                "replyToken": "tok",
                "source": {"userId": "attacker"},
            }]}),
            headers={"X-Line-Signature": "whatever"},
        )
        assert resp.status == 400

    async def test_webhook_accepted_with_valid_signature(self, client, tmp_claude_home):
        main.CONFIG_FILE = tmp_claude_home / "claude-desktop-config.json"
        main.CONFIG_FILE.write_text(json.dumps({"lineChannelSecret": "s3cret"}), encoding="utf-8")

        body = json.dumps({"events": []}).encode()
        resp = await client.post(
            "/api/line/webhook",
            data=body,
            headers={"X-Line-Signature": _sign("s3cret", body)},
        )
        assert resp.status == 200
