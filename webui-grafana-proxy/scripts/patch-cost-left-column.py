#!/usr/bin/env python3
"""
Fleet-wide WebUI patch: add session cost ($X.XX) as a NEW left column in sidebar.
Reverts the old right-column replacement if present, then applies the new approach.

Usage:
  python3 patch-cost-left-column.py              # patch local WebUI
  python3 patch-cost-left-column.py --remote mayano   # patch via SSH
  python3 patch-cost-left-column.py --all             # patch all known bots
"""
import os
import sys
import subprocess
import tempfile

SESSIONS_JS_PATH = "hermes-webui/static/sessions.js"
STYLE_CSS_PATH = "hermes-webui/static/style.css"

# ── Old patch patterns (to revert) ──
OLD_PATCH_LINE = "    ts.textContent=hasAttentionState?'':_formatSessionCost(s, tsMs);\n    if(!hasAttentionState && s && s.estimated_cost > 0) ts.title=_formatRelativeSessionTime(tsMs);"
ORIGINAL_LINE = "    ts.textContent=hasAttentionState?'':_formatRelativeSessionTime(tsMs);"
OLD_FUNC_SIG = "function _formatSessionCost(session, timestampMs) {"

# ── New patch patterns ──
NEW_FUNC = """function _formatSessionCost(session) {
  const cost = Number(session && session.estimated_cost);
  if (cost && cost > 0) {
    return '$' + cost.toFixed(2);
  }
  return '$0.00';
}"""

# Marker: end of _formatRelativeSessionTime function
FUNC_END_MARKER = "  return _formatSessionDate(timestampMs, nowMs);\n}"

# New cost element insertion (after titleRow creation + pin indicator)
NEW_COST_INSERT_AFTER = """    const titleRow=document.createElement('div');
    titleRow.className='session-title-row';
    if(s.pinned&&!isPinnedGroup){
      const pinInd=document.createElement('span');
      pinInd.className='session-pin-indicator';
      pinInd.innerHTML=ICONS.pin;
      titleRow.appendChild(pinInd);
    }"""

NEW_COST_ELEMENT = """    // Cost column — after pin indicator, before worktree/branch indicators
    const costEl=document.createElement('span');
    costEl.className='session-cost';
    costEl.textContent=_formatSessionCost(s);
    costEl.title='Cost: '+_formatSessionCost(s);
    titleRow.appendChild(costEl);"""

# Child label pattern
CHILD_LABEL_OLD = """      const childLabelFor=(child)=>{
        const childTitle=_sessionDisplayTitle(child)||'Untitled child session';
        const childTime=_formatRelativeSessionTime(_sessionTimestampMs(child));
        const parentNote=child._parent_segment_title?` via ${child._parent_segment_title}`:'';
        return `-> ${childTitle}${parentNote} - ${childTime}`;
      };"""

CHILD_LABEL_NEW = """      const childLabelFor=(child)=>{
        const childTitle=_sessionDisplayTitle(child)||'Untitled child session';
        const childTime=_formatRelativeSessionTime(_sessionTimestampMs(child));
        const childCost=_formatSessionCost(child);
        const parentNote=child._parent_segment_title?` via ${child._parent_segment_title}`:'';
        return `${childCost} -> ${childTitle}${parentNote} - ${childTime}`;
      };"""

# Lineage segment pattern
LINEAGE_SEG_OLD = """        const segTitle=_sessionDisplayTitle(seg)||t('session_lineage_segment_untitled');
        row.textContent=`-> ${segTitle} - ${segTime}`;"""

LINEAGE_SEG_NEW = """        const segTitle=_sessionDisplayTitle(seg)||t('session_lineage_segment_untitled');
        const segTime=_formatRelativeSessionTime(_sessionTimestampMs(seg));
        const segCost=_formatSessionCost(seg);
        row.textContent=`${segCost} -> ${segTitle} - ${segTime}`;"""

# CSS for .session-cost
CSS_MARKER = """  .session-time.is-hidden{display:none;}"""

CSS_ADD = """  .session-cost{
    display:inline-flex;
    min-width:48px;
    justify-content:flex-end;
    color:var(--muted);
    font-size:10px;
    font-variant-numeric:tabular-nums;
    white-space:nowrap;
    flex-shrink:0;
    opacity:.7;
  }
  .session-cost.is-hidden{display:none;}"""

