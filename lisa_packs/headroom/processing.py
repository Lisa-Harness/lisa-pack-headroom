"""Headroom processing loop — alert detection from assessment findings.

Only ``critical`` severity findings produce alerts; ``warn`` findings are
noted in triage but do not trigger the alert pipeline. For this pack the
single critical finding is ``proxy-down`` (proxy unreachable while
``HEADROOM_EXPECTED=true``) — losing compression savings mid-session is an
incident worth surfacing in the global incidents lane.
"""

from __future__ import annotations

from datetime import UTC, datetime

from kernel.intake.contracts import ProcessContext, ProcessedState
from kernel.models import AnomalyAlert
from lisa_packs.headroom.assessment import assess
from lisa_packs.headroom.context_items import _signal_item, _metrics_from_signal_item
from lisa_packs.headroom.config import get_headroom_connector_settings
from lisa_packs.headroom.models import HeadroomMetrics


def _snapshot_from_collections(ctx: ProcessContext) -> HeadroomMetrics | None:
    """Recover this collector's typed snapshot from the tick's collection results."""
    for result in ctx.collections:
        if result.collector_id == "headroom" and isinstance(
            result.snapshot, HeadroomMetrics
        ):
            return result.snapshot
    return None


class HeadroomProcessingLoop:
    """Alert detection for cached Headroom telemetry."""

    loop_id = "headroom"

    async def process(self, ctx: ProcessContext) -> ProcessedState:
        """Examine the Headroom assessment and emit alerts for critical findings."""
        metrics = _snapshot_from_collections(ctx)

        if metrics is None and ctx.state is not None:
            signal_item = _signal_item(ctx.state)
            if signal_item is not None:
                metrics = _metrics_from_signal_item(signal_item)

        if metrics is None:
            return ProcessedState(loop_id=self.loop_id)

        connector_settings = get_headroom_connector_settings()
        assessment = assess(
            metrics,
            savings_floor_pct=connector_settings.savings_floor_pct,
            regression_ratio=connector_settings.regression_ratio,
        )

        alerts = []
        for finding in assessment.findings:
            if finding.severity != "critical":
                continue
            alerts.append(
                AnomalyAlert(
                    severity="critical",
                    source="headroom",
                    message=f"{finding.title}: {finding.body}",
                    detected_at=datetime.now(UTC).isoformat(),
                )
            )

        if not alerts:
            return ProcessedState(loop_id=self.loop_id)

        return ProcessedState(
            loop_id=self.loop_id,
            active_alerts=alerts,
        )


__all__ = ["HeadroomProcessingLoop"]