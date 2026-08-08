# Patching the Dashboard

How to change the dashboard, deploy it, verify it, and where each mod has
landed. This is the operational companion to `README.md`.

---

## 1 · The deploy model (know this first)

| What you edit | Deploy action |
|---------------|---------------|
| `static/index.html` | **Nothing** — Caddy bind-mounts `static/` → `/srv`. Save the file, reload the browser tab. |
| `health.py` | `systemctl --user restart container-dashboard.service` (file is bind-mounted, so a restart is enough — **no image rebuild**) |
| `service-status.py` | `systemctl --user restart systemd-status.service` |
| `caddy/Caddyfile` | `podman restart caddy` |
| Anything that changes the image itself (`Containerfile`, package deps) | rebuild: `cd /home/shichi/containers/dashboard && podman build -t localhost/dashboard:latest .` then restart the unit |

> Because everything is bind-mounted, **a live cluster mod is just: edit →
> restart the right unit → verify**. No rebuilds in the normal drift.

---

## 2 · The standard patch loop

1. **Edit** the file (`health.py` and `index.html` are the usual suspects).
2. **Sanity-check the Python** (a `False` vs `false` typo crashes the whole
   `/api/health` — `SERVICES` is a Python list, not JSON):
   ```bash
   python3 -c "import ast; ast.parse(open('/home/shichi/dashboard/health.py').read())"
   ```
3. **Restart** only what changed (see table above).
4. **Verify** the wall it feeds — don't skip this:
   ```bash
   curl -s localhost:9000/api/health -o /tmp/h.json && python3 -c "import json;d=json.load(open('/tmp/h.json'));print(len(d['services']),'services');[print(s['name'],s['type_healthy'],s['port_healthy'],s['suspended']) for s in d['services']]"
   curl -s localhost:9000/api/stats -o /tmp/s.json && python3 -c "import json;d=json.load(open('/tmp/s.json'));print('uptime',d['uptime'],'cpu',d['cpu_pct'],'ram',d['ram']['pct'],'gpus',[g['name'] for g in d['gpus']])"
   curl -s localhost:9000/api/shares | python3 -m json.tool
   ```
   Then eyeball `http://localhost:80` (BasicAuth) for layout.
   *(Don't pipe `curl | python3` — the tirith security scanner blocks that
   pattern. Write to a temp file first, as above.)*

---

## 3 · Common recipes

