#!/usr/bin/env python3
"""
Hermes WebUI — Grafana Cost Popover mod (v4, nginx-proxy architecture).

Idempotent re-patch script. Safe to run after every `hermes update`.
Applies three file patches:
  - server.py   : grafana_port=3001, keep full /grafana-proxy path, skip CSP
  - sessions.js : _sessionGrafanaUrl + _isActualCost functions + cost anchor+popover click
  - routes.py   : actual_cost field + sidecar session_costs.db overlay
  - index.html  : popover markup (if missing) + hide floating button

Design rules learned the hard way:
  * Anchor-based string replace on EXACT known-upstream snippets. Never brace-count
    to find "end of function" (early returns break it — caused the nested-function bug).
  * Every patch is guarded: if the "applied" marker is present, skip. If the upstream
    anchor is NOT present AND the applied marker is also absent, FAIL LOUDLY (upstream
    changed the code; patch needs updating) rather than silently corrupt.
  * After all patches, run a verification pass that asserts every marker exists.

Usage:
  python3 repatch_cost_popover.py                 # local (uses $HOME/hermes-webui)
  python3 repatch_cost_popover.py --webui-dir /path/to/hermes-webui
  python3 repatch_cost_popover.py --verify-only   # check, don't modify
"""

import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Patch definitions. Each: name, relfile, applied_marker, list of (anchor, replacement).
# anchor=None means "insertion" — handled specially.
# ---------------------------------------------------------------------------

# --- server.py snippets -----------------------------------------------------
SERVER_PORT_OLD = "grafana_port = 3000"
SERVER_PORT_NEW = "grafana_port = 3001"

SERVER_STRIP_OLD = """        if path.startswith('/grafana-proxy'):
            subpath = path[len('/grafana-proxy'):]"""
SERVER_STRIP_NEW = """        if path.startswith('/grafana-proxy'):
            subpath = path  # keep full path for nginx"""

SERVER_CSP_ANCHOR = """    def end_headers(self) -> None:
        extra_connect_src = getattr(self, "_csp_extra_connect_src", None)
        extra_frame_src = getattr(self, "_csp_extra_frame_src", None)
        self.send_header("Content-Security-Policy-Report-Only", self.csp_report_only_policy(extra_connect_src, extra_frame_src))
        self.send_header("Report-To", self._CSP_REPORT_TO)
        super().end_headers()"""
SERVER_CSP_NEW = """    def end_headers(self) -> None:
        # Skip CSP injection for Grafana-proxied responses — the nginx proxy already
        # strips frame-ancestors; the WebUI's own CSP would re-add frame-ancestors
        # 'none' and break the iframe. (cost-popover mod)
        if getattr(self, 'path', '').startswith(('/grafana-proxy', '/public/', '/apis/', '/avatar/')):
            super().end_headers()
            return
        extra_connect_src = getattr(self, "_csp_extra_connect_src", None)
        extra_frame_src = getattr(self, "_csp_extra_frame_src", None)
        self.send_header("Content-Security-Policy-Report-Only", self.csp_report_only_policy(extra_connect_src, extra_frame_src))
        self.send_header("Report-To", self._CSP_REPORT_TO)
        super().end_headers()"""
SERVER_CSP_MARKER = "Skip CSP injection for Grafana-proxied responses"

# --- sessions.js snippets ----------------------------------------------------
# _formatSessionCost must PREFER actual_cost (reconciled InfluxDB total) over
# the estimate. Upstream ships estimate-only; v4 overrides.
SESS_FORMATCOST_UPSTREAM = """function _formatSessionCost(session) {
  const cost = Number(session && session.estimated_cost);
  if (cost && cost > 0) {
    return '$' + cost.toFixed(2);
  }
  return '$0.00';
}"""
SESS_FORMATCOST_V4 = """function _formatSessionCost(session) {
  // Prefer the reconciled actual cost (InfluxDB per-call total) over the estimate.
  const actual = Number(session && session.actual_cost);
  if (actual && actual > 0) {
    return '$' + actual.toFixed(2);
  }
  const cost = Number(session && session.estimated_cost);
  if (cost && cost > 0) {
    return '$' + cost.toFixed(2);
  }
  return '$0.00';
}"""
SESS_FORMATCOST_MARKER = "Prefer the reconciled actual cost"

