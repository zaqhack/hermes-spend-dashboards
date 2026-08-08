---
name: hermes-webui
description: "Customize, extend, and secure the Hermes WebUI — skins/themes, empty-state avatar, settings defaults, extension skin registration, and authentication/password/fleet security."
version: 1.3.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, webui, skin, theme, css, frontend, customization, auth, security, password]
---

# Hermes WebUI Customization

The Hermes WebUI is a browser-based chat frontend at `/home/shichi/hermes-webui/`.
It has its own skin/theme system, completely separate from the CLI skin system
(which uses YAML files in `~/.hermes/skins/` — see the `hermes-agent` skill's
`references/skin-customization.md` for that).

This skill covers adding custom skins, replacing the empty-state avatar,
changing the default skin, and the extension skin registration API.

## Post-Upgrade Restore (READ THIS FIRST after any `git pull`)

**Every `hermes update` / `git pull` on a WebUI agent overwrites the files our
local customizations patch.** Before doing anything else, run the fleet restore:

```bash
# On Shichi (holds the canonical repos):
python3 ~/workspace/grafana-webui-proxy/scripts/fleet_restore.py --all --verify-only
python3 ~/workspace/grafana-webui-proxy/scripts/fleet_restore.py --all --restart
```

This restores: nginx proxy port/path, CSP skip, cost popover, theme/skin, and
all regression checks. **Do not restart the local WebUI until all remotes are
patched** — restarting Shichi's WebUI kills this chat session.

**Quick sanity check** (run on any agent):
```bash
grep -c 'keep full path for nginx\|Skip CSP injection' ~/hermes-webui/server.py
grep -c 'function _sessionGrafanaUrl' ~/hermes-webui/static/sessions.js
```
Any `0` means the upstream regression wiped the patches — restore from the repo.

## When to Load This Skill

- User asks to add a custom skin/theme to the WebUI
- User wants to change the default WebUI appearance
- User wants to replace the new-chat / empty-state logo with a custom image
- User asks about `registerHermesSkin()` or WebUI extension themes
- User references files like `boot.js`, `style.css`, or `config.py` in the
  `hermes-webui/` directory
- User asks to set, change, or remove a WebUI password
- User asks about WebUI authentication, security, or fleet hardening
- User wants to audit which bots have auth enabled or are internet-exposed
- User asks about or wants to enable/configure the dashboard link in the WebUI sidebar
- User reports the Hermes Dashboard button not showing up or not working in the WebUI
- User sets up the Hermes Agent Dashboard (`hermes dashboard`) and wants it linked from the WebUI
- User wants to customize the session list sidebar (cost column, metadata display, sorting)
- User references `sessions.js`, `_formatRelativeSessionTime`, or session list rendering code
- User wants to proxy an internal service (Grafana, Prometheus, etc.) through the WebUI so it's accessible outside the LAN
- User asks about adding custom routes, reverse proxy endpoints, or `_proxy_to_grafana`-style handlers to `server.py`
- User references the WebUI's `do_GET`/`do_POST` dispatch flow or `BaseHTTPRequestHandler` routing

## Workflow: test patches on a non-critical agent first

When deploying any WebUI patch (skin, proxy, popover, cost column) that
requires a WebUI restart, apply and restart REMOTE agents first. The local
agent's WebUI restart kills the active chat session (pitfall #15). Remote
agents (e.g. Dantsu, Satono) have no chat sessions to lose.

If the user reports a problem, debug from the remote agent's terminal (SSH)
rather than the local one's — the remote agent's WebUI can be restarted
freely for testing, while the local one cannot without dropping the user.

**User preference (Zaq)**: she explicitly prefers deploying to Dantsu first
for WebUI infrastructure changes, since it has no active chat sessions and
its WebUI port is different from Shichi's. Always suggest this pattern
before touching the local WebUI.

## Architecture — Three-File Skin Registration

Every skin must be registered in **three files** that must stay in sync:

| Layer | File | Purpose |
|-------|------|---------|
| JS | `static/boot.js` ~L2287 | `_SKINS` array — picker UI + client validation |
| CSS | `static/style.css` ~L122+ | `:root[data-skin="..."]` blocks — visual styling |
| Python | `api/config.py` ~L8389 | `_SETTINGS_SKIN_VALUES` set — server validation |

### JS — `_SKINS` array

Each entry: `name` (display, lowercased to `data-skin` value), optional `value`
(for multi-word slugs like `neon-soft`), `colors` (3 hex swatch dots for picker).

```javascript
const _SKINS=[
  {name:'Default',  colors:['#FFD700','#FFBF00','#CD7F32']},
  {name:'Verdigris', value:'verdigris', colors:['#C89A5A','#0F1714','#22342C']},
];
```

### CSS — Skin blocks

Simple skins override accent variables only. Complex skins rewrite the full
palette and add component-level selectors.

```css
:root[data-skin="ares"]{--accent:#C0392B;--accent-hover:#A93226;--accent-bg:rgba(192,57,43,0.08);--accent-bg-strong:rgba(192,57,43,0.15);--accent-text:#922B21;}
:root.dark[data-skin="ares"]{--accent:#FF4444;--accent-hover:#CC3333;--accent-bg:rgba(255,68,68,0.08);--accent-bg-strong:rgba(255,68,68,0.15);--accent-text:#FF4444;}
```

### Python — Validation set

```python
_SETTINGS_SKIN_VALUES = {"default", "ares", "mono", "graphite", ...}
```

### Application — Critical: The `value:'default'` Trap

`_applySkin(name)` (boot.js ~L2690) sets `data-skin` on `<html>`:

```javascript
function _applySkin(name){
  const key=(name||'default').toLowerCase();
  if(key==='default') delete document.documentElement.dataset.skin;
  else document.documentElement.dataset.skin=key;
  _setResolvedTheme(_resolvedThemeBaseDark);
}
```

**🚨 CRITICAL**: When the key is `'default'`, the frontend **deletes** the
`data-skin` attribute entirely. CSS selectors like
`:root.dark[data-skin="default"]{...}` will **never match**. The element falls
back to base `:root.dark` styles (gold accent by default). You **cannot** create
a custom skin with `value:'default'` and expect CSS rules to apply — custom
skins MUST use a non-`'default'` value (e.g. `value:'dantsu'`,
`value:'shichi'`).

## How the Frontend Resolves the Skin on Load

Boot-time flow:

