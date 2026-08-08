# hermes-spend-dashboards

Everything needed to stand up **spend / usage dashboards** for a fleet of
[Hermes Agent](https://hermes-agent.nousresearch.com) bots: per-session cost
in the WebUI sidebar, a Grafana popover for drill-down, Grafana dashboards,
and the InfluxDB data pipeline that feeds them.

> ⚠️ **Repo hygiene:** no real secrets are committed. Grafana admin creds,
> InfluxDB tokens, and the WebUI BasicAuth hash all appear as
> **`<placeholder>`s or documented `REPLACE_ME` values** — see
> [Secrets & configuration](#secrets--configuration). This repo is meant to be
> readable by another bot/operator; keep it that way.

---

## What's in the box

| Dir | Item | What it is |
|-----|------|------------|
| `webui-grafana-proxy/` | **① WebUI popover mods** | Patches + scripts + nginx config that embed Grafana in the Hermes WebUI popover with per-session cost drill-down. |
| `grafana/` | **② Grafana dashboard configs** | Importable dashboard JSON for the two existing dashboard "types" (per-session + fleet aggregate) + the full observability-stack doc. |
| `data-pipeline/` | **③ Data shipping + InfluxDB** | `log_shipper.py` + `session_cost_db.py` — the cron pipeline that parses agent logs, computes per-call cost, and ships it to InfluxDB. |
| `ripper-dashboard/` | **(bonus)** | A separate, self-contained host/service health dashboard (FastAPI + Caddy). Distinct from the spend system — see the note below. |
| `docs/` | **④ Setup & reference** | Deep-dive references: end-to-end pipeline, the actual-cost sidecar, and the state.db-vs-InfluxDB analysis. |

### The two "dashboards" (don't confuse them)

This repo grew out of a folder originally named `dashboard/`, which is why a
**host/service health dashboard** (`ripper-dashboard/`) shares space with the
**spend dashboards** the repo is named for. They are unrelated systems:

- **Spend dashboards** (this repo's focus): WebUI cost popover → Grafana →
  InfluxDB. Money in, money out.
- **Ripper dashboard** (`ripper-dashboard/`): a FastAPI page showing whether
  services/GPU/shares on one box are up. No Grafana, no InfluxDB.

Both are documented here so the whole `dashboard/` working tree is captured in
one place.

---

## Architecture (the spend system)

```
Hermes agent.log (per bot)
      │  log_shipper.py  (cron */5m)
      ▼
InfluxDB  (bucket: system_metrics)  ◄──┐
      │   measurements: llm_api_call, tool_call
      │  session_cost_db.py (cron */15m) → per-bot session_costs.db (sidecar)
      ▼
Grafana :3000  ── nginx subpath proxy :3001 ──►  Hermes WebUI  /grafana-proxy/*
   ai-usage-tracking                                sidebar cost  (green = actual)
   ai-fleet-aggregate                               click → popover drill-down
```

Data flows one way: logs → InfluxDB → Grafana. The WebUI reads the **sidecar
SQLite** (not InfluxDB directly) for the green "actual" cost, so the sidebar
never takes a network dependency.

---

## Quick start (for another bot)

You need: an **observability host** (runs InfluxDB + Grafana + nginx — here
"Bright", `192.168.49.15`) and one or more **agent hosts** (run Hermes Agent +
the WebUI).

### A. Observability host (InfluxDB + Grafana + nginx)

1. **InfluxDB 2.x** — create org/bucket/token:
   - org: `mejiro-stable`, bucket: `system_metrics`
   - admin/write token (used by `log_shipper.py`): set `INFLUX_TOKEN`
2. **Grafana** — add the InfluxDB datasource (Flux), note its **datasource
   UID**, then import the two dashboards from `grafana/dashboards/`
   (replace `${DS_INFLUXDB}` with your datasource UID — see
   `grafana/README.md`).
3. **nginx subpath proxy** — run `webui-grafana-proxy/nginx/nginx.conf` on
   port **3001** in front of Grafana. This is what makes the WebUI iframe
   work. Full guide: `grafana/docs/OBSERVABILITY.md`.

### B. Each agent host (Hermes + WebUI)

1. **Data shipping** — copy `data-pipeline/log_shipper.py` +
   `session_cost_db.py` to `~/workspace/`, set `INFLUX_*` at the top, install
   the two cron lines (`data-pipeline/README.md`), run one `--backfill`.
2. **WebUI popover mods** — apply the patches in `webui-grafana-proxy/`
   (canonical entry point: `scripts/fleet_restore.py` /
   `scripts/repatch_cost_popover.py`). See `webui-grafana-proxy/README.md`.
3. Put the Grafana **service-account token** at
   `~/.hermes/secrets/grafana-proxy-token` (Viewer role).

### C. Verify

```bash
# InfluxDB has data
curl -s "$INFLUX_URL/api/v2/query?org=$ORG" -H "Authorization: Token $INFLUX_TOKEN" \
  -H "Content-Type: application/vnd.flux" -d 'from(bucket:"system_metrics") |> range(start:-1d) |> filter(fn:(r)=>r._measurement=="llm_api_call") |> count()'
# nginx rewrites appSubUrl
curl -s http://<obs-host>:3001/grafana-proxy/api/frontend/settings | grep -o '"appSubUrl":"[^"]*"'
# WebUI patches present on an agent
grep -c 'keep full path for nginx\|Skip CSP injection' ~/hermes-webui/server.py
grep -c 'function _sessionGrafanaUrl' ~/hermes-webui/static/sessions.js
```

---

## Secrets & configuration

Replace these with your own values; **none are safe to commit**:

| Value | Where it lives | Where it's referenced |
|-------|----------------|-----------------------|
| `INFLUX_TOKEN` (InfluxDB write) | env / script constant | `data-pipeline/log_shipper.py`, `session_cost_db.py` |
| Grafana admin user/pass | your Grafana install | used only to export/import dashboards |
| Grafana service-account token | `~/.hermes/secrets/grafana-proxy-token` per agent | WebUI `_proxy_to_grafana` |
| InfluxDB datasource UID | Grafana → Connections | `${DS_INFLUXDB}` placeholder in `grafana/dashboards/*.json` |
| WebUI BasicAuth hash | `ripper-dashboard/caddy/Caddyfile` (not committed) | `ripper-dashboard/` only |

Hostnames/IPs (`192.168.49.15`, `ripper.sgt.local`, dashboard slugs) are
**examples from our fleet** — substitute your own.

---

## Documentation map

- **Start here:** this file.
- `grafana/docs/OBSERVABILITY.md` — the observability host end-to-end
  (InfluxDB, Grafana, nginx proxy, troubleshooting table).
- `webui-grafana-proxy/README.md` + `docs/WORKFLOW.md` — the WebUI patch
  layers, deploy, and the post-`hermes update` restore runbook.
- `data-pipeline/README.md` — log_shipper + session_cost_db setup.
- `docs/ai-usage-cost-tracking.md` — the whole cost pipeline reference.
- `docs/actual-cost-sidecar.md` — the green/gray actual-cost sidecar design.
- `docs/cost-sources-analysis.md` — why the sidebar reads state.db, not InfluxDB.
- `ripper-dashboard/README.md` + `PATCHING.md` — the host/service dashboard.

## Provenance

Authored on the **Shichi / Ripper** Hermes deployment (zaqhack's fleet),
Aug 2026. Dashboards exported live from Bright's Grafana; scripts are the
running production versions.