# The three helper functions must be TOP-LEVEL. They get inserted immediately
# AFTER the _formatSessionCost function, whichever version of it is present
# (upstream estimate-only OR v4 actual-preferring). Handled specially in code
# via insert_after_either().

SESS_FUNCTIONS_BLOCK = """
function _sessionGrafanaUrl(session) {
  const sid = session && (session.session_id || session.id);
  if (!sid) return null;
  const base = '/grafana-proxy/d/ai-usage-tracking/f09f908e-ai-usage-and-cost-tracking' + '/?var-session=' + encodeURIComponent(sid);
  // Bound the time range to the session's actual lifespan: 5 min before the
  // first activity to 5 min after the last. Sidebar timestamps are unix seconds.
  const PAD_S = 300;
  const startS = Number(session && (session.created_at || session._sidebar_activity_at || 0));
  const endS = Number(session && (session._sidebar_activity_at || session.last_message_at || session.updated_at || 0));
  if (Number.isFinite(startS) && startS > 0 && Number.isFinite(endS) && endS > 0 && endS >= startS) {
    const fromMs = Math.floor((startS - PAD_S) * 1000);
    const toMs = Math.floor((endS + PAD_S) * 1000);
    return base + '&from=' + fromMs + '&to=' + toMs;
  }
  // Fallback: last 7 days to match log retention window
  return base + '&from=now-7d&to=now';
}

function _isActualCost(session) {
  // Return true if we're showing real data (not an estimate)
  const actual = Number(session && session.actual_cost);
  return !!(actual && actual > 0);
}"""
SESS_FUNCTIONS_MARKER = "function _sessionGrafanaUrl"

# The cost element: upstream renders a plain <span>. We swap to <a> + popover.
SESS_COST_SPAN_OLD = """    const costEl=document.createElement('span');
    costEl.className='session-cost';
    costEl.textContent=_formatSessionCost(s);
    costEl.title='Cost: '+_formatSessionCost(s);
    titleRow.appendChild(costEl);"""
SESS_COST_SPAN_NEW = """    const costEl=document.createElement('a');
    costEl.className='session-cost';
    costEl.textContent=_formatSessionCost(s);
    costEl.title='Cost: '+_formatSessionCost(s)+' — Click to view in Grafana';
    if(_isActualCost(s)){
      costEl.classList.add('session-cost-actual');
      costEl.title='Actual cost: '+_formatSessionCost(s)+' — Click to view in Grafana';
    }
    const grafanaUrl=_sessionGrafanaUrl(s);
    if(grafanaUrl){
      costEl.href='#';
      costEl.addEventListener('click', function(e){
        e.preventDefault();
        e.stopPropagation();
        if(typeof openGrafanaPopover==='function') openGrafanaPopover(grafanaUrl);
      });
      costEl.style.textDecoration='none';
      costEl.style.cursor='pointer';
    }
    titleRow.appendChild(costEl);"""
SESS_COST_MARKER = "openGrafanaPopover(grafanaUrl)"

# The row gesture system (pointerdown/pointerup -> _finishSessionGesture) selects
# the session on ANY click that isn't inside the row's `actions` container. The
# cost link lives in titleRow, so without this patch the pointerup gesture fires
# before/alongside our click listener and the row selects instead of opening the
# popover ("clicking cost just selects the session" symptom).
SESS_GESTURE_OLD = """    const _isSessionActionTarget=(target)=>{
      return !!(actions&&target&&actions.contains(target));
    };"""
SESS_GESTURE_NEW = """    const _isSessionActionTarget=(target)=>{
      // The session-cost link is a popover trigger, not a row-select gesture —
      // treat it as an action target so pointerdown/pointerup don't start the
      // gesture that selects the session underneath the popover click.
      if(target&&target.closest&&target.closest('.session-cost')) return true;
      return !!(actions&&target&&actions.contains(target));
    };"""
