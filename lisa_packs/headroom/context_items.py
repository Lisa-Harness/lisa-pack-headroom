"""Extension-owned context items for Headroom.

Atomizes the cached Headroom telemetry snapshot into stable, accurately
labeled :class:`ContextItem` candidates via the pure :func:`assess`
projector. Each finding becomes one context item with accurate
``evidence_type``, ``tags``, and ``salience_score`` (0.0 when healthy so
the mapper drops it outside ``headroom`` mode).

Snapshot channel: ``UnifiedState`` no longer carries ``signal_snapshots`` —
the intake merge renders each collector snapshot into one signal
``ContextItem`` (``item_id="state:signal:<collector>"``,
``evidence_type="signal_snapshot"``, body of ``key=value`` lines). This
builder re-reads its connector's signal item from ``state.context_items``
and parses the scalar fields back into :class:`HeadroomMetrics`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from kernel.extension_runtime import ExtensionRuntimeContext
from kernel.models import ContextItem, UnifiedState
from lisa_packs.headroom.assessment import assess
from lisa_packs.headroom.config import get_headroom_connector_settings
from lisa_packs.headroom.models import HeadroomMetrics

_SIGNAL_ITEM_ID = "state:signal:headroom"


def _default_collected_at(state: UnifiedState) -> str:
    return getattr(state, "compiled_at", None) or datetime.now(UTC).isoformat()


def _metrics_from_signal_item(item: ContextItem) -> HeadroomMetrics:
    """Rebuild scalar metrics from the intake signal item's ``key=value`` body."""
    payload: dict[str, object] = {}
    for line in item.body.splitlines():
        key, sep, raw = line.partition("=")
        if not sep or not key:
            continue
        raw = raw.strip()
        if raw.lower() in ("true", "false"):
            payload[key] = raw.lower() == "true"
            continue
        try:
            payload[key] = int(raw)
            continue
        except ValueError:
            pass
        try:
            payload[key] = float(raw)
            continue
        except ValueError:
            pass
        payload[key] = raw
    payload.setdefault("collected_at", item.collected_at)
    return HeadroomMetrics.model_validate(payload)


def _signal_item(state: UnifiedState) -> ContextItem | None:
    """Return this connector's intake signal item, or ``None`` when absent."""
    for item in getattr(state, "context_items", None) or []:
        if item.item_id == _SIGNAL_ITEM_ID:
            return item
    return None


def build_context_items(
    runtime_context: ExtensionRuntimeContext,
    state: UnifiedState,
) -> list[ContextItem]:
    """Expose Headroom context items without host-owned summary branches.

    Each finding from :func:`assess` becomes one :class:`ContextItem`
    record. Healthy findings have ``salience_score=0.0`` so the
    session-owned context mapper drops them outside ``headroom`` mode
    (lexical overlap alone cannot elevate them). Critical findings (proxy
    down while expected) carry high salience so they reach the incidents
    lane globally.
    """

    del runtime_context
    signal_item = _signal_item(state)
    if signal_item is None:
        return []

    metrics = _metrics_from_signal_item(signal_item)
    connector_settings = get_headroom_connector_settings()
    assessment = assess(
        metrics,
        savings_floor_pct=connector_settings.savings_floor_pct,
        regression_ratio=connector_settings.regression_ratio,
    )
    item_collected_at = metrics.collected_at or _default_collected_at(state)

    items: list[ContextItem] = []
    for finding in assessment.findings:
        items.append(
            ContextItem(
                item_id=f"headroom:finding:{finding.id}",
                namespace="headroom_findings",
                source="headroom",
                source_type="extension",
                evidence_type=finding.evidence_type or "headroom_finding",
                title=finding.title,
                body=finding.body,
                collected_at=item_collected_at,
                salience_score=finding.salience,
                confidence=0.9,
                tags=finding.tags or ["headroom"],
                match_reasons=[f"finding:{finding.id}"],
            )
        )

    return items


__all__ = ["build_context_items"]