1. Read `localStorage.getItem('hermes-skin')` for persisted user preference
2. If found AND valid (present in `_VALID_SKINS`), use that value — calls `_applySkin()`
3. If not found / not valid, fall back to server's `srvAppearance.skin` from the API settings endpoint
4. `_applySkin()` sets `data-skin="<value>"` on `<html>`, unless the value is `'default'` which removes it

**Implication**: Changing the server default skin in config.py or settings.json
ONLY affects users who have never visited the page before, or users who have
cleared localStorage. Returning users with `localStorage['hermes-skin']` keep
their old selection.

## Adding a New Core Skin — Step by Step

For a complete walkthrough with exact line numbers, CSS snippets, and a
checklist, see **`references/webui-skin-system.md`** in this skill.

Quick summary:

1. **`static/boot.js`** — Add entry to `_SKINS` array (search for `_SKINS=[`)
2. **`static/style.css`** — Add `:root[data-skin="name"]` CSS block (search for existing skin blocks)
3. **`api/config.py`** — Add skin name to `_SETTINGS_SKIN_VALUES` set (search for `_SETTINGS_SKIN_VALUES`)
4. **(Optional) `api/config.py`** — Change default skin (search for `"skin": "default"`)
5. **(Optional) `~/.hermes/webui/settings.json`** — Set default skin per-user (NOT `~/hermes-webui/settings.json`)
6. **(Optional) Image asset** — Place PNG in `static/` for custom avatar

## Empty-State / New-Chat Avatar

The welcome screen (`#emptyState` in `static/index.html` ~L440) contains an
inline SVG caduceus inside `.empty-logo`. To replace it per-skin, hide the SVG
and use `background-image` on `.empty-logo`.

### Preferred: Full image, no circle crop

The avatar image is usually a character portrait (portrait orientation). Show
the full image uncropped — users prefer this as it looks better:

```css
:root[data-skin="shichi"] .empty-logo svg{display:none;}
:root[data-skin="shichi"] .empty-logo{
  width:140px;height:186px;border-radius:8px;
  background-image:url("/static/shichi-gold-city.png");
  background-size:contain;background-position:center;background-repeat:no-repeat;
  margin-bottom:14px;
}
```

- Height of 186px = 140px × (1024/768) to match the 3:4 portrait aspect ratio
- `background-size:contain` shows the full image without cropping
- `border-radius:8px` gives soft corners without a circle cutout
- Adjust width/height to taste — the key is `contain` + no circle crop

### Alternative: Circle crop (classic)

If the user explicitly asks for a circular avatar, use `border-radius:50%` with
`background-size:cover` and a square container:

```css
:root[data-skin="shichi"] .empty-logo{
  width:140px;height:140px;border-radius:50%;
  background-image:url("/static/my-avatar.png");
  background-size:cover;background-position:center;
}
```

See the reference file for full CSS including glow adjustments.

## Extension Skin Registration (Runtime, No Source Edits)

`window.registerHermesSkin(descriptor)` (boot.js ~L2594) lets extensions
register skins at runtime. CSS is injected via a managed `<style>` element.

**Limitation**: Only CSS custom properties from `_ALLOWED_SKIN_TOKENS`
(~L2544) can be set. Structural overrides (like avatar replacement) are NOT
possible via extension skins — those require direct CSS file edits.

## Copying Skins Between Agents

To copy a WebUI skin from one agent to another (e.g., Satono → Dantsu):

1. Write a Python patch script that patches all three files (boot.js, style.css,
   config.py) on the target agent — see `references/webui-skin-system.md` pitfalls #7–10
2. `scp` the avatar image (if any) to the target's `~/hermes-webui/static/`
3. Set the default skin in `api/config.py` on the target
4. Restart the target's WebUI

**Reference skin**: The Satono Diamond skin definition (colors, CSS, avatar)
is documented in `references/satono-diamond-skin.md`. It was used on Dantsu
briefly but later replaced by the Dantsu Flame skin (same green palette,
different avatar).

Re-runnable scripts for Dantsu are at:
- `scripts/dantsu-restore-skins.py` — restores Dantsu Flame + Hermes skins post-update,
  sets favicons from `/ai/shared/dantsu_flavicon.png`, verifies CSS brace balance
- `scripts/gen_favicons.py` — extracts ALL embedded PNG(s) from an SVG favicon
  (handles multi-layer SVGs by alpha-compositing all layers), generates all
  sizes (32/192/512/ico/apple-touch). Usage:
  `python3 gen_favicons.py <source.svg> <static_dir>`
