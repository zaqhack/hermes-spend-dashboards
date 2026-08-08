"""
Ripper Service Health Checker
Hits all services concurrently and returns health status.
"""
import asyncio
import time
import json
import os
import redis.asyncio as redis_lib
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import aiohttp
import aiofiles

app = FastAPI()

STATE_FILE = "/opt/state.conf"

# ── Suspended services management ──
_STATE_KEY_TO_DISPLAY = {
    "qwen-27b": "Qwen 27B",
    "comfyui": "ComfyUI",
    "kanka": "Kanka",
    "marinara": "Marinara",
    "minicpm-v": "MiniCPM-V",
    "llm-proxy": "Caddy",
    "storage-inventory": "Storage Inventory",
    "mariadb": "MariaDB",
}
# Build reverse: display name -> state key
_DISPLAY_TO_STATE_KEY = {v: k for k, v in _STATE_KEY_TO_DISPLAY.items()}

async def load_suspended():
    """Load suspended service names from /opt/state.conf.
    Returns the set of display names whose state is "suspended"."""
    try:
        async with aiofiles.open(STATE_FILE, "r") as f:
            content = await f.read()
            data = json.loads(content)
            return set(
                _STATE_KEY_TO_DISPLAY[name]
                for name, val in data.items()
                if val == "suspended" and name in _STATE_KEY_TO_DISPLAY
            )
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

async def save_suspended(suspended_set):
    """Toggle a service's desired state in /opt/state.conf.
    suspended_set contains display names; converts to state keys before writing.
    Only modifies entries that already exist in the file."""
    try:
        async with aiofiles.open(STATE_FILE, "r") as f:
            data = json.loads(await f.read())
        for state_key in data:
            display = _STATE_KEY_TO_DISPLAY.get(state_key)
            data[state_key] = "suspended" if display and display in suspended_set else "run"
        async with aiofiles.open(STATE_FILE, "w") as f:
            await f.write(json.dumps(data, indent=2))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

@app.get("/api/suspended")
async def get_suspended():
    """Return the list of suspended service names."""
    suspended = await load_suspended()
    return {"suspended": sorted(suspended)}

@app.post("/api/suspended")
async def set_suspended(request: Request):
    """Toggle a service's suspended state. Expects JSON: {"name": "...", "suspended": true/false}"""
    body = await request.json()
    name = body.get("name")
    suspended_val = body.get("suspended", True)
    if not name:
        return JSONResponse({"error": "Missing 'name' field"}, status_code=400)
    suspended = await load_suspended()
    if suspended_val:
        suspended.add(name)
    else:
        suspended.discard(name)
    await save_suspended(suspended)
    return {"suspended": sorted(suspended), "name": name, "status": "suspended" if suspended_val else "active"}

# ── Service registry ──
# type: "systemd" (check via systemctl) or "container" (check via podman)
# port: optional — if set, run HTTP/TCP health check on that port
# For port checks: path, auth, json_field are optional extras

