#!/usr/bin/env python3
"""
Hermes WebUI post-update restore orchestrator.

Run this after EVERY `hermes update` / `git pull` on a WebUI. It re-applies
ALL local customizations and regression-tests them:

  1. Grafana cost-popover mod  (server.py, sessions.js, routes.py, style.css, index.html)
  2. Fleet theme/skin           (style.css, boot.js, api/config.py)

Both sub-patches are idempotent and anchor-based. If an upstream anchor has
moved (upstream changed the code we patch), the script FAILS LOUDLY and tells
you exactly which patch to review — instead of silently corrupting the file.

Usage (run on the TARGET agent):
  python3 webui_post_update_restore.py --host dantsu \
      --webui ~/hermes-webui \
      --cost-script /path/to/repatch_cost_popover.py \
      --themes /path/to/fleet-themes

  # Verify-only (check state, change nothing):
  python3 webui_post_update_restore.py --host dantsu --verify-only

  # Skip one layer:
  python3 webui_post_update_restore.py --host dantsu --skip-cost --skip-theme

Exit codes:
  0 = all patches present/applied, all checks passed
  2 = an anchor moved / a patch failed — needs manual review
"""

import argparse
import os
import subprocess
import sys

DEFAULT_COST_SCRIPT = os.path.expanduser("~/workspace/grafana-webui-proxy/scripts/repatch_cost_popover.py")
DEFAULT_THEMES = os.path.expanduser("~/workspace/fleet-themes")
DEFAULT_THEME_SCRIPT = "skins/scripts/repatch_theme.py"


def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode, r.stdout, r.stderr


def layer_cost(webui, cost_script, verify_only):
    print("\n" + "=" * 60)
    print("LAYER 1: Grafana cost-popover mod")
    print("=" * 60)
    if not os.path.exists(cost_script):
        print(f"FAIL: cost script not found at {cost_script}")
        return False
    cmd = [sys.executable, cost_script, "--webui-dir", webui]
    if verify_only:
        cmd.append("--verify-only")
    rc, out, err = run(cmd)
    print(out)
    if err.strip():
        print(err)
    return rc == 0


def layer_theme(host, webui, themes, verify_only):
    print("\n" + "=" * 60)
    print(f"LAYER 2: Fleet theme/skin ({host})")
    print("=" * 60)
    script = os.path.join(themes, DEFAULT_THEME_SCRIPT)
    defn = os.path.join(themes, "skins", "definitions", f"{host}.skin.json")
    if not os.path.exists(defn):
        print(f"SKIP: no skin definition for host '{host}' ({defn})")
        return True
    if not os.path.exists(script):
        print(f"FAIL: theme restore script not found at {script}")
        return False
    cmd = [sys.executable, script, "--host", host, "--themes", themes, "--webui", webui]
    if verify_only:
        cmd.append("--verify-only")
    rc, out, err = run(cmd)
    print(out)
    if err.strip():
        print(err)
    return rc == 0


def regression_checks(webui):
    """Lightweight sanity checks after patching. Returns True if all pass."""
    print("\n" + "=" * 60)
    print("REGRESSION CHECKS")
    print("=" * 60)
    ok = True

    # 1. server.py parses
    rc, out, err = run([sys.executable, "-c",
        "import ast,sys; ast.parse(open(sys.argv[1]).read())", os.path.join(webui, "server.py")])
    print(f"  server.py parses: {'OK' if rc == 0 else 'FAIL'}")
    if rc != 0:
        print("    " + err.strip().splitlines()[-1] if err.strip() else "")
        ok = False

    # 2. style.css brace balance
    css = open(os.path.join(webui, "static", "style.css")).read()
    bal = css.count("{") - css.count("}")
    print(f"  style.css brace balance: {bal} ({'OK' if bal == 0 else 'FAIL'})")
    if bal != 0:
        ok = False

    # 3. sessions.js: the three functions are top-level (not nested)
    sj = open(os.path.join(webui, "static", "sessions.js")).read()
    import re
    nested = re.search(r"\n {2,}function _sessionGrafanaUrl", sj)  # indented = nested = bad
    print(f"  _sessionGrafanaUrl top-level: {'OK' if 'function _sessionGrafanaUrl' in sj and not nested else 'FAIL (nested or missing)'}")
    if "function _sessionGrafanaUrl" not in sj or nested:
        ok = False

    # 4. routes.py parses
    rp = os.path.join(webui, "api", "routes.py")
    if os.path.exists(rp):
        rc, out, err = run([sys.executable, "-c",
            "import ast,sys; ast.parse(open(sys.argv[1]).read())", rp])
        print(f"  routes.py parses: {'OK' if rc == 0 else 'FAIL'}")
        if rc != 0:
            ok = False

    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--webui", default=os.path.expanduser("~/hermes-webui"))
    ap.add_argument("--cost-script", default=DEFAULT_COST_SCRIPT)
    ap.add_argument("--themes", default=DEFAULT_THEMES)
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--skip-cost", action="store_true")
    ap.add_argument("--skip-theme", action="store_true")
    ap.add_argument("--skip-checks", action="store_true")
    args = ap.parse_args()

    print(f"WebUI post-update restore — host={args.host} webui={args.webui}")
    if args.verify_only:
        print("(verify-only mode: no changes will be made)")

    ok = True
    if not args.skip_cost:
        ok = layer_cost(args.webui, args.cost_script, args.verify_only) and ok
    if not args.skip_theme:
        ok = layer_theme(args.host, args.webui, args.themes, args.verify_only) and ok

    if not args.skip_checks and not args.verify_only:
        ok = regression_checks(args.webui) and ok

    print("\n" + "=" * 60)
    if ok:
        print("RESULT: ✅ all layers present/applied, checks passed")
        if not args.verify_only:
            print("Next: systemctl --user restart hermes-webui.service")
        sys.exit(0)
    else:
        print("RESULT: ❌ FAILURES above — review before restarting the WebUI")
        sys.exit(2)


if __name__ == "__main__":
    main()
