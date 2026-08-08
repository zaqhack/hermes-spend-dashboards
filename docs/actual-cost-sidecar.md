# Actual Cost Sidecar DB (v4, Jul 26 2026)

Per-bot local cache mapping session IDs to real costs from InfluxDB. Lets the
WebUI sidebar show "real" (green) vs "estimated" (gray) costs without a network
dependency per sidebar refresh, and without touching state.db.

## Architecture

```
agent.log ──> log_shipper.py (cron */5min) ──> InfluxDB (Bright:8086)
                    │                                    │
                    │ inline sync after each write       │
                    ▼                                    ▼
              session_cost_db.py ──query──> session_costs.db (per-bot SQLite)
                    ▲ (cron */15min, --sync)             │
                    │                                    │ local read
              api/routes.py overlay ◄────────────────────┘
                    │
                    ▼
              sidebar: green = actual, gray = estimate
```

## Sidecar DB

- Path: `~/.hermes/session_costs.db`
- Schema: `session_costs(session_id TEXT PRIMARY KEY, cost_usd REAL, updated_at REAL)`
- Module: `~/workspace/session_cost_db.py`
  - `get_session_cost(sid) -> float|None`
  - `get_all_session_costs() -> dict`
  - `update_cost_db({sid: cost})` — INSERT OR REPLACE
  - `query_influx_for_sessions([sids]) -> {sid: cost}` — Flux query, 7d range,
    group by session, sum cost_usd
  - `sync_recent_sessions(hours=24)` — pulls recent session IDs from state.db
    (read-only), queries InfluxDB, updates sidecar
- CLI: `--sync` (cron), `--lookup <sid>` (debug)

## Sync triggers

1. `session_cost_db.py --sync` — cron every 15 min (separate crontab line from
   the shipper). Syncs sessions active in the last 24h.
2. `log_shipper.py` inline — after a successful InfluxDB write, extracts
   session IDs from the lines just written and syncs those. Wrapped in
   try/except; shipper never fails on sidecar errors.

## WebUI integration points

Backend (`api/routes.py`):
1. Add `"actual_cost",` to `_SIDEBAR_SESSION_RESPONSE_FIELDS` (right after
   `"estimated_cost",`).
2. In `_sidebar_session_response_item`, after the `attention` line: open
   `~/.hermes/session_costs.db` read-only, `SELECT cost_usd WHERE session_id
   = sid`, set `item["actual_cost"]`. Entire block in try/except — missing DB
   must never break the sidebar.

Frontend (`static/sessions.js`):
1. `_formatSessionCost` prefers `session.actual_cost` over
   `session.estimated_cost` when > 0.
2. `_isActualCost(session)` returns true when actual_cost > 0.
3. Parent row cost element: `costEl.classList.add('session-cost-actual')`
   when actual; title changes to "Actual cost: ...".
4. Child rows: `childLabelHTML(child)` returns
   `<span class="session-cost-actual">$X.XX</span> -> title - time` and is
   assigned via `mainBtn.innerHTML=` (textContent can't carry the class).
   `childLabelFor` is kept for plain-text callers.

CSS (`static/style.css`):
```css
.session-cost-actual{
  color:#4ade80 !important;
  opacity:1 !important;
}
```

## Deployment

`~/workspace/deploy_cost_tracking.py --all` on Shichi:

1. scp `session_cost_db.py` + updated `log_shipper.py` + pricing cache
2. Run initial `--sync` remotely
3. Rewrite crontab (both shipper */5 and sync */15 lines, paths per-bot)
4. Patch routes.py via scp'd `/tmp/patch_routes_sidebar.py` (see pitfall 9 —
   payload-as-file, not nested string literal)
5. scp updated `sessions.js` + `style.css`
6. `systemctl --user restart hermes-webui.service` remotely

Fleet list hardcoded: mayano, robroy, satono, suzuka, dantsu. Shichi local
done manually; user restarts local WebUI.

## Grafana dashboards

- Per-session drill-down: `/d/ai-usage-tracking` (session template var, all
  panels filtered, deep links use `&from=now-7d&to=now`)
- Fleet aggregate: `/d/ai-fleet-aggregate` — agent spending lines, hourly +
  daily stacked bars, per-agent stat tiles. No template vars.

Dashboard edits: `POST /api/dashboards/db` with full dashboard JSON,
`overwrite: true` for existing UIDs, service account token in Shichi mem0.

## Verified data notes

- state.db vs InfluxDB gap ~1.8x on one session ($5.86 vs $10.70): shipper
  counts every API call in the log; engine counts only tracked ones.
- InfluxDB silently deduplicates identical measurement+tags+timestamp writes
  (overwrite semantics) — backfill re-runs cannot inflate counts.
- Log rotation: shipper detects size shrink, resets offset to 0, re-parses
  new log. Old-log data stays in InfluxDB (7-day retention matches).
