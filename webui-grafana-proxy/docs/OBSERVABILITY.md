# Bright Observability Stack

Canonical documentation for Mejiro Bright's observability infrastructure.
**Host:** 192.168.49.15 (bright) · **Role:** Fleet observability + Grafana proxy

## Services Overview

| Service | Port | Container | Status | Purpose |
|---------|------|-----------|--------|---------|
| InfluxDB | 8086 | mejiro-influxdb | Running | Time-series DB for metrics + AI usage |
| Grafana | 3000 | mejiro-grafana | Running | Dashboards (admin UI, direct access) |
| **Grafana Proxy (nginx)** | **3001** | **grafana-proxy-nginx** | Running | **Subpath proxy for WebUI iframes** |
| Telegraf | — | mejiro-telegraf | ⚠️ Stopped | Local metrics collection (exited 3d ago) |

## Architecture

```
Fleet WebUIs (Shichi, Dantsu, etc.)
  └── /grafana-proxy/*  ─────────────────────────────┐
                                                      ▼
                                        Bright nginx :3001
                                        (grafana-proxy-nginx)
                                                      │
              ┌───────────────────────────────────────┤
              │ sub_filter rewrites                   │ referer-based
              │ appSubUrl → /grafana-proxy/           │ /api + /public forwarding
              ▼                                       ▼
        Grafana :3000 (mejiro-grafana)
              │
              ▼
        InfluxDB :8086 (mejiro-influxdb)
        ├── system_metrics bucket (Telegraf, llm_api_call, tool_call)
        └── hermes-agent-telemetry bucket
```

## docker-compose.yml

Located at `~/workspace/docker-compose.yml`. Defines:
- **influxdb**: influxdb:2.7, ports 8086:8086, volume `influxdb-data`
- **grafana**: grafana/grafana:latest, ports 3000:3000, volumes `grafana-data` + provisioning
- **telegraf**: telegraf:latest, host PID, privileged, local metrics

Admin credentials (also in compose file):
- InfluxDB: `bright` / `BrightWatch2025!`, org `mejiro-stable`, bucket `system_metrics`, admin token `mejiro-bright-token-2025`
- Grafana: `bright` / `BrightAdmin2025!`

## Grafana Proxy (nginx) — KEY COMPONENT

**This is what makes the WebUI Grafana popover work fleet-wide.**

- **Container:** `grafana-proxy-nginx` (nginx:1.27-bookworm)
- **Port:** 3001 (host) → 3001 (container)
- **Config:** `~/workspace/grafana-proxy/nginx.conf` (bind-mounted)
- **Network:** `workspace_observability` (shared with Grafana + InfluxDB)
- **Started manually** (not in docker-compose — see "Add to compose" below)

### Why it exists

Grafana's React SPA cannot serve from a subpath (`/grafana-proxy/`) without
`serve_from_sub_path=true` — which would break direct LAN access. The nginx
proxy solves this by:

1. **Rewriting `appSubUrl`** in `/api/frontend/settings` responses:
   `sub_filter '"appSubUrl":""' '"appSubUrl":"/grafana-proxy/"';`
   The SPA boots, reads `appSubUrl`, and routes correctly under the subpath.

2. **Rewriting `<base href>`** in HTML:
   `<base href="/">` → `<base href="/grafana-proxy/">`
   so relative asset URLs resolve through the proxy.

3. **Referer-based forwarding** for `/api/*` and `/public/*`:
   The SPA occasionally makes absolute-path requests that bypass the base href.
   nginx inspects the `Referer` header — if it contains `/grafana-proxy/`, the
   request is rewritten to `/grafana-proxy/api/...` and forwarded to Grafana.
   Without the referer (direct access), requests pass through unmodified.

4. **Forwarding `Authorization` header** so service-account tokens reach Grafana:
   ```nginx
   proxy_set_header Authorization $http_authorization;
   proxy_pass_request_headers on;
   ```

### Nginx container management

```bash
# Status
docker ps --filter name=grafana-proxy-nginx

# Logs (check for 404s from missed rewrites)
docker logs grafana-proxy-nginx --since 10m

# Reload after config change
docker restart grafana-proxy-nginx   # full restart (bind-mounted config is read at start)

# Start (if not running)
docker run -d \
  --name grafana-proxy-nginx \
  --restart unless-stopped \
  -p 3001:3001 \
  -v ~/workspace/grafana-proxy/nginx.conf:/etc/nginx/conf.d/default.conf:ro \
  --network workspace_observability \
  nginx:1.27-bookworm
```

### Config file: `~/workspace/grafana-proxy/nginx.conf`

Three `location` blocks:

| Location | Purpose |
|----------|---------|
| `/grafana-proxy/` | Main proxy: strip prefix → Grafana, sub_filter rewrites on responses |
| `/api/` | Catch absolute-path API calls from the SPA iframe (referer-gated rewrite) |
| `/public/` | Catch absolute-path asset calls (referer-gated rewrite) |
| `/health` | Simple 200 OK healthcheck |

The referer gate (`if ($http_referer ~* "^https?://[^/]+/grafana-proxy/")`) ensures
the `/api/` and `/public/` blocks only rewrite requests that came from the iframe.
Direct API/asset calls to nginx:3001 pass through unmodified.

### Per-agent WebUI changes (companion to this proxy)

For the popover to work on a given agent's WebUI:

1. **`server.py`**:
   - `grafana_port = 3001` (not 3000 — talk to nginx, not Grafana directly)
   - `_proxy_to_grafana()` must **not strip** `/grafana-proxy` prefix (nginx
     needs the full path to match its `location /grafana-proxy/` block)
   - `end_headers()` must skip WebUI CSP injection for `/grafana-proxy/`,
     `/public/`, `/apis/`, `/avatar/` paths — otherwise `frame-ancestors 'none'`
     in the WebUI's CSP blocks the iframe

