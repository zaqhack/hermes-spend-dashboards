#!/usr/bin/env python3
import subprocess
import sys

FLEET = {
    'mayano':  'mayano@192.168.49.17',
    'satono':  'satono@192.168.49.21',
    'robroy':  'robroy@192.168.49.18',
    'suzuka':  'suzuka@192.168.49.23',
}

def ssh_run(target, cmd, timeout=30):
    try:
        r = subprocess.run(['ssh', '-o', 'ConnectTimeout=5', target, cmd],
                         capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as e:
        return False, '', str(e)

def deploy(agent, target):
    print(f"\n{'='*50}")
    print(f"Deploying full proxy to {agent}")
    print(f"{'='*50}")
    
    # Check if proxy already exists
    ok, out, _ = ssh_run(target, 'grep -c "_proxy_to_grafana" ~/hermes-webui/server.py')
    count = int(out.strip().split('\n')[0] or 0)
    if count > 0:
        print(f"  Already has proxy ({count} refs), skipping proxy insert")
    else:
        # Copy the proxy method file
        ok, _, err = ssh_run(target, f'cat > /tmp/proxy_method.py << "ENDOFPROXY"\n{open("/tmp/proxy_method_clean.py").read()}\nENDOFPROXY')
        if not ok:
            print(f"  Failed to copy proxy method: {err}")
            return False
        
        # Build the insertion script
        insert_script = '''
import re

path = "/home/''' + agent + '''/hermes-webui/server.py"
with open(path, "r") as f:
    content = f.read()

# Read the proxy method
with open("/tmp/proxy_method.py", "r") as f:
    proxy_code = f.read()

# Find do_GET and insert before it
insert_point = content.find("    def do_GET(self)")
if insert_point < 0:
    print("ERROR: could not find do_GET")
    exit(1)

content = content[:insert_point] + proxy_code + "\n" + content[insert_point:]

# Add proxy call to each do_* method
proxy_check = """            # Grafana proxy — intercept before regular routing
            if parsed.path.startswith('/grafana-proxy') or parsed.path.startswith('/public/') or parsed.path.startswith('/apis/') or parsed.path.startswith('/avatar/'):
                self._proxy_to_grafana(parsed)
                return
"""

for method, handler in [('GET', 'handle_get'), ('POST', 'handle_post'), 
                         ('PUT', 'handle_put'), ('PATCH', 'handle_patch'),
                         ('OPTIONS', 'handle_options'), ('DELETE', 'handle_delete')]:
    old = f"""            parsed = urlparse(self.path)
            if not check_auth(self, parsed): return
            result = {handler}(self, parsed)"""
    new = f"""            parsed = urlparse(self.path)
            if not check_auth(self, parsed): return
{proxy_check}            result = {handler}(self, parsed)"""
    if old in content:
        content = content.replace(old, new)
        print(f"  Added proxy call to do_{method}")
    else:
        print(f"  WARNING: do_{method} pattern not found")

with open(path, "w") as f:
    f.write(content)
print("  Proxy method added successfully")
'''
        
        ok, out, err = ssh_run(target, f'cat > /tmp/add_proxy_{agent}.py << "ENDOFSCRIPT"\n{insert_script}\nENDOFSCRIPT\npython3 /tmp/add_proxy_{agent}.py')
        if not ok or 'ERROR' in out:
            print(f"  Failed: {out} {err}")
            return False
        print(out)
    
    # Also need the CSP skip in end_headers
    ok, out, _ = ssh_run(target, 'grep -c "Skip CSP injection" ~/hermes-webui/server.py')
    if int(out.strip().split('\n')[0] or 0) == 0:
        csp_script = '''
path = "/home/''' + agent + '''/hermes-webui/server.py"
with open(path, "r") as f:
    content = f.read()

old = """    def end_headers(self) -> None:
        extra_connect_src = getattr(self, \"_csp_extra_connect_src\", None)
        extra_frame_src = getattr(self, \"_csp_extra_frame_src\", None)
        self.send_header(\"Content-Security-Policy-Report-Only\", self.csp_report_only_policy(extra_connect_src, extra_frame_src))
        self.send_header(\"Report-To\", self._CSP_REPORT_TO)
        super().end_headers()"""

new = """    def end_headers(self) -> None:
        # Skip CSP injection for Grafana-proxied responses
        if getattr(self, 'path', '').startswith(('/grafana-proxy', '/public/', '/apis/', '/avatar/')):
            super().end_headers()
            return
        extra_connect_src = getattr(self, \"_csp_extra_connect_src\", None)
        extra_frame_src = getattr(self, \"_csp_extra_frame_src\", None)
        self.send_header(\"Content-Security-Policy-Report-Only\", self.csp_report_only_policy(extra_connect_src, extra_frame_src))
        self.send_header(\"Report-To\", self._CSP_REPORT_TO)
        super().end_headers()"""

if old in content:
    content = content.replace(old, new)
    with open(path, "w") as f:
        f.write(content)
    print("  CSP skip added")
else:
    print("  WARNING: end_headers pattern not found")
'''
        ok, out, err = ssh_run(target, f'cat > /tmp/add_csp_{agent}.py << "ENDOFSCRIPT"\n{csp_script}\nENDOFSCRIPT\npython3 /tmp/add_csp_{agent}.py')
        print(out)
    
    # Restart WebUI
    print("  Restarting WebUI...")
    ok, out, err = ssh_run(target, 'systemctl --user restart hermes-webui.service && sleep 3 && systemctl --user is-active hermes-webui.service')
    if ok and 'active' in out:
        print(f"  ✅ {agent} deployed successfully")
        return True
    else:
        print(f"  ⚠️  {agent} deployed but restart unclear: {out} {err}")
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
            print(f"Unknown: {agent}")
            sys.exit(1)
        targets = {agent: FLEET[agent]}
    
    results = {}
    for agent, target in targets.items():
        results[agent] = deploy(agent, target)
    
    print(f"\n{'='*50}")
    print("SUMMARY")
    print(f"{'='*50}")
    for agent, ok in results.items():
        status = "✅" if ok else "❌"
        print(f"  {status} {agent}")

if __name__ == '__main__':
    main()