SERVICES = [
    {"name": "BGE-M3 (Ripper)", "type": "systemd", "service": "embed-server-ripper.service", "port": 8081, "path": "/health"},
    {"name": "BGE-M3 (Mayano)", "type": "systemd", "service": "embed-server.service", "port": 8081, "path": "/health", "host": "192.168.49.17", "remote": True},
    {"name": "Caddy", "type": "container", "container": "caddy", "port": 80, "path": "/api/health", "auth": ("zaq", "zaqhack")},
    {"name": "ComfyUI", "type": "systemd", "service": "comfyui-native.service", "port": 8189, "path": "/"},
    {"name": "Container Registry", "type": "container", "container": "registry", "port": 5002, "path": "/v2/"},
    {"name": "Disque Gateway", "type": "systemd", "service": "hermes-gateway.service", "port": 8644, "path": "/webhooks/mesh-inbound", "host": "192.168.49.18", "remote": True, "accept_4xx": True},
    {"name": "Disque Listener", "type": "systemd", "service": "agent-webhook-listener.service", "port": 8645, "path": "/", "host": "192.168.49.18", "remote": True},
    {"name": "GPU Temp Service", "type": "systemd", "service": "gpu-temp-manager.service"},
    {"name": "Hermes Gateway", "type": "systemd", "service": "hermes-gateway.service", "port": 8644, "path": "/webhooks/mesh-inbound", "accept_4xx": True},
    {"name": "Hermes WebUI", "type": "systemd", "service": "hermes-webui.service", "port": 49020, "path": "/", "scheme": "https"},
    {"name": "Kanka", "type": "container", "container": "kanka_laravel.test_1", "port": 8880, "path": "/"},
    {"name": "Kanka Mailpit", "type": "container", "container": "kanka_mailpit_1", "port": 8028, "path": "/"},
    {"name": "Kanka MariaDB", "type": "container", "container": "kanka_mariadb_1"},
    {"name": "Kanka Meilisearch", "type": "container", "container": "kanka_meilisearch_1", "port": 7702, "path": "/health"},
    {"name": "Kanka MinIO", "type": "container", "container": "kanka_minio_1", "port": 9004, "path": "/minio/health/live"},
    {"name": "Kanka Redis", "type": "container", "container": "kanka_redis_1"},
    {"name": "Kanka Thumbor", "type": "container", "container": "kanka_thumbor_1"},
    {"name": "Kanka Thumbor Nginx", "type": "container", "container": "kanka_thumbor-nginx_1"},
    {"name": "Marinara", "type": "container", "container": "marinara_marinara_1", "port": 7860, "path": "/"},
    # Matrix Synapse removed — decommissioned Jul 2026 (fleet is webhook-only, no Matrix)
    {"name": "Mayano Gateway", "type": "systemd", "service": "hermes-gateway.service", "port": 8644, "path": "/webhooks/mesh-inbound", "host": "192.168.49.17", "remote": True, "accept_4xx": True},
    {"name": "Mayano Listener", "type": "systemd", "service": "agent-webhook-listener.service", "port": 8645, "path": "/", "host": "192.168.49.17", "remote": True},
    {"name": "Memory Proxy", "type": "systemd", "service": "memory-proxy.service", "port": 8090, "path": "/health"},
    {"name": "MiniCPM-V", "type": "container", "container": "minicpm-v", "port": 5001, "path": "/health"},
    {"name": "PostgreSQL", "type": "systemd", "service": "postgresql.service", "systemd_user": False},
    {"name": "Qdrant (Mayano)", "type": "systemd", "service": "qdrant.service", "port": 6333, "path": "/healthz", "host": "192.168.49.17", "remote": True},
    {"name": "Qdrant (Ripper)", "type": "systemd", "service": "container-qdrant.service", "port": 6333, "path": "/healthz"},
    {"name": "Qwen 27B", "type": "container", "container": "qwen36-llamacpp", "port": 5000, "path": "/health"},
    {"name": "Redis", "type": "systemd", "service": "redis-server.service", "systemd_user": False, "port": 6379},
    {"name": "Satono Listener", "type": "systemd", "service": "agent-webhook-listener.service", "port": 8645, "path": "/", "host": "192.168.49.21", "remote": True},
    {"name": "Shichi Listener", "type": "systemd", "service": "shichi-listener.service", "port": 8645, "path": "/"},
    {"name": "Storage Inventory", "type": "container", "container": "storage-inventory", "port": 8420, "path": "/"},
    {"name": "Suzuka Listener", "type": "systemd", "service": "agent-webhook-listener.service", "port": 8645, "path": "/", "host": "192.168.49.23", "remote": True},
]

# Rate limit: max 1 health check per 30 seconds
_last_health_check = 0.0
_cached_result = None
_in_flight = False

STATUS_URL = "http://127.0.0.1:9100"


