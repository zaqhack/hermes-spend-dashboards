# AI Usage Cost Tracking Pipeline (Jul 25 2026)

End-to-end reference for the fleet cost-tracking system that the WebUI cost
column links into. Built from Bright's log shipper + this session's Grafana
deep-link work.

## Components

1. **log_shipper.py** — per-bot cron (`*/5 * * * *`), parses
   `~/.hermes/logs/agent.log`, computes per-API-call cost via cached OpenRouter
   pricing, ships line protocol to InfluxDB on Bright
   (`192.168.49.15:8086`, org `mejiro-stable`, bucket `system_metrics`,
   token `mejiro-bright-token-2025`). Measurements: `llm_api_call` (fields:
   `cost_usd`, tokens, latency; tags: `agent`, `session`, `model`, `provider`)
   and `tool_call`. Session tags match state.db session IDs exactly.
   State file `~/.hermes/log_shipper_offset` (byte offset; backfill with
   `--backfill`, resets offset to end of file). Pricing cache
   `~/.hermes/model_pricing_cache.json` auto-refreshes every 24h if the bot has
   `OPENROUTER_API_KEY` in `~/.hermes/.env`.

2. **InfluxDB → Grafana** — dashboard UID `ai-usage-tracking` on
   `http://192.168.49.15:3000`. Panels: total-cost/call/token stats (IDs 1–4),
   Cost Over Time (10), Cost by Session (11), Input vs Output (20), Cache Hit
   % (21), Per-Call Cost spikes (30), Tool Calls (40).

3. **Session template variable** — added Jul 25 2026 via Grafana API. Textbox
   var `session`; ALL 10 panels have the filter line inserted right after the
   measurement filter (initially only panels 11+30 got it — stat widgets kept
   showing fleet-wide totals even with a session selected; fixed in dashboard
   v4):
   ```
   |> filter(fn: (r) => r.session == "$session" or "$session" == "")
   ```
   Empty value = unfiltered (dashboard works as before). URL:
   `http://192.168.49.15:3000/d/ai-usage-tracking/?var-session=<session_id>&from=now-7d&to=now`.
   The `&from=now-7d&to=now` is REQUIRED — see time-window pitfall below.

4. **WebUI deep link (v3 cost patch)** — `_sessionGrafanaUrl(session)` in
   `sessions.js` builds that URL from `session.session_id || session.id`;
   the sidebar cost element is an `<a target="_blank" rel="noopener">`.

## Fleet deployment

- `~/workspace/deploy_log_shipper.sh <ip> <user> <home>` — copies shipper +
  pricing cache from Bright, mkdirs logs dir, runs backfill, installs crontab.
  Backfill totals at deploy: 18,418 points, $266.05 across 6 bots.
- `~/workspace/patch-cost-grafana-link.py` — `--all` / `--remote <bot>` /
  local. Applies the anchor-element + hover-CSS change; restarts remote
  WebUIs; local restart is manual (kills the agent's own session).
- Fleet roster hardcoded in both scripts: mayano .17, robroy .18, satono .21,
  suzuka .23, dantsu .24. Shichi=local (ripper .20), Bright=already done.

## Pitfalls

- **Bright's original crontab template hardcoded `/home/bright`** — every bot's
  entry must use its own home dir. The deploy script handles this; don't
  copy-paste the template verbatim.
- **Dashboard updates via Grafana API**: fetch full dashboard JSON
  (`GET /api/dashboards/uid/ai-usage-tracking`), mutate, POST back to
  `/api/dashboards/db` with `{"dashboard": ..., "overwrite": true}`. Query
  strings inside panel targets contain real `\n` newlines — string replacement
  must match the exact format or silently no-ops (**verify after POST** by
  re-fetching and grepping the target query; a silent no-op on panel 30 was
  caught this way during the v3 deploy).
- **Service-account token for the Grafana API**: mesh-mcp-bot admin token in
  mem0 (glsa_...). Not stored in Shichi's .env.
- **state.db vs InfluxDB numbers differ** (state.db ~1.8x lower): state.db is
  engine-estimated, InfluxDB counts every logged call. Sidebar shows state.db
  (fast/local); the link jumps to InfluxDB for the precise figure. This is a
  feature, not a bug — don't try to reconcile them in the UI.
- **🚨 Sliding Grafana time window makes numbers "change" between visits.**
  Dashboard defaults to "Last 12 hours"; as the window slides, older calls
  age out and Total Cost / API Calls shrink ($18.43→$10.70, 507→181 calls in
  30 min, observed by zaq Jul 26). Data was never lost — the viewport moved.
  All session deep links MUST carry `&from=now-7d&to=now` (log retention is
  7 days). Diagnose before assuming loss: count points per session directly
  in InfluxDB and compare across ranges.
- **InfluxDB silently deduplicates identical points** (same measurement +
  tags + timestamp, overwrite semantics — verified empirically by writing the
  same point twice; count stayed 1). A re-run backfill after log rotation can
  never double-count, so duplicate inflation can be ruled out immediately
  when totals look wrong.
- **Log rotation is handled** — shipper detects file smaller than offset,
  restarts from 0 (`Log file rotated (was X, now Y)` in
  `~/workspace/logs/ai_log_shipper.log`). Rotated logs (`agent.log.1`) may be
  flagged "binary file matches" by grep — use `strings agent.log.1 | grep`
  when searching old logs.
