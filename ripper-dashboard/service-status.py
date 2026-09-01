#!/usr/bin/env python3
"""HTTP endpoint to check systemd service, podman container, and native process status.
Usage: python3 service-status.py
Listens on port 9100.
GET /check-systemd?service=hermes-gateway.service → {"healthy": true/false, "status": "active"/...}
GET /check-container?name=kanka_mariadb_1 → {"healthy": true/false, "status": "running"/...}
GET /check-process?name=redis-server → {"healthy": true/false, "status": "running"/...}
"""
import subprocess
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == "/check-systemd":
            self._check_systemd(parse_qs(parsed.query))
        elif parsed.path == "/check-container":
            self._check_container(parse_qs(parsed.query))
        elif parsed.path == "/check-process":
            self._check_process(parse_qs(parsed.query))
        elif parsed.path == "/gpu-stats":
            self._gpu_stats()
        else:
            self.send_response(404)
            self.end_headers()
    
    def _check_systemd(self, params):
        service = params.get("service", [None])[0]
        user_only = params.get("user", ["true"])[0].lower() != "false"
        if not service:
            self._json_response(400, {"error": "Missing 'service' parameter"})
            return
        try:
            cmd = ["systemctl", "--user", "is-active", service] if user_only else ["systemctl", "is-active", service]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            active = result.stdout.strip() == "active"
            self._json_response(200, {"healthy": active, "status": result.stdout.strip()})
        except Exception as e:
            self._json_response(500, {"healthy": False, "error": str(e)})
    
    def _check_container(self, params):
        name = params.get("name", [None])[0]
        if not name:
            self._json_response(400, {"error": "Missing 'name' parameter"})
            return
        try:
            result = subprocess.run(
                ["podman", "inspect", name, "--format", "{{.State.Running}}"],
                capture_output=True, text=True, timeout=5
            )
            running = result.stdout.strip() == "true"
            self._json_response(200, {"healthy": running, "status": "running" if running else "stopped"})
        except Exception as e:
            self._json_response(500, {"healthy": False, "error": str(e)})
    
    def _check_process(self, params):
        name = params.get("name", [None])[0]
        if not name:
            self._json_response(400, {"error": "Missing 'name' parameter"})
            return
        try:
            result = subprocess.run(
                ["pgrep", "-f", name],
                capture_output=True, text=True, timeout=5
            )
            running = result.returncode == 0
            self._json_response(200, {"healthy": running, "status": "running" if running else "stopped"})
        except Exception as e:
            self._json_response(500, {"healthy": False, "error": str(e)})
    
    def _gpu_stats(self):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw",
                 "--format=csv,noheader,nounits"], timeout=5
            ).decode()
            gpus = []
            for i, line in enumerate(out.strip().splitlines()):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 6:
                    gpus.append({
                        "index": i,
                        "name": parts[0],
                        "util_pct": int(parts[1]),
                        "temp_c": int(parts[2]),
                        "vram_used_mb": int(parts[3]),
                        "vram_total_mb": int(parts[4]),
                        "power_w": float(parts[5]) if parts[5] else 0.0,
                    })
            self._json_response(200, {"gpus": gpus})
        except Exception as e:
            self._json_response(500, {"gpus": [], "error": str(e)})

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9100), StatusHandler)
    print("Serving on port 9100...")
    server.serve_forever()