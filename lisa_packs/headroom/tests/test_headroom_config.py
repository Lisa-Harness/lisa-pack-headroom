"""Tests for Headroom pack configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from lisa_packs.headroom.config import (
    HeadroomSettings,
    expand_stats_file_path,
    get_headroom_connector_settings,
    resolve_mcp_command,
)

_DEFAULT_SAVINGS_FLOOR_PCT = 5.0
_DEFAULT_REGRESSION_RATIO = 0.6
_OVERRIDDEN_SAVINGS_FLOOR_PCT = 10.0


def test_default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADROOM_PROXY_URL", raising=False)
    monkeypatch.delenv("HEADROOM_MCP_BIN", raising=False)
    monkeypatch.delenv("HEADROOM_STATS_FILE", raising=False)
    monkeypatch.delenv("HEADROOM_EXPECTED", raising=False)
    monkeypatch.delenv("HEADROOM_SAVINGS_FLOOR_PCT", raising=False)
    monkeypatch.delenv("HEADROOM_REGRESSION_RATIO", raising=False)

    settings = HeadroomSettings()
    assert settings.headroom_proxy_url == "http://127.0.0.1:8787"
    assert settings.headroom_mcp_bin == "headroom"
    assert settings.headroom_stats_file == "~/.headroom/session_stats.jsonl"
    assert settings.headroom_expected is True
    assert settings.headroom_savings_floor_pct == _DEFAULT_SAVINGS_FLOOR_PCT
    assert settings.headroom_regression_ratio == _DEFAULT_REGRESSION_RATIO


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_PROXY_URL", "http://10.0.0.5:9000/")
    monkeypatch.setenv("HEADROOM_MCP_BIN", "/opt/tools/headroom")
    monkeypatch.setenv("HEADROOM_EXPECTED", "false")
    monkeypatch.setenv("HEADROOM_SAVINGS_FLOOR_PCT", "10")

    settings = HeadroomSettings()
    assert settings.headroom_proxy_url == "http://10.0.0.5:9000/"
    assert settings.headroom_mcp_bin == "/opt/tools/headroom"
    assert settings.headroom_expected is False
    assert settings.headroom_savings_floor_pct == _OVERRIDDEN_SAVINGS_FLOOR_PCT


def test_connector_settings_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_PROXY_URL", "http://127.0.0.1:8787/")

    connector_settings = get_headroom_connector_settings()
    assert connector_settings.proxy_url == "http://127.0.0.1:8787"
    assert connector_settings.timeout_seconds > 0


def test_resolve_mcp_command_absolute_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_MCP_BIN", "/opt/tools/headroom")

    # resolve_mcp_command reads the module-level settings singleton, which
    # was instantiated at import time — construct a fresh model to prove
    # the env var reaches a cold process (the MCP subprocess launch path).
    from lisa_packs.headroom import config as config_module

    fresh = HeadroomSettings()
    config_module.settings = fresh
    try:
        resolved = resolve_mcp_command()
        assert resolved == "/opt/tools/headroom"
    finally:
        config_module.settings = fresh.__class__()


def test_resolve_mcp_command_missing_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEADROOM_MCP_BIN", "definitely-not-a-real-binary-xyz")
    # PATH scrubbed so which() cannot find anything
    monkeypatch.setenv("PATH", str(tmp_path))

    from lisa_packs.headroom import config as config_module

    fresh = HeadroomSettings()
    config_module.settings = fresh
    try:
        assert resolve_mcp_command() == ""
    finally:
        config_module.settings = fresh.__class__()


def test_expand_stats_file_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Path.expanduser() reads HOME on POSIX and USERPROFILE on Windows,
    # so the test pins whichever one this platform uses.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    expanded = expand_stats_file_path("~/.headroom/session_stats.jsonl")
    assert expanded == str((home / ".headroom" / "session_stats.jsonl").resolve())


def test_owned_settings_drop_unrelated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Foreign env keys must not leak into the owned settings model."""
    monkeypatch.setenv("HEADROOM_UNRELATED_KEY", "value")

    settings = HeadroomSettings()
    assert not hasattr(settings, "headroom_unrelated_key")
