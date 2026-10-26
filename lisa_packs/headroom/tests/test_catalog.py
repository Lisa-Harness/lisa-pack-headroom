"""Tests for the Headroom pack's catalog discovery.

Verifies that the content root is importable, the mode and operation are
discoverable, front matter carries the canonical schema keys, and the
operation allowlist matches the discovered mode.
"""

from __future__ import annotations

import json

from sdk.content._catalog import (
    build_mode_catalog_json,
    build_operation_catalog_json,
    resolve_mode_roots,
    resolve_operation_roots,
)

from lisa_packs.headroom import get_content_root


def test_headroom_pack_content_root_is_directory() -> None:
    """The pack's content root must be a real directory."""
    root = get_content_root()
    assert root.is_dir(), f"Content root {root} is not a directory"


def test_headroom_mode_discovered() -> None:
    """The headroom mode is discovered from the pack's modes/ directory."""
    roots = resolve_mode_roots([get_content_root()])
    assert roots, "No modes/ directory found in headroom pack"

    catalog = json.loads(build_mode_catalog_json(roots))
    assert "headroom" in catalog, f"headroom mode not in catalog: {list(catalog)}"

    mode = catalog["headroom"]
    assert mode["mode_kind"] == "chat"
    assert mode["mode_provider_id"] == "lisa-pack-headroom"
    assert mode["pre_turn_operation_ids"] == ["headroom_status"]
    assert mode["command_pattern"].startswith("^(?:/)?headroom")
    assert mode["routable"] is True
    assert "headroom.compress" in mode["allowed_tool_ids"]
    assert "headroom.retrieve" in mode["allowed_tool_ids"]
    assert "headroom.stats" in mode["allowed_tool_ids"]
    assert mode["tools_authoritative"] is True


def test_headroom_operation_discovered() -> None:
    """The headroom_status operation is discovered from the operations/ directory."""
    roots = resolve_operation_roots([get_content_root()])
    assert roots, "No operations/ directory found in headroom pack"

    catalog = json.loads(build_operation_catalog_json(roots))
    assert "headroom_status" in catalog, (
        f"headroom_status operation not in catalog: {list(catalog)}"
    )


def test_mcp_manifest_parses_and_matches_mode_allowlist() -> None:
    """The mcp_servers.yaml must parse under the kernel manifest loader and
    its aliases must exactly cover the mode's allowed_tool_ids."""
    from kernel.mcp_launch.server_manifest import load_mcp_server_manifest

    root = get_content_root()
    servers = load_mcp_server_manifest(root / "mcp_servers.yaml")

    assert len(servers) == 1
    server = servers[0]
    assert server.server_id == "headroom"
    assert server.warm is False
    alias_names = {alias.alias_name for alias in server.aliases}
    assert alias_names == {"headroom.compress", "headroom.retrieve", "headroom.stats"}

    # The mode's allowed_tool_ids must be exactly the alias names.
    roots = resolve_mode_roots([get_content_root()])
    mode = json.loads(build_mode_catalog_json(roots))["headroom"]
    assert set(mode["allowed_tool_ids"]) == alias_names


def test_proxy_manifest_parses_with_status_block() -> None:
    """The proxies: block must parse under the kernel proxy loader, and its
    status: declaration must expose the /stats telemetry surface so hosts
    can render live savings for this pack."""
    from kernel.proxy_launch.manifest import load_proxy_server_manifest

    root = get_content_root()
    specs = load_proxy_server_manifest(root / "mcp_servers.yaml")

    assert len(specs) == 1
    spec = specs[0]
    assert spec.proxy_id == "headroom"
    assert spec.protocol.value == "openai"
    # The wildcard declares coverage for every manifest provider; the runtime
    # expands it into one sibling proxy per provider at compile time.
    assert spec.providers == ("all",)
    assert "all" in spec.provider_options
    status = spec.status_spec
    assert status is not None, "headroom declares a status: block for /proxy visibility"
    assert status.url_path == "/stats"
    assert "summary.compression.total_tokens_saved_all_layers" in status.fields
    assert "summary.cost.total_saved_usd" in status.fields
