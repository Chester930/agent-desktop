import json

import pytest

import codex_models


class _FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    def kill(self):
        pass

    async def communicate(self):
        return self._stdout, self._stderr


def _catalog(*models):
    return json.dumps({"models": list(models)}).encode("utf-8")


@pytest.mark.asyncio
async def test_fetch_codex_models_filters_to_list_visibility_only(monkeypatch):
    stdout = _catalog(
        {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "description": "Flagship.", "visibility": "list"},
        {"slug": "gpt-5.4-mini", "display_name": "GPT-5.4-Mini", "description": "Fast.", "visibility": "list"},
        {"slug": "codex-auto-review", "display_name": "Codex Auto Review", "visibility": "hide"},
    )
    proc = _FakeProcess(stdout)

    async def fake_spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(codex_models.asyncio, "create_subprocess_exec", fake_spawn)

    result = await codex_models.fetch_codex_models("codex", timeout=1)

    assert [m["slug"] for m in result] == ["gpt-5.6-sol", "gpt-5.4-mini"]
    assert result[0]["display_name"] == "GPT-5.6-Sol"
    assert result[0]["description"] == "Flagship."


@pytest.mark.asyncio
async def test_fetch_codex_models_falls_back_to_slug_when_display_name_missing(monkeypatch):
    stdout = _catalog({"slug": "gpt-5.4", "visibility": "list"})
    proc = _FakeProcess(stdout)

    async def fake_spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(codex_models.asyncio, "create_subprocess_exec", fake_spawn)

    result = await codex_models.fetch_codex_models("codex", timeout=1)

    assert result == [{"slug": "gpt-5.4", "display_name": "gpt-5.4", "description": ""}]


@pytest.mark.asyncio
async def test_fetch_codex_models_raises_on_nonzero_exit(monkeypatch):
    proc = _FakeProcess(b"", stderr=b"error: unrecognized subcommand 'debug'", returncode=1)

    async def fake_spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(codex_models.asyncio, "create_subprocess_exec", fake_spawn)

    with pytest.raises(codex_models.CodexModelsError, match="unrecognized subcommand"):
        await codex_models.fetch_codex_models("codex", timeout=1)


@pytest.mark.asyncio
async def test_fetch_codex_models_raises_on_invalid_json(monkeypatch):
    proc = _FakeProcess(b"not json")

    async def fake_spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(codex_models.asyncio, "create_subprocess_exec", fake_spawn)

    with pytest.raises(codex_models.CodexModelsError, match="invalid JSON"):
        await codex_models.fetch_codex_models("codex", timeout=1)


@pytest.mark.asyncio
async def test_fetch_codex_models_reports_missing_binary(monkeypatch):
    async def fake_spawn(*args, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(codex_models.asyncio, "create_subprocess_exec", fake_spawn)

    with pytest.raises(codex_models.CodexModelsError, match="unavailable"):
        await codex_models.fetch_codex_models("codex", timeout=1)
