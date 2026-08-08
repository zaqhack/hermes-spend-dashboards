#!/usr/bin/env python3
"""
Fleet driver for webui_post_update_restore.py.

Pushes the patch scripts + per-agent theme files to each target over SSH,
runs the orchestrator remotely, and reports per-agent results. Run from
Shichi (which holds the canonical repos).

Usage:
  python3 fleet_restore.py --all
  python3 fleet_restore.py --host dantsu
  python3 fleet_restore.py --all --verify-only
  python3 fleet_restore.py --all --restart   # also restart each WebUI after patching

Canonical repos on Shichi:
  cost/popover:  ~/workspace/grafana-webui-proxy/scripts/
  themes:        ~/workspace/fleet-themes/
"""

import argparse
import os
import subprocess
import sys

FLEET = {
    "shichi":  "shichi@192.168.49.20",
    "mayano":  "mayano@192.168.49.17",
    "satono":  "satono@192.168.49.21",
    "robroy":  "robroy@192.168.49.18",
    "suzuka":  "suzuka@192.168.49.23",
    "dantsu":  "dantsu@192.168.49.24",
    "bright":  "bright@192.168.49.15",
}

COST_REPO = os.path.expanduser("~/workspace/grafana-webui-proxy/scripts")
THEMES_REPO = os.path.expanduser("~/workspace/fleet-themes")

FILES_TO_PUSH = [
    (f"{COST_REPO}/repatch_cost_popover.py", "/tmp/repatch_cost_popover.py"),
    (f"{COST_REPO}/webui_post_update_restore.py", "/tmp/webui_post_update_restore.py"),
    (f"{THEMES_REPO}/skins/scripts/repatch_theme.py", "/tmp/repatch_theme.py"),
]


def sh(cmd, timeout=120):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def push(host, target):
    # scripts
    for src, dst in FILES_TO_PUSH:
        rc, out, err = sh(["scp", "-q", src, f"{target}:{dst}"])
        if rc != 0:
            return False, f"scp failed {src}: {err.strip()}"
    # theme definition + css + assets for this host
    defn = f"{THEMES_REPO}/skins/definitions/{host}.skin.json"
    css = f"{THEMES_REPO}/skins/css/{host}.css"
    staged = []
    if os.path.exists(defn):
        sh(["scp", "-q", defn, f"{target}:/tmp/{host}.skin.json"])
        staged.append("defn")
    if os.path.exists(css):
        sh(["scp", "-q", css, f"{target}:/tmp/{host}.css"])
        staged.append("css")
    # avatar + favicon named in the definition
    if os.path.exists(defn):
        import json
        d = json.load(open(defn))
        av = f"{THEMES_REPO}/avatars/{d['avatar']}"
        fv = f"{THEMES_REPO}/favicons/{d['favicon']}"
        if os.path.exists(av):
            sh(["scp", "-q", av, f"{target}:/tmp/{d['avatar']}"])
            staged.append("avatar")
        if os.path.exists(fv):
            sh(["scp", "-q", fv, f"{target}:/tmp/{d['favicon']}"])
            staged.append("favicon")
    return True, f"pushed ({','.join(staged)})"


def run_remote(host, target, verify_only, restart):
    # Build a small remote runner that points the orchestrator at /tmp files
    remote_themes = "/tmp/_fleet_themes"
    setup = (
        f"mkdir -p {remote_themes}/skins/definitions {remote_themes}/skins/css "
        f"{remote_themes}/skins/scripts {remote_themes}/avatars {remote_themes}/favicons && "
        f"cp /tmp/{host}.skin.json {remote_themes}/skins/definitions/ 2>/dev/null; "
        f"cp /tmp/{host}.css {remote_themes}/skins/css/ 2>/dev/null; "
        f"cp /tmp/repatch_theme.py {remote_themes}/skins/scripts/ 2>/dev/null; "
        f"cp /tmp/*.png {remote_themes}/avatars/ 2>/dev/null; "
        f"cp /tmp/*_flavicon.svg {remote_themes}/favicons/ 2>/dev/null; "
    )
    cmd = setup + (
        f"python3 /tmp/webui_post_update_restore.py --host {host} "
        f"--webui ~/hermes-webui --cost-script /tmp/repatch_cost_popover.py "
        f"--themes {remote_themes}"
        + (" --verify-only" if verify_only else "")
    )
    rc, out, err = sh(["ssh", target, cmd])
    result = out + (("\n" + err) if err.strip() else "")
    if restart and not verify_only and rc == 0:
        rrc, rout, rerr = sh(["ssh", target,
            "systemctl --user restart hermes-webui.service && sleep 3 && systemctl --user is-active hermes-webui.service"])
        result += f"\n  restart: {rout.strip()} {rerr.strip()}"
    return rc, result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--host", help="single agent")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--restart", action="store_true", help="restart each WebUI after patching")
    args = ap.parse_args()

    if not args.all and not args.host:
        ap.error("specify --all or --host <name>")

    hosts = list(FLEET.items()) if args.all else [(args.host, FLEET[args.host])]

    # Safety: patch remotes first, local (shichi) last — restarting the local
    # WebUI kills the active chat session.
    hosts.sort(key=lambda kv: kv[0] == "shichi")

    summary = {}
    for host, target in hosts:
        print("\n" + "#" * 60)
        print(f"# {host} ({target})")
        print("#" * 60)
        ok, msg = push(host, target)
        print(f"  push: {msg}")
        if not ok:
            summary[host] = False
            continue
        rc, out = run_remote(host, target, args.verify_only, args.restart)
        print(out)
        summary[host] = (rc == 0)

    print("\n" + "=" * 60)
    print("FLEET SUMMARY")
    print("=" * 60)
    for host, ok in summary.items():
        print(f"  {'✅' if ok else '❌'} {host}")
    sys.exit(0 if all(summary.values()) else 2)


if __name__ == "__main__":
    main()
