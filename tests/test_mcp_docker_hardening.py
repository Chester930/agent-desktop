"""T2: 收斂 docker.sock 提權面 — CORS 不再對任意 origin 開 credentials，
且 /api/mcp-local-config/{name}、/api/mcp/{name}/{action} 對 containerName/
composeService/composeFile 做輸入驗證，避免透過這兩個端點把攻擊者控制的
compose file 餵給有 docker.sock 存取權的 subprocess。"""
import json

import pytest

import main

pytestmark = pytest.mark.asyncio


class TestIsSafeDockerIdent:
    """純函式測試，不需要 HTTP server"""
    pytestmark = []

    def test_empty_is_allowed(self):
        assert main._is_safe_docker_ident("") is True

    def test_simple_name_allowed(self):
        assert main._is_safe_docker_ident("my-mcp_container.1") is True

    def test_leading_dash_rejected(self):
        # docker CLI 會把開頭 `-` 的字串誤判成旗標
        assert main._is_safe_docker_ident("--rm") is False

    def test_path_separator_rejected(self):
        assert main._is_safe_docker_ident("../../etc/passwd") is False
        assert main._is_safe_docker_ident("a/b") is False
        assert main._is_safe_docker_ident("a\\b") is False

    def test_space_rejected(self):
        assert main._is_safe_docker_ident("name with space") is False


class TestAllowedCorsOrigins:
    pytestmark = []

    def test_no_wildcard_origin(self):
        origins = main._allowed_cors_origins()
        assert "*" not in origins

    def test_includes_expected_dev_origins(self):
        origins = main._allowed_cors_origins()
        assert "http://localhost:4200" in origins
        assert "null" in origins  # 封裝 Electron file:// origin


class TestCorsEnforcement:
    """驗證 CORS 中介層真的擋掉不在白名單內的來源（不只是檢查 helper 函式的回傳值）。"""

    async def test_disallowed_origin_preflight_rejected(self, client):
        resp = await client.options(
            "/api/mcp-local-config/foo",
            headers={
                "Origin": "https://evil.com",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert resp.status == 403
        assert resp.headers.get("Access-Control-Allow-Origin") is None

    async def test_allowed_origin_preflight_succeeds(self, client):
        resp = await client.options(
            "/api/mcp-local-config/foo",
            headers={
                "Origin": "http://localhost:4200",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert resp.status == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:4200"


class TestMcpLocalConfigValidation:
    async def test_rejects_unsafe_container_name(self, client):
        resp = await client.put(
            "/api/mcp-local-config/test-mcp",
            json={"containerName": "--rm", "composeFile": "", "composeService": ""},
        )
        assert resp.status == 400

    async def test_rejects_unsafe_compose_service(self, client):
        resp = await client.put(
            "/api/mcp-local-config/test-mcp",
            json={"containerName": "", "composeFile": "", "composeService": "../escape"},
        )
        assert resp.status == 400

    async def test_rejects_nonexistent_compose_file(self, client):
        resp = await client.put(
            "/api/mcp-local-config/test-mcp",
            json={"containerName": "", "composeFile": "/definitely/not/a/real/path.yml", "composeService": ""},
        )
        assert resp.status == 400

    async def test_accepts_valid_config(self, client, tmp_path):
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services: {}\n", encoding="utf-8")

        resp = await client.put(
            "/api/mcp-local-config/test-mcp",
            json={
                "containerName": "my-mcp-container",
                "composeFile": str(compose_file),
                "composeService": "app",
            },
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True

        get_resp = await client.get("/api/mcp-local-config")
        cfg = await get_resp.json()
        assert cfg["test-mcp"]["containerName"] == "my-mcp-container"


class TestMcpActionDefensiveValidation:
    async def test_action_rejects_legacy_unsafe_container_name(self, client):
        """模擬舊版（無驗證）寫入的 config 檔仍殘留不安全值時，action 端點也要擋下。"""
        cfg = main._load_local_mcp_cfg()
        cfg["legacy-mcp"] = {"containerName": "--rm", "composeFile": "", "composeService": ""}
        main._save_local_mcp_cfg(cfg)

        resp = await client.post("/api/mcp/legacy-mcp/stop")
        assert resp.status == 400

    async def test_action_rejects_dangling_compose_file(self, client):
        cfg = main._load_local_mcp_cfg()
        cfg["legacy-mcp-2"] = {"containerName": "", "composeFile": "/no/such/compose.yml", "composeService": ""}
        main._save_local_mcp_cfg(cfg)

        resp = await client.post("/api/mcp/legacy-mcp-2/stop")
        assert resp.status == 400
