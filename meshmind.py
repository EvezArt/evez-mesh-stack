#!/usr/bin/env python3
"""
EVEZ MeshMind — AI Agent that monitors, diagnoses, and self-heals the mesh network.
Reads Reticulum topology, Docker service health, system metrics, and LoRa stats.
Uses Ollama for AI diagnosis. Generates healing actions and executes them.
Designed for Mojave Desert thermal conditions (Bullhead City, AZ — 130°F+).
"""
import os, sys, json, time, threading, subprocess, requests, socket, struct
from datetime import datetime, timezone
from collections import deque
import sqlite3

# ─── Config ────────────────────────────────────────────────────────
EVZ_DATA = os.getenv("EVZ_DATA", "/opt/evez/data")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
RNS_HOST = os.getenv("RNS_HOST", "localhost")
DATABASE_URL = os.getenv("DATABASE_URL", "")
HTTP_PORT = int(os.getenv("MESHMIND_PORT", "8899"))
THERMAL_WARN = 75.0  # °C — desert-adjusted (normal is 60)
THERMAL_CRIT = 85.0  # °C — auto-shutdown threshold
CHECK_INTERVAL = 30  # seconds between health checks
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# ─── Database ─────────────────────────────────────────────────────
class MeshDB:
    def __init__(self, db_path=f"{EVZ_DATA}/meshmind/meshmind.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, event_type TEXT, severity TEXT,
            source TEXT, message TEXT, action_taken TEXT, ai_diagnosis TEXT
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS topology (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, node_id TEXT, hops INTEGER,
            rssi REAL, snr REAL, interface TEXT, alive INTEGER
        )""")
        self.conn.commit()

    def log_event(self, event_type, severity, source, message, action="", diagnosis=""):
        self.conn.execute(
            "INSERT INTO events (timestamp,event_type,severity,source,message,action_taken,ai_diagnosis) VALUES (?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), event_type, severity, source, message, action, diagnosis)
        )
        self.conn.commit()

    def log_topology(self, node_id, hops, rssi, snr, interface, alive):
        self.conn.execute(
            "INSERT INTO topology (timestamp,node_id,hops,rssi,snr,interface,alive) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), node_id, hops, rssi, snr, interface, alive)
        )
        self.conn.commit()

    def recent_events(self, limit=50):
        rows = self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip([d[0] for d in self.conn.execute("SELECT * FROM events LIMIT 0").description], r)) for r in rows]

db = MeshDB()

# ─── Health Checks ────────────────────────────────────────────────
class HealthChecker:
    """Runs all health checks and returns a status report."""

    @staticmethod
    def cpu_temp():
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return float(f.read().strip()) / 1000.0
        except:
            try:
                out = subprocess.check_output(["sensors"], text=True, timeout=5)
                for line in out.split("\n"):
                    if "Core 0" in line or "Tctl" in line or "CPU" in line:
                        val = float(line.split("+")[1].split("°")[0])
                        return val
            except:
                pass
        return 0.0

    @staticmethod
    def docker_services():
        try:
            out = subprocess.check_output(
                ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
                text=True, timeout=10
            )
            services = {}
            for line in out.strip().split("\n"):
                if "\t" in line:
                    name, status = line.split("\t", 1)
                    services[name] = {"running": "Up" in status, "status": status}
            return services
        except:
            return {}

    @staticmethod
    def system_resources():
        try:
            with open("/proc/meminfo") as f:
                mem = {}
                for line in f:
                    k, v = line.split(":")
                    mem[k.strip()] = int(v.strip().split()[0])
            total = mem.get("MemTotal", 0) / 1024
            avail = mem.get("MemAvailable", 0) / 1024
            used_pct = (1 - avail / total) * 100 if total > 0 else 0

            load = os.getloadavg()
            with open("/proc/uptime") as f:
                uptime = float(f.read().split()[0])

            return {
                "mem_total_mb": round(total),
                "mem_avail_mb": round(avail),
                "mem_used_pct": round(used_pct, 1),
                "load_1": round(load[0], 2),
                "load_5": round(load[1], 2),
                "load_15": round(load[2], 2),
                "uptime_hours": round(uptime / 3600, 1),
            }
        except:
            return {}

    @staticmethod
    def reticulum_status():
        try:
            r = requests.get(f"http://{RNS_HOST}:4242/api/status", timeout=5)
            return r.json() if r.ok else {"error": r.status_code}
        except:
            return {"status": "unreachable"}

    @staticmethod
    def ollama_status():
        try:
            r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
            if r.ok:
                models = [m["name"] for m in r.json().get("models", [])]
                return {"running": True, "models": models}
            return {"running": False}
        except:
            return {"running": False}

    @staticmethod
    def network_interfaces():
        ifaces = {}
        try:
            out = subprocess.check_output(["ip", "-j", "addr"], text=True, timeout=5)
            for i in json.loads(out):
                if i.get("operstate") == "UP":
                    addrs = [a["local"] for a in i.get("addr_info", []) if a.get("family") == "inet"]
                    ifaces[i["ifname"]] = {"up": True, "addrs": addrs}
        except:
            pass
        return ifaces

    def full_check(self):
        temp = self.cpu_temp()
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "thermal": {
                "cpu_temp_c": temp,
                "warning": temp >= THERMAL_WARN,
                "critical": temp >= THERMAL_CRIT,
                "desert_mode": True,
            },
            "docker": self.docker_services(),
            "resources": self.system_resources(),
            "reticulum": self.reticulum_status(),
            "ollama": self.ollama_status(),
            "network": self.network_interfaces(),
        }

# ─── AI Diagnoser ─────────────────────────────────────────────────
class AIDiagnoser:
    """Uses Ollama to diagnose network issues and suggest healing actions."""

    def __init__(self, host=OLLAMA_HOST, model=OLLAMA_MODEL):
        self.host = host
        self.model = model

    def diagnose(self, health_report, events):
        prompt = f"""You are MeshMind, an AI network operations agent for EVEZ, a self-building mesh network in the Mojave Desert (Bullhead City, AZ — summer temps reach 130°F / 54°C).

Current health report:
{json.dumps(health_report, indent=2)}

Recent events:
{json.dumps(events[-10:], indent=2)}

Analyze the situation. For any issues found, provide:
1. DIAGNOSIS: What's wrong and why
2. SEVERITY: low/medium/high/critical
3. ACTION: Specific shell command or step to fix it
4. REASONING: Why this action will help

If everything is healthy, say "MESH HEALTHY" with a brief status summary.
Be concise. Focus on actionable fixes, not theory."""

        try:
            r = requests.post(f"{self.host}/api/generate", json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 512}
            }, timeout=60)
            if r.ok:
                return r.json().get("response", "Diagnosis unavailable")
            return f"Ollama error: {r.status_code}"
        except Exception as e:
            return f"Ollama unreachable: {e}"

# ─── Auto-Healer ──────────────────────────────────────────────────
class AutoHealer:
    """Executes healing actions based on health check results."""

    @staticmethod
    def restart_service(service_name):
        try:
            subprocess.check_call(["docker", "restart", service_name], timeout=30)
            return True
        except:
            return False

    @staticmethod
    def thermal_mitigation(temp):
        """Desert-specific thermal management."""
        actions = []
        if temp >= THERMAL_CRIT:
            # Critical — reduce load aggressively
            actions.append("Thermal critical — stopping non-essential services")
            for svc in ["evez-homer", "evez-grafana", "evez-syncthing"]:
                subprocess.call(["docker", "stop", svc], timeout=30)
                actions.append(f"Stopped {svc}")
        elif temp >= THERMAL_WARN:
            # Warning — reduce CPU load
            actions.append("Thermal warning — applying load reduction")
            # Set CPU to max power savings
            for cpu in os.listdir("/sys/devices/system/cpu/"):
                gov = f"/sys/devices/system/cpu/{cpu}/cpufreq/scaling_governor"
                if os.path.exists(gov):
                    with open(gov, "w") as f:
                        f.write("powersave")
            actions.append("CPU governor → powersave")
        return actions

    @staticmethod
    def network_heal(health_report):
        """Attempt to heal network issues."""
        actions = []
        # Check if Reticulum is down
        rns = health_report.get("reticulum", {})
        if rns.get("status") == "unreachable":
            actions.append("Reticulum unreachable — attempting restart")
            subprocess.call(["docker", "restart", "evez-reticulum"], timeout=30)
            actions.append("Reticulum restarted")

        # Check Docker services
        for name, info in health_report.get("docker", {}).items():
            if not info.get("running"):
                actions.append(f"{name} down — attempting restart")
                subprocess.call(["docker", "restart", name], timeout=30)
                actions.append(f"{name} restarted")

        return actions

# ─── Main Loop ────────────────────────────────────────────────────
checker = HealthChecker()
diagnoser = AIDiagnoser()
healer = AutoHealer()
events_log = deque(maxlen=1000)
last_diagnosis = ""

def health_loop():
    global last_diagnosis
    while True:
        try:
            report = checker.full_check()
            temp = report["thermal"]["cpu_temp_c"]

            # Log thermal events
            if report["thermal"]["critical"]:
                db.log_event("thermal", "critical", "cpu",
                    f"CPU temp {temp}°C exceeds {THERMAL_CRIT}°C",
                    "thermal_mitigation", "")
                actions = healer.thermal_mitigation(temp)
                for a in actions:
                    db.log_event("action", "auto", "healer", a, "executed", "")

            elif report["thermal"]["warning"]:
                db.log_event("thermal", "warning", "cpu",
                    f"CPU temp {temp}°C exceeds {THERMAL_WARN}°C",
                    "thermal_mitigation", "")

            # Log down services
            for name, info in report.get("docker", {}).items():
                if not info.get("running"):
                    db.log_event("service", "high", name,
                        f"Service {name} is down: {info.get('status','unknown')}",
                        "auto_restart", "")
                    healer.restart_service(name)

            # AI diagnosis every 5 minutes
            if int(time.time()) % 300 < CHECK_INTERVAL:
                events = db.recent_events(20)
                if events:
                    diagnosis = diagnoser.diagnose(report, events)
                    last_diagnosis = diagnosis
                    db.log_event("diagnosis", "info", "ai", diagnosis, "", diagnosis)

            # High load check
            load = report.get("resources", {}).get("load_1", 0)
            if load > 4.0:
                db.log_event("load", "warning", "system",
                    f"Load average {load} is high", "", "")

        except Exception as e:
            db.log_event("error", "high", "meshmind", str(e), "", "")

        time.sleep(CHECK_INTERVAL)

# ─── FastAPI ──────────────────────────────────────────────────────
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI(title="EVEZ MeshMind", version="1.0.0")

@app.on_event("startup")
def startup():
    t = threading.Thread(target=health_loop, daemon=True)
    t.start()

@app.get("/")
async def index():
    report = checker.full_check()
    return HTMLResponse(f"""<html><head><title>⚡ EVEZ MeshMind</title>
<style>body{{background:#0a0c14;color:#00ffc8;font-family:monospace;padding:20px;}}
.ok{{color:#0f0}} .warn{{color:#ff0}} .crit{{color:#f00}} a{{color:#00b4ff}}</style></head>
<body>
<h1>⚡ EVEZ MeshMind — AI Network Healer</h1>
<p>Self-healing mesh network • Desert-hardened for Bullhead City, AZ</p>
<h2>🌡️ Thermal</h2>
<p class="{"crit" if report["thermal"]["critical"] else "warn" if report["thermal"]["warning"] else "ok"}">
CPU: {report["thermal"]["cpu_temp_c"]}°C
(Warn: {THERMAL_WARN}°C / Crit: {THERMAL_CRIT}°C)</p>
<h2>🐳 Docker Services</h2>
<ul>{"".join(f'<li class="{"ok" if v["running"] else "crit"}">{k}: {v["status"]}</li>' for k,v in report["docker"].items())}</ul>
<h2>📊 Resources</h2>
<pre>{json.dumps(report["resources"], indent=2)}</pre>
<h2>🧠 Last AI Diagnosis</h2>
<pre>{last_diagnosis or "Waiting for first diagnosis..."}</pre>
<h2>📡 Reticulum</h2>
<pre>{json.dumps(report["reticulum"], indent=2)}</pre>
<hr><a href="/api/health">API: Full Health Report</a> • <a href="/api/events">API: Events</a>
</body></html>""")

@app.get("/api/health")
async def health():
    return checker.full_check()

@app.get("/api/events")
async def events(limit: int = 50):
    return {"events": db.recent_events(limit)}

@app.get("/api/diagnose")
async def diagnose_now():
    report = checker.full_check()
    events = db.recent_events(20)
    diagnosis = diagnoser.diagnose(report, events)
    return {"diagnosis": diagnosis, "health": report}

@app.post("/api/heal")
async def force_heal():
    report = checker.full_check()
    actions = healer.network_heal(report)
    if report["thermal"]["warning"]:
        actions += healer.thermal_mitigation(report["thermal"]["cpu_temp_c"])
    return {"actions_taken": actions, "health": checker.full_check()}


if __name__ == "__main__":
    print(f"⚡ EVEZ MeshMind starting on port {HTTP_PORT}")
    print(f"   Thermal thresholds: warn={THERMAL_WARN}°C, crit={THERMAL_CRIT}°C")
    print(f"   AI model: {OLLAMA_HOST} → {OLLAMA_MODEL}")
    print(f"   Check interval: {CHECK_INTERVAL}s")
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT)
