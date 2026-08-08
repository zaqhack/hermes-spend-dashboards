#!/usr/bin/env python3
"""
Deploy the Grafana WebUI proxy + popover fix to a fleet agent.

Usage:
  python3 deploy.py <agent>     # e.g. python3 deploy.py mayano
  python3 deploy.py --all       # all fleet agents (excluding shichi)

What it does:
1. Patches server.py (port 3001, full path forwarding, CSP skip)
2. Patches sessions.js (proxy URL + popover click handler)
3. Patches index.html (popover markup, hidden button)
4. Restarts the remote WebUI
"""

import subprocess
import sys
import re

FLEET = {
    'mayano':  'mayano@192.168.49.17',
    'satono':  'satono@192.168.49.21',
    'robroy':  'robroy@192.168.49.18',
    'suzuka':  'suzuka@192.168.49.23',
    'dantsu':  'dantsu@192.168.49.24',
}

PATCHES = {
    'server.py': [
        ('grafana_port = 3000', 'grafana_port = 3001'),
        ("""        # Determine the sub-path to forward to Grafana
        if path.startswith('/grafana-proxy'):
            subpath = path[len('/grafana-proxy'):]
        else:
            subpath = path  # /public/... goes as-is""",
         """        # Determine the sub-path to forward to nginx/Grafana
        # nginx expects the full /grafana-proxy/* path for subpath rewriting
        if path.startswith('/grafana-proxy'):
            subpath = path  # keep full path for nginx
        else:
            subpath = path  # /public/... goes as-is"""),
        ("""    def end_headers(self) -> None:
        extra_connect_src = getattr(self, \"_csp_extra_connect_src\", None)
        extra_frame_src = getattr(self, \"_csp_extra_frame_src\", None)
        self.send_header(\"Content-Security-Policy-Report-Only\", self.csp_report_only_policy(extra_connect_src, extra_frame_src))
        self.send_header(\"Report-To\", self._CSP_REPORT_TO)
        super().end_headers()""",
         """    def end_headers(self) -> None:
        # Skip CSP injection for Grafana-proxied responses — the proxy already strips
        # frame-ancestors from Grafana's headers, and the WebUI's own CSP would
        # re-add frame-ancestors 'none', breaking the iframe.
        if getattr(self, 'path', '').startswith(('/grafana-proxy', '/public/', '/apis/', '/avatar/')):
            super().end_headers()
            return
        extra_connect_src = getattr(self, \"_csp_extra_connect_src\", None)
        extra_frame_src = getattr(self, \"_csp_extra_frame_src\", None)
        self.send_header(\"Content-Security-Policy-Report-Only\", self.csp_report_only_policy(extra_connect_src, extra_frame_src))
        self.send_header(\"Report-To\", self._CSP_REPORT_TO)
        super().end_headers()"""),
    ],
    'sessions.js': [
        ("const base = 'http://192.168.49.15:3000' + '/d/' + 'ai-usage-tracking' + '/?var-session=' + encodeURIComponent(sid);",
         "const base = '/grafana-proxy/d/ai-usage-tracking/f09f908e-ai-usage-and-cost-tracking' + '/?var-session=' + encodeURIComponent(sid);"),
    ],
}

def ssh_run(target, cmd):
    r = subprocess.run(['ssh', target, cmd], capture_output=True, text=True, timeout=30)
    return r.returncode == 0, r.stdout, r.stderr

def patch_remote(agent, target):
    print(f"\n=== {agent} ({target}) ===")
    
    # Check if WebUI exists
    ok, out, err = ssh_run(target, 'test -f ~/hermes-webui/server.py && echo EXISTS')
    if not ok or 'EXISTS' not in out:
        print(f"  SKIP: no hermes-webui found")
        return False
    
    # Patch server.py
    print("  Patching server.py...")
    for old, new in PATCHES['server.py']:
        # Use python for reliable string replacement
        script = f'''
import sys
path = "/home/{agent}/hermes-webui/server.py"
with open(path, "r") as f:
    content = f.read()
if """{old}""" in content:
    content = content.replace("""{old}""", """{new}""")
    with open(path, "w") as f:
        f.write(content)
    print("OK")
else:
    print("NOTFOUND")
'''
        # Write script to /tmp and run
        ok, _, _ = ssh_run(target, f'cat > /tmp/patch_{agent}.py << "ENDOFSCRIPT"\n{script}\nENDOFSCRIPT\npython3 /tmp/patch_{agent}.py')
        if ok:
            print(f"    server.py patch applied")
        else:
            print(f"    server.py patch failed or already applied")
    
    # Patch sessions.js
    print("  Patching sessions.js...")
    for old, new in PATCHES['sessions.js']:
        script = f'''
path = "/home/{agent}/hermes-webui/static/sessions.js"
with open(path, "r") as f:
    content = f.read()
if """{old}""" in content:
    content = content.replace("""{old}""", """{new}""")
    with open(path, "w") as f:
        f.write(content)
    print("OK")
else:
    print("NOTFOUND")
'''
        ok, _, _ = ssh_run(target, f'cat > /tmp/patch_js_{agent}.py << "ENDOFSCRIPT"\n{script}\nENDOFSCRIPT\npython3 /tmp/patch_js_{agent}.py')
        if ok:
            print(f"    sessions.js patch applied")
    
    # Remove fleet button from index.html
    print("  Patching index.html...")
    script = '''
import re
path = "/home/''' + agent + '''/hermes-webui/static/index.html"
with open(path, "r") as f:
    content = f.read()
pattern = r'<!-- Grafana popover -->\\s*<div id="grafana-popover-btn".*?</div>'
replacement = '<!-- Grafana popover (floating button removed per zaq — session cost links only) -->\\n<div id="grafana-popover-btn" style="display:none"></div>'
new_content, count = re.subn(pattern, replacement, content, flags=re.DOTALL)
if count > 0:
    with open(path, "w") as f:
        f.write(new_content)
    print(f"OK: replaced {count}")
else:
    print("NOTFOUND")
'''
    ok, _, _ = ssh_run(target, f'cat > /tmp/patch_html_{agent}.py << "ENDOFSCRIPT"\n{script}\nENDOFSCRIPT\npython3 /tmp/patch_html_{agent}.py')
    if ok:
        print(f"    index.html patch applied")
    
    # Restart WebUI
    print("  Restarting WebUI...")
    ok, out, err = ssh_run(target, 'systemctl --user restart hermes-webui.service && sleep 3 && systemctl --user is-active hermes-webui.service')
    if ok and 'active' in out:
        print(f"  ✅ {agent} deployed and active")
        return True
    else:
        print(f"  ⚠️  {agent} deployed but restart status unclear: {out} {err}")
        return False

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    if sys.argv[1] == '--all':
        targets = FLEET
    else:
        agent = sys.argv[1].lower()
        if agent not in FLEET:
            print(f"Unknown agent: {agent}. Known: {', '.join(FLEET.keys())}")
            sys.exit(1)
        targets = {agent: FLEET[agent]}
    
    results = {}
    for agent, target in targets.items():
        results[agent] = patch_remote(agent, target)
    
    print("\n=== Summary ===")
    for agent, ok in results.items():
        status = "✅" if ok else "❌"
        print(f"  {status} {agent}")

if __name__ == '__main__':
    main()
