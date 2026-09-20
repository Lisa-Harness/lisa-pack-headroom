"""Headroom signal connector snapshot models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HeadroomMetrics(BaseModel):
    """One cached Headroom telemetry snapshot.

    Populated from the proxy's HTTP stats endpoint when the proxy is up,
    or defensively from the shared session-stats JSONL file when it is not.
    All numeric fields default to zero so partial telemetry still validates.
    """

    collected_at: str
    source: str = "unconfigured"
    """Which feed produced this snapshot: 'proxy' | 'jsonl' | 'unconfigured' | 'error'."""

    proxy_up: bool = False
    proxy_url: str = ""

    # Session compression counters (1-hour sliding window where available).
    compressions_1h: int = 0
    retrievals_1h: int = 0
    tokens_saved_1h: int = 0
    tokens_processed_1h: int = 0
    savings_pct_1h: float = 0.0
    cost_saved_usd_1h: float = 0.0

    # Proxy-level metrics (only when the proxy is up).
    proxy_requests_1h: int = 0
    cache_hit_rate: float = 0.0

    # Health signals.
    ccr_expired_1h: int = 0
    ccr_retrieval_failures_1h: int = 0
    mcp_calls_1h: int = 0
    mcp_context_tokens_1h: int = 0

    # Trend baseline (trailing 7-day average, when available).
    trailing_savings_pct_7d: float = 0.0
    trailing_tokens_saved_7d: int = 0

    status: str = "ok"
    """'ok' | 'degraded' | 'error' | 'unconfigured'."""

    triage_notes: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    """One interpreted finding from a Headroom snapshot."""

    id: str
    """Stable identifier used as the context item id suffix."""

    severity: str = "info"
    """``info`` | ``warn`` | ``critical`` — drives salience and alert emission."""

    title: str = ""
    body: str = ""
    evidence_type: str = ""
    tags: list[str] = Field(default_factory=list)
    salience: float = 0.0
    """0.0 when healthy so the mapper drops it outside ``headroom`` mode."""


class HeadroomAssessment(BaseModel):
    """Pure interpretation of a :class:`HeadroomMetrics` snapshot.

    Produced by :func:`lisa_packs.headroom.assessment.assess`. Contains no
    collection logic — only deterministic projection from the cached
    snapshot into typed findings, hypotheses, and investigation anchors.
    """

    status: str = "ok"
    findings: list[Finding] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    investigation_anchors: list[str] = Field(default_factory=list)