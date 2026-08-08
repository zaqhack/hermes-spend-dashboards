#!/usr/bin/env python3
"""
Log Shipper — Parses Hermes agent.log, computes per-call token costs,
and writes them to InfluxDB for Grafana visualization.

This is the core of the mystery-spike detection system. It runs on each
bot via cron (every 5 minutes), parses new log lines since the last run,
computes per-call costs using cached OpenRouter model pricing, and writes
per-API-call data points to InfluxDB.

Designed to work on any fleet bot — just needs Python 3 and network
access to InfluxDB on Bright (192.168.49.15:8086).

Usage:
    python3 log_shipper.py                    # Ship new log entries
    python3 log_shipper.py --refresh-pricing   # Re-fetch model pricing
    python3 log_shipper.py --backfill          # Re-parse entire log
    python3 log_shipper.py --dry-run           # Parse but don't write
"""

import re
import sys
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime
from collections import defaultdict

# ── Configuration ──────────────────────────────────────────────

INFLUX_URL = "http://192.168.49.15:8086"
INFLUX_TOKEN = "mejiro-bright-token-2025"
INFLUX_ORG = "mejiro-stable"
INFLUX_BUCKET = "system_metrics"

LOG_PATH = os.path.expanduser("~/.hermes/logs/agent.log")
PRICING_CACHE_PATH = os.path.expanduser("~/.hermes/model_pricing_cache.json")
STATE_FILE = os.path.expanduser("~/.hermes/log_shipper_offset")

# Agent name — set per-bot. Override with AGENT_NAME env var.
# Defaults to hostname.
AGENT_NAME = os.environ.get("AGENT_NAME", "")
if not AGENT_NAME:
    try:
        with open("/etc/hostname") as f:
            AGENT_NAME = f.read().strip()
    except:
        AGENT_NAME = "unknown"

# OpenRouter API key (for refreshing pricing)
OPENROUTER_API_KEY = None
_env_path = os.path.expanduser("~/.hermes/.env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            if line.startswith("OPENROUTER_API_KEY="):
                OPENROUTER_API_KEY = line.split("=", 1)[1].strip()
                break

# ── Regex Patterns ────────────────────────────────────────────

LOG_TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}),(\d+)\s+')

API_CALL_RE = re.compile(
    r'\[(\w+)\]\s+agent\.conversation_loop:\s+API call #(\d+):\s+'
    r'model=(\S+)\s+provider=(\S+)\s+'
    r'in=(\d+)\s+out=(\d+)\s+total=(\d+)\s+'
    r'latency=([\d.]+)s\s+'
    r'(?:cache=(\d+)/(\d+)\s+\((\d+)%\))?'
)

TOOL_COMPLETE_RE = re.compile(
    r'\[(\w+)\]\s+agent\.tool_executor:\s+tool\s+(\w+)\s+completed\s+\(([\d.]+)s,\s+(\d+)\s+chars\)'
)

# ── Pricing ───────────────────────────────────────────────────

def refresh_pricing():
    """Fetch the full OpenRouter model catalog and cache pricing locally."""
    if not OPENROUTER_API_KEY:
        print("No OPENROUTER_API_KEY — cannot refresh pricing", file=sys.stderr)
        return False

    print("Fetching model pricing from OpenRouter...")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
    except Exception as e:
        print(f"Failed to fetch pricing: {e}", file=sys.stderr)
        return False

    models = data.get("data", [])
    pricing = {}
    for m in models:
        slug = m.get("id", "")
        p = m.get("pricing", {})
        pricing[slug] = {
            "prompt": float(p.get("prompt", "0") or "0"),
            "completion": float(p.get("completion", "0") or "0"),
            "cache_read": float(p.get("input_cache_read", "0") or "0"),
            "cache_write": float(p.get("input_cache_write", "0") or "0"),
        }

    with open(PRICING_CACHE_PATH, "w") as f:
        json.dump({
            "fetched_at": time.time(),
            "model_count": len(pricing),
            "pricing": pricing,
        }, f, indent=2)

    print(f"Cached pricing for {len(pricing)} models → {PRICING_CACHE_PATH}")
    return True