### Add / remove a service
Edit the `SERVICES` list in `health.py`. Entry shape:
```python
{"name": "Display Name", "type": "systemd|container|process",
 "service": "unit.service",          # for type=systemd
 "container": "podman-name",          # for type=container
 "process": "pgrep-pattern",          # for type=process
 "port": 1234, "path": "/health",     # optional port check
 "auth": ("user", "pass")            # optional BasicAuth for the port check
 "host": "192.168.49.17", "remote": True}   # service on another LAN box
```
Rules:
- **Sort alphabetically by `name`.** One entry per service.
- Omit `port` for services with no HTTP endpoint (type-check only).
- `remote: True` requires `host` + `port` — skips the local systemd/container
  check (can't reach it) and relies on the HTTP port check only.
- `accept_4xx: True` for POST-only endpoints (Hermes gateways return 405 to
  our GET — that's *healthy*).
- Redis is special-cased to PING (port 6379).
Then restart + verify (Section 2).

### Add / remove a Samba share
1. Edit `SAMBA_SHARES` in `health.py` (`name`, `path`, `comment`).
2. Add the matching `-v /path:/path:ro` to the dashboard `podman run`, the
   systemd unit, and `containers/dashboard/docker-compose.yml`.
3. Restart the dashboard container. Verify `/api/shares` shows the new share
   and `accessible: true`.

### Suspend a service (no code change)
Suspended services are driven by `/opt/state.conf` (single source of truth),
not by the dashboard code:
```bash
# Suspend  (uses the DISPLAY name, e.g. "Qwen 27B")
curl -X POST localhost:9000/api/suspended -H 'Content-Type: application/json' \
  -d '{"name":"Qwen 27B","suspended":true}'
# Unsuspend: {"name":"Qwen 27B","suspended":false}
```
Display-name → state-key mapping lives in `_STATE_KEY_TO_DISPLAY` in
`health.py`. The health-check cron jobs (`00d02c5f7d24` 2h, `7026dcd519ce`
weekly) read the same file and actively **stop** suspended services, so this is
the only place you toggle them.

---

## 4 · Change log (known mods)

Reconstructed from the skill + session history. Roughly chronological:

- **2026-05-20** — Initial build: FastAPI health checker + Caddy :80 +
  3-col layout, system stats sidebar, `/api/health` & `/api/stats`,
  `ip_unprivileged_port_start=80`, local registry.
- **May 2026** — `/api/stats` rewritten to parse `/proc/uptime`,
  `/proc/stat`, `/proc/meminfo`, `/sys/class/hwmon` (k10temp + x53 coolant),
  with GPU via the `:9100` bridge (`service-status.py` `/gpu-stats`) — the
  container has no nvidia-smi.
- **May 2026** — Recursive-loop guard (`_in_flight` lock + 25 s cache) so
  Caddy→dashboard→Caddy recursion can't storm every service. 5 s check
  timeout, `limit=20, limit_per_host=5` pool. BasicAuth passthrough
  (`auth=("zaq","zaqhack")`) on the Caddy health entry.
- **Jun 14 2026** — Suspended-service system migrated from passive
  `suspended_services.json` to active enforcement via `/opt/state.conf`.
  Added `GET/POST /api/suspended`.
- **Jul 2026** — `/api/shares` + Samba Shares section on the page (needs the
  share paths bind-mounted into the container).
- **Jul 2026** — Layout tightened to `260px 85px 70px` columns, sidebar 155px,
  container max-width 500px; port numbers hugging tight to the name column.
- **Jul 2026** — Uptime reformatted to compact `#d #h #m`.
- **Jul 2026** — GPU 1 (RTX 3060) and BGE-M3 removed from the dashboard
  (GPU moved to Mayano); only `gpu0-block` remains in the HTML.
- **Jul 2026** — Matrix Synapse removed (fleet went webhook-only).
- **Jul 2026** — Remote mesh services added (Mayano, RobRoy/Satono/Suzuka
  listeners + gateways) using `host` + `remote`.
- **Aug 2026** — Memory Proxy unit name corrected (`memory-proxy.service`,
  not `container-memory-proxy.service`) to fix a perpetual type-healthy=false.
- **Aug 2026** — Post-reboot stale-cache pitfall documented (future
  `checked_at` from a clock step makes everything look down; fix = restart
  the dashboard container).

---

## 5 · What needs to happen next (open items)

- [ ] **Multi-box dashboard tabs** — assessed as feasible, **not built yet**.
      The plan: a tabbed status page so other fleet boxes (Mayano, etc.) get
      their own view without a new app.
- [ ] **Reconcile drift between the skill refs and the code.** Three
      overlapping refs (`dashboard-caddy.md`, `dashboard-caddy-setup.md`,
      `dashboard-service-monitoring.md`) disagree with each other and with the
      current code (stale "20 services", "both GPUs", JS port filters that no
      longer exist — the registry was cleaned and the filters deleted).
      This repo's README/PATCHING are the code-accurate source.
- [ ] **Verify remote/`remote: true` entries against the live fleet.** The
      `SERVICES` list still carries Disque Gateway/Listener and Mayano
      Gateway/Listener; confirm each DHCP-reserved host is current before the
      next cleanup pass.
- [ ] **Decide the fate of the stale `Dockerfile`** in this dir (py3.11, not
      used — real image comes from `containers/dashboard/Containerfile`).

---

## 6 · GitHub push notes

- **Never commit the running `zaq` / `zaqhack` BasicAuth into a public repo.**
  The Caddyfile contains the bcrypt hash and `health.py` carries the
  `("zaq","zaqhack")` tuple for the health check. For a public push these
  should move to env vars / a `.env` excluded by `.gitignore`; for a **private**
  repo the current values are acceptable.
- Recommended structure if made into a public project:
  ```
  ripper-dashboard/
    README.md            ← this repo's README
    PATCHING.md          ← this file
    health.py
    static/index.html
    service-status.py
    caddy/Caddyfile      ← with secrets removed / env-var'd
    containers/dashboard/
  ```

---

## 7 · Related

- `README.md` (this repo) — architecture, files, endpoints, design spec.
- Hermes skill `ripper-server` — full operational knowledge and pitfalls.
  `references/dashboard-caddy.md`, `dashboard-caddy-setup.md`,
  `dashboard-service-monitoring.md`, `service-registry.md`,
  `suspended-services.md`.
- `ripper.md` at `/home/shichi/ripper.md` — host-level service table.
