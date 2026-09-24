"""Headroom status pre-turn operation.

Reads the cached Headroom signal item from the compiled
:class:`UnifiedState` (``state.context_items``), runs the pure
:func:`assess` projector, and renders a deterministic savings-and-health
report as the pre-turn briefing for ``headroom`` chat mode.

Note: this operation deliberately reuses the signal-item round-trip parser
from :mod:`lisa_packs.headroom.context_items` rather than duplicating it —
``UnifiedState`` no longer carries ``signal_snapshots``.
"""

from __future__ import annotations

from kernel.models import OperationExecutionContext, OperationResult, UnifiedState

from lisa_packs.headroom.assessment import assess
from lisa_packs.headroom.config import get_headroom_connector_settings
from lisa_packs.headroom.context_items import _metrics_from_signal_item, _signal_item
from lisa_packs.headroom.models import HeadroomAssessment


def _render_report(assessment: HeadroomAssessment) -> str:
    """Render a HeadroomAssessment as a concise pre-turn briefing."""
    lines: list[str] = [f"## Headroom Status — {assessment.status.upper()}"]
    lines.append("")

    if assessment.status == "unconfigured":
        lines.append(
            "Headroom is not configured. Install Headroom "
            "(`uv tool install --python 3.13 \"headroom-ai[all]\"`), run "
            "`headroom proxy --port 8787`, export `HEADROOM_MCP_BIN` and "
            "`HEADROOM_PROXY_URL`, then set `HEADROOM_EXPECTED=true`."
        )
        return "\n".join(lines)

    # Surface critical/warn findings first
    severity_order = {"critical": 0, "warn": 1, "info": 2}
    sorted_findings = sorted(
        assessment.findings,
        key=lambda f: (severity_order.get(f.severity, 3), f.id),
    )

    for finding in sorted_findings:
        marker = ""
        if finding.severity == "critical":
            marker = "🚨 "
        elif finding.severity == "warn":
            marker = "⚠️ "
        lines.append(f"- {marker}**{finding.title}**: {finding.body}")

    if assessment.hypotheses:
        lines.append("")
        lines.append("### Hypotheses")
        for h in assessment.hypotheses[:8]:
            lines.append(f"- {h}")

    lines.append("")
    lines.append(
        "_Ask me to investigate, or to compress large content mid-turn via `headroom.compress`._"
    )
    return "\n".join(lines)


async def execute(ctx: OperationExecutionContext) -> OperationResult:
    """Run the Headroom assessment and return the report as pre-turn briefing."""

    state: UnifiedState | None = None
    if ctx.state_provider is not None:
        state = ctx.state_provider()

    if state is None:
        return OperationResult(
            output_text=(
                "⚠️ No cached runtime state available. "
                "The Headroom signal snapshot has not been collected yet."
            )
        )

    signal_item = _signal_item(state)
    if signal_item is None:
        return OperationResult(
            output_text=(
                "⚠️ No Headroom snapshot in cached state. "
                "Ensure the Headroom signal connector has run at least once."
            )
        )

    try:
        metrics = _metrics_from_signal_item(signal_item)
    except Exception as exc:  # pylint: disable=broad-except
        return OperationResult(
            status="error",
            output_text=f"Failed to parse Headroom snapshot: {type(exc).__name__}: {exc}",
            error_detail=f"{type(exc).__name__}: {exc}",
        )

    connector_settings = get_headroom_connector_settings()
    assessment = assess(
        metrics,
        savings_floor_pct=connector_settings.savings_floor_pct,
        regression_ratio=connector_settings.regression_ratio,
    )
    return OperationResult(
        output_text=_render_report(assessment),
        output_json={
            "status": assessment.status,
            "source": metrics.source,
            "proxy_up": metrics.proxy_up,
            "tokens_saved_1h": metrics.tokens_saved_1h,
            "savings_pct_1h": metrics.savings_pct_1h,
            "cost_saved_usd_1h": metrics.cost_saved_usd_1h,
        },
    )