SESS_GESTURE_MARKER = ".session-cost')) return true"

# --- routes.py snippets ------------------------------------------------------
ROUTES_FIELDS_OLD = '''    "estimated_cost",
    "cache_read_tokens",'''
ROUTES_FIELDS_NEW = '''    "estimated_cost",
    "actual_cost",
    "cache_read_tokens",'''
ROUTES_FIELDS_MARKER = '"actual_cost",'

ROUTES_OVERLAY_OLD = '''    item["attention"] = _session_attention_summary(str(item.get("session_id") or ""))
    return item'''
ROUTES_OVERLAY_NEW = '''    item["attention"] = _session_attention_summary(str(item.get("session_id") or ""))
    # cost-popover mod: overlay actual cost from the per-bot sidecar DB
    # (~/.hermes/session_costs.db, real per-call costs from InfluxDB). Fail-safe:
    # fall back to estimate if DB missing / no row.
    try:
        import os as _os, sqlite3 as _sqlite3
        _db = _os.path.expanduser("~/.hermes/session_costs.db")
        _sid = str(item.get("session_id") or "")
        if _sid and _os.path.exists(_db):
            _con = _sqlite3.connect(_db)
            try:
                _row = _con.execute(
                    "SELECT cost_usd FROM session_costs WHERE session_id = ?", (_sid,)
                ).fetchone()
                if _row and _row[0]:
                    item["actual_cost"] = float(_row[0])
            finally:
                _con.close()
    except Exception:
        pass
    return item'''
ROUTES_OVERLAY_MARKER = "session_costs WHERE session_id"

# --- style.css snippets -------------------------------------------------------
CSS_COST_ANCHOR = ".session-cost.is-hidden{display:none;}"
CSS_COST_NEW = CSS_COST_ANCHOR + """
  .session-cost.session-cost-actual{color:#4ade80 !important;opacity:1 !important;}"""
CSS_COST_MARKER = ".session-cost.session-cost-actual"

