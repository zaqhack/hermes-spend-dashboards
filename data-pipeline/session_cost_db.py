#!/usr/bin/env python3
"""
Session Cost Sidecar DB — maps session IDs to real costs from InfluxDB.

The log shipper calls update_cost_db() after each successful InfluxDB write
to keep a local SQLite cache of per-session actual costs. The WebUI reads
this to display "real" costs alongside estimates.

DB: ~/.hermes/session_costs.db
Table: session_costs (session_id TEXT PRIMARY KEY, cost_usd REAL, updated_at REAL)
"""

import sqlite3
import os
import time
import urllib.request
import json

DB_PATH = os.path.expanduser("~/.hermes/session_costs.db")
INFLUX_URL = "http://192.168.49.15:8086"
INFLUX_TOKEN = "mejiro-bright-token-2025"
INFLUX_ORG = "mejiro-stable"
INFLUX_BUCKET = "system_metrics"


def _get_db():
    """Open the sidecar DB, creating the table if needed."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_costs (
            session_id TEXT PRIMARY KEY,
            cost_usd REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    return conn


def get_session_cost(session_id: str) -> float | None:
    """Look up the actual cost for a session. Returns None if unknown."""
    if not os.path.exists(DB_PATH):
        return None
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT cost_usd FROM session_costs WHERE session_id = ?",
            (session_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_all_session_costs() -> dict[str, float]:
    """Return all session costs as a dict {session_id: cost}."""
    if not os.path.exists(DB_PATH):
        return {}
    conn = _get_db()
    try:
        rows = conn.execute("SELECT session_id, cost_usd FROM session_costs").fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        conn.close()


def update_cost_db(session_costs: dict[str, float]):
    """Write session costs to the sidecar DB."""
    if not session_costs:
        return
    conn = _get_db()
    try:
        now = time.time()
        conn.executemany(
            "INSERT OR REPLACE INTO session_costs (session_id, cost_usd, updated_at) VALUES (?, ?, ?)",
            [(sid, cost, now) for sid, cost in session_costs.items()]
        )
        conn.commit()
    finally:
        conn.close()


def query_influx_for_sessions(session_ids: list[str]) -> dict[str, float]:
    """Query InfluxDB for the total cost of specific sessions."""
    if not session_ids:
        return {}

    # Build Flux query with OR conditions for session IDs
    session_filter = " or ".join(f'r.session == "{sid}"' for sid in session_ids)
    flux = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "llm_api_call")
  |> filter(fn: (r) => r._field == "cost_usd")
  |> filter(fn: (r) => {session_filter})
  |> group(columns: ["session"])
  |> sum(column: "_value")'''

    req = urllib.request.Request(
        f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}",
        data=flux.encode(),
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        # Parse InfluxDB CSV response
        lines = resp.read().decode().strip().split("\n")
        costs = {}
        for line in lines:
            if line.startswith("#") or line.startswith(",result,table"):
                continue
            parts = line.split(",")
            if len(parts) >= 7:
                # Format: ,result,table,_start,_stop,_value,session
                session_id = parts[-1].strip()
                try:
                    cost = float(parts[5])
                    costs[session_id] = cost
                except (ValueError, IndexError):
                    pass
        return costs
    except Exception as e:
        print(f"InfluxDB query failed: {e}", file=__import__("sys").stderr)
        return {}


def sync_recent_sessions(hours: int = 24):
    """Sync costs for sessions active in the last N hours.

    Reads recent session IDs from the agent's state.db, queries InfluxDB
    for their actual costs, and updates the sidecar DB.
    """
    state_db = os.path.expanduser("~/.hermes/state.db")
    if not os.path.exists(state_db):
        print(f"state.db not found: {state_db}", file=__import__("sys").stderr)
        return

    # Get recent session IDs from state.db
    conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    try:
        cutoff = time.time() - (hours * 3600)
        rows = conn.execute(
            "SELECT id FROM sessions WHERE started_at >= ? OR ended_at >= ?",
            (cutoff, cutoff)
        ).fetchall()
        session_ids = [r[0] for r in rows]
    finally:
        conn.close()

    if not session_ids:
        print("No recent sessions to sync")
        return

    print(f"Syncing costs for {len(session_ids)} sessions...")
    costs = query_influx_for_sessions(session_ids)
    update_cost_db(costs)
    print(f"Updated {len(costs)} session costs in sidecar DB")


if __name__ == "__main__":
    import sys
    if "--sync" in sys.argv:
        sync_recent_sessions()
    elif "--lookup" in sys.argv:
        sid = sys.argv[sys.argv.index("--lookup") + 1]
        cost = get_session_cost(sid)
        print(f"{sid}: ${cost:.6f}" if cost is not None else f"{sid}: unknown")
    else:
        print("Usage:")
        print("  session_cost_db.py --sync          # Sync recent sessions from InfluxDB")
        print("  session_cost_db.py --lookup <sid>  # Look up a session cost")
