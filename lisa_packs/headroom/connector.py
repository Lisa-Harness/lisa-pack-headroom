"""Headroom token-savings signal connector.

Monitors the local Headroom proxy (https://headroomlabs.ai) for compression
health and savings telemetry. Polled by a host scheduler.

Telemetry sources, in priority order:
1. The proxy's HTTP stats endpoint (``GET {proxy_url}/stats``) — live
   compression counters, cache hits, and estimated cost saved.
2. The shared session-stats JSONL file
   (``~/.headroom/session_stats.jsonl``) — the cross-agent stats feed that
   works even when the proxy is down.

What signals it emits:
- connector status: 'ok', 'degraded', or 'error'
- proxy_up: whether the proxy answered
- tokens_saved_1h / savings_pct_1h: rolling savings window
- ccr_expired_1h / ccr_retrieval_failures_1h: reversibility health
- trailing_savings_pct_7d: trend baseline for regression detection

Classification logic:
- 'error' when neither source produced data and the proxy is expected
- 'unconfigured' when the proxy is unreachable and HEADROOM_EXPECTED=false
- 'degraded' when the proxy is up but the JSONL fallback had to be used
  (proxy stats unavailable mid-collection) — rare; see _classify.
- 'ok' otherwise

API timeouts: the stats call uses external_signal_timeout_seconds
(typically 10-15s).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from kernel.intake.connectors import SignalCollector

from lisa_packs.headroom.config import (
    expand_stats_file_path,
    get_headroom_connector_settings,
)
from lisa_packs.headroom.models import HeadroomMetrics

logger = logging.getLogger(__name__)

_STATS_WINDOW_HOURS = 1
_SEVEN_DAYS_HOURS = 24 * 7


@dataclass
class _JsonlWindow:
    """Rolling counters folded out of the shared session-stats JSONL feed."""

    compressions_1h: int = 0
    retrievals_1h: int = 0
    tokens_saved_1h: int = 0
    tokens_processed_1h: int = 0
    mcp_calls_1h: int = 0
    mcp_context_tokens_1h: int = 0
    ccr_expired_1h: int = 0
    ccr_failures_1h: int = 0
    saved_7d: int = 0
    processed_7d: int = 0


def _fold_jsonl_events(
    events: list[dict[str, Any]],
    *,
    cutoff_1h: datetime,
    cutoff_7d: datetime,
) -> _JsonlWindow:
    """Fold raw session-stats events into the 1h and 7d rolling windows.

    Tolerant to missing/renamed keys: every counter reads through
    :func:`_to_int`, and events without a parsable timestamp are skipped.
    """
    window = _JsonlWindow()
    for event in events:
        ts = _parse_ts(str(event.get("timestamp", event.get("ts", ""))))
        if ts is None:
            continue
        event_kind = str(event.get("event", event.get("kind", ""))).lower()
        status = str(event.get("status", "")).lower()
        if ts >= cutoff_7d:
            window.saved_7d += _to_int(event.get("tokens_saved", 0))
            window.processed_7d += _to_int(
                event.get("tokens_processed", event.get("original_tokens", 0))
            )
        if ts < cutoff_1h:
            continue
        window.mcp_calls_1h += 1
        window.mcp_context_tokens_1h += _to_int(
            event.get("context_tokens", event.get("original_tokens", 0))
        )
        if "compress" in event_kind or event_kind == "":
            window.compressions_1h += 1
            window.tokens_saved_1h += _to_int(event.get("tokens_saved", 0))
            window.tokens_processed_1h += _to_int(
                event.get("tokens_processed", event.get("original_tokens", 0))
            )
        elif "retrieve" in event_kind:
            window.retrievals_1h += 1
            if status in ("error", "expired", "miss"):
                window.ccr_failures_1h += 1
        if status == "expired":
            window.ccr_expired_1h += 1
    return window


class HeadroomSignalCollector(SignalCollector[HeadroomMetrics]):
    """Headroom proxy health and savings monitor.

    Singleton connector that polls the local Headroom proxy stats endpoint,
    falling back to the shared session-stats JSONL file. Injected as a
    dependency into the bottom-half tick, initialized once at app startup.

    Attributes:
        collector_id: 'headroom' (used to identify this collector in logs and results)
        display_name: 'Headroom' (human-readable name for UI/reports)

    Dependencies (all optional — an unreachable, unopted-in Headroom is not
    an error, see HEADROOM_EXPECTED):
        - HEADROOM_PROXY_URL (default http://127.0.0.1:8787)
        - HEADROOM_MCP_BIN (default 'headroom', resolved via PATH)
        - HEADROOM_STATS_FILE (default ~/.headroom/session_stats.jsonl)
        - HEADROOM_EXPECTED (default true)
    """

    collector_id = "headroom"
    display_name = "Headroom"

    def missing_configuration(self) -> list[str]:
        """Check if the Headroom environment is minimally configured.

        The connector degrades gracefully: the proxy is optional, but when
        HEADROOM_EXPECTED=true the binary should at least exist so the mode's
        MCP aliases can launch. Returns a list of missing variable names.
        """
        connector_settings = get_headroom_connector_settings()
        missing: list[str] = []
        if connector_settings.expected and not connector_settings.proxy_url:
            missing.append("HEADROOM_PROXY_URL")
        return missing

    def build_unconfigured_snapshot(self, collected_at: str) -> HeadroomMetrics:
        """Snapshot when Headroom is not expected / not reachable (silent mode)."""
        connector_settings = get_headroom_connector_settings()
        return HeadroomMetrics(
            collected_at=collected_at,
            source="unconfigured",
            proxy_up=False,
            proxy_url=connector_settings.proxy_url,
            status="unconfigured",
        )

    def build_error_snapshot(self, collected_at: str) -> HeadroomMetrics:
        """Snapshot when collection fails unexpectedly."""
        connector_settings = get_headroom_connector_settings()
        return HeadroomMetrics(
            collected_at=collected_at,
            source="error",
            proxy_up=False,
            proxy_url=connector_settings.proxy_url,
            status="error",
        )

    async def collect(self, client: httpx.AsyncClient, collected_at: str) -> HeadroomMetrics:
        """Collect Headroom proxy stats, falling back to the session JSONL file.

        Args:
            client: httpx.AsyncClient to use for the stats endpoint call.
            collected_at: ISO 8601 timestamp (UTC) for when collection started.

        Returns:
            HeadroomMetrics with the merged telemetry window.
        """
        connector_settings = get_headroom_connector_settings()
        metrics = HeadroomMetrics(
            collected_at=collected_at,
            proxy_url=connector_settings.proxy_url,
        )

        proxy_stats: dict[str, Any] | None = None
        try:
            proxy_stats = await self._fetch_proxy_stats(
                client, connector_settings.proxy_url, connector_settings.timeout_seconds
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("headroom: proxy stats fetch failed: %s", exc)

        metrics.proxy_up = proxy_stats is not None
        if proxy_stats is not None:
            metrics.source = "proxy"
            self._merge_proxy_stats(metrics, proxy_stats)

        # Fallback (and trend enrichment) from the JSONL session stats file.
        jsonl_events = self._read_stats_file(connector_settings.stats_file)
        if jsonl_events:
            self._merge_jsonl_window(metrics, jsonl_events)
            if metrics.source in {"unconfigured", "proxy"} and metrics.source != "proxy":
                # jsonl alone still counts as a usable feed
                metrics.source = "jsonl"

        self._classify(metrics, connector_settings.expected)
        return metrics

    # ------------------------------------------------------------------
    # Proxy stats endpoint
    # ------------------------------------------------------------------

    async def _fetch_proxy_stats(
        self, client: httpx.AsyncClient, proxy_url: str, timeout_seconds: float
    ) -> dict[str, Any] | None:
        """GET the proxy's stats endpoint; return parsed JSON or None."""
        response = await client.get(
            f"{proxy_url}/stats",
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        return payload

    def _merge_proxy_stats(self, metrics: HeadroomMetrics, stats: dict[str, Any]) -> None:
        """Defensively merge the proxy stats payload into the snapshot.

        Headroom's exact /stats shape varies by version (see
        ``headroom_stats`` MCP tool); unknown shapes contribute what they
        can and the rest stay zero.
        """
        metrics.proxy_requests_1h = _to_int(stats.get("requests", stats.get("proxy_requests", 0)))
        metrics.cache_hit_rate = _to_float(stats.get("cache_hit_rate", 0.0))
        metrics.cost_saved_usd_1h = _to_float(
            stats.get("estimated_cost_saved_usd", stats.get("cost_saved_usd", 0.0))
        )
        metrics.compressions_1h = _to_int(stats.get("compressions", 0))
        metrics.retrievals_1h = _to_int(stats.get("retrievals", 0))
        metrics.tokens_saved_1h = _to_int(stats.get("tokens_saved", 0))
        tokens_processed = _to_int(stats.get("tokens_processed", stats.get("tokens_in", 0)))
        metrics.tokens_processed_1h = tokens_processed
        if tokens_processed > 0:
            metrics.savings_pct_1h = (
                _to_float(stats.get("savings_percent", 0.0))
                or 100.0 * metrics.tokens_saved_1h / tokens_processed
            )
        else:
            metrics.savings_pct_1h = _to_float(stats.get("savings_percent", 0.0))
        recent = stats.get("recent_events")
        if isinstance(recent, list):
            metrics.raw["recent_event_count"] = len(recent)

    # ------------------------------------------------------------------
    # Session stats JSONL fallback
    # ------------------------------------------------------------------

    def _read_stats_file(self, stats_file: str) -> list[dict[str, Any]]:
        """Read and parse the shared session-stats JSONL file (best effort)."""
        if not stats_file:
            return []
        path = Path(expand_stats_file_path(stats_file))
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        events.append(record)
        except OSError as exc:
            logger.warning("headroom: failed reading stats file %s: %s", path, exc)
            return []
        return events

    def _merge_jsonl_window(self, metrics: HeadroomMetrics, events: list[dict[str, Any]]) -> None:
        """Fold JSONL session events into the rolling windows.

        The shared file aggregates main + sub-agent MCP instances; each line
        is one compression/retrieval event with a timestamp and token counts
        (see Headroom docs: "Sub-agent stats are aggregated via a shared
        stats file at ~/.headroom/session_stats.jsonl"). Tolerant to
        missing/renamed keys.
        """
        now = _parse_ts(metrics.collected_at) or datetime.now(UTC)
        window = _fold_jsonl_events(
            events,
            cutoff_1h=now - timedelta(hours=_STATS_WINDOW_HOURS),
            cutoff_7d=now - timedelta(hours=_SEVEN_DAYS_HOURS),
        )

        if not metrics.proxy_up:
            # JSONL is the authoritative feed when the proxy is down.
            metrics.compressions_1h = window.compressions_1h
            metrics.retrievals_1h = window.retrievals_1h
            metrics.tokens_saved_1h = window.tokens_saved_1h
            metrics.tokens_processed_1h = window.tokens_processed_1h
            if window.tokens_processed_1h > 0:
                metrics.savings_pct_1h = 100.0 * window.tokens_saved_1h / window.tokens_processed_1h
        metrics.mcp_calls_1h = window.mcp_calls_1h
        metrics.mcp_context_tokens_1h = window.mcp_context_tokens_1h
        metrics.ccr_expired_1h = window.ccr_expired_1h
        metrics.ccr_retrieval_failures_1h = window.ccr_failures_1h
        if window.processed_7d > 0:
            metrics.trailing_savings_pct_7d = 100.0 * window.saved_7d / window.processed_7d
        metrics.trailing_tokens_saved_7d = window.saved_7d

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _classify(self, metrics: HeadroomMetrics, expected: bool) -> None:
        """Set the normalized run status on the snapshot."""
        if metrics.proxy_up:
            metrics.status = "ok"
            return
        # Proxy down: jsonl activity still means Headroom is partly alive.
        if metrics.source == "jsonl":
            metrics.status = "degraded"
            metrics.triage_notes.append(
                "Proxy unreachable but session-stats file shows recent MCP activity; "
                "MCP tools may work while proxy-level savings are lost."
            )
            return
        if not expected:
            metrics.status = "unconfigured"
            metrics.source = "unconfigured"
            return
        metrics.status = "error"


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


__all__ = ["HeadroomSignalCollector"]