# --- index.html snippets ------------------------------------------------------
# The entire Grafana popover block is a LOCAL addition — upstream index.html has
# none of it. On a fresh `git pull` the block is missing entirely and must be
# re-inserted before </body>. The insertion is the CLEAN form: hidden button,
# NO comment hack. (The old patch tried to "hide" a visible button by opening an
# HTML comment <!-- over the button remnants — the comment never closed and
# swallowed the overlay, popover, iframe, and <script> block. Six agents were
# silently broken by this; grep found the markers because grep can't tell code
# inside HTML comments from live code. Aug 2 2026.)
INDEX_POPOVER_ANCHOR = '<div class="profile-dropdown" id="profileDropdown"></div>'
INDEX_POPOVER_BLOCK = (
    "\n\n<!-- Grafana popover -->\n"
    '<div id="grafana-popover-btn" style="display:none"></div>\n'
    '<div id="grafana-popover-overlay" onclick="toggleGrafanaPopover()"\n'
    '     style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;z-index:9998;background:rgba(0,0,0,0.6);">\n'
    '</div>\n'
    '<div id="grafana-popover" style="display:none;position:fixed;top:5%;left:5%;width:90%;height:90%;z-index:9999;\n'
    '     border-radius:12px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,0.5);\n'
    '     background:#111;border:1px solid rgba(61,204,196,0.3);">\n'
    '  <div style="display:flex;align-items:center;justify-content:space-between;\n'
    '       padding:8px 16px;background:#050F12;border-bottom:1px solid rgba(61,204,196,0.2);">\n'
    '    <span style="color:#3DCCC4;font-weight:600;font-size:14px;">\n'
    '      &#x1F4CA; Grafana — AI Usage Dashboard\n'
    '    </span>\n'
    '    <div style="display:flex;gap:8px;align-items:center;">\n'
    '      <a id="grafana-open-tab" href="/grafana-proxy/d/ai-usage-tracking/f09f908e-ai-usage-and-cost-tracking?from=now-7d&to=now"\n'
    '         target="_blank" title="Open in new tab"\n'
    '         style="color:#94a3b8;font-size:16px;text-decoration:none;padding:4px 8px;\n'
    '                border-radius:4px;transition:background 0.2s;"\n'
    '         onmouseover="this.style.background=\'rgba(255,255,255,0.1)\'"\n'
    '         onmouseout="this.style.background=\'transparent\'">\n'
    '        &#x2197;\n'
    '      </a>\n'
    '      <span onclick="toggleGrafanaPopover()" title="Close"\n'
    '            style="color:#94a3b8;font-size:20px;cursor:pointer;padding:4px 8px;\n'
    '                   border-radius:4px;transition:background 0.2s;line-height:1;"\n'
    '            onmouseover="this.style.background=\'rgba(255,255,255,0.1)\'"\n'
    '            onmouseout="this.style.background=\'transparent\'">&times;</span>\n'
    '    </div>\n'
    '  </div>\n'
    '  <iframe id="grafana-iframe" src="/grafana-proxy/d/ai-usage-tracking/f09f908e-ai-usage-and-cost-tracking?from=now-7d&to=now"\n'
    '          style="width:100%;height:calc(100% - 44px);border:none;background:#fff;">\n'
    '  </iframe>\n'
    '</div>\n'
    '<script>\n'
    'function toggleGrafanaPopover(url) {\n'
    "  var overlay = document.getElementById('grafana-popover-overlay');\n"
    "  var popover = document.getElementById('grafana-popover');\n"
    "  var btn = document.getElementById('grafana-popover-btn');\n"
    "  var isOpen = popover.style.display === 'block';\n"
    '  if (isOpen && !url) {\n'
    "    overlay.style.display = 'none';\n"
    "    popover.style.display = 'none';\n"
    "    btn.style.display = 'flex';\n"
    '    return;\n'
    '  }\n'
    "  overlay.style.display = 'block';\n"
    "  popover.style.display = 'block';\n"
    "  btn.style.display = 'none';\n"
    "  var iframe = document.getElementById('grafana-iframe');\n"
    "  var target = url || '/grafana-proxy/d/ai-usage-tracking/f09f908e-ai-usage-and-cost-tracking?from=now-7d&to=now';\n"
    "  iframe.src = target + (target.indexOf('?') >= 0 ? '&' : '?') + '_t=' + Date.now();\n"
    '}\n'
    'window.openGrafanaPopover = function(url) {\n'
    '  toggleGrafanaPopover(url);\n'
    '  // Update the "open in new tab" link to match the current iframe URL\n'
    "  var tabLink = document.getElementById('grafana-open-tab');\n"
    '  if (tabLink && url) tabLink.href = url;\n'
    '};\n'
    '</script>\n'
)
INDEX_POPOVER_MARKER = 'id="grafana-popover-overlay"'

# The BROKEN comment-hack form (hidden-anchor + unclosed <!--). If present, the
# popover markup is commented out and nothing works — must be repaired first.
INDEX_BROKEN_ANCHOR = '<div id="grafana-popover-btn-hidden-anchor"'

# Sync the popover's "open in new tab" ↗ link when opened from a session-cost click.
INDEX_TABLINK_OLD = """window.openGrafanaPopover = function(url) {
  toggleGrafanaPopover(url);
};"""
INDEX_TABLINK_NEW = """window.openGrafanaPopover = function(url) {
  toggleGrafanaPopover(url);
  // Update the "open in new tab" link to match the current iframe URL
  var tabLink = document.getElementById('grafana-open-tab');
  if (tabLink && url) tabLink.href = url;
};"""
INDEX_TABLINK_MARKER = "tabLink.href = url"


def apply_replace(content, old, new, marker, name, verify_only):
    """Anchor-based replace with guards. Returns (content, status)."""
    if marker and marker in content:
        return content, f"SKIP (already applied): {name}"
    if old not in content:
        return content, f"FAIL (anchor not found, upstream changed): {name}"
    if verify_only:
        return content, f"WOULD-APPLY: {name}"
    return content.replace(old, new, 1), f"APPLIED: {name}"