# Fleet definition
FLEET = {
    "shichi": {"home": "/home/shichi", "host": None},
    "mayano": {"home": "/home/mayano", "host": "192.168.49.17"},
    "satono": {"home": "/home/satono", "host": "192.168.49.21"},
    "robroy": {"home": "/home/robroy", "host": "192.168.49.18"},
    "suzuka": {"home": "/home/suzuka", "host": "192.168.49.23"},
    "dantsu": {"home": "/home/dantsu", "host": "192.168.49.24"},
    "bright": {"home": "/home/bright", "host": "192.168.49.15"},
}


def patch_sessions_js(filepath, dry_run=False):
    """Apply all sessions.js patches. Returns True if changes were made."""
    with open(filepath, "r") as f:
        content = f.read()

    changes = []

    # Step 1: Revert old right-column patch if present
    if OLD_PATCH_LINE in content:
        content = content.replace(OLD_PATCH_LINE, ORIGINAL_LINE)
        changes.append("reverted old right-column cost patch")

    # Step 2: Remove old _formatSessionCost(session, timestampMs) function if present
    if OLD_FUNC_SIG in content:
        # Find and remove the old function (from sig to closing brace)
        start = content.find(OLD_FUNC_SIG)
        if start >= 0:
            # Find the closing brace of this function
            depth = 0
            i = start
            func_end = -1
            while i < len(content):
                if content[i] == '{':
                    depth += 1
                elif content[i] == '}':
                    depth -= 1
                    if depth == 0:
                        func_end = i + 1
                        break
                i += 1
            if func_end > 0:
                # Also remove surrounding blank lines
                remove_start = start
                remove_end = func_end
                # Extend backwards to eat preceding newlines
                while remove_start > 0 and content[remove_start - 1] == '\n':
                    remove_start -= 1
                # Extend forwards to eat trailing newlines
                while remove_end < len(content) and content[remove_end] == '\n':
                    remove_end += 1
                content = content[:remove_start] + content[remove_end:]
                changes.append("removed old _formatSessionCost(session, timestampMs)")

    # Step 3: Add new _formatSessionCost(session) function
    if "function _formatSessionCost" not in content:
        marker_idx = content.find(FUNC_END_MARKER)
        if marker_idx >= 0:
            insert_at = marker_idx + len(FUNC_END_MARKER)
            content = content[:insert_at] + "\n\n" + NEW_FUNC + content[insert_at:]
            changes.append("added new _formatSessionCost(session)")
        else:
            print("  ❌ Could not find end of _formatRelativeSessionTime")

    # Step 4: Add cost element to title row
    if NEW_COST_ELEMENT not in content:
        if NEW_COST_INSERT_AFTER in content:
            content = content.replace(NEW_COST_INSERT_AFTER, NEW_COST_INSERT_AFTER + "\n" + NEW_COST_ELEMENT)
            changes.append("added cost element to title row")
        else:
            print("  ⚠️  Could not find title row pin indicator insertion point")
            # Check if already patched without pin indicator
            if "session-cost" in content:
                print("     ✓ Cost element already present (different insertion point)")
                changes.append("cost element already present")

    # Step 5: Patch child label
    if CHILD_LABEL_OLD in content:
        content = content.replace(CHILD_LABEL_OLD, CHILD_LABEL_NEW)
        changes.append("patched child label to include cost")
    elif CHILD_LABEL_NEW in content:
        changes.append("child label already patched")

    # Step 6: Patch lineage segment label
    if LINEAGE_SEG_OLD in content:
        content = content.replace(LINEAGE_SEG_OLD, LINEAGE_SEG_NEW)
        changes.append("patched lineage segment to include cost")
    elif LINEAGE_SEG_NEW in content:
        changes.append("lineage segment already patched")

    if dry_run:
        print(f"  Dry run: {len(changes)} change(s) would be made")
        return bool(changes)

    if not changes:
        return False

    # Write backup
    bak = filepath + ".bak"
    if not os.path.exists(bak):
        with open(bak, "w") as f:
            with open(filepath, "r") as src:
                f.write(src.read())
        print(f"  ✓ Backup saved: {bak}")

    with open(filepath, "w") as f:
        f.write(content)

    print(f"  ✓ Applied {len(changes)} change(s)")
    return True


def patch_style_css(filepath, dry_run=False):
    """Add .session-cost CSS to style.css. Returns True if changes were made."""
    with open(filepath, "r") as f:
        content = f.read()

    if ".session-cost{" in content:
        print("  ✓ .session-cost CSS already present")
        return False

    if CSS_MARKER not in content:
        print("  ❌ Could not find .session-time.is-hidden marker in style.css")
        return False

    if dry_run:
        print("  Dry run: would add .session-cost CSS")
        return True

    # Write backup
    bak = filepath + ".bak"
    if not os.path.exists(bak):
        with open(bak, "w") as f:
            with open(filepath, "r") as src:
                f.write(src.read())
        print(f"  ✓ Backup saved: {bak}")

    content = content.replace(CSS_MARKER, CSS_MARKER + "\n" + CSS_ADD)

    with open(filepath, "w") as f:
        f.write(content)

    print("  ✓ Added .session-cost CSS")
    return True


