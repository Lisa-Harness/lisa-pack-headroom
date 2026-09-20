# headroom mode — lane definitions

## incidents
Global alert lane. Critical Headroom findings (proxy down while expected)
arrive here as `headroom:finding:proxy-down` items with high salience.

## headroom_state
Per-turn snapshot lane. Findings from the cached assessment
(`savings-healthy`, `no-savings`, `savings-regressed`, `ccr-expiry`,
`mcp-overhead`, `proxy-up`) plus the raw signal item
(`state:signal:headroom`). Healthy findings carry salience 0.0 and are
admitted only when the `headroom` mode is active.

## history
Conversation history lane. Standard turn transcript.

## general
Everything else.