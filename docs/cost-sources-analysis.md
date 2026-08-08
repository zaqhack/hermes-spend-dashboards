# Cost Data Sources: state.db vs InfluxDB (Jul 25 2026)

Analysis done when zaq asked whether the WebUI sidebar could show "real" costs
from the new InfluxDB pipeline instead of `estimated_cost_usd`.

## The pipeline

- `log_shipper.py` (from Bright, `~/workspace/AI Usage Tracking/`) runs per-bot
  via user crontab every 5 min. Parses `~/.hermes/logs/agent.log`, computes
  per-call cost from cached OpenRouter pricing, writes InfluxDB line protocol.
- InfluxDB: `http://192.168.49.15:8086`, org `mejiro-stable`, bucket
  `system_metrics`, token `mejiro-bright-token-2025`.
- Measurement `llm_api_call`: tags `agent`, `session`, `model`, `provider`;
  fields `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_pct`,
  `latency_s`, `cost_usd`. The `session` tag is the exact state.db session ID
  (both hex IDs like `9b1dd53fa043` and dated IDs like `20260725_204228_c9645b`).
- Measurement `tool_call`: tags `agent`, `session`, `tool`.
- Grafana dashboard: http://192.168.49.15:3000/d/ai-usage-tracking
- Fleet backfill totals (Jul 25): 18,418 points, $266.05 across 7 bots
  (Shichi $112.72, Mayano $86.43, Satono $41.56, Dantsu $25.13, RobRoy $0.14,
  Suzuka $0.07).

## Query pattern (per-session cost sum)

```bash
curl -s "http://192.168.49.15:8086/api/v2/query?org=mejiro-stable" \
  -H "Authorization: Token mejiro-bright-token-2025" \
  -H "Content-Type: application/vnd.flux" -X POST \
  -d 'from(bucket: "system_metrics")
    |> range(start: -30d)
    |> filter(fn: (r) => r._measurement == "llm_api_call")
    |> filter(fn: (r) => r._field == "cost_usd")
    |> filter(fn: (r) => r.session == "<SESSION_ID>")
    |> sum(column: "_value")
    |> group()
    |> sum(column: "_value")'
# Returns CSV: ,_result,0,<total_cost>
```

## Verified comparison (session 9b1dd53fa043, webui, kimi-k3)

| Source | Parent | +6 subagent children | Total |
|--------|--------|---------------------|-------|
| state.db `estimated_cost_usd` | $5.86 | $0.64 | $6.50 |
| InfluxDB `llm_api_call` sum | $10.70 | $0.83 | $11.53 |

state.db underestimates ~1.8x. The shipper sees every API call logged
(retries, background, edge cases); the agent engine only records what it
tracks. Also note: subagent costs in state.db are populated correctly for
recent sessions (the old "children show $0.00" limitation is gone as of
Jul 2026).

## Why the sidebar stays on state.db

1. **Local SQLite, zero network.** Sidebar renders per page load; InfluxDB
   adds 50–200ms latency per refresh and breaks entirely if Bright is down.
2. **Caching collision.** `/api/sessions` is aggressively cached (5s TTL, 30s
   streaming). Costs would need a second cache layer refreshed on a different
   cadence.
3. **History.** state.db covers all sessions ever; InfluxDB only covers what
   the shipper found in `agent.log` (7-day log retention — older history is
   unrecoverable).
4. **Right tool for the job.** Precision accounting belongs in Grafana
   (per-call drill-down, spike hunting); the sidebar is an at-a-glance
   comparator.

If a "real cost" view is ever wanted in WebUI, do it as a per-session detail
panel (one Flux query on demand when a session is opened), never in the
sidebar list path.

## state.db schema notes (cost-relevant columns)

`sessions` table: `estimated_cost_usd REAL`, `actual_cost_usd REAL`,
`cost_status TEXT`, `cost_source TEXT`, `pricing_version TEXT`,
`api_call_count INTEGER`, `parent_session_id TEXT`, `source TEXT`
(`webui`/`subagent`/`cron`/`matrix`/`cli`). Child sessions link via
`parent_session_id`; `SUM(estimated_cost_usd) ... WHERE parent_session_id = X`
gives the subagent rollup.