2. **`static/sessions.js`** (`_sessionGrafanaUrl`):
   ```js
   const base = '/grafana-proxy/d/ai-usage-tracking/f09f908e-ai-usage-and-cost-tracking'
                + '/?var-session=' + encodeURIComponent(sid);
   ```
   URL includes the dashboard slug (`f09f908e-...`) so Grafana's SPA router
   matches the canonical route. Time range is bounded to the session's lifespan
   (5min pad each side), falling back to `now-7d` (matches log retention).

3. **Click handler**: cost element calls `window.openGrafanaPopover(url)`
   instead of `window.open(url, '_blank')`. Popover function also updates the
   "open in new tab" ↗ link's href to the current session URL.

4. **Service account token** at `~/.hermes/secrets/grafana-proxy-token`
   (same token across all agents; scoped Viewer, can't modify dashboards).

### Token rotation

If the grafana-proxy token needs rotation:
1. Generate new SA token in Grafana (Admin → Service Accounts → `grafana-proxy`)
2. Update `~/.hermes/secrets/grafana-proxy-token` on every agent (or via Ansible)
3. No WebUI restart needed — token is read per-request

## Grafana

- **URL (direct/admin):** http://192.168.49.15:3000
- **URL (via any WebUI):** https://{agent}.sgt.cc:{port}/grafana-proxy/
- **Dashboards:**
  - `ai-usage-tracking` (uid) — 🐎 AI Usage & Cost Tracking, per-session drill-down
  - `ai-fleet-aggregate` (uid) — 🐎 AI Fleet Aggregate Spending, cross-agent comparison
  - Plus mejiro-stable fleet dashboards (per-machine + ops-center)
- **MCP:** `mcp-grafana` running as uvx tool, token at `~/workspace/grafana_mcp_token`
- **Backups:** `~/workspace/grafana-dashboard-backups/` (dashboard JSON exports)

## InfluxDB

- **URL:** http://192.168.49.15:8086
- **Org:** `mejiro-stable`
- **Buckets:**
  - `system_metrics` — Telegraf host metrics + `llm_api_call` + `tool_call`
    measurements (AI usage tracking). Tagged by `agent`, `session`, `model`,
    `provider`.
  - `hermes-agent-telemetry` — Hermes agent operational metrics
- **Writers:**
  - Telegraf (local + fleet remote) → `system_metrics`
  - `~/workspace/log_shipper.py` cron every 5min → parses `~/.hermes/logs/agent.log`
    on each bot, ships per-API-call costs to `system_metrics` (measurement
    `llm_api_call`)
  - `~/workspace/session_cost_db.py` cron every 15min → builds per-bot sidecar
    SQLite of session costs

## Telegraf (⚠️ currently stopped)

Local container `mejiro-telegraf` exited 3 days ago (cleanly, exit 0).
Config at `~/workspace/telegraf/telegraf.conf`. To restart:
```bash
cd ~/workspace && docker compose up -d telegraf
```

Fleet-wide Telegraf runs as systemd USER services on each agent
(see `~/ansible/playbooks/standardize-telegraf.yml` on Shichi).

## Crontab

```
*/5 * * * *  log_shipper.py        # AI usage → InfluxDB
*/15 * * * * session_cost_db.py    # Sidecar cost DB sync
```

## Common operations

**Restart the whole stack:**
```bash
cd ~/workspace && docker compose restart          # InfluxDB + Grafana + Telegraf
docker restart grafana-proxy-nginx                 # nginx (not in compose yet)
```

**Check what the WebUI proxy sees:**
```bash
TOKEN=$(cat ~/.hermes/secrets/grafana-proxy-token)
curl -s -H "Authorization: Bearer $TOKEN" \
  http://192.168.49.15:3001/grafana-proxy/api/frontend/settings | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print('appSubUrl:', d['appSubUrl'])"
# Should print: appSubUrl: /grafana-proxy/
```

**Add nginx to docker-compose** (recommended — currently started manually):
Add this service block to `~/workspace/docker-compose.yml`:
```yaml
  grafana-proxy:
    image: nginx:1.27-bookworm
    container_name: grafana-proxy-nginx
    restart: unless-stopped
    ports:
      - "3001:3001"
    volumes:
      - ./grafana-proxy/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    networks:
      - observability
```
Then `docker compose up -d grafana-proxy`. (Remove the manually-started
container first: `docker rm -f grafana-proxy-nginx`.)

## Troubleshooting

| Symptom | Likely cause | Check |
|---------|--------------|-------|
| Popover shows Grafana "Page not found" | `appSubUrl` not rewritten | `curl .../api/frontend/settings` via :3001 — should show `/grafana-proxy/` |
| Popover shows nginx 404 | WebUI stripping prefix before forwarding | Agent's `server.py`: `subpath = path` (not `path[len('/grafana-proxy'):]`) |
| Popover blank, browser console shows CSP frame-ancestors error | WebUI injecting its own CSP | Agent's `server.py end_headers()` must skip `/grafana-proxy/` paths |
| Popover loads but assets 404 in nginx log | Referer gate not matching | `docker logs grafana-proxy-nginx` — look for `/api/` or `/public/` 404s |
| "Unauthorized" in popover | Token not forwarded | nginx config must have `proxy_set_header Authorization $http_authorization;` |
| Dashboard loads but no data | Wrong time window | Deep links must include `&from=now-7d&to=now` (or session lifespan) |

---
*Last updated: Jul 28 2026 — after nginx subpath proxy deployment*
