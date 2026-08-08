# Grafana WebUI Proxy

Fleet-wide solution for embedding Grafana dashboards in the Hermes WebUI popover,
with per-session cost drill-down.

## What this solves

1. **Grafana SPA can't serve from a subpath** — `serve_from_sub_path=true` would
   break direct LAN access. The nginx proxy rewrites `appSubUrl` in flight.
2. **WebUI CSP blocks iframes** — `frame-ancestors 'none'` is injected on every
   response. The WebUI skips CSP for `/grafana-proxy/*` paths.
3. **Session cost links** — click any session cost → popover opens with the
   AI Usage dashboard filtered to that session ID and time range.

## Architecture

```
Browser (any WebUI)
  → /grafana-proxy/* 
  → WebUI server.py (port 3001, full path forwarded, CSP skipped)
  → Bright nginx :3001 (subpath rewriting, referer-based asset forwarding)
  → Grafana :3000 (untouched, direct access still works)
```

## Repo layout

```
nginx/nginx.conf              # nginx proxy config (deploy to Bright)
webui-patches/                # extracted patches for WebUI files
scripts/deploy.py             # fleet deployment script
docs/OBSERVABILITY.md         # Bright's full observability stack docs
```

## Quick deploy

```bash
# Deploy to a single agent
python3 scripts/deploy.py mayano

# Deploy to all fleet agents
python3 scripts/deploy.py --all
```

## Post-upgrade restore (after `hermes update` / `git pull`)

```bash
# Verify what would change (no modifications):
python3 scripts/fleet_restore.py --all --verify-only

# Apply to one agent (test on Dantsu first — no active chat to drop):
python3 scripts/fleet_restore.py --host dantsu --restart

# Apply fleet-wide (remotes first, local shichi LAST):
python3 scripts/fleet_restore.py --all --restart
```

**Sanity check** (run on any agent):
```bash
grep -c 'keep full path for nginx\|Skip CSP injection' ~/hermes-webui/server.py
grep -c 'function _sessionGrafanaUrl' ~/hermes-webui/static/sessions.js
```
Any `0` means upstream regressed the patches — restore from this repo.

## Files changed per agent

| File | Change |
|------|--------|
| `server.py` | `grafana_port = 3001`, forward full `/grafana-proxy` path, skip CSP |
| `sessions.js` | `_sessionGrafanaUrl` uses proxy path + dashboard slug |
| `index.html` | Popover markup, floating button hidden, `openGrafanaPopover()` syncs tab link |

## Key files

- **nginx/nginx.conf** — copy to `~/workspace/grafana-proxy/nginx.conf` on Bright,
  then `docker restart grafana-proxy-nginx`
- **webui-patches/** — reference snippets showing exactly what changed in each file
- **docs/OBSERVABILITY.md** — full Bright observability stack documentation

## Token

All agents use the same Grafana service-account token at
`~/.hermes/secrets/grafana-proxy-token`. Viewer role, can't modify dashboards.