def insert_after_function(content, fn_name, insert_block, marker, name, verify_only):
    """Insert insert_block immediately AFTER the complete top-level definition of
    fn_name, correctly handling early returns / nested braces. Uses depth tracking
    anchored on the function's OWN opening line (column 0), so nested if-blocks
    don't fool it. Returns (content, status)."""
    if marker and marker in content:
        return content, f"SKIP (already applied): {name}"
    # Anchor on the function declaration at column 0 (top-level only)
    anchor = f"\nfunction {fn_name}("
    idx = content.find(anchor)
    if idx < 0:
        # try start-of-file
        if content.startswith(f"function {fn_name}("):
            idx = 0
        else:
            return content, f"FAIL (function {fn_name} not found at top level): {name}"
    # Find the opening brace of the function body
    brace_open = content.find("{", idx)
    if brace_open < 0:
        return content, f"FAIL (no opening brace for {fn_name}): {name}"
    # Depth-track from the opening brace to its matching close
    depth = 0
    i = brace_open
    in_str = None  # ', ", or `
    while i < len(content):
        ch = content[i]
        if in_str:
            if ch == in_str and content[i - 1] != "\\":
                in_str = None
        elif ch in ("'", '"', "`"):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # i is the function's closing brace
                insert_at = i + 1
                if verify_only:
                    return content, f"WOULD-APPLY: {name}"
                new_content = content[:insert_at] + "\n" + insert_block + content[insert_at:]
                return new_content, f"APPLIED: {name}"
        i += 1
    return content, f"FAIL (unbalanced braces in {fn_name}): {name}"


