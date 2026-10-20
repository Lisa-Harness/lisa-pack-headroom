# lisa-pack-headroom

Lisa Headroom pack — token-savings signal connector plus mid-turn context
compression via the Headroom MCP server.

The pack watches the local Headroom proxy for compression health and savings
telemetry, and exposes Headroom's mid-turn compression to the model so large
tool outputs are compressed before the model reasons over them.

## What it provides

| Layer | Path | Role |
| --- | --- | --- |
| Signal connector | `connector.py` | Polls the proxy stats endpoint, falls back to the shared session-stats JSONL file. |
| Context items | `context_items.py` | Turns the cached snapshot into labeled `ContextItem` candidates. |
| Processing loop | `processing.py` | Watches the snapshot and surfaces critical alerts. |
| Operation | `operations/headroom_status/` | Reports proxy health and savings. |
| Mode | `modes/headroom/` | The operator-facing status mode. |
| MCP manifest | `mcp_servers.yaml` | Declares the proxy and the Headroom MCP server with its aliases. |

### Aliases

- **`headroom.compress`** — compress large content (tool output, logs, JSON,
  files) before reasoning over it. Returns compressed text, a CCR hash for
  the original, and token savings.
- **`headroom.retrieve`** — retrieve the original uncompressed content by its
  CCR hash, optionally filtered by a query.
- **`headroom.stats`** — session compression statistics: compressions,
  retrievals, tokens saved, estimated cost saved, and recent events.

### Telemetry sources

1. The proxy's HTTP stats endpoint (`GET {proxy_url}/stats`) — live
   compression counters, cache hits, and estimated cost saved.
2. The shared session-stats JSONL file
   (`~/.headroom/session_stats.jsonl`) — the cross-agent stats feed that
   works even when the proxy is down.

Connector status is `ok`, `degraded`, `unconfigured`, or `error`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `HEADROOM_MCP_BIN` | *(unset)* | Absolute path to the `headroom` CLI. Unset means the MCP server stays disconnected and alias calls fail cleanly. |
| `HEADROOM_PROXY_URL` | `http://127.0.0.1:8787` | Headroom proxy root. |
| `HEADROOM_STATS_FILE` | `~/.headroom/session_stats.jsonl` | Shared session-stats file. |
| `HEADROOM_EXPECTED` | `true` | Whether the proxy is expected to be running. |
| `HEADROOM_SAVINGS_FLOOR_PCT` | `5.0` | Savings below this raise a warning. |
| `HEADROOM_REGRESSION_RATIO` | `0.6` | Ratio against the 7-day average that counts as a regression. |

Install the Headroom CLI and export `HEADROOM_MCP_BIN` to enable the pack:

```bash
uv tool install headroom
export HEADROOM_MCP_BIN="$(which headroom)"
```

## Installation

```bash
pip install lisa-pack-headroom
```

## Development

```bash
uv run ruff format --check lisa_packs/headroom
uv run ruff check lisa_packs/headroom
uv run mypy lisa_packs/headroom
uv run pytest -m "not live"
```

Run the checks from a sibling framework checkout so the shared
`[tool.ruff]` and `[tool.mypy]` settings apply; `pytest` runs from the pack
root so `pythonpath = ["."]` makes `lisa_packs.headroom` importable.

## License

MIT.
