#!/usr/bin/env python3
"""Minimal HTTP endpoint that reports gpu-temp-manager.service status.
Uses only Python standard library.
Run with: python3 gpu-temp-status.py
Listens on port 9099 by default.
"""
import subprocess
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "gpu-temp-manager.service"],
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
    server = HTTPServer(("0.0.0.0", 9099), HealthHandler)
    print("Serving on port 9099...")
    server.serve_forever()
