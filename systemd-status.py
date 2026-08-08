#!/usr/bin/env python3
"""HTTP endpoint to check systemd user service status.
Usage: python3 systemd-status.py
Listens on port 9100.
GET /check?service=hermes-gateway.service → {"healthy": true/false, "status": "active"/...}
"""
import subprocess
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/check":
            self.send_response(404)
            self.end_headers()
            return
        
        params = parse_qs(parsed.query)
        service = params.get("service", [None])[0]
        
        if not service:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Missing 'service' parameter"}).encode())
            return
        
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", service],
                capture_output=True, text=True, timeout=5
            )
            active = result.stdout.strip() == "active"
            response = {"healthy": active, "status": result.stdout.strip()}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        except Exception as e:
            response = {"healthy": False, "error": str(e)}
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
    
    def log_message(self, format, *args):
        pass  # Suppress request logging

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9100), StatusHandler)
    print("Serving on port 9100...")
    server.serve_forever()
