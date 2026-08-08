# WebUI Post-Update Restore Workflow

**Run this after every `hermes update` / `git pull` on any WebUI agent.**
Upstream updates overwrite the files our local customizations patch. This
workflow re-applies them safely and regression-tests the result.

## Architecture: the nginx subpath proxy (Jul 28 2026)

The Grafana popover works because of a **three-layer proxy chain**:

```
Browser → WebUI (Python) → nginx (:3001) → Grafana (:3000)
```

- **WebUI (Python)** — forwards `/grafana-proxy/*` to nginx **without stripping the prefix**
- **nginx (:3001)** — strips `/grafana-proxy/` via `proxy_pass`, rewrites responses:
  - `appSubUrl` in `/api/frontend/settings` → `/grafana-proxy/`
  - `<base href="/">` → `<base href="/grafana-proxy/">`
  - `/api/*` and `/public/*` requests with a `/grafana-proxy/` referer are rewritten back
- **Grafana (:3000)** — untouched, direct LAN access still works

**Why nginx?** Grafana's SPA reads `appSubUrl` from `/api/frontend/settings` to route.
Without it set, the SPA sees `/grafana-proxy/d/...` and 404s. nginx's `sub_filter`
rewrites that one JSON response so the SPA boots correctly. Python's `http.client`
can't do streaming response rewriting.

**Key files:**
- `nginx/grafana-proxy.conf` — the nginx config (deployed to Bright, container `grafana-proxy-nginx`)
- `scripts/deploy_full_proxy.py` — deploys nginx + patches WebUI proxy port + path forwarding

## The two customization layers

| Layer | What | Files touched | Canonical repo |
|-------|------|---------------|----------------|
| **Cost popover** | Sidebar session cost → green actual + Grafana popover drill-down | `server.py`, `static/sessions.js`, `api/routes.py`, `static/style.css`, `static/index.html` | `~/workspace/grafana-webui-proxy/` |
| **Theme/skin** | Per-agent color theme + avatar + favicon | `static/style.css`, `static/boot.js`, `api/config.py` | `~/workspace/fleet-themes/` |

## Quick start

```bash
# On Shichi (holds both canonical repos). Verify-only first — see what would change:
python3 ~/workspace/grafana-webui-proxy/scripts/fleet_restore.py --all --verify-only

# Apply to one agent (test on Dantsu first — it has no active chat to drop):
python3 ~/workspace/grafana-webui-proxy/scripts/fleet_restore.py --host dantsu --restart

# Apply fleet-wide (remotes patched first, local shichi LAST):
python3 ~/workspace/grafana-webui-proxy/scripts/fleet_restore.py --all --restart
```

## Post-upgrade checklist (what to restore)

After any `hermes update` / `git pull` on a WebUI agent, verify these markers:

| Check | File | Marker | If missing |
|-------|------|--------|-----------|
| nginx proxy port | `server.py` | `grafana_port = 3001` | Run `deploy_full_proxy.py` |
| Full path forwarding | `server.py` | `subpath = path  # keep full path for nginx` | Run `deploy_full_proxy.py` |
| CSP skip for proxy | `server.py` | `Skip CSP injection for Grafana-proxied` | Run `add_csp_skip.py` |
| Cost popover | `sessions.js` | `_sessionGrafanaUrl` + `openGrafanaPopover` | Run `repatch_cost_popover.py` |
| Slugged URL | `sessions.js` | `f09f908e-ai-usage-and-cost-tracking` | Run `repatch_cost_popover.py` |
| Theme/skin | `style.css` | Agent-specific skin block | Run `repatch_theme.py` |

**One-command full restore:** `python3 ~/workspace/grafana-webui-proxy/scripts/fleet_restore.py --all --restart`

## What each script does

- **`scripts/repatch_cost_popover.py`** — the cost/popover patcher. 9 anchor-based,
  idempotent patches across 5 files. `--verify-only` checks without modifying.
  Fails loudly (exit 2) if an upstream anchor moved.

- **`scripts/webui_post_update_restore.py`** — single-agent orchestrator. Runs the
  cost layer + theme layer + regression checks. Use on the target agent directly.

- **`scripts/fleet_restore.py`** — fleet driver. SSH-pushes scripts + per-agent theme
  files, runs the orchestrator remotely, patches remotes before local shichi.

- **`fleet-themes/skins/scripts/repatch_theme.py`** — the theme/skin patcher.
  Anchor-based, idempotent, `--verify-only`, fails loud on moved anchors.
  Restores all 5 theme patches (style.css block, `_SKINS` entry,
  `_normalizeAppearance` fallback, config.py validation set + default) plus the
  asset layer (avatar + `favicon.svg` by hash + derived favicon PNG set
  regenerated from the canonical SVG, with staleness detection — see
  fleet-themes README "Lessons learned"). Orchestrator calls it. **Replaces
  the old `restore-skin.py`.**

## Regression checks (automatic)

After applying, the orchestrator asserts:
1. `server.py` parses (`ast.parse`)
2. `style.css` brace balance == 0
3. `_sessionGrafanaUrl` is **top-level** in sessions.js (catches the nested-function bug)
4. `api/routes.py` parses

Any failure → exit 2, "review before restarting."

## If a patch FAILs ("anchor not found, upstream changed")

Upstream changed the code we patch. Do NOT force it. Update the anchor:
1. `git log` the WebUI repo to see what changed in that region.
2. Update the `*_OLD` anchor string in `repatch_cost_popover.py` to the new upstream shape.
3. Keep the `*_NEW` (our customization) the same.
4. Re-run `--verify-only` to confirm it now resolves.
5. Commit the updated anchor to the repo.

## The golden rules (hard-won)

- **Never brace-count to find a function's end** for insertion. Early `return`
  inside an `if` breaks it (caused the `_isActualCost is not defined` bug).
  Use `insert_after_function()` which depth-tracks from the function's own
  top-level opening brace.
- **Anchor on exact upstream snippets**, not line numbers (they drift per agent).
- **Every patch has an idempotency marker** — already-applied patches SKIP.
- **Fail loud, not silent** — a missing anchor means "review," never "guess."
- **Patch remotes before local shichi** — restarting shichi's WebUI drops the chat.
- **Browser hard-refresh after patching** — the PWA service worker caches
  sessions.js/style.css aggressively. Updated *values* (API data) appear, but
  changed *logic/colors* (JS/CSS) need Ctrl+Shift+R.

## After patching, the user must hard-refresh

The WebUI is a PWA. Tell the user: **Ctrl+Shift+R** (or DevTools → right-click
reload → Empty Cache and Hard Reload). If a feature still looks missing after
that, unregister the service worker (DevTools → Application → Service Workers →
Unregister) and reload.
