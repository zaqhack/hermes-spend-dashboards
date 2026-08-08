import sys

agent = sys.argv[1] if len(sys.argv) > 1 else 'unknown'
path = f"/home/{agent}/hermes-webui/server.py"

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
