"""Extension-owned environment settings for Headroom.

Headroom (https://github.com/headroomlabs-ai/headroom) is a local context
compression layer: an optional proxy on 127.0.0.1:8787 plus an MCP server
(``headroom mcp serve``) exposing ``headroom_compress`` /
``headroom_retrieve`` / ``headroom_stats``. This pack never imports
``headroom-ai`` — it talks to the proxy's HTTP stats endpoint and launches
the MCP server as a cold binary, so no heavy dependency lands in the packs
venv and compression keeps Headroom's own shared CCR store.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from sdk.config import settings as runtime_settings
from sdk.pack_settings import build_owned_settings_sources


class HeadroomSettings(BaseSettings):
    """Extension-owned environment settings for Headroom."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del settings_cls
        return build_owned_settings_sources(
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    headroom_proxy_url: str = "http://127.0.0.1:8787"
    """Base URL of the local Headroom proxy (``headroom proxy --port 8787``)."""

    headroom_mcp_bin: str = "headroom"
    """Path to the ``headroom`` CLI binary used by the cold MCP server.

    Defaults to a bare ``headroom`` resolved via PATH. Set
    ``HEADROOM_MCP_BIN`` to an absolute path when the MCP host's PATH does
    not include the tool env that installed ``headroom-ai``.
    """

    headroom_stats_file: str = "~/.headroom/session_stats.jsonl"
    """Fallback telemetry: the shared sub-agent session stats JSONL file."""

    headroom_expected: bool = True
    """Whether a running proxy is expected. When false, an unreachable
    proxy is reported as 'unconfigured' (silent) instead of 'error'."""

    headroom_savings_floor_pct: float = 5.0
    """Below this rolling savings percentage (with traffic) the pack flags
    a possible routing misconfiguration."""

    headroom_regression_ratio: float = 0.6
    """Current savings below this fraction of the trailing 7d average
    indicates a possible compression regression."""

    @property
    def external_signal_timeout_seconds(self) -> float:
        return runtime_settings.external_signal_timeout_seconds

    @external_signal_timeout_seconds.setter
    def external_signal_timeout_seconds(self, value: float) -> None:
        runtime_settings.external_signal_timeout_seconds = value


settings = HeadroomSettings()


@dataclass(frozen=True)
class HeadroomConnectorSettings:
    """Typed settings used by the Headroom signal connector."""

    proxy_url: str
    mcp_bin: str
    stats_file: str
    expected: bool
    savings_floor_pct: float
    regression_ratio: float
    timeout_seconds: float


def get_headroom_connector_settings() -> HeadroomConnectorSettings:
    """Return the Headroom connector settings from the shared runtime config."""

    return HeadroomConnectorSettings(
        proxy_url=settings.headroom_proxy_url.strip().rstrip("/"),
        mcp_bin=settings.headroom_mcp_bin.strip(),
        stats_file=settings.headroom_stats_file.strip(),
        expected=settings.headroom_expected,
        savings_floor_pct=settings.headroom_savings_floor_pct,
        regression_ratio=settings.headroom_regression_ratio,
        timeout_seconds=settings.external_signal_timeout_seconds,
    )


def resolve_mcp_command() -> str:
    """Resolve the ``headroom`` CLI to an absolute path when it is on PATH.

    ``mcp_servers.yaml`` expands ``${HEADROOM_MCP_BIN}`` at load time from
    the raw environment (unset -> empty string), so the cold MCP server
    cannot rely on the entry-point-owned settings for its launch command.
    Exposing the resolved absolute path lets a host bootstrap export it
    before manifest loading; ``shutil.which`` also disambiguates bare
    ``headroom`` for diagnostics (``missing_configuration``).
    """

    raw = settings.headroom_mcp_bin.strip()
    if not raw:
        return ""
    if "/" in raw or "\\" in raw:
        return raw
    return shutil.which(raw) or ""


def expand_stats_file_path(stats_file: str) -> str:
    """Expand ``~`` and env vars in the stats file path."""

    from pathlib import Path
    import os

    expanded = os.path.expandvars(stats_file)
    return str(Path(expanded).expanduser().resolve())


@lru_cache(maxsize=1)
def _cached_mcp_command() -> str:
    return resolve_mcp_command()