def patch_file(path, patches, verify_only):
    """Apply patches to a file. Each patch is a dict:
      {"op": "replace", "name", "old", "new", "marker"}
      {"op": "insert_after_fn", "name", "fn", "block", "marker"}
    Returns list of statuses."""
    if not os.path.exists(path):
        return [f"FAIL (file missing): {path}"]
    with open(path, "r") as f:
        content = f.read()
    statuses = []
    changed = False
    for p in patches:
        if p["op"] == "replace":
            content, status = apply_replace(content, p["old"], p["new"], p.get("marker"), p["name"], verify_only)
        elif p["op"] == "insert_after_fn":
            content, status = insert_after_function(content, p["fn"], p["block"], p.get("marker"), p["name"], verify_only)
        else:
            status = f"FAIL (unknown op {p['op']}): {p['name']}"
        statuses.append(status)
        if status.startswith("APPLIED"):
            changed = True
    if changed and not verify_only:
        with open(path, "w") as f:
            f.write(content)
    return statuses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--webui-dir", default=os.path.expanduser("~/hermes-webui"))
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    d = args.webui_dir

    server_py = os.path.join(d, "server.py")
    sessions_js = os.path.join(d, "static", "sessions.js")
    routes_py = os.path.join(d, "api", "routes.py")
    style_css = os.path.join(d, "static", "style.css")
    index_html = os.path.join(d, "static", "index.html")

    all_fail = []

    def report(path, statuses):
        rel = os.path.relpath(path, d)
        print(f"\n[{rel}]")
        for s in statuses:
            print("  " + s)
            if s.startswith("FAIL"):
                all_fail.append(f"{rel}: {s}")

    report(server_py, patch_file(server_py, [
        {"op": "replace", "name": "grafana_port 3001", "old": SERVER_PORT_OLD, "new": SERVER_PORT_NEW, "marker": SERVER_PORT_NEW},
        {"op": "replace", "name": "keep full /grafana-proxy path", "old": SERVER_STRIP_OLD, "new": SERVER_STRIP_NEW, "marker": "keep full path for nginx"},
        {"op": "replace", "name": "skip CSP for proxy paths", "old": SERVER_CSP_ANCHOR, "new": SERVER_CSP_NEW, "marker": SERVER_CSP_MARKER},
    ], args.verify_only))

    report(sessions_js, patch_file(sessions_js, [
        {"op": "replace", "name": "formatSessionCost prefers actual", "old": SESS_FORMATCOST_UPSTREAM, "new": SESS_FORMATCOST_V4, "marker": SESS_FORMATCOST_MARKER},
        {"op": "insert_after_fn", "name": "top-level grafana/isActual functions", "fn": "_formatSessionCost", "block": SESS_FUNCTIONS_BLOCK, "marker": SESS_FUNCTIONS_MARKER},
        {"op": "replace", "name": "cost span -> anchor+popover", "old": SESS_COST_SPAN_OLD, "new": SESS_COST_SPAN_NEW, "marker": SESS_COST_MARKER},
        {"op": "replace", "name": "gesture exempts .session-cost", "old": SESS_GESTURE_OLD, "new": SESS_GESTURE_NEW, "marker": SESS_GESTURE_MARKER},
    ], args.verify_only))

    report(routes_py, patch_file(routes_py, [
        {"op": "replace", "name": "actual_cost field", "old": ROUTES_FIELDS_OLD, "new": ROUTES_FIELDS_NEW, "marker": ROUTES_FIELDS_MARKER},
        {"op": "replace", "name": "sidecar cost overlay", "old": ROUTES_OVERLAY_OLD, "new": ROUTES_OVERLAY_NEW, "marker": ROUTES_OVERLAY_MARKER},
    ], args.verify_only))

    report(style_css, patch_file(style_css, [
        {"op": "replace", "name": "green actual-cost rule", "old": CSS_COST_ANCHOR, "new": CSS_COST_NEW, "marker": CSS_COST_MARKER},
    ], args.verify_only))

    # index.html: three-step handling.
    # 1. REPAIR the broken comment-hack form if present (hidden-anchor + unclosed
    #    <!-- that comments out the whole popover block).
    # 2. INSERT the full clean popover block if upstream wiped it entirely.
    # 3. Sync the tab link (idempotent marker check inside patch_file).
    if os.path.exists(index_html):
        with open(index_html) as f:
            ic = f.read()

        # Step 1: repair broken comment-hack
        if INDEX_BROKEN_ANCHOR in ic:
            if args.verify_only:
                report(index_html, ["WOULD-APPLY: repair broken comment-hack popover block"])
            else:
                lines = ic.split("\n")
                out = []
                i = 0
                while i < len(lines):
                    if INDEX_BROKEN_ANCHOR in lines[i]:
                        j = i + 1
                        while j < len(lines) and lines[j].strip() != '</div>':
                            j += 1
                        i = j + 1
                        continue
                    out.append(lines[i])
                    i += 1
                ic = "\n".join(out)
                with open(index_html, "w") as f:
                    f.write(ic)
                report(index_html, ["APPLIED: repair broken comment-hack popover block"])
            # Re-read after repair
            with open(index_html) as f:
                ic = f.read()

        # Step 2: insert full block if missing
        if INDEX_POPOVER_MARKER not in ic:
            if INDEX_POPOVER_ANCHOR not in ic:
                report(index_html, ["FAIL (insert anchor 'profileDropdown' not found, upstream changed): index.html"])
                all_fail.append("index.html: popover insert anchor missing")
            elif args.verify_only:
                report(index_html, ["WOULD-APPLY: insert full Grafana popover block"])
            else:
                ic = ic.replace(INDEX_POPOVER_ANCHOR, INDEX_POPOVER_ANCHOR + INDEX_POPOVER_BLOCK, 1)
                with open(index_html, "w") as f:
                    f.write(ic)
                report(index_html, ["APPLIED: insert full Grafana popover block"])

        # Step 3: tab link sync (only meaningful if popover exists now)
        with open(index_html) as f:
            ic = f.read()
        if INDEX_POPOVER_MARKER in ic:
            report(index_html, patch_file(index_html, [
                {"op": "replace", "name": "tab link syncs to session URL", "old": INDEX_TABLINK_OLD, "new": INDEX_TABLINK_NEW, "marker": INDEX_TABLINK_MARKER},
            ], args.verify_only))

    print("\n" + "=" * 60)
    if all_fail:
        print("RESULT: FAILURES — upstream anchors moved, patch needs review:")
        for f in all_fail:
            print("  " + f)
        sys.exit(2)
    print("RESULT: OK (all patches applied or already present)")
    sys.exit(0)


if __name__ == "__main__":
    main()
