"""Tests for the signal-item round-trip and context item builder."""

from __future__ import annotations

from kernel.models import ContextItem, UnifiedState
from lisa_packs.headroom.context_items import (
    _metrics_from_signal_item,
    _signal_item,
    build_context_items,
)


def _signal_item_body() -> ContextItem:
    """Mimic the host intake rendering of a headroom collector snapshot."""
    return ContextItem(
        item_id="state:signal:headroom",
        namespace="signals",
        source="headroom",
        source_type="intake",
        evidence_type="signal_snapshot",
        title="Signal: headroom (ok)",
        body="\n".join(
            [
                "status=ok",
                "source=proxy",
                "proxy_up=true",
                "proxy_url=http://127.0.0.1:8787",
                "compressions_1h=40",
                "retrievals_1h=6",
                "tokens_saved_1h=120000",
                "savings_pct_1h=30.0",
                "trailing_savings_pct_7d=32.0",
            ]
        ),
        collected_at="2026-09-20T12:00:00Z",
    )


def test_metrics_round_trip_preserves_scalars() -> None:
    metrics = _metrics_from_signal_item(_signal_item_body())

    assert metrics.status == "ok"
    assert metrics.source == "proxy"
    assert metrics.proxy_up is True
    assert metrics.proxy_url == "http://127.0.0.1:8787"
    assert metrics.compressions_1h == 40
    assert metrics.tokens_saved_1h == 120000
    assert metrics.savings_pct_1h == 30.0
    assert metrics.trailing_savings_pct_7d == 32.0
    assert metrics.collected_at == "2026-09-20T12:00:00Z"


def test_build_context_items_from_state() -> None:
    state = UnifiedState(
        compiled_at="2026-09-20T12:00:00Z",
        context_items=[_signal_item_body()],
    )

    items = build_context_items(None, state)  # type: ignore[arg-type]

    assert items, "expected finding items from a healthy snapshot"
    assert all(item.item_id.startswith("headroom:finding:") for item in items)
    assert all(item.source == "headroom" for item in items)
    # Healthy findings must be zero-salience so the mapper drops them
    # outside headroom mode.
    assert all(item.salience_score == 0.0 for item in items)


def test_build_context_items_critical_has_high_salience() -> None:
    down = ContextItem(
        item_id="state:signal:headroom",
        namespace="signals",
        source="headroom",
        source_type="intake",
        evidence_type="signal_snapshot",
        title="Signal: headroom (error)",
        body="\n".join(["status=error", "source=error", "proxy_up=false"]),
        collected_at="2026-09-20T12:00:00Z",
    )
    state = UnifiedState(compiled_at="2026-09-20T12:00:00Z", context_items=[down])

    items = build_context_items(None, state)  # type: ignore[arg-type]

    proxy_down = [i for i in items if i.item_id.endswith("proxy-down")]
    assert proxy_down, "expected a proxy-down finding item"
    assert proxy_down[0].salience_score >= 0.8


def test_build_context_items_empty_state() -> None:
    state = UnifiedState(compiled_at="2026-09-20T12:00:00Z", context_items=[])

    assert build_context_items(None, state) == []  # type: ignore[arg-type]


def test_signal_item_lookup_ignores_foreign_collectors() -> None:
    foreign = ContextItem(
        item_id="state:signal:cloudflare",
        namespace="signals",
        body="status=ok",
        collected_at="2026-09-20T12:00:00Z",
    )
    state = UnifiedState(
        compiled_at="2026-09-20T12:00:00Z",
        context_items=[foreign, _signal_item_body()],
    )

    item = _signal_item(state)
    assert item is not None
    assert item.item_id == "state:signal:headroom"