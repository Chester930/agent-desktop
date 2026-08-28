def test_config_key_command_rejects_shell_operators(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "_load_config",
        lambda: {"apiKeyCmd": "echo safe-key & whoami"},
    )

    assert main._resolve_api_key() == ""


def test_config_key_command_uses_argument_parsing(monkeypatch):
    import main

    monkeypatch.setattr(main, "_load_config", lambda: {"apiKeyCmd": "echo safe-key"})

    assert main._resolve_api_key() == "safe-key"


def test_codex_config_key_command_has_same_shell_guard(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "_load_config",
        lambda: {"codexApiKeyCmd": "echo safe-key | whoami"},
    )

    assert main._resolve_codex_api_key() == ""
