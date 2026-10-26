"""Assessment projector for Headroom telemetry.

Projects a cached :class:`HeadroomMetrics` snapshot into a
:class:`HeadroomAssessment`, checking its age against the current time.
No API calls or I/O. Mirrors the cloudflare pack's projector contract.

Finding severities (thresholds live in config:
``headroom_savings_floor_pct``, ``headroom_regression_ratio``):

- ``proxy-down`` (critical): the proxy is expected but unreachable AND the
  JSONL fallback found no recent session activity. Headroom being down is
  an incident for a host that opted into savings.
- ``no-savings`` (warn): traffic is flowing but the rolling savings percent
  sits below the floor — usually a routing misconfiguration (agent not
  pointed at the proxy, wrong base URL, or compression disabled).
- ``savings-regressed`` (warn): current savings well below the trailing
  7-day average — the classic release-regression signal Headroom's own
  beacon exists to catch.
- ``ccr-expiry`` (warn): retrieval failures / expired CCR entries mean
  lossy compression is no longer reversible on demand.
- ``mcp-overhead`` (info): MCP call context cost approaching tokens saved —
  the documented failure mode where /usage attributes a large share to the
  headroom MCP server itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lisa_packs.headroom.models import Finding, HeadroomAssessment, HeadroomMetrics

_SAVING = "token_savings"
_COMPRESSION = "compression"
_HEALTH = "headroom_health"

_STALE_AFTER_HOURS = 6
"""A cached snapshot older than this reads as stale: the host's intake
tick (``lisa intake`` — a host-owned scheduler's one-shot) has not run,
so findings describe a past proxy state, not the live one."""


def _snapshot_age_hours(metrics: HeadroomMetrics) -> float | None:
    """Age of the cached snapshot in hours, or ``None`` when undatable."""
    try:
        collected_at = datetime.fromisoformat(metrics.collected_at)
    except (TypeError, ValueError):
        return None
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - collected_at).total_seconds() / 3600.0


def assess(
    metrics: HeadroomMetrics,
    *,
    savings_floor_pct: float = 5.0,
    regression_ratio: float = 0.6,
    stale_after_hours: float = _STALE_AFTER_HOURS,
) -> HeadroomAssessment:
    """Project a cached Headroom snapshot into findings and hypotheses.

    Args:
        metrics: The cached snapshot from the signal connector.
        savings_floor_pct: Rolling savings percent below which active
            traffic is flagged as a possible routing misconfiguration.
        regression_ratio: Fraction of the trailing 7d savings average below
            which the current window reads as a regression.
        stale_after_hours: Age after which the cached snapshot itself is
            flagged (the intake tick that refreshes it has not run).

    Returns:
        :class:`HeadroomAssessment` with severity-tagged findings.
    """

    findings: list[Finding] = []
    hypotheses: list[str] = []
    anchors: list[str] = []

    # -- snapshot staleness (checked before every other finding) --------
    age_hours = _snapshot_age_hours(metrics)
    if age_hours is not None and age_hours > stale_after_hours:
        findings.append(
            Finding(
                id="stale-snapshot",
                severity="warn",
                title="Telemetry snapshot is stale",
                body=(
                    f"This report was cached {age_hours:.0f}h ago and has not "
                    "refreshed: the host's intake tick has not run. Run "
                    "`/intake` (or schedule `lisa intake`) and re-enter "
                    "headroom mode for live proxy state."
                ),
                evidence_type=_HEALTH,
                tags=["headroom", "intake", "stale"],
                salience=0.6,
            )
        )

    # -- proxy reachability ---------------------------------------------
    if metrics.status == "unconfigured":
        return HeadroomAssessment(
            status="unconfigured",
            findings=[
                *findings,
                Finding(
                    id="unconfigured",
                    severity="info",
                    title="Headroom not configured",
                    body=(
                        "The Headroom proxy is not reachable and is not expected "
                        "(`HEADROOM_EXPECTED=false` or no telemetry found). "
                        "Set `HEADROOM_EXPECTED=true` once `headroom proxy` runs."
                    ),
                    evidence_type=_HEALTH,
                    tags=["headroom", "unconfigured"],
                    salience=0.0,
                ),
            ],
        )

    if metrics.proxy_up:
        findings.append(
            Finding(
                id="proxy-up",
                severity="info",
                title="Headroom proxy up",
                body=f"Proxy reachable at {metrics.proxy_url}.",
                evidence_type=_HEALTH,
                tags=["headroom", "proxy", "up"],
                salience=0.0,
            )
        )
    elif metrics.source == "jsonl":
        # Proxy unreachable: critical while expected (the host opted into
        # savings and lost them); only the JSONL fallback mutes this to a
        # degraded-but-alive warn. A snapshot that reached collect() with
        # neither feed (status='error') is the expected-down case.
        findings.append(
            Finding(
                id="proxy-down",
                severity="warn",
                title="Headroom proxy down (MCP still active)",
                body=(
                    f"Proxy unreachable at {metrics.proxy_url} but the session-stats "
                    "file shows recent MCP activity — mid-turn compression still "
                    "works; proxy-level savings are lost until it restarts."
                ),
                evidence_type=_HEALTH,
                tags=["headroom", "proxy", "down"],
                salience=0.5,
            )
        )
    else:
        findings.append(
            Finding(
                id="proxy-down",
                severity="critical",
                title="Headroom proxy down",
                body=(
                    f"Proxy unreachable at {metrics.proxy_url} while "
                    "`HEADROOM_EXPECTED=true`. Compression savings are lost "
                    "until the proxy restarts (`headroom proxy --port 8787`)."
                ),
                evidence_type=_HEALTH,
                tags=["headroom", "proxy", "down"],
                salience=0.9,
            )
        )
        hypotheses.append("Headroom proxy process stopped or crashed.")
        hypotheses.append("Proxy bound to a different port (HEADROOM_PROXY_URL).")

    # -- savings ----------------------------------------------------------
    traffic = metrics.proxy_requests_1h + metrics.compressions_1h
    if metrics.proxy_up and traffic > 0 and metrics.savings_pct_1h < savings_floor_pct:
        findings.append(
            Finding(
                id="no-savings",
                severity="warn",
                title="No meaningful token savings",
                body=(
                    f"Traffic is flowing ({traffic} requests/compressions in the last hour) "
                    f"but savings are only {metrics.savings_pct_1h:.1f}% "
                    f"(floor {savings_floor_pct:.0f}%). Likely a routing or "
                    "content-router misconfiguration."
                ),
                evidence_type=_SAVING,
                tags=["headroom", "savings", "routing"],
                salience=0.5,
            )
        )
        hypotheses.append("Agent not routed through the proxy (check base URLs).")
        hypotheses.append("Content router selecting no compressor for payloads.")

    if (
        metrics.trailing_savings_pct_7d > 0
        and metrics.savings_pct_1h > 0
        and metrics.savings_pct_1h < regression_ratio * metrics.trailing_savings_pct_7d
    ):
        findings.append(
            Finding(
                id="savings-regressed",
                severity="warn",
                title="Savings regressed vs trailing average",
                body=(
                    f"Current savings {metrics.savings_pct_1h:.1f}% vs trailing 7d "
                    f"{metrics.trailing_savings_pct_7d:.1f}% — below the "
                    f"{regression_ratio:.0%} ratio. A Headroom release or workload "
                    "shift may have degraded compression."
                ),
                evidence_type=_SAVING,
                tags=["headroom", "savings", "regression"],
                salience=0.5,
            )
        )
        hypotheses.append("Headroom version change regressed compression ratios.")

    # -- CCR health -------------------------------------------------------
    if metrics.ccr_retrieval_failures_1h > 0 or metrics.ccr_expired_1h > 0:
        findings.append(
            Finding(
                id="ccr-expiry",
                severity="warn",
                title="CCR retrieval failures or expirations",
                body=(
                    f"{metrics.ccr_retrieval_failures_1h} failed retrievals and "
                    f"{metrics.ccr_expired_1h} expired entries in the last hour. "
                    "Originals may no longer be retrievable on demand — consider "
                    "raising HEADROOM_CCR_TTL_SECONDS."
                ),
                evidence_type=_COMPRESSION,
                tags=["headroom", "ccr", "expiry"],
                salience=0.5,
            )
        )
        hypotheses.append("TTL too short for the session length.")

    # -- MCP overhead -------------------------------------------------------
    if (
        metrics.mcp_calls_1h > 0
        and metrics.tokens_saved_1h > 0
        and metrics.mcp_context_tokens_1h >= 0.5 * metrics.tokens_saved_1h
    ):
        findings.append(
            Finding(
                id="mcp-overhead",
                severity="info",
                title="MCP call overhead approaching savings",
                body=(
                    f"MCP interactions cost ~{metrics.mcp_context_tokens_1h} context "
                    f"tokens against ~{metrics.tokens_saved_1h} saved. Consider "
                    "compressing larger payloads per call or relying on the proxy "
                    "path instead of many small headroom.compress calls."
                ),
                evidence_type=_COMPRESSION,
                tags=["headroom", "mcp", "overhead"],
                salience=0.3,
            )
        )

    # -- healthy summary -----------------------------------------------------
    if metrics.proxy_up and metrics.tokens_saved_1h > 0:
        findings.append(
            Finding(
                id="savings-healthy",
                severity="info",
                title="Token savings on track",
                body=(
                    f"{metrics.tokens_saved_1h} tokens saved in the last hour "
                    f"({metrics.savings_pct_1h:.1f}% of processed)."
                ),
                evidence_type=_SAVING,
                tags=["headroom", "savings"],
                salience=0.0,
            )
        )

    anchors.append("headroom.stats — live session compression statistics")
    anchors.append("headroom.retrieve — fetch an original by CCR hash when detail is needed")

    has_critical = any(f.severity == "critical" for f in findings)
    has_warn = any(f.severity == "warn" for f in findings)
    status = "degraded" if has_critical or has_warn else "ok"

    return HeadroomAssessment(
        status=status,
        findings=findings,
        hypotheses=hypotheses[:8],
        investigation_anchors=anchors,
    )


__all__ = ["assess"]
