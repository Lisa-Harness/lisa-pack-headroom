"""Headroom pack-local import boundary tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from kernel.testing.import_boundaries import collect_import_violations

_EXT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.eval
def test_headroom_extension_imports_no_app_modules() -> None:
    """lisa_packs.headroom must remain isolated from the app layer."""
    violations = collect_import_violations(
        _EXT_ROOT,
        forbidden_prefixes={"app."},
        allowed_prefixes={"kernel.", "sdk.", "sdk.packs."},
    )
    if violations:
        lines = [f"  {path}:{lineno}: {text}" for path, lineno, text in violations]
        pytest.fail(
            "Forbidden app-layer imports found in lisa_packs.headroom:\n" + "\n".join(lines),
            pytrace=False,
        )


@pytest.mark.eval
def test_no_headroom_ai_dependency_imported() -> None:
    """The pack must not import the headroom-ai package — the MCP server
    subprocess owns compression; a hard dependency would pin a heavy
    package into the packs venv."""
    violations = collect_import_violations(
        _EXT_ROOT,
        forbidden_prefixes={"headroom", "headroom_ai"},
        allowed_prefixes={"kernel.", "sdk.", "sdk.packs.", "lisa_packs."},
    )
    if violations:
        lines = [f"  {path}:{lineno}: {text}" for path, lineno, text in violations]
        pytest.fail(
            "Forbidden headroom-ai imports found in lisa_packs.headroom:\n" + "\n".join(lines),
            pytrace=False,
        )
