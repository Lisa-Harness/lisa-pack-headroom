# lisa-pack-headroom

Headroom integration for [Lisa](https://github.com/TheMigusGroup/lisa-harness) — a two-plane pack that monitors Headroom token savings in the bottom half, then gives the model mid-turn context compression in the top half.

> **Compress first, retrieve later.** Every turn in `headroom` mode starts with a cached savings-and-health briefing from the bottom-half snapshot. The model then uses the `headroom.*` MCP aliases to compress large payloads mid-turn — keeping a CCR hash so the original stays retrievable on demand.

## What it provides

| Layer | Name | Kind | Purpose |
| --- | --- | --- | --- |
| Mode | `headroom` | chat | `/headroom` — cached savings report, then live mid-turn compression |
| Operation | `headroom_status` | runtime (pre-turn) | Deterministic report from cached snapshot via `assess()` |
| Connector | `HeadroomSignalCollector` | signal | Polls proxy `/stats` (JSONL fallback) for savings/health telemetry |
| Processing | `HeadroomProcessingLoop` | processing | Emits alerts for critical findings (proxy down while expected) |
| Context items | `build_context_items` | context | Atomizes assessment into stable lane-ready candidates |
| MCP Server | `headroom` | binary (cold) | `headroom mcp serve --proxy-url …` via `runtime_kind: binary` |
| Alias | `headroom.compress` | synthetic | Compress large content; returns compressed text + CCR hash + savings |
| Alias | `headroom.retrieve` | synthetic | Fetch the original by CCR hash (optionally query-filtered) |
| Alias | `headroom.stats` | synthetic | Session compression statistics |

The pack is discovered via the `lisa.extensions.content_roots` entry point. The signal connector is registered via `lisa.extensions.signal_connectors`, the processing loop via `lisa.extensions.processing_loops`, and the context item builder via `lisa.extensions.context_items`.

The pack deliberately has **no `headroom-ai` dependency** — compression runs in Headroom's own MCP subprocess with its shared CCR store, and the connector speaks plain HTTP/JSONL. An import-boundary test enforces this.

## Install

Install Headroom itself separately (the CLI must be reachable):

```bash
uv tool install --python 3.13 "headroom-ai[all]"
headroom proxy --port 8787
```

Then add this pack to the framework workspace dependencies and reinstall:

```bash
uv sync --extra dev
```

## Configure

The pack reads environment variables:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `HEADROOM_PROXY_URL` | no | `http://127.0.0.1:8787` | Local Headroom proxy base URL |
| `HEADROOM_MCP_BIN` | no | `headroom` | Path to the `headroom` CLI for the cold MCP server |
| `HEADROOM_STATS_FILE` | no | `~/.headroom/session_stats.jsonl` | Fallback telemetry (shared sub-agent stats) |
| `HEADROOM_EXPECTED` | no | `true` | `false` = proxy absence is silent (`unconfigured`), not an incident |
| `HEADROOM_SAVINGS_FLOOR_PCT` | no | `5.0` | Rolling savings % below which active traffic is flagged |
| `HEADROOM_REGRESSION_RATIO` | no | `0.6` | Fraction of trailing 7d savings below which a regression is flagged |

Export `HEADROOM_MCP_BIN` as an **absolute path** (`command -v headroom`) — the kernel manifest loader expands `${HEADROOM_MCP_BIN}` from the raw environment at load time, and an unset variable becomes an empty command, which fails cold-connect cleanly (aliases then return "MCP server … is not connected.").

## Signals and findings

The connector classifies each snapshot `ok` / `degraded` / `error` / `unconfigured` and the projector emits severity-tagged findings:

| Finding | Severity | Condition |
| --- | --- | --- |
| `proxy-down` | critical | Proxy unreachable while `HEADROOM_EXPECTED=true` |
| `no-savings` | warn | Traffic flowing but savings under the floor (routing misconfiguration) |
| `savings-regressed` | warn | Current savings well below trailing 7d average |
| `ccr-expiry` | warn | CCR retrieval failures or expired entries |
| `mcp-overhead` | info | MCP call context cost approaching tokens saved |
| `savings-healthy` | info | Healthy summary (zero-salience — dropped outside `headroom` mode) |

Critical findings become `AnomalyAlert` records via the processing loop; healthy findings carry `salience_score=0.0` so the context mapper drops them outside the `headroom` mode.

## Mid-turn compression contract

The mode body instructs the model to:

1. Call `headroom.compress` **before** quoting or analyzing any large payload.
2. Keep the returned CCR `hash` — call `headroom.retrieve` (optionally with a `query`) when a dropped detail is needed, instead of re-reading the full payload.
3. Skip compression for small payloads, verbatim prose, or already-compressed content.

## Development

```bash
cd headroom
uv run pytest -p no:cacheprovider
```