def restart_webui(bot_name, host=None):
    """Restart the Hermes WebUI service on the target."""
    if host:
        cmd = f"ssh -o StrictHostKeyChecking=no {bot_name}@{host} 'systemctl --user restart hermes-webui.service 2>&1'"
    else:
        cmd = "systemctl --user restart hermes-webui.service 2>&1"

    print(f"  🔄 Restarting WebUI on {bot_name}...")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
    if r.returncode == 0 or r.returncode == 143 or r.returncode == -15:
        print(f"  ✅ WebUI restarted")
    else:
        print(f"  ⚠️  Restart returned {r.returncode}: {r.stderr[:200]}")


def patch_local():
    """Patch the local WebUI instance."""
    home = os.path.expanduser("~")
    js_path = os.path.join(home, SESSIONS_JS_PATH)
    css_path = os.path.join(home, STYLE_CSS_PATH)

    if not os.path.exists(js_path):
        print(f"❌ {js_path} not found")
        return False

    print(f"\n📁 Patching local: {js_path}")
    js_changed = patch_sessions_js(js_path)

    if os.path.exists(css_path):
        print(f"📁 Patching local: {css_path}")
        css_changed = patch_style_css(css_path)
    else:
        css_changed = False

    return js_changed or css_changed


def patch_remote(bot_name, info):
    """Patch a remote bot's WebUI via SSH."""
    home = info["home"]
    host = info["host"]
    remote_js = f"{home}/{SESSIONS_JS_PATH}"
    remote_css = f"{home}/{STYLE_CSS_PATH}"

    print(f"\n📁 Patching {bot_name} @ {host}")

    # Create temp files
    js_tmp = tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w")
    js_tmp_path = js_tmp.name
    js_tmp.close()

    css_tmp = tempfile.NamedTemporaryFile(suffix=".css", delete=False, mode="w")
    css_tmp_path = css_tmp.name
    css_tmp.close()

    try:
        # Fetch sessions.js
        r = subprocess.run(
            f"scp -o StrictHostKeyChecking=no {bot_name}@{host}:{remote_js} {js_tmp_path}",
            shell=True, capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            print(f"  ❌ Failed to fetch sessions.js: {r.stderr[:200]}")
            return False

        # Fetch style.css
        r = subprocess.run(
            f"scp -o StrictHostKeyChecking=no {bot_name}@{host}:{remote_css} {css_tmp_path}",
            shell=True, capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            print(f"  ❌ Failed to fetch style.css: {r.stderr[:200]}")
            return False

        # Patch sessions.js
        js_changed = patch_sessions_js(js_tmp_path)
        if js_changed:
            r = subprocess.run(
                f"scp -o StrictHostKeyChecking=no {js_tmp_path} {bot_name}@{host}:{remote_js}",
                shell=True, capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                print(f"  ❌ Failed to push sessions.js: {r.stderr[:200]}")
                return False
            print(f"  ✅ sessions.js updated")

        # Patch style.css
        css_changed = patch_style_css(css_tmp_path)
        if css_changed:
            r = subprocess.run(
                f"scp -o StrictHostKeyChecking=no {css_tmp_path} {bot_name}@{host}:{remote_css}",
                shell=True, capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                print(f"  ❌ Failed to push style.css: {r.stderr[:200]}")
                return False
            print(f"  ✅ style.css updated")

        # Restart
        if js_changed or css_changed:
            restart_webui(bot_name, host)

    finally:
        for p in [js_tmp_path, css_tmp_path]:
            if os.path.exists(p):
                os.unlink(p)

    return True


def main():
    targets = []
    if "--all" in sys.argv:
        targets = [(k, v) for k, v in FLEET.items() if k != "shichi"]
    elif "--remote" in sys.argv:
        idx = sys.argv.index("--remote") + 1
        if idx < len(sys.argv):
            name = sys.argv[idx].lower()
            if name in FLEET:
                targets = [(name, FLEET[name])]
            else:
                print(f"❌ Unknown bot '{name}'. Known: {', '.join(FLEET.keys())}")
                return 1
        else:
            print("❌ --remote requires a bot name")
            return 1
    else:
        targets = [("local", {"home": os.path.expanduser("~"), "host": None})]

    for name, info in targets:
        if info["host"]:
            patch_remote(name, info)
        else:
            patch_local()

    print("\n✅ Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