async def check_type(session, svc):
    """Check if systemd service, podman container, or native process is running."""
    # Remote services: can't check systemd/container locally — rely on port check only
    if svc.get("remote"):
        return True

    if svc["type"] == "systemd":
        user_flag = svc.get("systemd_user", True)
        url = f"{STATUS_URL}/check-systemd?service={svc['service']}&user={'true' if user_flag else 'false'}"
    elif svc["type"] == "process":
        url = f"{STATUS_URL}/check-process?name={svc['process']}"
    else:
        url = f"{STATUS_URL}/check-container?name={svc['container']}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            body = await resp.json(content_type=None)
            return body.get("healthy", False)
    except Exception:
        return False


async def check_port(session, svc):
    """Check port health — HTTP GET, Redis PING, or TCP connect."""
    port = svc.get("port")
    if port is None:
        return None  # No port to check

    # Redis PING
    if port == 6379:
        try:
            r = redis_lib.Redis(host="127.0.0.1", port=6379, socket_connect_timeout=2)
            healthy = await r.ping()
            await r.aclose()
            return healthy
        except Exception:
            return False

    # HTTP check - accept 2xx and 3xx as healthy
    host = svc.get("host", "127.0.0.1")
    scheme = svc.get("scheme", "http")
    url = f"{scheme}://{host}:{port}{svc.get('path', '/')}"
    kwargs = {}
    if svc.get("auth"):
        kwargs["auth"] = aiohttp.BasicAuth(*svc["auth"])
    if scheme == "https":
        # Local TLS (e.g. Hermes WebUI) — skip cert verification, cert won't match loopback
        kwargs["ssl"] = False
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False, **kwargs) as resp:
            if svc.get("json_field"):
                body = await resp.json(content_type=None)
                return body.get(svc["json_field"], False)
            # Accept 2xx and 3xx as healthy
            if resp.status < 400:
                return True
            # Some services (e.g. Hermes gateway) return 405 for GET on POST-only endpoints — that's healthy
            if svc.get("accept_4xx") and resp.status < 500:
                return True
            return False
    except Exception as e:
        print(f"Port check failed for {svc['name']}: {e}")
        return False


async def check_service(session, svc, suspended_set):
    """Check one service — returns {name, type, type_healthy, port, port_healthy, suspended}."""
    name = svc["name"]
    suspended = name in suspended_set

    if suspended:
        # Skip actual checks — mark as healthy to avoid false alarms
        return {
            "name": name,
            "type": svc["type"],
            "type_healthy": True,
            "port": svc.get("port"),
            "port_healthy": True,
            "suspended": True,
        }

    type_ok, port_ok = await asyncio.gather(
        check_type(session, svc),
        check_port(session, svc),
    )
    return {
        "name": name,
        "type": svc["type"],
        "type_healthy": type_ok,
        "port": svc.get("port"),
        "port_healthy": port_ok,
        "suspended": False,
    }


@app.get("/api/health")
async def health():
    global _last_health_check, _cached_result, _in_flight
    now = time.time()
    if _cached_result and (now - _last_health_check) < 25:
        return _cached_result

    if _in_flight:
        if _cached_result:
            return _cached_result
        for _ in range(10):
            await asyncio.sleep(0.5)
            if _cached_result:
                return _cached_result
        return JSONResponse({"services": [], "checked_at": int(now), "degraded": True})

    _in_flight = True
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20, limit_per_host=5)
        ) as session:
            suspended_set = await load_suspended()
            tasks = [check_service(session, svc, suspended_set) for svc in SERVICES]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        services = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                svc = SERVICES[i]
                services.append({
                    "name": svc["name"],
                    "type": svc["type"],
                    "type_healthy": False,
                    "port": svc.get("port"),
                    "port_healthy": None,
                    "suspended": False,
                })
            else:
                services.append(r)

        _cached_result = JSONResponse({"services": services, "checked_at": int(now)})
        _last_health_check = now
        return _cached_result
    finally:
        _in_flight = False


