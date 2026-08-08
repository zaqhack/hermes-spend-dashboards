# grafana — dashboard configs

Importable Grafana dashboard JSON for the two existing spend-dashboard
"types", plus the full observability-stack doc.

## Files

| Path | What |
|------|------|
| `dashboards/ai-usage-tracking.json` | **Per-session drill-down.** Cost over time, cost by session, token in/out, cache-hit %, per-call spikes, tool calls. Has a `session` template variable wired into every panel. |
| `dashboards/ai-fleet-aggregate.json` | **Fleet aggregate.** Agent-vs-agent spend lines, hourly + daily stacked bars, per-agent cost/call stat tiles. No template vars. |
| `docs/OBSERVABILITY.md` | The Bright observability stack end-to-end: InfluxDB, Grafana, the nginx subpath proxy, crontab, troubleshooting. **Read this first.** |

## Prereqs

1. **InfluxDB datasource** in Grafana (type `influxdb`, query language
   **Flux**, org `mejiro-stable`, bucket `system_metrics`, token = your write
   token). Note the datasource's **UID** (Grafana → Connections → Data
   sources → click it → the UID is in the URL).

2. **Data flowing** — see `../data-pipeline/`.

## Import

The JSON uses a placeholder for the datasource UID: `${DS_INFLUXDB}`.

**Option A — replace then paste (UI):**
```bash
sed 's/\${DS_INFLUXDB}/<your-datasource-uid>/g' dashboards/ai-usage-tracking.json
```
then Grafana → Dashboards → New → Import → paste.

**Option B — API (provisioning-style):**
```bash
for f in dashboards/*.json; do
  jq --arg ds "<your-datasource-uid>" '
    (.. | objects | select(has("uid") and .uid=="${DS_INFLUXDB}") | .uid) = $ds
  ' "$f" > /tmp/dash.json
  curl -s -X POST "http://<grafana>/api/dashboards/db" \
    -H "Authorization: Bearer <service-account-token>" \
    -H "Content-Type: application/json" \
    -d "{\"dashboard\": $(cat /tmp/dash.json), \"overwrite\": true}"
done
```

## The `session` template variable (important)

`ai-usage-tracking` filters by a `session` textbox variable. For it to work,
**every panel query** must contain:

```
|> filter(fn: (r) => r.session == "$session" or "$session" == "")
```

The exported JSON already has this in all panels. The `or "$session" == ""`
clause keeps the dashboard usable when no session is selected.

## Deep-link contract

The WebUI builds drill-down URLs like:

```
/d/ai-usage-tracking/<slug>/?var-session=<session_id>&from=now-7d&to=now
```

The `&from=now-7d&to=now` is **required** — the default "last 12 hours"
window slides and makes totals appear to shrink (the data didn't change; the
viewport did). Log retention is 7 days, so `now-7d` is the correct bound. See
`../docs/ai-usage-cost-tracking.md`.
