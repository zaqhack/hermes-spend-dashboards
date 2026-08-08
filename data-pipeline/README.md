# data-pipeline — log shipping + InfluxDB

The cron pipeline that turns each bot's `agent.log` into per-API-call cost
data in InfluxDB, plus the per-bot sidecar DB the WebUI reads for "actual"
costs.

## Files

| File | Runs on | What it does |
|------|---------|--------------|
| `log_shipper.py` | each agent (cron `*/5m`) | Parse `~/.hermes/logs/agent.log`, compute per-call cost from cached OpenRouter pricing, write line protocol to InfluxDB. |
| `session_cost_db.py` | each agent (cron `*/15m`) | Build/maintain `~/.hermes/session_costs.db` (session → actual cost) by querying InfluxDB. |

## Configuration (edit at top of each file)

```python
INFLUX_URL    = "http://<obs-host>:8086"   # your InfluxDB
INFLUX_TOKEN  = "<write-token>"            # REPLACE_ME
INFLUX_ORG    = "mejiro-stable"
INFLUX_BUCKET = "system_metrics"
```

`log_shipper.py` also reads `OPENROUTER_API_KEY` from `~/.hermes/.env` to
refresh the model pricing cache (`~/.hermes/model_pricing_cache.json`,
auto-refreshes every 24h). `AGENT_NAME` env var overrides the hostname tag.

## Setup on an agent

```bash
mkdir -p ~/workspace ~/.hermes/logs
cp log_shipper.py session_cost_db.py ~/workspace/

# one-time: pricing cache + backfill the existing log
python3 ~/workspace/log_shipper.py --refresh-pricing
python3 ~/workspace/log_shipper.py --backfill

# crontab (use THIS bot's home dir — don't copy another bot's paths)
( crontab -l 2>/dev/null
  echo '*/5  * * * *  cd ~/workspace && python3 log_shipper.py   >> ~/workspace/logs/ai_log_shipper.log 2>&1'
  echo '*/15 * * * *  cd ~/workspace && python3 session_cost_db.py --sync >> ~/workspace/logs/session_cost_sync.log 2>&1'
) | crontab -
```

## What gets written

- **Measurement `llm_api_call`** — tags `agent`, `session`, `model`,
  `provider`; fields `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_pct`, `latency_s`, `cost_usd`.
- **Measurement `tool_call`** — tags `agent`, `session`, `tool`; fields
  `duration_s`, `output_chars`.

The `session` tag equals the Hermes `state.db` session ID — that's the join
key for the Grafana deep links and the sidecar DB.

## Useful flags

```bash
python3 log_shipper.py --dry-run          # parse, don't write
python3 log_shipper.py --backfill         # re-parse entire log (safe: InfluxDB dedups identical points)
python3 session_cost_db.py --sync         # sync last-24h sessions from InfluxDB
python3 session_cost_db.py --lookup <sid> # debug a single session cost
```

State file `~/.hermes/log_shipper_offset` tracks the byte offset; log rotation
is handled (offset resets to 0 when the file shrinks).

See `../docs/ai-usage-cost-tracking.md` for the full pipeline reference and
`../docs/actual-cost-sidecar.md` for the sidecar design.
