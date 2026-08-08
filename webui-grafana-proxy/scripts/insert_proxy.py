import re
import sys

agent = sys.argv[1] if len(sys.argv) > 1 else 'unknown'
path = f"/home/{agent}/hermes-webui/server.py"

with open(path, "r") as f:
    content = f.read()

# Read the proxy method
with open("/tmp/proxy_method.py", "r") as f:
    proxy_code = f.read()

# Find do_GET and insert before it
insert_point = content.find("    def do_GET(self)")
if insert_point < 0:
    print("ERROR: could not find do_GET")
    sys.exit(1)

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