def load_pricing():
    """Load cached pricing. Auto-refresh if stale (>24h) or missing."""
    if not os.path.exists(PRICING_CACHE_PATH):
        if not refresh_pricing():
            return {}

    with open(PRICING_CACHE_PATH) as f:
        cache = json.load(f)

    # Auto-refresh if older than 24 hours
    age = time.time() - cache.get("fetched_at", 0)
    if age > 86400:
        print(f"Pricing cache is {age/3600:.1f}h old — refreshing...")
        if not refresh_pricing():
            # Use stale cache as fallback
            pass
        else:
            with open(PRICING_CACHE_PATH) as f:
                cache = json.load(f)

    return cache.get("pricing", {})


def compute_call_cost(model, input_tokens, output_tokens, cache_read, pricing):
    """
    Compute the cost of a single API call.

    Cost = (non_cached_input × prompt_price)
         + (cache_read × cache_read_price)
         + (output_tokens × completion_price)

    All prices are per-token from the OpenRouter models API.
    """
    p = pricing.get(model, {})
    prompt_price = p.get("prompt", 0)
    completion_price = p.get("completion", 0)
    cache_read_price = p.get("cache_read", 0)

    non_cached = input_tokens - cache_read
    cost = (
        non_cached * prompt_price +
        cache_read * cache_read_price +
        output_tokens * completion_price
    )
    return cost


# ── State Management ──────────────────────────────────────────

def get_offset():
    """Get the byte offset of the last parsed log line."""
    try:
        with open(STATE_FILE) as f:
            return int(f.read().strip())
    except (IOError, ValueError):
        return 0


def save_offset(offset):
    """Save the byte offset for the next run."""
    with open(STATE_FILE, "w") as f:
        f.write(str(offset))


# ── Log Parsing ───────────────────────────────────────────────

def parse_log_lines(lines, pricing, agent_name):
    """Parse log lines and return InfluxDB line protocol strings."""
    influx_lines = []
    pending_tool = {}  # session -> last tool completion (for correlation)

    for line in lines:
        ts_match = LOG_TS_RE.match(line)
        if not ts_match:
            continue
        ts_str = ts_match.group(1)
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            epoch = int(dt.timestamp())
        except ValueError:
            continue

        # API call
        m = API_CALL_RE.search(line)
        if m:
            session = m.group(1)
            model = m.group(3)
            provider = m.group(4)
            input_tok = int(m.group(5))
            output_tok = int(m.group(6))
            cache_read = int(m.group(9)) if m.group(9) else 0
            cache_pct = int(m.group(11)) if m.group(11) else 0
            latency = float(m.group(8))

            cost = compute_call_cost(model, input_tok, output_tok, cache_read, pricing)

            # Build InfluxDB line protocol
            # Tags: agent, session, model, provider
            # Fields: input_tokens, output_tokens, cache_read, cache_pct,
            #         latency_s, cost_usd, tool (if correlated)

            # Escape tag values
            def esc(s):
                return s.replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")

            tags = (
                f"agent={esc(agent_name)},"
                f"session={esc(session)},"
                f"model={esc(model)},"
                f"provider={esc(provider)}"
            )

            fields = (
                f"input_tokens={input_tok}i,"
                f"output_tokens={output_tok}i,"
                f"cache_read_tokens={cache_read}i,"
                f"cache_pct={cache_pct}i,"
                f"latency_s={latency},"
                f"cost_usd={cost:.10f}"
            )

            # Add tool as a field (string) if we can correlate
            tool = pending_tool.get(session)
            if tool and tool['epoch'] >= epoch - 5:
                fields += f',tool="{esc(tool["tool"])}"'

            influx_lines.append(f"llm_api_call,{tags} {fields} {epoch}")
            continue

        # Tool completion
        m = TOOL_COMPLETE_RE.search(line)
        if m:
            session = m.group(1)
            tool = m.group(2)
            duration = float(m.group(3))
            chars = int(m.group(4))

            # Store for correlation with the NEXT api call
            pending_tool[session] = {
                'tool': tool,
                'epoch': epoch,
            }

            # Also write a tool_call data point
            def esc(s):
                return s.replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")

            tags = f"agent={esc(agent_name)},session={esc(session)},tool={esc(tool)}"
            fields = f"duration_s={duration},output_chars={chars}i"
            influx_lines.append(f"tool_call,{tags} {fields} {epoch}")

    return influx_lines


# ── InfluxDB Writer ────────────────────────────────────────────