@app.get("/api/stats")
async def stats():
    result = {
        "uptime": "unknown",
        "cpu_pct": 0,
        "cpu_temp_c": None,
        "coolant_temp_c": None,
        "ram": {"total_mb": 0, "used_mb": 0, "pct": 0},
        "gpus": [],
        "updated_at": time.time(),
    }
    
    try:
        with open("/proc/uptime") as f:
            up_sec = float(f.readline().split()[0])
            d = int(up_sec) // 86400
            h = (int(up_sec) % 86400) // 3600
            m = (int(up_sec) % 3600) // 60
            parts = []
            if d: parts.append(f"{d}d")
            if h: parts.append(f"{h}h")
            if m: parts.append(f"{m}m")
            result["uptime"] = " ".join(parts) if parts else "0m"
    except: pass
    
    try:
        with open("/proc/stat") as f:
            cpu1 = [int(x) for x in f.readline().split()[1:]]
        await asyncio.sleep(0.1)
        with open("/proc/stat") as f:
            cpu2 = [int(x) for x in f.readline().split()[1:]]
        idle1, total1 = cpu1[3], sum(cpu1)
        idle2, total2 = cpu2[3], sum(cpu2)
        result["cpu_pct"] = round((1 - (idle2 - idle1) / (total2 - total1)) * 100, 1)
    except: pass
    
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    meminfo[k.strip()] = int(v.strip().split()[0])
        total_mb = meminfo.get("MemTotal", 0) // 1024
        avail_mb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0)) // 1024
        used_mb = total_mb - avail_mb
        result["ram"] = {
            "total_mb": total_mb,
            "used_mb": used_mb,
            "pct": round(used_mb / total_mb * 100, 1) if total_mb else 0,
        }
    except: pass
    
    try:
        for d in os.listdir("/sys/class/hwmon"):
            hwpath = f"/sys/class/hwmon/{d}"
            try:
                with open(f"{hwpath}/name") as f:
                    name = f.read().strip()
            except: continue
            if name == "k10temp":
                for lbl in os.listdir(hwpath):
                    if lbl.startswith("temp1_") and lbl.endswith("_input"):
                        with open(f"{hwpath}/{lbl}") as f:
                            result["cpu_temp_c"] = int(f.read().strip()) / 1000
            elif name == "x53":
                for lbl in os.listdir(hwpath):
                    if lbl.startswith("temp1_") and lbl.endswith("_input"):
                        with open(f"{hwpath}/{lbl}") as f:
                            result["coolant_temp_c"] = int(f.read().strip()) / 1000
    except: pass
    
    try:
        async with aiohttp.ClientSession() as gpu_session:
            async with gpu_session.get(f"{STATUS_URL}/gpu-stats", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    body = await resp.json(content_type=None)
                    result["gpus"] = body.get("gpus", [])
    except: pass
    
    return result


# ── Samba shares (static endpoint) ──
# NOTE: Must be defined BEFORE the StaticFiles mount at "/" so FastAPI routes
# this endpoint instead of falling through to static file serving.
SAMBA_SHARES = [
    {"name": "ai", "path": "/ai/shared", "comment": "Shared AI Files"},
    {"name": "bluepool", "path": "/bluepool", "comment": "Bluepool NVMe RAIDZ1 Storage (14.5TB)"},
    {"name": "images", "path": "/ai/images", "comment": "AI Images - Models, Outputs, HF Cache, ComfyUI"},
]


@app.get("/api/shares")
async def shares():
    import os
    result = []
    for s in SAMBA_SHARES:
        p = s["path"]
        try:
            stat = os.statvfs(p)
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
            used = total - free
            pct = round(used / total * 100, 1) if total else 0
            result.append({**s, "total_bytes": total, "used_bytes": used, "free_bytes": free, "used_pct": pct, "accessible": True})
        except Exception:
            result.append({**s, "total_bytes": 0, "used_bytes": 0, "free_bytes": 0, "used_pct": 0, "accessible": False})
    return {"shares": result}

# Serve static dashboard HTML (must be LAST — catches all unmatched routes)
app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")