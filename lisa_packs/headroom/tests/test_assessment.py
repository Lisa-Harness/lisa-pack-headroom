"""Tests for the pure assessment projector."""

from __future__ import annotations

from lisa_packs.headroom.assessment import assess
from lisa_packs.headroom.models import HeadroomMetrics

_EXPECTED_CRITICAL_SALIENCE = 0.8


def _ok_metrics() -> HeadroomMetrics:
    return HeadroomMetrics(
        collected_at="2026-09-20T12:00:00Z",
        source="proxy",
        proxy_up=True,
        proxy_url="http://127.0.0.1:8787",
        compressions_1h=40,
        retrievals_1h=6,
        tokens_saved_1h=120000,
        tokens_processed_1h=400000,
        savings_pct_1h=30.0,
        cost_saved_usd_1h=1.2,
        proxy_requests_1h=80,
        cache_hit_rate=0.62,
        trailing_savings_pct_7d=32.0,
        trailing_tokens_saved_7d=900000,
        status="ok",
    )


def _proxy_down_metrics() -> HeadroomMetrics:
    return HeadroomMetrics(
        collected_at="2026-09-20T12:00:00Z",
        source="error",
        proxy_up=False,
        proxy_url="http://127.0.0.1:8787",
        status="error",
    )


def _unconfigured_metrics() -> HeadroomMetrics:
    return HeadroomMetrics(
        collected_at="2026-09-20T12:00:00Z",
        source="unconfigured",
        status="unconfigured",
    )


def test_assess_ok_snapshot_produces_zero_salience_findings() -> None:
    """Healthy findings should have salience=0.0 so the mapper drops them outside headroom mode."""
    assessment = assess(_ok_metrics())

    assert assessment.status == "ok"
    assert assessment.findings, "expected at least the healthy summary finding"
    for finding in assessment.findings:
        assert finding.severity == "info"
        assert finding.salience == 0.0


def test_assess_ok_no_critical_alerts() -> None:
    assessment = assess(_ok_metrics())
    assert not [f for f in assessment.findings if f.severity == "critical"]


def test_assess_proxy_down_is_critical() -> None:
    """Proxy unreachable while expected must surface a critical finding with high salience."""
    assessment = assess(_proxy_down_metrics())

    critical = [f for f in assessment.findings if f.id == "proxy-down"]
    assert critical, "expected proxy-down finding"
    assert critical[0].severity == "critical"
    assert critical[0].salience >= _EXPECTED_CRITICAL_SALIENCE
    assert assessment.status == "degraded"
    assert any("proxy" in h.lower() for h in assessment.hypotheses)


def test_assess_proxy_down_with_jsonl_activity_is_warn() -> None:
    """Recent MCP activity via the JSONL fallback mutes proxy-down to a warn."""
    metrics = _proxy_down_metrics().model_copy(update={"source": "jsonl"})
    assessment = assess(metrics)

    flagged = [f for f in assessment.findings if f.id == "proxy-down"]
    assert flagged, "expected proxy-down finding"
    assert flagged[0].severity == "warn"


def test_assess_unconfigured_is_silent() -> None:
    """An opted-out host gets an unconfigured snapshot, not an incident."""
    assessment = assess(_unconfigured_metrics())

    assert assessment.status == "unconfigured"
    assert len(assessment.findings) == 1
    assert assessment.findings[0].severity == "info"
    assert assessment.findings[0].salience == 0.0
    assert not any(f.severity == "critical" for f in assessment.findings)


def test_assess_no_savings_with_traffic() -> None:
    """Traffic flowing but savings under the floor flags a routing problem."""
    metrics = _ok_metrics().model_copy(
        update={
            "savings_pct_1h": 1.0,
            "tokens_saved_1h": 4000,
        }
    )
    assessment = assess(metrics, savings_floor_pct=5.0)

    flagged = [f for f in assessment.findings if f.id == "no-savings"]
    assert flagged, "expected no-savings finding"
    assert flagged[0].severity == "warn"
    assert flagged[0].salience > 0.0
    assert assessment.status == "degraded"


def test_assess_no_traffic_no_savings_flag() -> None:
    """Idle systems don't get flagged: zero traffic means nothing to save."""
    metrics = _ok_metrics().model_copy(
        update={
            "proxy_requests_1h": 0,
            "compressions_1h": 0,
            "savings_pct_1h": 0.0,
        }
    )
    assessment = assess(metrics)

    assert not [f for f in assessment.findings if f.id == "no-savings"]


def test_assess_savings_regressed_vs_trailing() -> None:
    """Current window well under the 7d average flags a regression."""
    metrics = _ok_metrics().model_copy(
        update={
            "savings_pct_1h": 12.0,
            "trailing_savings_pct_7d": 40.0,
        }
    )
    assessment = assess(metrics, regression_ratio=0.6)

    flagged = [f for f in assessment.findings if f.id == "savings-regressed"]
    assert flagged, "expected savings-regressed finding"
    assert flagged[0].severity == "warn"


def test_assess_savings_above_regression_ratio_not_flagged() -> None:
    metrics = _ok_metrics().model_copy(
        update={
            "savings_pct_1h": 30.0,
            "trailing_savings_pct_7d": 32.0,
        }
    )
    assessment = assess(metrics, regression_ratio=0.6)

    assert not [f for f in assessment.findings if f.id == "savings-regressed"]


def test_assess_ccr_expiry_flag() -> None:
    metrics = _ok_metrics().model_copy(
        update={
            "ccr_expired_1h": 3,
            "ccr_retrieval_failures_1h": 2,
        }
    )
    assessment = assess(metrics)

    flagged = [f for f in assessment.findings if f.id == "ccr-expiry"]
    assert flagged, "expected ccr-expiry finding"
    assert flagged[0].severity == "warn"


def test_assess_mcp_overhead_info() -> None:
    """MCP context cost at or above half of savings is an info-level nudge."""
    metrics = _ok_metrics().model_copy(
        update={
            "mcp_calls_1h": 60,
            "mcp_context_tokens_1h": 80000,
        }
    )
    assessment = assess(metrics)

    flagged = [f for f in assessment.findings if f.id == "mcp-overhead"]
    assert flagged, "expected mcp-overhead finding"
    assert flagged[0].severity == "info"


def test_assess_includes_investigation_anchors() -> None:
    assessment = assess(_ok_metrics())
    assert any("headroom.stats" in a for a in assessment.investigation_anchors)
    assert any("headroom.retrieve" in a for a in assessment.investigation_anchors)


def test_assess_error_snapshot_has_no_collection_error_finding() -> None:
    """A status=error snapshot with no feed IS the proxy-down case (critical),
    not a separate collection-error warn."""
    metrics = HeadroomMetrics(
        collected_at="2026-09-20T12:00:00Z",
        source="error",
        proxy_up=False,
        status="error",
    )
    assessment = assess(metrics)

    assert not [f for f in assessment.findings if f.id == "collection-error"]
    assert [f for f in assessment.findings if f.id == "proxy-down"]