def write_to_influx(lines, dry_run=False):
    """Write line protocol data to InfluxDB."""
    if not lines:
        return 0
    if dry_run:
        for line in lines[:5]:
            print(f"  [DRY RUN] {line[:120]}...")
        return len(lines)

    body = "\n".join(lines).encode() + b"\n"
    req = urllib.request.Request(
        f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=s",
        data=body,
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status
    except urllib.error.HTTPError as e:
        print(f"InfluxDB write error {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return e.code
    except Exception as e:
        print(f"InfluxDB connection error: {e}", file=sys.stderr)
        return None


# ── Main ──────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    backfill = "--backfill" in sys.argv

    if "--refresh-pricing" in sys.argv:
        refresh_pricing()
        return

    # Load pricing
    pricing = load_pricing()
    if not pricing:
        print("WARNING: No pricing data — costs will be $0. Run with --refresh-pricing first.", file=sys.stderr)

    # Determine what to parse
    if backfill:
        offset = 0
        print("Backfill mode — parsing entire log file")
    else:
        offset = get_offset()
        print(f"Resuming from byte offset {offset}")

    # Read new log content
    if not os.path.exists(LOG_PATH):
        print(f"Log file not found: {LOG_PATH}", file=sys.stderr)
        return

    file_size = os.path.getsize(LOG_PATH)

    # Handle log rotation: if file is smaller than our offset, start from beginning
    if offset > file_size:
        print(f"Log file rotated (was {offset}, now {file_size}) — starting from beginning")
        offset = 0

    with open(LOG_PATH) as f:
        f.seek(offset)
        new_content = f.read()
        new_offset = f.tell()

    if not new_content.strip():
        print(f"No new log lines (offset {new_offset}, file size {file_size})")
        return

    # Parse
    lines = new_content.strip().split("\n")
    print(f"Processing {len(lines)} new log lines from {AGENT_NAME}...")

    influx_lines = parse_log_lines(lines, pricing, AGENT_NAME)

    # Count API calls vs tool calls
    api_count = sum(1 for l in influx_lines if l.startswith("llm_api_call"))
    tool_count = sum(1 for l in influx_lines if l.startswith("tool_call"))

    print(f"  Parsed: {api_count} API calls, {tool_count} tool calls")

    if api_count > 0:
        # Compute total cost of this batch
        total_cost = 0
        for line in influx_lines:
            if line.startswith("llm_api_call"):
                # Extract cost_usd field
                m = re.search(r'cost_usd=([\d.]+)', line)
                if m:
                    total_cost += float(m.group(1))
        print(f"  Computed cost: ${total_cost:.6f}")

    # Write to InfluxDB
    if influx_lines:
        status = write_to_influx(influx_lines, dry_run)
        if dry_run:
            print(f"  [DRY RUN] Would write {len(influx_lines)} points")
        elif status and status < 400:
            print(f"  Wrote {len(influx_lines)} points to InfluxDB (HTTP {status})")
            # Save offset only on successful write
            if not backfill:
                save_offset(new_offset)
            else:
                save_offset(file_size)
        else:
            print(f"  FAILED to write (status={status}) — offset not saved")
    else:
        print(f"  No data points to write")
        if not backfill:
            save_offset(new_offset)

    # ── Sidecar cost DB sync ──────────────────────────────────────
    # After writing to InfluxDB, sync the sidecar DB with actual costs
    # for all sessions seen in this batch. The WebUI reads this to show
    # "real" costs alongside estimates.
    if influx_lines and not dry_run:
        try:
            # Import here so the shipper still works if session_cost_db is missing
            sys.path.insert(0, os.path.expanduser("~/workspace"))
            from session_cost_db import update_cost_db, query_influx_for_sessions

            # Extract session IDs from the lines we just wrote
            session_ids = set()
            for line in influx_lines:
                if line.startswith("llm_api_call"):
                    # Parse session from tags: llm_api_call,agent=...,session=...,...
                    parts = line.split(",")
                    for part in parts:
                        if part.startswith("session="):
                            sid = part.split("=", 1)[1].replace("\\", "")
                            session_ids.add(sid)
                            break

            if session_ids:
                costs = query_influx_for_sessions(list(session_ids))
                update_cost_db(costs)
                print(f"  Synced {len(costs)} session costs to sidecar DB")
        except Exception as e:
            # Don't fail the shipper if sidecar sync fails
            print(f"  Sidecar sync warning: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
