# Ripper Service Dashboard

The fleet health + system stats page for Ripper (192.168.49.20). Served at
`http://ripper` / `http://192.168.49.20` (port 80) behind Caddy BasicAuth.

Dark, clean, **3-column** list of every service (name | [type] | :port) plus a
system sidebar (uptime, CPU, temps, RAM, GPU) and a Samba shares section.

> Operative documentation (deep-dive, pitfalls, procedures) also lives in the
> Hermes skill `ripper-server` (`references/dashboard-*.md`). This README is the
> co-located, code-accurate version. If they disagree, **this file and the code
> win**.

---

## Architecture

```
Browser (BasicAuth zaqhack / <secret>)
   │
   ▼
Caddy  :80  ──────────── container-caddy.service (podman, --network host)
   │  ├─ /            → static HTML from /srv  (bind-mount of static/ — INSTANT edits)
   │  └─ /api/*       → reverse_proxy localhost:9000
   ▼
FastAPI  :9000  ─────── container-dashboard.service (podman, --network host)
   │  health.py
   │  ├─ GET /api/health      → concurrent checks against every SERVICE entry
   │  ├─ GET /api/stats       → live system stats (procfs / hwmon + GPU via :9100)
   │  ├─ GET /api/shares      → Samba share usage (os.statvfs on bind-mounted paths)
   │  └─ GET /api/suspended   → read/toggle suspended services in /opt/state.conf
   │
   └─ HTTP → :9100  ─── host `systemd-status.service` (service-status.py)
                          ├─ /check-systemd?service=<unit>[&user=true|false]
                          ├─ /check-container?name=<ctr>
                          ├─ /check-process?name=<proc>
                          └─ /gpu-stats   → nvidia-smi (the container has NO nvidia-smi)
```

**Why the :9100 bridge exists:** the dashboard container image has no
`systemctl`, `podman`, or `nvidia-smi` on it. The host runs
`service-status.py` on :9100 (a stdlib `http.server`, no dependencies) to
answer those queries. The container uses `--network host`, so
`127.0.0.1:9100` from inside the container IS the host.

---

## Files

| Path | Purpose | Runtime |
|------|---------|---------|
| `health.py` | FastAPI app — `/api/health`, `/api/stats`, `/api/shares`, `/api/suspended`, static mount | container (bind-mounted) |
| `static/index.html` | Frontend — 3-column rows, sidebar, Samba bars, suspend hint, 30s refresh | Caddy `/srv` (bind-mounted) |
| `service-status.py` | :9100 host bridge — systemd/container/process/gpu checks | `systemd-status.service` |
| `systemd-status.py` | Older alias/generator for the :9100 server (superseded by `service-status.py`) | host |
| `gpu-temp-status.py` | GPU temp/clock probe (feeds `gpu-temp-manager.service`) | host |
| `suspended_services.json` | **Legacy** (pre-Jun 14 2026) passive suspend list — superseded by `/opt/state.conf` | unused |
| `Dockerfile` | **Stale** (py3.11, has COPY) — the image is actually built from `containers/dashboard/Containerfile` (py3.12). Kept only for reference. | unused |
| `../caddy/Caddyfile` | :80 reverse proxy + BasicAuth + API passthrough | `container-caddy.service` |
| `../containers/dashboard/Containerfile` + `docker-compose.yml` | The *real* image def + reference compose | build/ref |

---

## API

| Endpoint | Returns | Notes |
|----------|---------|-------|
| `GET /api/health` | `{services:[{name,type,type_healthy,port,port_healthy,suspended}], checked_at}` | Cache 25s; `_in_flight` guard prevents Caddy→dashboard recursion |
| `GET /api/stats` | `{uptime, cpu_pct, cpu_temp_c, coolant_temp_c, ram:{...}, gpus:[...], updated_at}` | Scheduled via `/proc/*`, `/sys/class/hwmon`, :9100/gpu-stats |
| `GET /api/shares` | `{shares:[{name,path,comment,total_bytes,used_bytes,free_bytes,used_pct,accessible}]}` | Requires share paths bind-mounted into the container |
| `GET /api/suspended` | `{suspended:[names]}` | Read-only listing |
| `POST /api/suspended` | toggles `{"name":"Qwen 27B","suspended":true}` in `/opt/state.conf` | Uses **display name**, maps to state-file key |

---

## Design spec (user preferences — keep these)

- **Grid columns `260px 85px 70px`** — name | [type] | :port. No `1fr` flex-grow.
- **Service names always white** — `color: var(--text)`; no blue/purple visited
  styling on the `<a class="service-row">` links (they open in a new tab).
- **`[type]` colored** — blue running, red down, yellow `[suspended]`.
- **`:port` colored** — blue ok, red fail, gray `—` when no port.
- **Sidebar 155px**, compact monospace numerics. Uptime formatted `#d #h #m`
  (no "day/hour" words) — backend does the formatting in `health.py`.
- GPU blocks render only for IDs present in the HTML; Rippers has **one** GPU
  block (`gpu0-block`). If a GPU's block is absent, the JS just skips it.
- **No standalone status dot.** Color IS the status.
- Frontend polls `/api/health`, `/api/stats`, `/api/shares` every **30 s**,
  with a `?t=Date.now()` cache-buster.

---

## Deploy / run

Everything is containerized under the `shichi` user, `--network host`.

```bash
# Dashboard API (health.py is bind-mounted — restart to pick up Python changes)
systemctl --user restart container-dashboard.service

# Static HTML — NO restart needed (Caddy bind-mounts static/ → /srv). Just save.
# Caddy config / routing
podman restart caddy            # or: systemctl --user restart container-caddy.service

# :9100 host bridge
systemctl --user restart systemd-status.service
```

Units (all under `~/.config/systemd/user/`, enabled, auto-start on boot):

- `container-dashboard.service` — Type=oneshot `podman run` of `localhost/dashboard:latest`
- `container-caddy.service` — direct `podman run` of `docker.io/caddy:latest`
- `systemd-status.service` — runs `service-status.py` on :9100

**Dashboard run command** (from the skill — matches the unit):

```bash
podman run -d --name dashboard --network host --restart unless-stopped \
  -v /home/shichi/dashboard/health.py:/app/health.py:ro \
  -v /home/shichi/dashboard/static:/app/static:ro \
  -v /opt/state.conf:/opt/state.conf:ro \
  -v /ai/shared:/ai/shared:ro \
  -v /bluepool:/bluepool:ro \
  localhost/dashboard:latest
```

The `/ai/shared`, `/bluepool` (and `/ai/images` in the reference compose)
mounts exist so `/api/shares` can `statvfs()` them. Without a mount the share
reports `accessible: false`.

Port 80 rootless binding required the kernel tweak (already persisted):
`net.ipv4.ip_unprivileged_port_start=80` in `/etc/sysctl.d/90-ripper-ports.conf`.

---

## How to talk to it

```bash
# Public (through Caddy, BasicAuth):
curl -u zaq:zaqhack  http://localhost:80/api/health
# Direct (dashboard container, no auth):
curl localhost:9000/api/health
curl localhost:9100/gpu-stats
```

See `PATCHING.md` for how to change things, the change log, and what's next.
