"""Tests for the headroom processing loop (critical alerts only)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kernel.intake.contracts import CollectionResult, ProcessContext, ProcessedState
from lisa_packs.headroom.models import HeadroomMetrics
from lisa_packs.headroom.processing import HeadroomProcessingLoop


def _context_with_snapshot(snapshot: HeadroomMetrics) -> ProcessContext:
    return ProcessContext(
        collections=[CollectionResult(collector_id="headroom", snapshot=snapshot)],
    )


@pytest.mark.asyncio
async def test_proxy_down_emits_critical_alert() -> None:
    loop = HeadroomProcessingLoop()
    snapshot = HeadroomMetrics(
        collected_at="2026-09-20T12:00:00Z",
        source="error",
        proxy_up=False,
        status="error",
    )

    state = await loop.process(_context_with_snapshot(snapshot))

    assert isinstance(state, ProcessedState)
    assert state.loop_id == "headroom"
    assert state.active_alerts, "expected a critical alert for proxy-down"
    alert = state.active_alerts[0]
    assert alert.severity == "critical"
    assert alert.source == "headroom"
    assert "proxy down" in alert.message.lower()


@pytest.mark.asyncio
async def test_healthy_snapshot_no_alerts() -> None:
    loop = HeadroomProcessingLoop()
    snapshot = HeadroomMetrics(
        collected_at="2026-09-20T12:00:00Z",
        source="proxy",
        proxy_up=True,
        tokens_saved_1h=120000,
        savings_pct_1h=30.0,
        proxy_requests_1h=80,
        status="ok",
    )

    state = await loop.process(_context_with_snapshot(snapshot))

    assert state.active_alerts == []


@pytest.mark.asyncio
async def test_warn_findings_do_not_alert() -> None:
    """Only critical findings produce alerts — warns stay in triage."""
    loop = HeadroomProcessingLoop()
    snapshot = HeadroomMetrics(
        collected_at="2026-09-20T12:00:00Z",
        source="proxy",
        proxy_up=True,
        proxy_requests_1h=80,
        compressions_1h=40,
        savings_pct_1h=1.0,  # below floor -> no-savings warn finding
        status="ok",
    )

    state = await loop.process(_context_with_snapshot(snapshot))

    assert state.active_alerts == []


@pytest.mark.asyncio
async def test_no_collections_no_state() -> None:
    loop = HeadroomProcessingLoop()

    state = await loop.process(ProcessContext())

    assert state.active_alerts == []


@pytest.mark.asyncio
async def test_foreign_collections_ignored() -> None:
    loop = HeadroomProcessingLoop()
    ctx = ProcessContext(
        collections=[
            CollectionResult(
                collector_id="cloudflare",
                snapshot={"status": "degraded"},
            )
        ],
    )

    state = await loop.process(ctx)

    assert state.active_alerts == []


@pytest.mark.asyncio
async def test_detect_timestamp_is_iso_utc() -> None:
    loop = HeadroomProcessingLoop()
    snapshot = HeadroomMetrics(
        collected_at=datetime.now(UTC).isoformat(),
        source="error",
        proxy_up=False,
        status="error",
    )

    state = await loop.process(_context_with_snapshot(snapshot))

    assert state.active_alerts
    assert state.active_alerts[0].detected_at