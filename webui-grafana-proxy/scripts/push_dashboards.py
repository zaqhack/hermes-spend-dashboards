import json
import os
import subprocess
import sys

# Service-account token with Editor role (can modify dashboards).
# Read from env — never hardcode. Export before running:
#   export GRAFANA_EDIT_TOKEN="glsa_..."
EDIT_TOKEN = os.environ.get("GRAFANA_EDIT_TOKEN", "")
if not EDIT_TOKEN:
    sys.exit("Set GRAFANA_EDIT_TOKEN (Grafana service-account token, Editor role).")
BASE = "http://192.168.49.15:3000"

for uid in ["ai-usage-tracking", "ai-fleet-aggregate"]:
    d = json.load(open(f"/tmp/dash/{uid}.json"))
    dash = d["dashboard"]
    payload = {
        "dashboard": dash,
        "overwrite": True,
        "message": "Pin per-agent series colors to canonical fleet palette (satono dark green, dantsu bright green, ripper gold, etc.)",
    }
    body = json.dumps(payload)
    r = subprocess.run(
        [
            "curl", "-s", "-X", "POST",
            f"{BASE}/api/dashboards/db",
            "-H", f"Authorization: Bearer {EDIT_TOKEN}",
            "-H", "Content-Type: application/json",
            "-d", body,
        ],
        capture_output=True, text=True,
    )
    print(f"{uid}: {r.stdout}")