- `scripts/setup-agent-skin-template.py` — **universal template** for setting up
  a custom skin on any agent. Edit the CONFIGURATION block at the top (SKIN_VALUE,
  ACCENT, colors, avatar filename), then scp + ssh + run. Used successfully for
  Mayano Top Gun (orange #FA863F) on the first try with zero CSS issues.

Deployed skins across the fleet (CSS status = whether the agent's
`static/style.css` currently has the custom CSS block loaded as of Jul 2026):
| Agent | Skin value | Accent | Avatar | Favicon | CSS in style.css |
|-------|-----------|--------|--------|---------|-----------------|
| Shichi | `shichi` | Gold #D4AF37 | Gold City yukata | shichi_flavicon.svg | ✅ |
| Dantsu | `dantsu` | Green #50FF3A | Dantsu Flame + ServiceNow flag | dantsu_flavicon.svg | ❌ wiped |
| Satono | `satono` | Green #50FF3A | Satono Diamond | satono_flavicon.svg | ❌ wiped |
| Mayano | `mayano` | Orange #FA863F | Mayano Top Gun (wedding dress) | mayano_flavicon.svg | ❌ wiped |
| RobRoy | `robroy` | Gray-blue #8B9DAF + sage green #A8C4B4 | Zenno Rob Roy (librarian) | robroy_flavicon.svg | ❌ wiped |
| Suzuka | `suzuka` | Soft green #5BCC8F + mint #A8E6CF | Silence Suzuka (racing outfit) | suzuka_flavicon.svg | ❌ wiped |
| Bright | `bright` | Teal #3DCCC4 (dark blue-shifted) | Mejiro Bright | bright_flavicon.svg | ✅ |

**Canonical asset source**: All of these files (CSS blocks, avatars, favicon
SVG+XCF sources, skin definitions, restore scripts) are version-controlled in
the **fleet-themes Git repo** at `/home/shichi/workspace/fleet-themes/`. See
`references/fleet-themes-repo.md` for structure and usage. This is the single
source of truth — use it to restore any agent's theme post-update.

Satono and Dantsu use a vivid neon green (#50FF3A) — brighter than the original
#63DF4E — to distinguish them from Bright's blue-shifted teal (#3DCCC4). Bright's
background is darker (#08161A) for visual contrast when flipping between windows.

The old `scripts/dantsu-restore-satono-skin.py` is **obsolete** — the Satono skin
was removed from Dantsu and replaced by the Dantsu Flame skin (same green palette,
different avatar, value `dantsu` instead of `satono`).

**Canonical theme patcher**: `~/workspace/fleet-themes/skins/scripts/repatch_theme.py`
— the theme-layer equivalent of `repatch_cost_popover.py`. Anchor-based,
idempotent, `--verify-only`, fails loud on moved anchors. Restores ALL FIVE
theme patches (style.css block, `_SKINS` entry, `_normalizeAppearance`
fallback, config.py validation set + default) plus the asset layer (avatar +
favicon.svg by hash, and the derived favicon PNG set regenerated from the
canonical SVG by a stdlib compositor that honors `<image>` x/y/width/height
placement and viewBox cropping). Regression-checks ast.parse / brace balance /
node --check / asset existence. Called by `webui_post_update_restore.py`
(theme layer) and driven fleet-wide by `fleet_restore.py`. **Replaces the old
`restore-skin.py`** (grep-presence, no verify mode, didn't touch favicons).

For copying session data (conversation history) between agents, see the
`fleet-standardization` skill's `references/session-migration.md` — which also
covers the WebUI session metadata files that must be copied alongside the
SQLite DB rows.

## WebUI Authentication & Security

The WebUI has optional authentication (off by default). For the full auth
architecture, password change procedures, fleet security audit steps, and
hardening recommendations, see **`references/webui-auth-and-security.md`**.

Quick summary:
- Password set via `HERMES_WEBUI_PASSWORD` env var (priority) or
  `password_hash` in `~/.hermes/webui/settings.json`
- PBKDF2-HMAC-SHA256, 600k iterations, per-installation salt
- If neither is set, **auth is completely disabled** — anyone can access
- Use unique passwords per bot; never reuse the sudo/system password
Check fleet auth status with the audit procedure in the reference file.

## Dashboard Link

The WebUI has a built-in Hermes Agent Dashboard auto-detect and link button. For full details (HTML elements, URL resolution, auth setup, troubleshooting), see **`references/dashboard-link.md`**. For the probe mechanism, configuration, and relevant source files, see **`references/webui-dashboard-integration.md`**.

Quick facts:
- The dashboard link appears automatically in the sidebar when `hermes dashboard` is running on the same machine — no config needed
- Controlled by `webui.dashboard.enabled` in config.yaml (`"auto"`/`"always"`/`"never"`)
- The probe hits `http://127.0.0.1:9119/api/status` server-side (not from the browser) — the endpoint is public even when auth is enabled
- Dashboard auth env vars: `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`, `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD`, `HERMES_DASHBOARD_BASIC_AUTH_SECRET`
- Login page is at `/login` (username/password form), NOT `/auth/login?provider=basic` (which triggers OAuth flow and fails)

## Session List Customization

The sidebar session list is rendered by `static/sessions.js`. Cost data (`estimated_cost`) is available in the API response per session. For the full rendering architecture, key functions, and the fleet-deployed cost-column patch, see **`references/session-list-rendering.md`**. The `webui-cost-column-patch` skill contains the deployable script. When a user reports the sidebar cost "changed between clicks" or disagrees with Grafana, verify all three cost sources (state.db estimate / sidecar actual / InfluxDB points) before concluding anything — recipe in **`references/session-cost-reconcile-verification.md`**.

## Reverse Proxy Endpoints

The WebUI can proxy internal LAN services (Grafana, Prometheus, internal APIs)
through its own port, making them accessible from outside the LAN without new
ports or DNS entries. Useful when the user is remote and needs to access LAN-
only dashboards.

**Two architectures exist** — know which one you're patching:

1. **Direct proxy (old, pre-Jul 28 2026)**: WebUI → Grafana :3000 directly.
   The WebUI's `_proxy_to_grafana` strips `/grafana-proxy` and forwards to
   Grafana. Works for simple HTML but **breaks Grafana's React SPA** — the
   SPA reads `appSubUrl` from `/api/frontend/settings` and can't route under
   a subpath without `serve_from_sub_path=true`.

2. **nginx subpath proxy (current, Jul 28 2026+)**: WebUI → nginx :3001
   (Bright) → Grafana :3000. nginx `sub_filter` rewrites `appSubUrl` to
   `/grafana-proxy/` in flight, so the SPA routes correctly. nginx also
   referer-gates the SPA's absolute-path `/api/` and `/public/` requests.
   Grafana itself stays untouched (direct LAN access still works).
   **Full architecture + 404 decision tree: `references/grafana-subpath-proxy.md`.**
   **Verified nginx config: `templates/grafana-proxy-nginx.conf`.**

**Pattern**: Intercept requests in the `Handler` class's `do_GET`/`do_POST`/etc.
before `handle_get`, forward to the target via `http.client`, and rewrite the
response (base href, Location, Set-Cookie, CSP) to work through the proxy path.

**Key gotchas** (fixed in this session):
- **Content-Encoding**: http.client returns raw compressed bytes; the proxy
  must force `Accept-Encoding: identity` so base-href rewrites work on plain
  text. Forwarding the browser's gzip/br headers causes `ERR_CONTENT_DECODING_FAILED`
  in the browser because the rewritten body is plain but the proxied
  Content-Encoding header says gzip.
- **CSP report-only**: Firefox/Zen enforces `frame-ancestors` from BOTH
  `Content-Security-Policy` and `Content-Security-Policy-Report-Only`. Both
  must be stripped for the iframe to render.
- **Content-Length**: HTTP/1.1 keep-alive requires explicit Content-Length
  or chunked encoding. A proxy that rewrites bodies must re-add it.
- **Grafana dashboard slug**: the SPA frontend fails on `/d/<uid>` — needs
  the full `/d/<uid>/<slug>` URL.
- **🚨 WebUI's own CSP injection breaks proxied iframe embedding.** The
  WebUI's `end_headers()` injects `Content-Security-Policy-Report-Only`
  with `frame-ancestors 'none'` on EVERY response, including proxied
  Grafana ones. The proxy strips Grafana's own `frame-ancestors`, then
  the WebUI adds its own back — Firefox/Zen correctly refuses to render
  the iframe. Fix: skip WebUI CSP injection for `/grafana-proxy/*`,
  `/public/*`, `/apis/*`, `/avatar/*` paths in `end_headers()`.
  ```python
  def end_headers(self) -> None:
      if getattr(self, 'path', '').startswith(('/grafana-proxy', '/public/', '/apis/', '/avatar/')):
          super().end_headers()
          return
      # ... normal CSP injection
  ```
- **Session cost links must use proxy path, not raw LAN IP.** The cost
  element's `_sessionGrafanaUrl()` originally built
  `http://192.168.49.15:3000/d/...` which fails from external networks.
  Changed to `/grafana-proxy/d/...` (relative, works through the proxy)
  and made the click handler call `openGrafanaPopover(url)` instead of
  opening a new tab. The popover's "open in new tab" link is updated to
  match the session-filtered URL.
- **Docker container DNS**: if Grafana's datasource uses a container hostname
  (`influxdb:8086`), Grafana and InfluxDB must share a user-defined Docker
  network (default bridge lacks DNS resolution).

For the full reference implementation (Grafana dashboard proxy with service
account auth, base href rewriting, asset forwarding over `/public/`, `/apis/`,
and `/avatar/` paths, and popover overlay), see
**`references/webui-reverse-proxy.md`** in this skill.

**Re-runnable installer**: `scripts/grafana-proxy-patch.py` — idempotently
installs the full Grafana proxy (`_proxy_to_grafana` + intercepts in
do_GET/POST/PUT/PATCH/DELETE/OPTIONS, including the `/apis/` and `/avatar/`
asset paths) and a themed popover button/overlay into any agent's WebUI.
Theme via `--accent1/--accent2/--accent-dark` (Shichi: gold defaults; Dantsu:
`'#50FF3A' '#80FF66' '#0a1a0a'`). Also copy `~/.hermes/secrets/grafana-proxy-token`
(service account on Bright, Viewer role) to the target. Run after any
`git pull` that wipes the server.py/index.html patches; verify with
`python3 -c "import ast; ast.parse(...)"` before restarting.

**Session cost → popover integration**: The cost element in the sidebar now
opens the Grafana popover (not a new tab) with the session-filtered proxy URL.
The `_sessionGrafanaUrl()` function in `sessions.js` builds `/grafana-proxy/...`
URLs instead of raw `http://192.168.49.15:3000/...`. The click handler calls
`openGrafanaPopover(url)`. The popover's "open in new tab" link is updated to
match. This requires both the `sessions.js` patch and the `end_headers()` CSP
skip (pitfall #20) to be in place.

## Pitfalls

1. **Three files must stay in sync** — missing any one causes silent fallback to "default".
2. **🚨 CRITICAL: `value:'default'` will NEVER work as a custom skin value.** The
   frontend's `_applySkin()` explicitly deletes the `data-skin` attribute when
   the value is `'default'`, so no CSS block with `[data-skin="default"]` will
   ever match. Custom skins MUST use a non-`'default'` value (e.g.
   `value:'dantsu'`, `value:'shichi'`). The `'default'` skin is the base theme
   that uses base `:root.dark` styles (gold accent).
3. **Existing users keep localStorage skin** — changing server default only affects new visitors. **Additionally, the frontend syncs localStorage back to the server**: if a browser visits with `localStorage['hermes-skin']='satono'` cached, the boot sync logic (boot.js ~L3270) detects the mismatch and POSTs the localStorage value to `/api/settings`, overwriting whatever you set on the server. So even after you change the server default to `dantsu`, a returning browser with `satono` cached will write `satono` back to `settings.json` on next load. To break the loop: either delete the old skin entirely (so `_VALID_SKINS.has()` returns false and the value is discarded), or have the user clear `hermes-skin` from localStorage.
4. **Extension skins can't do structural CSS** — only CSS custom property tokens.
5. **Multi-word skin names need explicit `value`** field in the `_SKINS` entry.
6. **Reserved core skin keys** block extension registration of the same name.
7. **Line numbers vary per agent** — always `grep -n` for the actual line on the target agent before patching.
8. **Default skin is in `api/config.py`** (search for `"skin": "default"`), not `settings.json`. Update config.py on the TARGET when copying skins. Also update `~/.hermes/webui/settings.json` (the state dir) — NOT `~/hermes-webui/settings.json` (the project dir), which the API does not read.
9. **WebUI updates overwrite custom skin patches.** Every `git pull`, reinstall,
   or version upgrade replaces `boot.js`, `style.css`, and `config.py` from
   the repo, wiping all custom skin registrations. Always save skin patches as
   re-runnable Python scripts under this skill's `scripts/` directory for quick
   post-update recovery (e.g., `scripts/dantsu-restore-skins.py`).
   Avatar PNG files in `static/` survive updates since they're not tracked.
10. **Avatar replacements need Ctrl+F5 / Cmd+Shift+R.** When replacing an avatar
    image with the same filename (e.g. `dantsu.png` replaced with a newer version),
    the browser caches the old one aggressively. Users must hard-refresh to see
    the new image. A server restart does NOT fix this.
11. **🚨 CRITICAL: CSS brace balance — never `sed`-delete skin blocks by pattern
    matching alone.** When removing a skin's CSS from `style.css`, never use
    `sed '/pattern/d'` to delete only lines matching the skin name (e.g.
    `sed '/data-skin="satono"/d'`). This removes the **selector** lines but
    leaves the **body** lines (indented declarations + closing braces) as
    orphans. The resulting brace imbalance (e.g. -6 extra closing braces)
    breaks the CSS parser for everything **after** the orphaned lines —
    including any custom skin blocks appended later. The dantsu CSS block was
    present and syntactically correct but never applied because 6 orphaned
    closing braces from a botched satono removal poisoned the parser before
    it reached the dantsu block. **Always remove the complete CSS block**
    (selector + all body lines + closing brace), not just matching lines. After
    any CSS edit, verify brace balance:
    ```python
    with open('style.css') as f: c = f.read()
    print(f"Balance: {c.count('{') - c.count('}')}")
    # Must be 0 for the file to parse correctly
    ```
12. **Favicon customization.** The WebUI serves favicons from
    `~/hermes-webui/static/` — the relevant files are `favicon-32.png`,
    `favicon-192.png`, `favicon-512.png`, `apple-touch-icon.png`, and
    `favicon.svg`. To set a custom favicon, overwrite the PNG files (a single
    source image can be copied to all four). The SVG favicon (`favicon.svg`)
    is listed first in `index.html` and takes precedence in modern browsers —
    overwrite or remove it too if you want the PNG to show. Favicons survive
    WebUI updates since they're not tracked in the repo. Users may need a
    hard refresh (Ctrl+F5) to see the new favicon due to aggressive browser
    caching. **SVG favicons with embedded base64 PNGs**: many AI-generated
 SVGs are wrappers around rasterized PNG(s) embedded as
 `data:image/png;base64,...`. **Some SVGs contain MULTIPLE layered
 `<image>` elements** (e.g. background, character, border as separate
 layers) — Satono's favicon SVG had 3 layers. If you only extract the
 first `<image>`, you may get just the background layer (e.g. a black
 square). To generate all PNG favicon sizes from such an SVG, use
 `scripts/gen_favicons.py` — it extracts ALL embedded PNGs, alpha-composites
 them bottom-to-top, and resizes the composited result to
 32/192/512/ico/apple-touch sizes. Copy the SVG itself to `favicon.svg`
 for the vector favicon (modern browsers prefer it).
13. **`_normalizeAppearance` fallback override.** The function
    `_normalizeAppearance(theme, skin)` in `boot.js` (~L2597) controls which
    skin is used when the server or localStorage value is invalid. The default
    fallback is `'default'` (the gold base theme). Satono's WebUI patches this
    line to fall back to `'satono'` instead:
    ```javascript
    // Default (Dantsu and most agents):
    const nextSkin=_VALID_SKINS.has(rawSkin)?rawSkin:(legacy?legacy.skin:'default');
    // Satono (patched):
    const nextSkin=_VALID_SKINS.has(rawSkin)?rawSkin:(legacy?legacy.skin:'satono');
    ```
    This is a deeper-level default override that ensures the custom skin is
    used even when `_VALID_SKINS` rejects the server-provided value (e.g.
    during a boot race where the skin hasn't been registered yet). Consider
    patching this on agents where the custom skin must be the true default.
14. **Fleet patch scripts must never touch other agents' skin files.** When
    deploying a cross-fleet WebUI patch (e.g. the cost column), the script
    must only modify the files the patch owns (`sessions.js`, and append-only
    blocks in `style.css`) — never `boot.js`, `config.py`, or skin CSS
    blocks. Skins are per-agent; overwriting them breaks that agent's theme.
    **CSS-block restore**: Use the fleet-themes repo at
    `/home/shichi/workspace/fleet-themes/skins/css/<agent>.css` — append the
    file to `style.css` and verify brace balance. No standalone restore
    script needed per agent; the canonical CSS for all 7 agents lives in one
    place.
15. **Restarting the LOCAL WebUI kills the agent's own active chat session.**\n    When fleet-patching, patch+restart all REMOTE agents first and let the\n    user restart the local WebUI themselves (or do it last, expecting the\n    session to drop mid-work).\n16. **🚨 Skin template fallback inserts into wrong `];` — silent fallback to default.**\n    The `setup-agent-skin-template.py` script's fallback path used `js.find("];")`\n    which finds the FIRST `];` in boot.js — this may be inside unrelated code\n    (e.g., session restoration `S.messages=[...]` at ~L3755), NOT the `_SKINS=[`\n    array (~L2656). The skin entry gets inserted in the wrong location,\n    `_VALID_SKINS` never includes the new skin, and the frontend silently falls\n    back to the default gold theme. The script has been fixed to locate\n    `_SKINS=[` first, then search for `];` only after that position. When\n    writing custom skin patch scripts, always anchor on the `_SKINS=[`\n    declaration, not a bare `];` search.
17. **🚨 "Clobbered" skins/popover may be browser cache, not a wiped patch — verify server-side FIRST.**
    When the user reports themes reverted or a custom feature (e.g. the Grafana
    popover button) missing after an update, do NOT assume `git pull` wiped
    the patches and start re-patching. The WebUI is a PWA (`pwa-startup.js`)
    with a service worker and aggressive caching — a stale cached
    `index.html`/`boot.js` shows the OLD UI even when all patches are intact
    on disk and being served. Diagnose in order:
    1. Grep the files on disk for patch markers (skin name in `_SKINS`,
       feature div id in `index.html`, handler in `server.py`).
    2. Fetch the SERVED pages and compare — the WebUI runs TLS
       (`HERMES_WEBUI_TLS_CERT`/`HERMES_WEBUI_TLS_KEY` env vars), so plain
       `curl http://127.0.0.1:PORT/` returns an EMPTY reply, not an error.
       Use `curl -sk https://...`.
    3. `/` requires auth when a password is set: get the password from
       `/proc/<MainPID>/environ` (`HERMES_WEBUI_PASSWORD=...`), then
       `curl -sk -c jar -X POST https://127.0.0.1:PORT/api/auth/login -H 'Content-Type: application/json' -d '{"password":"..."}'`
       and `curl -sk -b jar https://127.0.0.1:PORT/`. Unauthenticated GETs
       return empty or `{"error":"Authentication required"}`. (Login endpoint
       is `/api/auth/login`, NOT `/api/login`.)
    4. If disk AND served content both contain the patches, the fix is
       client-side: Ctrl+Shift+R, or unregister the service worker + clear
       site storage (DevTools → Application → Service Workers → Unregister),
       or console:
       `navigator.serviceWorker.getRegistrations().then(rs=>rs.forEach(r=>r.unregister())); caches.keys().then(ks=>ks.forEach(k=>caches.delete(k))); location.reload(true);`
    **Plain-HTTP exception (Bright, Aug 2026):** service workers only exist on
    secure contexts (HTTPS or localhost). Agents served over plain HTTP LAN
    (e.g. `http://192.168.49.15:49015`) have NO service worker and NO PWA
    cache — `navigator.serviceWorker` is `undefined` and the unregister
    one-liner throws `TypeError: Cannot read properties of undefined
    (reading 'getRegistrations')`. That error in the user's console is
    diagnostic gold: it instantly rules out the whole PWA-cache branch, so
    skip step 4 and go straight to the code-path ladder in pitfall 29.
    Only re-patch if the disk files actually lack the changes. (Hit on Shichi
    Jul 2026: "skins clobbered again + no Grafana popover" was purely a stale
    PWA cache; every patch was live.)
    **Corollary — the reverse trap (Aug 2026):** if the browser console shows
    the JS error with a **fresh version query string** (e.g.
    `sessions.js?v=exp-v0.52.158-...-dirty-<hash>:8072`), the browser IS
    fetching the current file — do NOT tell the user to hard-refresh and stop
    there. A `ReferenceError: <fn> is not defined` with a fresh `?v=` means the
    served file genuinely lacks (or mis-scopes) the function: the patch script
    itself broke it. Fetch `/static/sessions.js` with the exact `?v=` string
    and grep for the function definition AND its call site, then inspect the
    served region directly. **Never brace-count to find a function's end for
    insertion** — an early `return` inside an `if` makes naive depth-tracking
    stop at the if-block's `}`, nesting the inserted functions inside the host
    function where they're invisible globally (caused exactly this crash on
    Dantsu: `_sessionGrafanaUrl`/`_isActualCost` nested inside
    `_formatSessionCost`). Use depth tracking anchored on the target function's
    OWN top-level opening brace, string-literal-aware — see
    `insert_after_function()` in
    `~/workspace/grafana-webui-proxy/scripts/repatch_cost_popover.py`. After
    any JS insertion, grep the served file for `^function <name>` (column 0) —
    if it's indented, you nested it.
18. **Grafana proxy + popover now has a universal restore script.** Use
    `scripts/grafana-proxy-patch.py` (see Reverse Proxy Endpoints section) to
    reinstall on any agent post-`git pull`. It is theme-parameterized
    (`--accent1/2/--accent-dark`) and idempotent. Note: **the stock
    do_POST/PUT/PATCH/DELETE on a fresh WebUI are one-liners**
    (`self._handle_write(handle_post)`) with no `parsed = urlparse(...)` head —
    Shichi's expanded form is itself a patch. Patch scripts must match BOTH
    shapes or they silently skip the write verbs (GET-only proxy: page loads,
    but dashboard queries fail because `/api/ds/query` is a POST). The script
    handles both; do_OPTIONS also needs an intercept inserted after its def
    line (before the CORS docstring body) for Grafana preflights.
19. **Test the full proxy chain with curl before declaring done** (all with
    auth cookie, see #17): `GET /` contains popover markup;
    `GET /grafana-proxy/d/<slug>` → 200 with `<base href="/grafana-proxy/">`;
    `GET /public/img/grafana_icon.svg` → 200 (asset path);
    `POST /grafana-proxy/api/ds/query` with `{}` → **400 from Grafana is
    SUCCESS** (proves POST routing reaches Grafana; a 404/500 means the
    do_POST intercept is missing).

20. **🚨 WebUI's own CSP injection breaks proxied iframe embedding.** The
    WebUI's `end_headers()` injects `Content-Security-Policy-Report-Only`
    with `frame-ancestors 'none'` on EVERY response, including proxied
    Grafana ones. The proxy strips Grafana's `frame-ancestors`, then the
    WebUI adds its own back — Firefox/Zen correctly refuses to render the
    iframe. Fix: skip WebUI CSP injection for proxy paths in `end_headers()`:
    ```python
    def end_headers(self) -> None:
        if getattr(self, 'path', '').startswith(('/grafana-proxy', '/public/', '/apis/', '/avatar/')):
            super().end_headers()
            return
        # ... normal CSP injection
    ```
    **Guard attribute is `self.path`, not `self._path`** — `BaseHTTPRequestHandler`
    stores the raw request line in `self.path`. Using `_path` silently always
    returns `''` and the skip never fires.

21. **Session cost links must use proxy path, not raw LAN IP.** The cost
    element's `_sessionGrafanaUrl()` originally built
    `http://192.168.49.15:3000/d/...` which fails from external networks.
    Changed to `/grafana-proxy/d/...` (relative, works through the proxy)
    and made the click handler call `openGrafanaPopover(url)` instead of
    opening a new tab. The popover's "open in new tab" link is updated to
    match the session-filtered URL. See `webui-cost-column-patch` skill
    v3.1 for the deployable script.

22. **🚨 nginx subpath proxy: double-strip = 404.** When the WebUI proxies
    to nginx :3001 (not Grafana :3000 directly), it must forward the FULL
    `/grafana-proxy/*` path. nginx's `location /grafana-proxy/` does its own
    stripping via `proxy_pass`. If the WebUI also strips, nginx falls to its
    default static root and 404s (`open() "/etc/nginx/html/d/..." failed`).
    Verify in nginx log: `docker logs grafana-proxy-nginx` — requests should
    arrive as `/grafana-proxy/d/...`, not `/d/...`.

23. **🚨 Byte-string regex with single quotes = SyntaxError.** The proxy's
    base-href regex `b'<base[^>]*href=["\']([^"\']*)["\'][^>]*>'` uses `\'`
    inside a single-quoted byte literal — this terminates the string and
    causes `SyntaxError: closing parenthesis ']' does not match opening
    parenthesis` on service start. systemd restart-loops hide the real error
    in `journalctl --user -u hermes-webui.service`. The fix uses hex escapes:
    `b'<base[^>]*href=["\x27]([^"\x27]*)["\x27][^>]*>'`. Verify transplanted
    code with `ast.parse` (wrapped in a dummy class if it's a method snippet)
    BEFORE restarting any remote WebUI.

24. **Fleet bots use `_handle_write()`, not per-`do_*` patterns.** Newer
    fleet agents (Mayano, Satono, RobRoy, Suzuka) route POST/PUT/PATCH/
    OPTIONS/DELETE through a shared `_handle_write(route_func)` helper with
    a different surrounding pattern than do_GET. A per-`do_*` string-replace
    patch only lands on do_GET — the proxy works for page loads but dashboard
    queries fail because `/api/ds/query` is a POST. Patch the proxy check
    into `_handle_write` too, after `check_auth` and before `route_func`.
    Verify with `grep -c '_proxy_to_grafana' server.py` (expect ≥3: def +
    do_GET + `_handle_write`).

25. **No floating Grafana button.** The bottom-right green "fleet stats"
    button was removed Jul 28 2026 per zaq — session cost click is the ONLY
    popover entry point. Never re-add the floating button when re-patching
    after a WebUI `git pull`; keep the hidden
    `<div id="grafana-popover-btn" style="display:none"></div>` placeholder
    because `toggleGrafanaPopover()` still references it. **Hide it by
    WRITING THE CLEAN FORM, never by commenting out the visible button** —
    the old comment-hack (`<!--` over the button remnants, never closed)
    swallowed the entire popover block on six agents (pitfall 32).

26. **🚨 Upstream merged its OWN proxy — with the OLD assumptions. Restore
    per `references/restore-after-upstream-regression.md`.** The Aug 2026
    WebUI `git pull` (webui `320789ae`) landed a first-party
    `_proxy_to_grafana` that LOOKS right but regresses to the pre-nginx
    architecture: `grafana_port = 3000` (not 3001), strips the
    `/grafana-proxy` prefix before forwarding (double-strip → nginx 404,
    pitfall 22), no CSP skip in `end_headers()` (pitfall 20), and DELETES
    `_sessionGrafanaUrl` + the popover click handler from `sessions.js`
    (cost becomes a plain `<span>` again). The popover markup in
    `index.html` survives, so the feature LOOKS half-present. Verify with
    `grep -c 'keep full path for nginx\|Skip CSP injection' server.py` and
    `grep -c 'function _sessionGrafanaUrl' static/sessions.js` — any 0 means
    the v5 layer was regressed. **Canonical preservation repo:
    `~/workspace/grafana-webui-proxy/`** (nginx config, extracted working
    patches, deploy scripts, OBSERVABILITY.md) — restore from there, not
    from memory. The old `scripts/grafana-proxy-patch.py` installer
    re-adds the proxy method + CSP skip but does NOT re-add the deleted
    `_sessionGrafanaUrl`/click-handler or flip the port/path to the nginx
    architecture — apply those separately. **Canonical one-shot restore:
    `~/workspace/grafana-webui-proxy/scripts/repatch_cost_popover.py`** — 9
    anchor-based idempotent patches across all 5 files, `--verify-only` mode,
    fails loud (exit 2) on moved anchors. This update ALSO wiped (pitfall 26
    originally under-documented): `_formatSessionCost` actual-cost preference
    (sidebar reverts to showing state.db estimate instead of the reconciled
    InfluxDB actual) and the `.session-cost-actual` green CSS rule (costs
    render gray even when `actual_cost` IS in the API payload — the "should
    be bright green but all gray" symptom), plus the floating Grafana button
    reappears in index.html (re-hide it, pitfall 25). Orchestrate with
    `webui_post_update_restore.py` (single agent: cost layer + theme layer +
    regression checks) or `fleet_restore.py` (SSH fleet driver, remotes before
    shichi). Runbook: `docs/WORKFLOW.md` in the same repo.

26b. **🚨 `git stash -u` sweeps untracked `venv/` → WebUI 203/EXEC after upgrade.**
    When updating a WebUI via `git stash push -u && git pull`, the `-u` flag
    stashes **untracked** files — and agents whose WebUI runs from its OWN
    `~/hermes-webui/venv/` (Bright and RobRoy use `ExecStart=~/hermes-webui/venv/bin/python3`,
    unlike Mayano/Satono/Suzuka/Dantsu which use the `~/.hermes/hermes-agent/venv`)
    lose `venv/`. The webui service then crash-loops with `status=203/EXEC`
    ("Executable not found" — `venv/bin/python3` gone). Diagnosis:
    `systemctl --user status hermes-webui` shows `203/EXEC`; the venv exists
    but `venv/bin/python3` is missing. **Recovery** — restore the untracked
    tree from the stash's 3rd parent (does NOT touch freshly-patched tracked
    files like boot.js/style.css/server.py):
    ```bash
    systemctl --user stop hermes-webui.service
    cd ~/hermes-webui
    git checkout 'stash@{0}^3' -- .   # restores venv/, avatars, settings.json
    systemctl --user reset-failed hermes-webui.service
    systemctl --user restart hermes-webui.service
    ```
    Verify `venv/bin/python3 --version` before restart. **Prevention:** either
    confirm the agent's WebUI uses the shared hermes-agent venv (stash-safe)
    or gitignore `venv/` so `-u` skips it. Only agents with their own webui
    venv (Bright, RobRoy) are at risk. Fleet-wide upgrade playbook
    (`/tmp/fleet-update.sh` + `restore-skin.py` in the fleet-themes repo)
    includes this recovery step.

27. **🚨 Inline `style.color='inherit'` on the cost element silently defeats the
    green `.session-cost-actual` class — the "only TODAY turns green" symptom.**
    The v4 click-handler sets `costEl.style.color='inherit'` next to
    `textDecoration='none'`. Inline styles ALWAYS beat stylesheet rules, so even
    though `_isActualCost()` adds the green class, the inline color forces the
    muted value on every row; only the currently-selected (today) row appears
    green because selection styling changes the inherited color. **Diagnostic
    signature:** the API returns `actual_cost` floats and the sidebar VALUES
    update ($0.06→$0.10, $0.50→$11.58), but older reconciled rows render gray.
    **Fix:** remove the inline `costEl.style.color='inherit'` (keep
    `textDecoration`/`cursor`), and make the green rule high-specificity +
    `!important`:
    `.session-cost.session-cost-actual{color:#4ade80 !important;opacity:1 !important;}`.
    Diagnose order before blaming PWA cache (pitfall 17): (1) `/api/sessions`
    returns `actual_cost`? (2) served sessions.js has the inline
    `color='inherit'`? (3) served style.css has the green rule? Only if all
    three are correct is it a cache issue.

28. **🚨 Clickable cost exists ONLY on parent rows — child rows and lineage
    segments render cost as plain text.** Verified Jul 28 2026: `childLabelHTML`
    appears 0 times in sessions.js; child rows build their label via
    `childLabelFor(child)` assigned to `mainBtn.textContent`
    (`` `${childCost} -> ${childTitle}${parentNote} - ${childTime}` ``), and
    lineage segments use the same plain-string pattern. Only the PARENT row's
    `costEl` is a real `<a>` with the `openGrafanaPopover` click listener. If
    the user reports "cost click just selects the session," first ask WHICH row
    they clicked — on child/lineage rows, selecting the session IS the current
    behavior, not a broken handler. Making child costs clickable requires
    switching the label from `textContent` to innerHTML/DOM nodes with their
    own listener (deliberately not done — `textContent` can't carry the
    `.session-cost-actual` green class either, which is why child costs also
    can't turn green).

29. **🚨 When a patched click handler "does nothing," debug in this order before
    re-patching:** (1) `_sessionGrafanaUrl(session)` returns null when the row
    lacks `session_id`/`id` — then NO listener is attached and the click falls
    through to row selection; verify the API payload actually carries
    `session_id` for that row type. (2) `openGrafanaPopover` is defined in
    `index.html`, not sessions.js — if the served index.html is stale (PWA
    cache, pitfall 17), `typeof openGrafanaPopover==='function'` is false and
    the handler silently no-ops (the current code has no `else` fallback on
    Dantsu-derived patches). (3) A capture-phase ancestor listener intercepts
    the click before `stopPropagation` can help — `stopPropagation` does NOT
    stop capture-phase listeners on ancestors. Confirm with DevTools →
    Event Listeners on the row.
    **The browser-vs-server isolation ladder (validated Aug 2026 on Bright):**
    when server-side greps all pass but the user's click still does nothing,
    disambiguate with three console probes — each isolates a different layer:
    ```js
    typeof openGrafanaPopover        // "undefined" → loaded HTML shell is stale (shell != subresources)
    document.querySelector('.session-cost').outerHTML   // wiring present? (href='#', class, listener can't be seen but markup can)
    openGrafanaPopover('/grafana-proxy/d/ai-usage-tracking/f09f908e-ai-usage-and-cost-tracking?from=now-7d&to=now')
                                     // popover opens → click path is the problem; throws → popover infra is the problem
    ```
    And compare the **version hash**: the `?v=exp-v0.52.158-...-dirty-<hash>`
    in the user's console preload warnings vs the hash in the server's served
    `/` (curl it and grep `v=exp-`). Matching hashes = browser IS on the
    current build, stop prescribing hard-refreshes and debug the code path.
    Mismatch = the tab's shell predates the restart — close the tab entirely
    and open a fresh one (a hard reload of a `?v=`-versioned shell can keep
    the old shell when the mtime-hash didn't change).

30. **🚨 The row gesture system selects the session UNDERNEATH the cost-link
    click — "clicking cost just selects the session" with the listener correctly
    attached.** Session rows don't open on `click`; they open via a pointer
    gesture system: `el.onpointerdown` → `_beginSessionGesture`,
    `el.onpointerup` → `_finishSessionGesture` → `_openSidebarSession(s)`.
    Both guard on `_isSessionActionTarget(e.target)`, which upstream defines as
    "is the target inside the row's `actions` container." The cost `<a>` lives
    in `titleRow`, NOT `actions` — so the gesture fires on the cost click and
    selects the session regardless of the anchor's own
    `e.stopPropagation()` (that's a click-event call; the gesture runs on
    pointerup). **Fix (in canonical repatch_cost_popover.py):** extend
    `_isSessionActionTarget` to also return true for
    `target.closest('.session-cost')`. Diagnosis path: if the listener is
    attached (pitfall 29 checks pass) but the row still selects, grep for
    `_isSessionActionTarget` and check whether `.session-cost` is exempted.

31. **🚨 `webui-cost-column-patch` skill is user-owned and STALE (v4, pre-popover).**
    It still describes the cost link as a `target=_blank` deep link to the raw
    LAN IP (`http://192.168.49.15:3000/...`) and predates: the popover-click
    behavior, the nginx :3001 layer, the CSP skip, the gesture exemption
    (pitfall 30), and the canonical `repatch_cost_popover.py` patcher (which
    also now includes the `tabLink.href` sync and the gesture fix — 11 patches
    total, and `fleet_restore.py` covers bright too). **Treat THIS skill
    (hermes-webui) + `~/workspace/grafana-webui-proxy/` as the source of
    truth** for anything cost-popover-related. Recommend
    `hermes curator adopt webui-cost-column-patch` so it can be updated (its
    scripts/ dir still has the useful v2/v4 patchers; the SKILL.md narrative
    needs the v5 rewrite).

32. **🚨 An unclosed `<!--` in index.html silently kills the popover — and grep
    CAN'T see it.** The old "hide floating button" patch replaced the button's
    opening tag with a hidden div + `<!--` intended to comment out the button
    remnants — but never wrote the closing `-->`. The comment ran to end-of-file,
    swallowing the overlay, popover div, iframe, AND the `<script>` block
    defining `toggleGrafanaPopover`/`openGrafanaPopover`. Symptoms: cost click
    does nothing (handler's `typeof openGrafanaPopover==='function'` guard
    no-ops), `document.getElementById('grafana-popover')` is null, but
    `grep -c grafana-popover index.html` still finds the markers because **grep
    can't tell code inside HTML comments from live code** — every "verified
    green" check passed while six agents were broken (only Dantsu, which had
    the clean form, worked). **Definitive test is browser-side, not grep:**
    `typeof openGrafanaPopover` in the console, or
    `document.body.innerHTML.slice(-200)` ending in `-->`. Fix: delete the
    hidden-anchor div + commented remnants entirely (Dantsu clean form). The
    canonical `repatch_cost_popover.py` now (a) auto-repairs the broken form,
    (b) inserts the full clean block when upstream wipes it, and (c) its
    marker is `id="grafana-popover-overlay"` — which only exists OUTSIDE
    comments in the clean form, so a commented-out block registers as
    "missing" and gets repaired. Verify repairs with a real HTML parser or
    browser, never grep alone.

33. **🚨 Favicon has SIX files; customizing only `favicon.svg` leaves 5 upstream
    caduceus files behind — and a naive "files exist + svg unchanged → skip"
    check skips right past them.** The WebUI links `favicon.svg` (modern
    browsers) + `favicon-32/192/512.png` + `favicon.ico` + `apple-touch-icon.png`.
    All six are upstream-tracked, so a `git pull` resets them all — but a manual
    restore that only copies the SVG (what happened on Shichi) leaves the PNG/ICO
    set as the upstream caduceus. Symptom: "flavicon is wrong" even though
    `favicon.svg` is correct on disk — because the tab switcher / apple-touch /
    ICO consumers show the stale raster, not the SVG. `repatch_theme.py`
    regenerates the whole derived set from the canonical SVG and — critically —
    detects staleness by re-deriving `favicon-32.png` and hash-comparing
    ("derived PNGs don't match canonical SVG"), NOT by existence+mtime. **The
    meta-lesson: a restore script whose skip condition is "target files exist
    and source is unchanged" will happily preserve a half-restored state
    forever.** Skip conditions must compare the target against the canonical
    SOURCE-derived content, not against absence.

34. **🚨 SVG `<image>` layers need x/y/width/height + viewBox placement — naive
    alpha_composite of raw decoded PNGs is wrong.** Shichi's flavicon SVG has 3
    layers, one (`raster1`) at `x=-62 y=-2 width=512 height=683` extending past
    the `0 0 400 400` viewBox (the SVG crops it). `gen_favicons.py`'s
    `Image.alpha_composite(canvas, img)` requires same-size images and CRASHES
    on this file — it only ever worked on Satono's same-size layers. The stdlib
    compositor in `repatch_theme.py` (`extract_composited_image`) parses each
    `<image>` tag's placement attrs, scales each layer to its declared display
    size, blits with alpha compositing clipped to the viewBox canvas, and crops
    the output to the viewBox. Verified 92.6% pixel-exact vs headless-Chromium
    `<img>` render of the same SVG (remaining 7% is a bottom-edge strip where
    Chromium anti-aliases/letterboxes the direct-file view differently — the
    `<img>` embed is what browsers actually do for favicons). If you ever need
    to regenerate favicon PNGs, use `repatch_theme.py`, never `gen_favicons.py`
    on multi-layer or offset-layer SVGs.
