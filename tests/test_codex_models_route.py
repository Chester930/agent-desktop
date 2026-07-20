import pytest

import codex_models
import main


async def test_codex_models_route_returns_filtered_list(client, monkeypatch):
    async def fake_fetch(bin_path):
        return [{"slug": "gpt-5.4", "display_name": "GPT-5.4", "description": "Strong model."}]

    monkeypatch.setattr(codex_models, "fetch_codex_models", fake_fetch)
    main._codex_models_cache = {"data": None, "expires": 0.0}

    response = await client.get("/api/codex/models")

    assert response.status == 200
    body = await response.json()
    assert body == [{"slug": "gpt-5.4", "display_name": "GPT-5.4", "description": "Strong model."}]


async def test_codex_models_route_maps_client_error_to_502(client, monkeypatch):
    async def fake_fetch(bin_path):
        raise codex_models.CodexModelsError("Codex CLI is unavailable")

    monkeypatch.setattr(codex_models, "fetch_codex_models", fake_fetch)
    main._codex_models_cache = {"data": None, "expires": 0.0}

    response = await client.get("/api/codex/models")

    assert response.status == 502
    assert "unavailable" in (await response.json())["error"]


async def test_codex_models_route_uses_cache_within_ttl(client, monkeypatch):
    call_count = 0

    async def fake_fetch(bin_path):
        nonlocal call_count
        call_count += 1
        return [{"slug": "gpt-5.4", "display_name": "GPT-5.4", "description": ""}]

    monkeypatch.setattr(codex_models, "fetch_codex_models", fake_fetch)
    main._codex_models_cache = {"data": None, "expires": 0.0}

    await client.get("/api/codex/models")
    await client.get("/api/codex/models")

    assert call_count == 1
